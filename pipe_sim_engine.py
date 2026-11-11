"""
Pipe Simulation Engine
======================
Physics calculations, Verlet integration, fluid dynamics, jet particle updates,
and low-resolution wetness map math for the Red Circuit pipe simulation.
"""
import math
import time
from PIL import Image

try:
    import numpy as np
except ImportError:
    np = None

TAG = "pipe_sim"       # Canvas item tag for all simulation elements
BIND_TAG = "PipeSimTag" # Private Tkinter bindtag for mouse input capture
G = 1400.0             # gravity, pixels / s^2
N_PTS = 16             # spine points
SEG = 26.0             # rest length of one segment (px, at scale 1)
RAD = 15.0             # pipe radius (px, at scale 1)
POOL = 300             # droplet canvas items (fixed pool)
Z_PERSP = 600.0        # pseudo-3D depth: bigger = weaker perspective
FRAME_MS = 33          # ~30 FPS target
DROP_COLORS = ("#6fb4e8", "#93cdf2", "#b9e1f8", "#dff2fd")  # light spray droplets, slow -> fast
JK = 140   # slices of the continuous water jet
FK = 160   # foam blobs at the impact point


def new_jet_arrays():
    z = lambda: np.zeros(JK)
    return {
        "x": z(), "y": z(), "vx": z(), "vy": z(), "v0": np.ones(JK), "born": z(),
        "alive": np.zeros(JK, bool), "hit": np.zeros(JK, bool), "hw": z(), "nx": z(), "ny": z(),
        "hs": z(), "hitt": z(), "segid": np.zeros(JK, int), "jit": np.ones(JK), "w0": np.ones(JK)
    }


def make_noise():
    """Seamless tileable turbulence texture (uint8, 2048x2048) used for the frothy look."""
    rng = np.random.default_rng(7)
    acc = np.zeros((1024, 1024), np.float32)
    for cells, wgt in ((16, 0.18), (32, 0.27), (64, 0.30), (128, 0.25)):
        sc = 1024 // cells
        g = np.pad(rng.random((cells, cells)).astype(np.float32), 2, mode="wrap")
        im = Image.fromarray((g * 255).astype(np.uint8)).resize(((cells + 4) * sc, (cells + 4) * sc),
                                                                Image.Resampling.BICUBIC)
        acc += np.asarray(im, np.float32)[2 * sc:2 * sc + 1024, 2 * sc:2 * sc + 1024] / 255.0 * wgt
    acc = np.clip((acc - acc.min()) / (acc.max() - acc.min()), 0, 1)
    acc = np.clip((acc - 0.5) * 2.6 + 0.5, 0, 1)
    return np.tile((acc * 255).astype(np.uint8), (2, 2))


def init_pipe_state(W, H):
    rng = np.random.default_rng()
    pos = np.zeros((N_PTS, 3))
    pos[:, 0] = W * 0.25 + np.arange(N_PTS) * SEG
    pos[:, 1] = H * 0.35

    S = {
        "W": W, "H": H, "ox": 0.0, "oy": 0.0,
        "pos": pos, "prev": pos.copy(),
        "scale": 1.0, "pressure": 1000.0, "pinned": False, "anchor": pos[0, :2].copy(),
        "grab": None, "mode": None, "target": np.zeros(2), "cur": np.zeros(2), "off": np.zeros(2),
        "mouse": (0, 0), "mv": [0.0, 0.0], "mtime": 0.0, "pend": [0.0, 0.0], "phase": 0.0,
        "reach": 0.0, "exit": 0.0, "fdir": np.array([1.0, 0.0]), "evel": np.zeros(2), "acc": 0.0,
        "rad": np.full(N_PTS, RAD),
        "ev": np.arange(0, N_PTS - 1, 2), "od": np.arange(1, N_PTS - 1, 2),
        "frame": 0, "wet_dt": 0.0, "wet_every": 4, "jet_every": 2, "jk": 3, "wet_vis": False,
        "max_active": POOL, "ema": 16.0, "fcount": 0, "last": time.perf_counter(), "job": None,
        "wc": [-1.0] * (N_PTS - 1), "wvis": [False] * (N_PTS - 1), "ringvis": False,
        "it": {}, "time": 0.0, "segid": 0, "emitting": False, "jet_vis": False, "noff": np.zeros(2), "jet_tk": None,
        "rng": rng
    }
    S["j"] = new_jet_arrays()
    S["foam"] = {k: np.zeros(FK) for k in ("fx", "fy", "fr", "fvx", "fvy", "life", "lmax", "sx", "sy")}
    S["foam"]["alive"] = np.zeros(FK, bool)
    S["noise"] = make_noise()
    S["d"] = {
        "px": np.zeros(POOL), "py": np.zeros(POOL), "vx": np.zeros(POOL), "vy": np.zeros(POOL),
        "size": np.ones(POOL), "age": np.zeros(POOL),
        "alive": np.zeros(POOL, bool), "prev_alive": np.zeros(POOL, bool),
        "key": np.full(POOL, -1, int),
    }
    alloc_wet(S, W, H, create=True)
    return S


def update_radii(S):
    persp = Z_PERSP / (Z_PERSP - np.clip(S["pos"][:, 2], -300, 400))
    S["rad"] = RAD * S["scale"] * persp


def pipe_pins(S):
    pos = S["pos"]
    if S["pinned"]:
        pos[0, :2] = S["anchor"]
        pos[0, 2] = 0.0
    g = S["grab"]
    if g is not None and S["mode"] == "point":
        pos[g, :2] = S["cur"]


def step_pipe(S, dt):
    pos, prev = S["pos"], S["prev"]
    W, H = S["W"], S["H"]
    scale = S["scale"]
    seg = SEG * scale
    m = RAD * scale + 6

    if S["mode"] == "whole":
        px, py = S["pend"]
        S["pend"] = [0.0, 0.0]
        pos[:, 0] += px
        pos[:, 1] += py
        sx = max(m - pos[:, 0].min(), 0.0) - max(pos[:, 0].max() - (W - m), 0.0)
        sy = max(m - pos[:, 1].min(), 0.0) - max(pos[:, 1].max() - (H - m), 0.0)
        pos[:, 0] += sx
        pos[:, 1] += sy
        prev[:] = pos
        if S["pinned"]:
            S["anchor"] = pos[0, :2].copy()
        if time.perf_counter() - S["mtime"] > 0.05:
            S["mv"] = [0.0, 0.0]
        S["evel"] = np.array(S["mv"], float)
        update_radii(S)
        return

    n_sub = 3
    h = dt / n_sub
    pinned = S["pinned"]
    gy = G * (0.75 if pinned else 0.04)
    damp = 0.990 if pinned else 0.9935
    bend_rest = 2.0 * seg * 0.97
    ev, od = S["ev"], S["od"]
    for _ in range(n_sub):
        vel = (pos - prev) * damp
        sp = np.sqrt((vel * vel).sum(axis=1, keepdims=True))
        vel *= np.minimum(1.0, 45.0 / np.maximum(sp, 1e-9))
        prev[:] = pos
        pos += vel
        pos[:, 1] += gy * h * h
        pos[:, 2] *= 0.985
        if S["grab"] is not None and S["mode"] == "point":
            S["cur"] = S["cur"] + (S["target"] - S["cur"]) * 0.55
            if pinned and S["grab"] == 0:
                S["anchor"] = S["cur"].copy()
        pipe_pins(S)
        for _it in range(8):
            for idx in (ev, od):
                d = pos[idx + 1] - pos[idx]
                dist = np.sqrt((d * d).sum(axis=1)) + 1e-9
                cor = d * (((dist - seg) / dist) * 0.5)[:, None]
                pos[idx] += cor
                pos[idx + 1] -= cor
            d = pos[2:] - pos[:-2]
            dist = np.sqrt((d * d).sum(axis=1)) + 1e-9
            neg = np.minimum(dist - bend_rest, 0.0)
            cor = d * ((neg / dist) * 0.12)[:, None]
            pos[:-2] += cor
            pos[2:] -= cor
            pipe_pins(S)
        np.clip(pos[:, 0], m, W - m, out=pos[:, 0])
        np.clip(pos[:, 1], m, H - m, out=pos[:, 1])
        np.clip(pos[:, 2], -120, 120, out=pos[:, 2])
        np.clip(prev[:, 0], m, W - m, out=prev[:, 0])
        np.clip(prev[:, 1], m, H - m, out=prev[:, 1])

    v2 = ((pos[-1, :2] - prev[-1, :2]) + (pos[-2, :2] - prev[-2, :2])) * 0.5 / h
    sp = float(np.hypot(v2[0], v2[1]))
    if sp > 2500.0:
        v2 *= 2500.0 / sp
    S["evel"] = S["evel"] * 0.5 + v2 * 0.5
    update_radii(S)


def update_flow(S, dt):
    """Energy balance along the spine: v^2 = push - friction - 2*g*(height above the inlet)."""
    pos, scale = S["pos"], S["scale"]
    v0sq = (S["pressure"] ** 2) * scale
    frac = np.arange(N_PTS) / (N_PTS - 1.0)
    vsq = v0sq * (1.0 - 0.10 * frac) - 2.0 * G * (pos[0, 1] - pos[:, 1])
    bad = np.flatnonzero(vsq <= 0.0)
    if bad.size:
        b = int(bad[0])
        if b == 0:
            target = 0.0
        else:
            v1, v2 = float(vsq[b - 1]), float(vsq[b])
            target = (b - 1) + v1 / (v1 - v2)
        exit_t = 0.0
    else:
        target = N_PTS - 1.0
        exit_t = min(1450.0, math.sqrt(max(float(vsq[-1]), 0.0)))
    r = S["reach"]
    if target > r:
        r = min(target, r + dt * 520.0 / (SEG * scale))
    else:
        r = max(target, r - dt * 900.0 / (SEG * scale))
    S["reach"] = r
    full = r >= N_PTS - 1 - 1e-6
    k = 8.0 if (exit_t > 0 and full) else 14.0
    S["exit"] += ((exit_t if full else 0.0) - S["exit"]) * min(1.0, dt * k)
    if S["exit"] < 20.0 and not (exit_t > 0 and full):
        S["exit"] = 0.0
    d = pos[-1, :2] - pos[-3, :2]
    ln = math.hypot(d[0], d[1])
    if ln > 1e-6:
        fd = S["fdir"] + (d / ln - S["fdir"]) * min(1.0, dt * 12.0)
        n = math.hypot(fd[0], fd[1]) or 1.0
        S["fdir"] = fd / n


def spawn_droplets(S, x, y, vx, vy, size, reserve=0.0):
    D = S["d"]
    a = D["alive"]
    free = np.flatnonzero(~a)
    cap = int(S["max_active"] * (1.0 - reserve)) - int(a.sum())
    n = min(len(x), len(free), cap)
    if n <= 0:
        return 0
    sl = free[:n]
    D["px"][sl], D["py"][sl] = x[:n], y[:n]
    D["vx"][sl], D["vy"][sl] = vx[:n], vy[:n]
    D["size"][sl] = size[:n]
    D["age"][sl] = 0.0
    a[sl] = True
    return n


def emit_water(S, dt):
    J = S["j"]
    S["time"] += dt
    if S["exit"] < 30.0 or S["reach"] < N_PTS - 1 - 0.02:
        S["emitting"] = False
        return
    if not S["emitting"]:
        S["emitting"] = True
        S["segid"] += 1
    rng, pos = S["rng"], S["pos"]
    dx, dy = float(S["fdir"][0]), float(S["fdir"][1])
    rr = float(S["rad"][-1])
    free = np.flatnonzero(~J["alive"])
    for q in range(min(2, len(free))):
        t_back = (1.0 - (q + 0.5) / 2.0) * dt
        sp = S["exit"] * (1.0 + rng.normal(0.0, 0.008))
        ang = rng.normal(0.0, 0.003) + 0.005 * math.sin(S["time"] * 9.0)
        ca, sa = math.cos(ang), math.sin(ang)
        vx = (dx * ca - dy * sa) * sp + S["evel"][0]
        vy = (dx * sa + dy * ca) * sp + S["evel"][1]
        i = free[q]
        J["x"][i] = pos[-1, 0] + dx * rr * 0.15 + vx * t_back
        J["y"][i] = pos[-1, 1] + dy * rr * 0.15 + vy * t_back
        J["vx"][i], J["vy"][i] = vx, vy
        J["v0"][i] = max(60.0, math.hypot(vx, vy))
        J["born"][i] = S["time"] - t_back
        J["alive"][i], J["hit"][i] = True, False
        J["w0"][i] = 2.0 * rr
        J["jit"][i] = rng.uniform(0.95, 1.1)
        J["segid"][i] = S["segid"]


def get_jet_widths(S):
    J = S["j"]
    age = S["time"] - J["born"]
    spd = np.hypot(J["vx"], J["vy"])
    w = J["w0"] * np.clip(np.sqrt(J["v0"] / (spd + 40.0)), 0.5, 1.15) * (1.0 + 0.8 * np.minimum(age, 1.5)) * J["jit"]
    w = np.where(J["hit"], J["hw"] * 1.15, w)
    return np.clip(w, 0.5 * J["w0"], 3.2 * J["w0"])


def spawn_children(S, x, y, nx, ny, vx, vy, vn, size):
    rng = S["rng"]
    s = -vn
    k = np.clip((s / 170.0).astype(int) + 1, 2, 4)
    rep = np.repeat(np.arange(len(x)), k)
    m = len(rep)
    ang = np.arctan2(ny, nx)[rep] + rng.uniform(-1.25, 1.25, m)
    sp = s[rep] * rng.uniform(0.12, 0.55, m)
    tvx = (vx - vn * nx)[rep] * 0.45
    tvy = (vy - vn * ny)[rep] * 0.45
    spawn_droplets(S, x[rep] + nx[rep] * 2, y[rep] + ny[rep] * 2,
                   tvx + np.cos(ang) * sp, tvy + np.sin(ang) * sp,
                   size[rep] * rng.uniform(0.38, 0.65, m), reserve=0.08)


def deposit_wetness(S, xs, ys, amt):
    cell, gw, gh, wet = S["cell"], S["gw"], S["gh"], S["wet"]
    cx = np.clip((xs / cell).astype(int), 0, gw - 1)
    cy = np.clip((ys / cell).astype(int), 0, gh - 1)
    np.add.at(wet, (cy, cx), amt)
    half = amt * 0.5
    for ddx, ddy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        np.add.at(wet, (np.clip(cy + ddy, 0, gh - 1), np.clip(cx + ddx, 0, gw - 1)), half)


def step_jet(S, dt):
    J, Fm, rng = S["j"], S["foam"], S["rng"]
    W, H = S["W"], S["H"]
    a = J["alive"]
    if a.any():
        fly = a & ~J["hit"]
        if fly.any():
            f = np.where(fly, 1.0 - 0.05 * dt, 1.0)
            J["vx"] *= f
            J["vy"] *= f
            J["vy"] += np.where(fly, G * dt, 0.0)
            J["x"] += np.where(fly, J["vx"] * dt, 0.0)
            J["y"] += np.where(fly, J["vy"] * dt, 0.0)
            fi = np.flatnonzero(fly & (J["y"] > 0) & (J["y"] < H) & (J["x"] > 0) & (J["x"] < W))
            if fi.size:
                deposit_wetness(S, J["x"][fi], J["y"][fi], 0.18)
        floor = fly & (J["y"] >= H - 2)
        left = fly & (J["x"] <= 1) & ~floor
        right = fly & (J["x"] >= W - 2) & ~floor & ~left
        hi = np.flatnonzero(floor | left | right)
        if hi.size:
            nx = np.where(floor[hi], 0.0, np.where(left[hi], 1.0, -1.0))
            ny = np.where(floor[hi], -1.0, 0.0)
            wd = get_jet_widths(S)[hi]
            vx, vy = J["vx"][hi].copy(), J["vy"][hi].copy()
            spd = np.hypot(vx, vy)
            hx, hy = np.clip(J["x"][hi], 1, W - 2), np.minimum(J["y"][hi], H - 2)
            J["hit"][hi], J["hw"][hi], J["nx"][hi], J["ny"][hi] = True, wd, nx, ny
            J["hs"][hi], J["hitt"][hi] = spd, S["time"]
            J["x"][hi], J["y"][hi], J["vx"][hi], J["vy"][hi] = hx, hy, 0.0, 0.0
            vn = np.minimum(vx * nx + vy * ny, -60.0)
            spawn_children(S, hx, hy, nx, ny, vx, vy, vn, np.full(hi.size, 2.4))
            deposit_wetness(S, hx, hy, wd * 0.03)
        a &= ~((J["hit"] & (S["time"] - J["hitt"] > 0.22)) | (S["time"] - J["born"] > 5.0))

        rate = 60.0 * min(1.0, S["exit"] / 500.0)
        cand = np.flatnonzero(a & ~J["hit"] & (S["time"] - J["born"] > 0.06))
        ns = int(rng.poisson(rate * dt))
        if cand.size and ns:
            pick = rng.choice(cand, ns)
            sp = np.maximum(np.hypot(J["vx"][pick], J["vy"][pick]), 1.0)
            nxs, nys = -J["vy"][pick] / sp, J["vx"][pick] / sp
            side = rng.choice([-1.0, 1.0], ns)
            wd = get_jet_widths(S)[pick]
            out = rng.uniform(25.0, 110.0, ns) * side
            spawn_droplets(S, J["x"][pick] + nxs * side * wd * 0.45, J["y"][pick] + nys * side * wd * 0.45,
                           J["vx"][pick] * 0.95 + nxs * out, J["vy"][pick] * 0.95 + nys * out,
                           rng.uniform(0.7, 1.5, ns))

        hh = np.flatnonzero(a & J["hit"])
        if hh.size:
            free = np.flatnonzero(~Fm["alive"])
            n = min(10, len(free))
            if n:
                j = rng.choice(hh, n)
                k = np.clip(J["hs"][j] / 900.0, 0.25, 1.4)
                nxj, nyj, hw = J["nx"][j], J["ny"][j], J["hw"][j]
                tx, ty = -nyj, nxj
                side = rng.choice([-1.0, 1.0], n)
                off = rng.normal(0.0, 0.8, n) * hw
                sl = free[:n]
                Fm["fx"][sl] = J["x"][j] + tx * off + nxj * rng.uniform(0, 0.3, n) * hw
                Fm["fy"][sl] = J["y"][j] + ty * off + nyj * rng.uniform(0, 0.3, n) * hw
                Fm["fr"][sl] = hw * rng.uniform(0.35, 0.7, n)
                Fm["fvx"][sl] = tx * side * rng.uniform(40, 170, n) * k + nxj * rng.uniform(40, 260, n) * k
                Fm["fvy"][sl] = ty * side * rng.uniform(40, 170, n) * k + nyj * rng.uniform(40, 260, n) * k
                Fm["lmax"][sl] = Fm["life"][sl] = rng.uniform(0.35, 0.7, n)
                Fm["sx"][sl] = np.where(nyj != 0, 1.0, 0.6)
                Fm["sy"][sl] = np.where(nyj != 0, 0.6, 1.0)
                Fm["alive"][sl] = True
    fa = Fm["alive"]
    if fa.any():
        Fm["fvy"] += G * 0.35 * dt
        Fm["fx"] += Fm["fvx"] * dt
        Fm["fy"] += Fm["fvy"] * dt
        Fm["fr"] *= 1.0 + 0.25 * dt
        Fm["life"] -= dt
        fa &= Fm["life"] > 0


def step_droplets(S, dt):
    D = S["d"]
    a = D["alive"]
    if not a.any():
        return
    rng = S["rng"]
    W, H = S["W"], S["H"]
    px, py, vx, vy, sz, age = D["px"], D["py"], D["vx"], D["vy"], D["size"], D["age"]

    f = np.maximum(0.0, 1.0 - (0.9 / (1.0 + sz)) * dt)
    vx *= f
    vy *= f
    vy += G * dt
    px += vx * dt
    py += vy * dt
    age += dt

    parts = []

    floor = a & (py >= H - 1)
    left = a & (px <= 0) & ~floor
    right = a & (px >= W - 1) & ~floor & ~left
    wall = floor | left | right
    wi = np.flatnonzero(wall)
    if wi.size:
        nx = np.where(floor[wi], 0.0, np.where(left[wi], 1.0, -1.0))
        ny = np.where(floor[wi], -1.0, 0.0)
        vn = vx[wi] * nx + vy[wi] * ny
        s = np.maximum(-vn, 0.0)
        hx, hy = np.clip(px[wi], 1, W - 2), np.clip(py[wi], 1, H - 2)
        deposit_wetness(S, hx, hy, sz[wi] * (0.35 + np.minimum(s, 900.0) / 900.0 * 0.8))
        b = (s > 170.0) & (sz[wi] > 0.9)
        if b.any():
            parts.append((hx[b], hy[b], nx[b], ny[b], vx[wi][b], vy[wi][b], vn[b], sz[wi][b]))
        a[wi] = False

    cand = a & (age > 0.1)
    if cand.any():
        ci = np.flatnonzero(cand)
        pos, rad = S["pos"], S["rad"]
        cxp, cyp, crp = pos[:N_PTS - 1, 0], pos[:N_PTS - 1, 1], rad[:N_PTS - 1]
        ddx = px[ci, None] - cxp[None, :]
        ddy = py[ci, None] - cyp[None, :]
        dist = np.hypot(ddx, ddy)
        pen = crp[None, :] + sz[ci, None] * 0.6 - dist
        j = pen.argmax(axis=1)
        rows = np.flatnonzero(pen[np.arange(len(ci)), j] > 0)
        if rows.size:
            jr = j[rows]
            dd = np.maximum(dist[rows, jr], 1e-6)
            pnx, pny = ddx[rows, jr] / dd, ddy[rows, jr] / dd
            idx = ci[rows]
            vn = vx[idx] * pnx + vy[idx] * pny
            into = vn < 0
            if into.any():
                idx, pnx, pny, vn, jr = idx[into], pnx[into], pny[into], vn[into], jr[into]
                s = -vn
                brk = (s > 220.0) & (sz[idx] > 0.9)
                if brk.any():
                    bi = idx[brk]
                    parts.append((px[bi].copy(), py[bi].copy(), pnx[brk], pny[brk],
                                  vx[bi].copy(), vy[bi].copy(), vn[brk], sz[bi].copy()))
                    deposit_wetness(S, np.clip(px[bi], 1, W - 2), np.clip(py[bi], 1, H - 2), sz[bi] * 0.4)
                    a[bi] = False
                sl = ~brk
                if sl.any():
                    si = idx[sl]
                    vx[si] -= 1.25 * vn[sl] * pnx[sl]
                    vy[si] -= 1.25 * vn[sl] * pny[sl]
                    px[si] = cxp[jr[sl]] + pnx[sl] * (crp[jr[sl]] + sz[si] * 0.6 + 0.5)
                    py[si] = cyp[jr[sl]] + pny[sl] * (crp[jr[sl]] + sz[si] * 0.6 + 0.5)

    sp = a & (age > 0.15) & (rng.random(POOL) < 0.45 * dt) & (py >= 0) & (py < H) & (px >= 0) & (px < W)
    if sp.any():
        si = np.flatnonzero(sp)
        deposit_wetness(S, px[si], py[si], sz[si] * 0.13)
        a[si] = False

    a &= ~((age > 6.0) | (py < -H * 1.5))

    for p in parts:
        spawn_children(S, *p)


def alloc_wet(S, W, H, create=False):
    cell = 8
    gw, gh = -(-W // cell), -(-H // cell)
    S.update(cell=cell, gw=gw, gh=gh,
             wet=np.zeros((gh, gw), np.float32),
             colbias=(0.3 + 0.7 * S["rng"].random(gw)).astype(np.float32),
             buf=np.zeros((gh, gw, 4), np.uint8),
             wsize=(gw * 2, gh * 2))
    S["wet_tk"] = None
    S["wet_vis"] = False


def step_wet(S, dt):
    S["wet_dt"] += dt
    S["frame"] += 1
    if S["frame"] % S["wet_every"]:
        return False
    u = S["wet_dt"] / 0.1
    S["wet_dt"] = 0.0
    w = S["wet"]
    np.minimum(w, 3.0, out=w)
    over = np.maximum(w - 0.9, 0.0)
    if over.any():
        flow = over * (0.12 * min(u, 3.0)) * S["colbias"][None, :] * \
            (S["rng"].random(w.shape, dtype=np.float32) < 0.6)
        w -= flow
        w[1:] += flow[:-1]
    w *= 0.985 ** u
    w -= 0.003 * u
    np.maximum(w, 0.0, out=w)
    w[-1] *= 0.85
    w[-2] *= 0.95
    return True
