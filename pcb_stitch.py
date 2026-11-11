"""PCB photo stitching, classical OpenCV + NumPy only (SIFT + RANSAC + least squares). No AI, no Tk: safe in a worker thread.

Pipeline (flat-board engine, cv2.Stitcher is no longer used):
  1. features   : CLAHE-enhanced photo -> many SIFT features (matching resolution MATCH_SIDE)
  2. pair match : every pair, similarity model (rotate + scale + shift), spread / scale sanity checks
  3. placement  : spanning tree of the strongest overlaps, then global least-squares refinement
  4. rejection  : photos that disagree with their neighbours are removed (a wrong place is worse than none)
  5. recovery   : every unplaced photo is matched against ALL features of the mosaic built so far, repeated until
                  nothing more attaches (relaxed settings on the second level)
  6. compose    : native resolution, sharpest source wins per pixel, exposure matching, soft seams, strip by strip
  7. holes      : empty areas completely surrounded by photo = a missing photo
"""
import os
import heapq
from concurrent.futures import ThreadPoolExecutor
import cv2
import numpy as np
from PIL import Image

MATCH_SIDE = 2000                 # photos are matched at this size (was 1200)
N_FEATURES = 8000                 # SIFT features per photo (was 3000)
RATIO = 0.80                      # Lowe ratio test for photo-to-photo matching
MIN_INLIERS = 12                  # similarity model has 4 degrees of freedom, so 12 agreeing points is strong evidence
SPREAD = 0.003                    # inlier points must span at least this fraction of the photo area (not one cluster)
RANSAC_PX = 3.0                   # inlier tolerance (matching-resolution pixels)
SCALE_RANGE = (0.2, 5.0)          # allowed zoom difference between two photos (close-ups among wide shots)
REJECT_PX = 8.0                   # a photo whose matches disagree with its neighbours by more than this is removed
RECOVER_LEVELS = ((0.80, 10), (0.88, 8))   # (ratio, min inliers) for orphan recovery: strict first, relaxed second
DUP_PX = 6.0                      # mosaic points closer than this are the same physical spot seen in two photos
MAX_OUT_PIXELS = 64_000_000       # output map size limit (memory)
STRIP_ROWS = 1024                 # composition works on strips of this height
FLANN = ({"algorithm": 1, "trees": 4}, {"checks": 64})


# ---------------------------------------------------------------------------------------------------- small helpers
def _decode(path):
  img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
  if img is None:
    raise ValueError(f"Cannot read photo: {os.path.basename(path)}")
  return img


def _apply(M, pts):
  pts = np.asarray(pts, np.float64).reshape(-1, 2)
  return pts @ M[:2, :2].T + M[:2, 2]


def _umeyama(S, D):
  """Best similarity transform (rotation + uniform scale + shift) taking points S onto D, closed form, as 3x3."""
  s, d = S[:, 0] + 1j * S[:, 1], D[:, 0] + 1j * D[:, 1]
  ms, md = s.mean(), d.mean()
  sc, dc = s - ms, d - md
  den = float((np.abs(sc) ** 2).sum())
  a = (np.conj(sc) * dc).sum() / den if den > 1e-9 else 1.0 + 0j
  t = md - a * ms
  return np.array([[a.real, -a.imag, t.real], [a.imag, a.real, t.imag], [0, 0, 1.0]])


def _components(nodes, adj):
  seen, out = set(), []
  for s in nodes:
    if s in seen:
      continue
    comp, todo = [], [s]
    seen.add(s)
    while todo:
      i = todo.pop()
      comp.append(i)
      for j in adj[i]:
        if j not in seen:
          seen.add(j)
          todo.append(j)
    out.append(sorted(comp))
  return out


# ---------------------------------------------------------------------------------------------------- 1. features
def _features(path):
  full = _decode(path)
  H0, W0 = full.shape[:2]
  f = MATCH_SIDE / max(H0, W0)
  small = cv2.resize(full, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else full
  del full
  gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
  gray = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)  # faint silkscreen / traces give features too
  kp, des = cv2.SIFT_create(N_FEATURES, contrastThreshold=0.02).detectAndCompute(gray, None)
  h, w = gray.shape
  return {"size": (w, h), "orig": (W0, H0), "des": des, "pts": np.float32([k.pt for k in kp]).reshape(-1, 2)}


# ---------------------------------------------------------------------------------------------------- 2. matching
def _fit(src, dst, area, min_inl):
  """Robust similarity fit src -> dst with sanity checks. Returns (3x3, inlier src pts, inlier dst pts) or None."""
  if len(src) < min_inl:
    return None
  M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=RANSAC_PX,
                                       maxIters=4000, confidence=0.999, refineIters=10)
  if M is None or inl is None:
    return None
  inl = inl.ravel().astype(bool)
  n = int(inl.sum())
  if n < min_inl or (n < 0.2 * len(src) and n < 40):
    return None
  if not SCALE_RANGE[0] < float(np.hypot(M[0, 0], M[1, 0])) < SCALE_RANGE[1]:
    return None
  pa = src[inl]
  if cv2.contourArea(cv2.convexHull(pa.reshape(-1, 1, 2).astype(np.float32))) < SPREAD * area:
    return None  # all matches in one spot: could be a repeated pad pattern, not a real overlap
  return np.vstack([M, [0, 0, 1.0]]), pa.astype(np.float64), dst[inl].astype(np.float64)


def _match_pair(a, b):
  if a["des"] is None or b["des"] is None or len(a["pts"]) < MIN_INLIERS or len(b["pts"]) < MIN_INLIERS:
    return None
  flann = cv2.FlannBasedMatcher(*FLANN)
  good = [m[0] for m in flann.knnMatch(a["des"], b["des"], k=2) if len(m) == 2 and m[0].distance < RATIO * m[1].distance]
  if len(good) < MIN_INLIERS:
    return None
  src = a["pts"][[g.queryIdx for g in good]]
  dst = b["pts"][[g.trainIdx for g in good]]
  area = min(a["size"][0] * a["size"][1], b["size"][0] * b["size"][1])
  return _fit(src, dst, area, MIN_INLIERS)


# ---------------------------------------------------------------------------------------------------- 3. placement
def _spanning(main, adj):
  """Strongest overlaps first. T[i] maps photo i (matching pixels) into the common mosaic plane."""
  root = max(main, key=lambda i: sum(e["n"] for e in adj[i].values()))
  T, order, heap = {root: np.eye(3)}, [root], []

  def push(i):
    for j, e in adj[i].items():
      if j not in T:
        heapq.heappush(heap, (-e["n"], i, j))
  push(root)
  while heap:
    _, i, j = heapq.heappop(heap)
    if j in T:
      continue
    T[j] = T[i] @ np.linalg.inv(adj[i][j]["A"])  # A maps i -> j
    order.append(j)
    push(j)
  return root, T, order


def _refine(T, adj, root, iters=10):
  """Global least squares (Gauss-Seidel): each photo is re-fitted to where all its neighbours say its points belong."""
  ids = [i for i in T if i != root]
  for _ in range(iters):
    for i in ids:
      S, D = [], []
      for j, e in adj[i].items():
        if j in T:
          S.append(e["pi"])
          D.append(_apply(T[j], e["pj"]))
      if S:
        T[i] = _umeyama(np.vstack(S), np.vstack(D))


def _residual(i, T, adj):
  r = [np.linalg.norm(_apply(T[i], e["pi"]) - _apply(T[j], e["pj"]), axis=1) for j, e in adj[i].items() if j in T]
  return float(np.median(np.concatenate(r))) if r else 0.0


def _reachable(root, T, adj):
  seen, todo = {root}, [root]
  while todo:
    i = todo.pop()
    for j in adj[i]:
      if j in T and j not in seen:
        seen.add(j)
        todo.append(j)
  return seen


def _reject_inconsistent(root, T, order, adj):
  """Removes photos whose matches disagree with their neighbours (a confident wrong place would warp the whole map)."""
  for _ in range(6):
    res = {i: _residual(i, T, adj) for i in T if i != root}
    if not res:
      break
    worst = max(res, key=res.get)
    if res[worst] <= REJECT_PX:
      break
    del T[worst]
    order.remove(worst)
    for i in set(T) - _reachable(root, T, adj):
      del T[i]
      order.remove(i)
    _refine(T, adj, root)


# ---------------------------------------------------------------------------------------------------- 5. recovery
def _pool(ids, feats, T):
  P, D = [], []
  for i in ids:
    if feats[i]["des"] is not None and len(feats[i]["pts"]):
      P.append(_apply(T[i], feats[i]["pts"]))
      D.append(feats[i]["des"])
  return (np.vstack(P), np.vstack(D)) if P else (None, None)


def _place(f, matcher, pool_pts, ratio, min_inl):
  """Matches one photo against every feature of the mosaic. Ratio test ignores neighbours that are the same physical
  point seen in another photo (otherwise overlapping photos would make every match look ambiguous)."""
  if f["des"] is None or len(f["pts"]) < min_inl:
    return None
  good = []
  for m in matcher.knnMatch(f["des"], k=5):
    if len(m) < 2:
      continue
    p0 = pool_pts[m[0].trainIdx]
    for nb in m[1:]:
      if np.hypot(*(pool_pts[nb.trainIdx] - p0)) > DUP_PX:
        if m[0].distance < ratio * nb.distance:
          good.append(m[0])
        break
    else:
      good.append(m[0])
  if len(good) < min_inl:
    return None
  src = f["pts"][[g.queryIdx for g in good]]
  dst = pool_pts[[g.trainIdx for g in good]].astype(np.float32)
  r = _fit(src, dst, f["size"][0] * f["size"][1], min_inl)
  return r[0] if r else None


def _recover(feats, T, order, orphans, progress):
  """Retries unplaced photos against the whole mosaic; each success grows the mosaic and may rescue the next one."""
  while orphans:
    pts, des = _pool(order, feats, T)
    if des is None:
      return
    got = False
    for ratio, min_inl in RECOVER_LEVELS:
      matcher = cv2.FlannBasedMatcher(*FLANN)
      matcher.add([des])
      matcher.train()
      for i in sorted(orphans, key=lambda k: -len(feats[k]["pts"])):
        M = _place(feats[i], matcher, pts, ratio, min_inl)
        if M is not None:
          T[i] = M
          order.append(i)
          orphans.remove(i)
          got = True
          progress(None, f"Recovered a small piece ({len(orphans)} left)")
      if got:
        break
    if not got:
      return


# ---------------------------------------------------------------------------------------------------- 6. composition
def _gain(canvas, weight, img, M, x0, y0, x1, y1):
  """Per-channel brightness gain that makes this photo match what is already on the map where they overlap."""
  st = 4
  rw, rh = (x1 - x0) // st, (y1 - y0) // st
  one = np.ones(3, np.float32)
  if rw < 8 or rh < 8:
    return one
  Ms = np.array([[1 / st, 0, -x0 / st], [0, 1 / st, -y0 / st], [0, 0, 1.0]]) @ M
  small = cv2.warpAffine(img, Ms[:2], (rw, rh), flags=cv2.INTER_LINEAR)
  valid = cv2.erode(cv2.warpAffine(np.full(img.shape[:2], 255, np.uint8), Ms[:2], (rw, rh), flags=cv2.INTER_NEAREST),
                    np.ones((3, 3), np.uint8)) > 0
  ref = canvas[y0:y0 + rh * st:st, x0:x0 + rw * st:st]
  m = valid & (weight[y0:y0 + rh * st:st, x0:x0 + rw * st:st] > 0)
  m &= (small.max(2) < 250) & (ref.max(2) < 250) & (small.min(2) > 5)  # ignore glare and crushed shadows
  if int(m.sum()) < 400:
    return one
  g = np.median(ref[m].astype(np.float32), axis=0) / np.maximum(np.median(small[m].astype(np.float32), axis=0), 1.0)
  return np.clip(g, 0.75, 1.33).astype(np.float32)


def _paste(canvas, weight, img, M, interp):
  H, W = weight.shape
  h, w = img.shape[:2]
  quad = _apply(M, [[0, 0], [w, 0], [w, h], [0, h]])
  x0, y0 = (max(0, int(np.floor(v)) - 1) for v in quad.min(0))
  x1, y1 = (min(lim, int(np.ceil(v)) + 2) for v, lim in zip(quad.max(0), (W, H)))
  if x1 <= x0 or y1 <= y0:
    return
  gain = _gain(canvas, weight, img, M, x0, y0, x1, y1)
  ones = np.full((h, w), 255, np.uint8)
  rw = x1 - x0
  for ys in range(y0, y1, STRIP_ROWS):
    ye = min(ys + STRIP_ROWS, y1)
    Ms = M.copy()
    Ms[0, 2] -= x0
    Ms[1, 2] -= ys
    warped = cv2.warpAffine(img, Ms[:2], (rw, ye - ys), flags=interp)
    valid = cv2.erode(cv2.warpAffine(ones, Ms[:2], (rw, ye - ys), flags=cv2.INTER_NEAREST), np.ones((3, 3), np.uint8))
    vb = valid > 0
    if not vb.any():
      continue
    if np.abs(gain - 1).max() > 0.01:
      warped = np.clip(warped.astype(np.float32) * gain, 0, 255).astype(np.uint8)
    # per-pixel quality: local sharpness (detail), faded near the photo's own border so seams land on its edge
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    sharp = cv2.GaussianBlur(np.abs(cv2.Laplacian(gray, cv2.CV_32F, ksize=3)), (0, 0), 6)
    wn = (sharp + 1.0) * np.minimum(cv2.distanceTransform(valid, cv2.DIST_L2, 3), 48.0) / 48.0
    wn[~vb] = 0
    cs = canvas[ys:ye, x0:x1]
    ws = weight[ys:ye, x0:x1].astype(np.float32)
    win = vb & (wn > 1.15 * ws)  # small bias to the incumbent: no patchy flip-flopping in flat areas
    alpha = cv2.GaussianBlur(win.astype(np.float32), (0, 0), 2.5)  # soft seam
    alpha[ws == 0] = 1.0
    alpha *= vb
    cs[:] = (cs * (1 - alpha[..., None]) + warped * alpha[..., None]).astype(np.uint8)
    weight[ys:ye, x0:x1] = np.where(win, np.minimum(wn, 60000.0), ws).astype(np.float16)


def _compose(paths, feats, T, order, progress):
  ids = list(order)
  fm = {i: feats[i]["size"][0] / feats[i]["orig"][0] for i in ids}                      # matching px per full-res px
  Fm = {i: np.diag([fm[i], fm[i], 1.0]) for i in ids}
  sc = {i: float(np.sqrt(abs(np.linalg.det((T[i] @ Fm[i])[:2, :2])))) for i in ids}     # mosaic units per full-res px
  corners = np.vstack([_apply(T[i], [[0, 0], [feats[i]["size"][0], 0], feats[i]["size"], [0, feats[i]["size"][1]]])
                       for i in ids])
  mn, mx = corners.min(0), corners.max(0)
  ext = mx - mn
  if not np.all(np.isfinite(ext)) or ext.min() <= 0:
    raise RuntimeError("Photo positions are inconsistent; try again with more overlap.")
  k = min(1.0 / min(sc.values()), float(np.sqrt(MAX_OUT_PIXELS / (ext[0] * ext[1]))))  # keep the best photo's detail
  W, H = int(np.ceil(ext[0] * k)) + 1, int(np.ceil(ext[1] * k)) + 1
  Sout = np.array([[k, 0, -k * mn[0]], [0, k, -k * mn[1]], [0, 0, 1.0]])
  canvas = np.zeros((H, W, 3), np.uint8)
  weight = np.zeros((H, W), np.float16)
  for n, i in enumerate(ids):
    progress(0.70 + 0.26 * n / len(ids), f"Blending photo {n + 1} of {len(ids)}")
    img = _decode(paths[i])
    g = min(1.0, k * sc[i])  # output pixels per photo pixel: shrink big photos with INTER_AREA first (sharper)
    if g < 0.95:
      img = cv2.resize(img, None, fx=g, fy=g, interpolation=cv2.INTER_AREA)
    else:
      g = 1.0
    M = Sout @ T[i] @ Fm[i] @ np.diag([1 / g, 1 / g, 1.0])
    _paste(canvas, weight, img, M, cv2.INTER_CUBIC if k * sc[i] / g > 1.05 else cv2.INTER_LINEAR)
    del img
  return canvas, (weight > 0).astype(np.uint8) * 255


# ---------------------------------------------------------------------------------------------------- 7. holes
def _trim_and_holes(pano, mask):
  """Cuts the empty border and finds every gap = empty area inside the outline of the covered region (a missing photo),
  whether or not it touches the edge. Gaps are painted with a grey hatch so they can never be mistaken for board."""
  x, y, w, h = cv2.boundingRect(mask)
  pano, mask = pano[y:y + h, x:x + w], mask[y:y + h, x:x + w]
  f = min(1.0, 1000 / max(w, h))
  small = cv2.resize(mask, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) > 200
  small = cv2.morphologyEx(small.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
  cnts, _ = cv2.findContours(small, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
  hull = np.zeros_like(small)
  if cnts:
    cv2.fillPoly(hull, [cv2.convexHull(np.vstack(cnts))], 1)
  gap = cv2.morphologyEx(hull & (1 - small), cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))  # thin slivers at ragged edges are not gaps
  n, lab, stats, _ = cv2.connectedComponentsWithStats(gap, connectivity=4)
  inner = cv2.erode(hull, np.ones((5, 5), np.uint8), iterations=2, borderType=cv2.BORDER_CONSTANT, borderValue=0)
  min_area = max(40, 0.003 * int(hull.sum()))           # a hole fully surrounded by photo is certainly a missing photo
  min_edge = 0.02 * int(hull.sum())                      # a gap on the outline may just be ragged edge: must be big
  holes, keep = [], np.zeros_like(gap)
  for k in range(1, n):
    if stats[k, cv2.CC_STAT_AREA] < min_area:
      continue
    comp = (lab == k).astype(np.uint8)
    if (comp & (1 - inner)).any() and stats[k, cv2.CC_STAT_AREA] < min_edge:
      continue
    keep |= comp
    dist = cv2.distanceTransform(comp, cv2.DIST_L2, 3)
    cy, cx = np.unravel_index(int(dist.argmax()), dist.shape)  # the deepest point of the gap, always inside it
    holes.append((float((cx + 0.5) / f), float((cy + 0.5) / f)))
  if holes:  # hatch, painted strip by strip to keep memory low
    big = cv2.resize(keep, (w, h), interpolation=cv2.INTER_NEAREST) > 0
    xx = np.arange(w)
    for ys in range(0, h, STRIP_ROWS):
      ye = min(ys + STRIP_ROWS, h)
      m = big[ys:ye] & (mask[ys:ye] == 0)
      if m.any():
        stripe = (((xx[None, :] + np.arange(ys, ye)[:, None]) // 14) % 2).astype(np.uint8)
        pano[ys:ye][m] = np.where(stripe[m][:, None] == 1, 105, 55).astype(np.uint8)
  return pano, holes


# ---------------------------------------------------------------------------------------------------- entry point
def run(paths, progress):
  """progress(fraction or None, text). Returns {"image", "holes", "warnings", "info", "skipped"}.
  Every photo is always used (the sharpest source wins per pixel, so extra photos cost nothing)."""
  n, names = len(paths), [os.path.basename(p) for p in paths]

  feats = []
  with ThreadPoolExecutor(min(3, os.cpu_count() or 2)) as pool:  # OpenCV releases the GIL
    for i, f in enumerate(pool.map(_features, paths)):
      progress(0.25 * (i + 1) / n, f"Reading photo {i + 1} of {n}")
      feats.append(f)

  adj = {i: {} for i in range(n)}
  pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
  with ThreadPoolExecutor(os.cpu_count() or 2) as pool:
    for k, ((i, j), r) in enumerate(zip(pairs, pool.map(lambda p: _match_pair(feats[p[0]], feats[p[1]]), pairs))):
      if k % 5 == 0:
        progress(0.25 + 0.25 * k / len(pairs), f"Matching photos ({k}/{len(pairs)})")
      if r:
        M, pa, pb = r
        adj[i][j] = {"A": M, "n": len(pa), "pi": pa, "pj": pb}
        adj[j][i] = {"A": np.linalg.inv(M), "n": len(pa), "pi": pb, "pj": pa}

  main = max(_components(range(n), adj), key=len)
  if len(main) < 2:
    raise RuntimeError("No two photos overlap. Take photos that share part of the board.")

  progress(0.52, "Placing photos")
  root, T, order = _spanning(main, adj)
  _refine(T, adj, root)
  _reject_inconsistent(root, T, order, adj)

  progress(0.60, "Recovering small pieces")
  orphans = [i for i in range(n) if i not in T]
  _recover(feats, T, order, orphans, progress)

  progress(0.70, "Building the map")
  pano, mask = _compose(paths, feats, T, order, progress)

  progress(0.97, "Checking for missing photos")
  pano, holes = _trim_and_holes(pano, mask)

  warnings = []
  for i in orphans:
    nf = len(feats[i]["pts"])
    if nf < 150:
      warnings.append(f"Could not place: {names[i]} (too little texture, {nf} features)")
    else:
      warnings.append(f"Could not place: {names[i]} (no overlap found with the rest of the board)")
  if holes:
    warnings.insert(0, f"Image missing x{len(holes)} (marked on the map)")
  info = f"Used {len(T)} of {n} photos."
  return {"image": Image.fromarray(cv2.cvtColor(pano, cv2.COLOR_BGR2RGB)), "holes": holes,
          "warnings": warnings, "info": info, "skipped": []}


def enhance(img, sharp=0, bright=0, detail=0):
  """Non-destructive helper: always called on the untouched original. Works on lightness only, so colours never shift."""
  if not (sharp or bright or detail):
    return img
  lab = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2LAB)
  L = lab[..., 0]
  if detail:
    L = cv2.createCLAHE(clipLimit=1 + detail / 25, tileGridSize=(8, 8)).apply(L)
  if sharp:
    s = sharp / 50
    L = cv2.addWeighted(L, 1 + s, cv2.GaussianBlur(L, (0, 0), 2), -s, 0)
  if bright:
    L = cv2.convertScaleAbs(L, alpha=1, beta=bright)
  lab[..., 0] = L
  return Image.fromarray(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB))
