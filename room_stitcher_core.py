"""
Red Circuit - Room Stitcher Core (pure OpenCV / NumPy, no UI)

A full manual panorama pipeline (the same stages professional stitchers use),
instead of the black-box cv2.Stitcher:

  1. Load + de-blur filter      drop photos that are clearly motion-blurred
  2. SIFT features              far more reliable than ORB on plain painted walls
  3. Matching                   all-pairs (closes the 360 loop) or windowed (fast)
  4. Bundle adjustment          solves every camera's rotation + focal length together
  5. Wave correction            keeps the horizon level
  6. Exposure compensation      removes brightness steps between photos
  7. Graph-cut seams            seams routed around objects, not through them
  8. Multi-band blending        invisible joins, at near-original resolution
  9. Auto-crop                  removes ragged black borders

Projection modes
  "spherical"    : whole room incl. ceiling + floor (360 x 180).
  "cylindrical"  : flat wall view. Photos aimed steeply up/down are left out
                   (they would be stretched to infinity), and the result is
                   cropped to a clean rectangle.
"""

import os
import re
import math
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

PRESETS = {
    # work_mp : megapixels per photo used for feature detection / alignment
    # feats   : SIFT keypoints per photo
    # pano_mp : maximum size of the final panorama in megapixels
    # seam_mp : megapixels per photo used when routing seams
    # match   : "full" = every photo vs every photo, "range" = neighbours only
    "Fast":     dict(work_mp=0.40, feats=1200, pano_mp=10, seam_mp=0.10, match="range"),
    "Balanced": dict(work_mp=0.70, feats=2000, pano_mp=30, seam_mp=0.20, match="full"),
    "Maximum":  dict(work_mp=1.00, feats=3000, pano_mp=60, seam_mp=0.30, match="full"),
}

# Matching sensitivity ladder: tried in order until >=90% of photos link up.
# Rung 2 is for hard rooms (plain painted walls, dim light, few details).
LADDER = [
    dict(conf=0.70, min_m=8, ratio=0.65, feats_mul=1.0, clahe=True),
    dict(conf=0.40, min_m=6, ratio=0.75, feats_mul=2.0, clahe=True),
]
RANGE_WIDTH = 25          # neighbours matched in "range" mode
BLUR_FRACTION = 0.35      # photos sharper than this fraction of the median are kept
MAX_PITCH_DEG = 50.0      # cylindrical mode: ignore photos aimed steeper than this


class StitchError(Exception):
    pass


def natural_key(path):
    """IMG_2.jpg sorts before IMG_10.jpg (capture order)."""
    name = os.path.basename(path)
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _noop(*_a, **_k):
    pass


# ----------------------------------------------------------------------------- helpers
def _make_finder(n_feats):
    try:
        return cv2.SIFT_create(nfeatures=n_feats)
    except Exception:
        return cv2.ORB_create(nfeatures=n_feats * 2)


def _to_np(x):
    return x.get() if hasattr(x, "get") else x


def resolve_quality(quality, n):
    """'Auto' picks a preset from the photo count."""
    if quality in PRESETS:
        return quality
    return "Balanced" if n <= 150 else "Fast"


def _ram_cap_mp():
    """Largest panorama (megapixels) that should fit in free RAM (needs psutil; else no cap)."""
    try:
        import psutil
        return max(4.0, psutil.virtual_memory().available * 0.3 / 60.0 / 1e6)
    except Exception:
        return 1e9


def _auto_mode(cameras):
    """Full turn (>=300 deg of yaw covered) -> spherical, otherwise flat wall view."""
    yaws = sorted(math.degrees(math.atan2(float(c.R[2, 0]), float(c.R[2, 2]))) for c in cameras)
    gaps = [yaws[i + 1] - yaws[i] for i in range(len(yaws) - 1)] + [yaws[0] + 360 - yaws[-1]]
    return "spherical" if 360 - max(gaps) >= 300 else "cylindrical"


def _largest_rect(mask):
    """Largest axis-aligned all-True rectangle in a boolean mask -> (x, y, w, h)."""
    H, W = mask.shape
    heights = [0] * W
    best = (0, 0, 0, 0, 0)
    rows = mask.tolist()
    for y in range(H):
        row = rows[y]
        for x in range(W):
            heights[x] = heights[x] + 1 if row[x] else 0
        stack = []
        for x in range(W + 1):
            h = heights[x] if x < W else 0
            start = x
            while stack and stack[-1][1] >= h:
                s, sh = stack.pop()
                area = sh * (x - s)
                if area > best[0]:
                    best = (area, s, y - sh + 1, x - s, sh)
                start = s
            stack.append((start, h))
    return best[1:]


def _auto_crop(img, valid, inscribed):
    """Crops black borders. inscribed=True -> clean rectangle (if it keeps most of the picture)."""
    ys, xs = np.where(valid)
    if len(xs) == 0:
        return img
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    img, valid = img[y0:y1, x0:x1], valid[y0:y1, x0:x1]
    if not inscribed:
        return img[2:-2, 2:-2] if min(img.shape[:2]) > 100 else img   # drop partially-covered edge pixels

    H, W = valid.shape
    f = min(1.0, 900.0 / max(H, W))
    small = cv2.resize(valid.astype(np.uint8), (max(1, int(W * f)), max(1, int(H * f))),
                       interpolation=cv2.INTER_NEAREST)
    small = cv2.erode(small, np.ones((3, 3), np.uint8))
    x, y, w, h = _largest_rect(small.astype(bool))
    if w * h < 0.5 * small.sum():          # rectangle would throw away too much -> keep bbox
        return img
    x, y, w, h = int(x / f), int(y / f), int(w / f), int(h / f)
    mx, my = int(w * 0.005) + 1, int(h * 0.005) + 1   # tiny inward margin against edge pixels
    return img[y + my:y + h - my, x + mx:x + w - mx]


# ----------------------------------------------------------------------------- main entry
def _stitch_once(paths, mode, quality, emit, pano_scale):
    """
    paths   : list of photo file paths
    mode    : "spherical" | "cylindrical"
    quality : key of PRESETS
    emit    : callback receiving
                ("p", fraction, text)                     fixed progress
                ("stage", start, end, text, tau_seconds)  creeping progress for one long OpenCV call
    returns : (BGR uint8 ndarray, info dict)
    """
    cfg = dict(PRESETS[quality])
    cfg["pano_mp"] = min(cfg["pano_mp"], _ram_cap_mp()) * pano_scale
    paths = sorted(paths, key=natural_key)
    n_in = len(paths)
    warp_type = None   # decided after cameras are known (mode may be "auto")
    workers = max(2, min(8, os.cpu_count() or 4))
    cv2.setNumThreads(os.cpu_count() or 4)

    # ------------------------------------------------------------ 1. load work-size copies (0 -> 8%)
    sc_box = {}

    def load_work(p):
        img = cv2.imread(p)
        if img is None:
            return None
        h, w = img.shape[:2]
        if "sc" not in sc_box:                       # one common scale for the whole set
            sc_box["sc"] = min(1.0, math.sqrt(cfg["work_mp"] * 1e6 / (h * w)))
        sc = sc_box["sc"]
        small = img if sc >= 1 else cv2.resize(img, (max(1, round(w * sc)), max(1, round(h * sc))),
                                               interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return dict(path=p, size=(w, h), img=small, sharp=float(cv2.Laplacian(gray, cv2.CV_64F).var()))

    # first photo alone (defines the scale), then the rest in parallel
    items = []
    first = load_work(paths[0])
    if first is not None:
        items.append(first)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, it in enumerate(pool.map(load_work, paths[1:]), start=2):
            if it is not None:
                items.append(it)
            emit("p", 0.08 * i / n_in, f"Step 1/6: Loading photos {i}/{n_in} ({int(8 * i / n_in)}%)...")
    if len(items) < 2:
        raise StitchError("Fewer than 2 valid images could be loaded.")

    # drop clearly blurred shots (only if plenty remain)
    med = float(np.median([it["sharp"] for it in items]))
    sharp_items = [it for it in items if it["sharp"] >= BLUR_FRACTION * med]
    if len(sharp_items) >= max(2, int(0.7 * len(items))):
        dropped_blur = len(items) - len(sharp_items)
        items = sharp_items
    else:
        dropped_blur = 0
    sc = sc_box["sc"]
    n = len(items)

    # ------------------------------------------------------------ 2+3. features + matching (8 -> 45%)
    def make_feat(tune):
        done = [0]

        def feat(it):
            g = cv2.cvtColor(it["img"], cv2.COLOR_BGR2GRAY)
            if tune["clahe"]:                      # local contrast boost: finds detail on plain walls
                g = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(g)
            f = cv2.detail.computeImageFeatures2(_make_finder(int(cfg["feats"] * tune["feats_mul"])), g)
            done[0] += 1
            emit("p", 0.08 + 0.17 * done[0] / len(items),
                 f"Step 2/6: Finding feature points {done[0]}/{len(items)} ({int(8 + 17 * done[0] / len(items))}%)...")
            return f
        return feat

    def run_matcher(feats, kind, tune):
        if kind == "range":
            m = cv2.detail_BestOf2NearestRangeMatcher(RANGE_WIDTH, False, tune["ratio"], tune["min_m"], tune["min_m"])
        else:
            m = cv2.detail_BestOf2NearestMatcher(False, tune["ratio"], tune["min_m"], tune["min_m"])
        pm = m.apply2(feats)
        m.collectGarbage()
        return pm

    kinds = ["range", "full"] if (cfg["match"] == "range" and n > RANGE_WIDTH + 5) else ["full"]
    n0 = n
    best, retried = None, False
    for r, tune in enumerate(LADDER):
        if r:
            retried = True
            emit("p", 0.08, "Photos are hard to match - retrying with more sensitive settings...")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            features = list(pool.map(make_feat(tune), items))
        for kind in kinds:
            label = "neighbouring photos" if kind == "range" else "all photo pairs"
            est = (n0 * RANGE_WIDTH if kind == "range" else n0 * (n0 - 1) / 2) * 0.004 * tune["feats_mul"]
            emit("stage", 0.25, 0.45, f"Step 3/6: Matching {label} ({n0} photos)", max(4.0, est))
            pm = run_matcher(features, kind, tune)
            idx = np.array(cv2.detail.leaveBiggestComponent(features, pm, tune["conf"])).flatten()
            keep = [int(i) for i in idx]
            if len(keep) >= 0.9 * n0:
                break                                 # good enough; otherwise retry with all pairs
        if best is None or len(keep) > len(best[0]):
            best = (keep, features, kind, pm, tune)
        if len(keep) >= 0.9 * n0:
            break
    emit("stage_end")
    keep, features, kind, pm, tune = best

    if len(keep) < 2:
        raise StitchError("These photos could not be linked together. Neighbouring photos need about "
                          "30-50% overlap, and should be taken from one spot (turn on the spot, don't walk).")
    if len(keep) < n:                                 # re-match only the photos that belong together
        items = [items[i] for i in keep]
        features = [features[i] for i in keep]
        n = len(items)
        pm = run_matcher(features, kind, tune)

    # ------------------------------------------------------------ 4. cameras (45 -> 50%)
    emit("p", 0.46, "Step 4/6: Solving camera positions (46%)...")
    ok, cameras = cv2.detail_HomographyBasedEstimator().apply(features, pm, None)
    if not ok:
        raise StitchError("Camera estimation failed. Photos need more overlap or more visible detail.")
    for cam in cameras:
        cam.R = cam.R.astype(np.float32)

    adjuster = cv2.detail_BundleAdjusterRay()
    adjuster.setConfThresh(1.0)
    mask = np.zeros((3, 3), np.uint8)
    mask[0, 0] = mask[0, 1] = mask[0, 2] = mask[1, 1] = mask[1, 2] = 1
    adjuster.setRefinementMask(mask)
    ok, cameras = adjuster.apply(features, pm, cameras)
    if not ok:
        raise StitchError("Camera refinement failed. Try 'Maximum' quality or add more overlapping photos.")
    del features, pm

    # level horizon
    rmats = cv2.detail.waveCorrect([np.copy(c.R) for c in cameras], cv2.detail.WAVE_CORRECT_HORIZ)
    for c, R in zip(cameras, rmats):
        c.R = R

    if mode == "auto":
        mode = _auto_mode(cameras)
    warp_type = "spherical" if mode == "spherical" else "cylindrical"

    # cylindrical: leave out photos aimed steeply up/down
    dropped_pitch = 0
    if warp_type == "cylindrical":
        good = [i for i, c in enumerate(cameras)
                if abs(math.degrees(math.asin(max(-1.0, min(1.0, float(c.R[2, 1])))))) <= MAX_PITCH_DEG]
        if len(good) >= 2 and len(good) < len(cameras):
            dropped_pitch = len(cameras) - len(good)
            cameras = [cameras[i] for i in good]
            items = [items[i] for i in good]
            n = len(items)

    focals = sorted(c.focal for c in cameras)
    warped_scale = focals[len(focals) // 2] if len(focals) % 2 else \
        0.5 * (focals[len(focals) // 2 - 1] + focals[len(focals) // 2])

    # ------------------------------------------------------------ 5. exposure + seams (50 -> 62%)
    emit("stage", 0.50, 0.62, f"Step 5/6: Matching brightness and finding seams ({n} photos)", 6.0 + n * 0.25)
    f_seam = min(1.0, math.sqrt(cfg["seam_mp"] / cfg["work_mp"]))
    swa = f_seam
    seam_warper = cv2.PyRotationWarper(warp_type, warped_scale * swa)
    corners, sizes, imgs_w, masks_w = [], [], [], []
    for it, cam in zip(items, cameras):
        small = it["img"] if f_seam >= 1 else cv2.resize(it["img"], None, fx=f_seam, fy=f_seam,
                                                         interpolation=cv2.INTER_AREA)
        K = cam.K().astype(np.float32)
        K[0, 0] *= swa; K[0, 2] *= swa; K[1, 1] *= swa; K[1, 2] *= swa
        corner, wimg = seam_warper.warp(small, K, cam.R, cv2.INTER_LINEAR, cv2.BORDER_REFLECT)
        m0 = 255 * np.ones(small.shape[:2], np.uint8)
        _, wmask = seam_warper.warp(m0, K, cam.R, cv2.INTER_NEAREST, cv2.BORDER_CONSTANT)
        corners.append(corner); sizes.append((wimg.shape[1], wimg.shape[0]))
        imgs_w.append(_to_np(wimg)); masks_w.append(_to_np(wmask))

    # global per-photo gain: cheap and stable even when 100+ photos overlap (block-wise gain blows up in memory)
    compensator = cv2.detail_GainCompensator()
    compensator.feed(corners=corners, images=imgs_w, masks=masks_w)

    if n <= 200:
        seam_finder = cv2.detail_GraphCutSeamFinder("COST_COLOR")
    else:
        seam_finder = cv2.detail_DpSeamFinder("COLOR")
    seam_masks = seam_finder.find([im.astype(np.float32) for im in imgs_w], corners, masks_w)
    seam_masks = [_to_np(m) for m in seam_masks]
    del imgs_w, masks_w
    emit("stage_end")

    # ------------------------------------------------------------ 6. plan output size
    orig_sizes = [it["size"] for it in items]

    def plan(c):
        asp = c / sc
        warper = cv2.PyRotationWarper(warp_type, warped_scale * asp)
        cs, ss = [], []
        for cam, (w, h) in zip(cameras, orig_sizes):
            K = cam.K().astype(np.float32)
            K[0, 0] *= asp; K[0, 2] *= asp; K[1, 1] *= asp; K[1, 2] *= asp
            roi = warper.warpRoi((int(w * c), int(h * c)), K, cam.R)
            cs.append((roi[0], roi[1])); ss.append((roi[2], roi[3]))
        return warper, cs, ss, cv2.detail.resultRoi(corners=cs, sizes=ss)

    cscale = 1.0
    for _ in range(8):
        warper, corners, sizes, dst = plan(cscale)
        px = dst[2] * dst[3]
        if px <= cfg["pano_mp"] * 1e6:
            break
        cscale *= math.sqrt(cfg["pano_mp"] * 1e6 / px) * 0.98
    if dst[2] * dst[3] > cfg["pano_mp"] * 1.5e6:
        raise StitchError("The panorama came out absurdly large - the alignment is unreliable. "
                          "Use photos with clearer overlap, or try the 'Wall view' mode.")
    asp = cscale / sc

    # ------------------------------------------------------------ 7. warp + blend (62 -> 97%)
    blend_width = math.sqrt(dst[2] * dst[3]) * 5 / 100.0
    bands = int(max(1, min(8, math.log(max(blend_width, 2.0)) / math.log(2.0) - 1)))
    blender = cv2.detail_MultiBandBlender()
    blender.setNumBands(bands)
    blender.prepare(dst)

    def load_full(i):
        img = cv2.imread(items[i]["path"])
        if cscale < 1:
            img = cv2.resize(img, (int(img.shape[1] * cscale), int(img.shape[0] * cscale)),
                             interpolation=cv2.INTER_AREA)
        return img

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=3) as pool:
        window = deque(pool.submit(load_full, i) for i in range(min(4, n)))
        nxt = len(window)
        for i in range(n):
            img = window.popleft().result()
            if nxt < n:
                window.append(pool.submit(load_full, nxt)); nxt += 1

            K = cameras[i].K().astype(np.float32)
            K[0, 0] *= asp; K[0, 2] *= asp; K[1, 1] *= asp; K[1, 2] *= asp
            corner, wimg = warper.warp(img, K, cameras[i].R, cv2.INTER_LINEAR, cv2.BORDER_REFLECT)
            wimg = _to_np(wimg)
            m0 = 255 * np.ones(img.shape[:2], np.uint8)
            _, wmask = warper.warp(m0, K, cameras[i].R, cv2.INTER_NEAREST, cv2.BORDER_CONSTANT)
            wmask = _to_np(wmask)

            compensator.apply(i, corner, wimg, wmask)
            seam = cv2.resize(cv2.dilate(seam_masks[i], None), (wmask.shape[1], wmask.shape[0]),
                              interpolation=cv2.INTER_LINEAR)
            wmask = cv2.bitwise_and(seam, wmask)
            blender.feed(wimg.astype(np.int16), wmask, corner)

            frac = (i + 1) / n
            el = time.time() - t0
            emit("p", 0.62 + 0.35 * frac, f"Step 6/6: Blending photo {i+1}/{n} ({int(62 + 35 * frac)}%, {int(el)}s)...")

    emit("p", 0.97, "Finishing: combining all photos (97%)...")
    result, result_mask = blender.blend(None, None)
    result = np.clip(_to_np(result), 0, 255).astype(np.uint8)
    valid = _to_np(result_mask) > 0

    emit("p", 0.985, "Finishing: trimming borders (98%)...")
    result = _auto_crop(result, valid, inscribed=(mode == "cylindrical"))

    info = dict(mode=mode, retried=retried, total=n_in, used=n, dropped_blur=dropped_blur, dropped_pitch=dropped_pitch,
                width=result.shape[1], height=result.shape[0])
    return result, info


def stitch_room(paths, mode="auto", quality="Auto", emit=_noop):
    """
    Public entry point.
      mode    : "auto" | "spherical" | "cylindrical"
      quality : "Auto" | "Fast" | "Balanced" | "Maximum"
    Automatically retries at lower output resolution if memory runs out.
    returns (BGR uint8 ndarray, info dict)
    """
    quality = resolve_quality(quality, len(paths))
    for scale in (1.0, 0.5, 0.25):
        try:
            img, info = _stitch_once(paths, mode, quality, emit, scale)
            info["quality"], info["pano_scale"] = quality, scale
            return img, info
        except (MemoryError, cv2.error) as e:
            if isinstance(e, cv2.error) and "memory" not in str(e).lower():
                raise
            emit("p", 0.0, "Low on memory - retrying at lower resolution...")
    raise StitchError("Out of memory even at reduced resolution. Close other programs or use Fast quality.")
