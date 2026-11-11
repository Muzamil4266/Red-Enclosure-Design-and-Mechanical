"""
Visual Duplicate Image Engine (part 1 of 3)
===========================================
Core matching algorithms, perceptual hashing, pixel signature verification,
image quality evaluation, and OS Recycle Bin file management utilities.
"""
import os
import stat
import sys
import random
from collections import defaultdict
import numpy as np
from PIL import Image, ImageFilter, ImageOps
from send2trash import send2trash

# ---- matching accuracy settings (two stages: phash candidates, then pixel verification) ----
PHASH_MAX_DIST = 6        # stage 1: max differing bits (of 64) for two images to become candidates
SIG_SIZE = 256            # stage 2: images are compared as SIG_SIZE x SIG_SIZE grayscale
ASPECT_TOL = 0.02         # max relative difference in width/height ratio
MEAN_TOL = 8.0            # max average gray-level difference over the whole image (0-255)
GRID = 16                 # image is also checked as a GRID x GRID mosaic of tiles
TILE_TOL = 8.0            # max brightness change of ANY single tile (catches added parts / crops / local edits)
BIG_DIFF = 48             # a pixel counts as "changed" if it differs by more than this (after +-1px tolerance)
CHANGED_FRAC_TOL = 0.004  # max fraction of changed pixels (catches moved lines / extra strokes)

IS_WIN = sys.platform.startswith("win")
FAIL_HINT = ("Common causes: the file is open in another program (photo viewer, OneDrive / Dropbox sync), the drive "
             "has no Recycle Bin (USB stick or network drive), or the Recycle Bin is switched off for that drive. "
             "The app never deletes permanently, so those files were left where they are.")


def _shell_recycle(path):
    """Windows only: asks the Windows shell to move one file to the Recycle Bin (works from any thread)."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _pack_ = 1 if ctypes.sizeof(ctypes.c_void_p) == 4 else 8
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    op = SHFILEOPSTRUCTW()
    op.wFunc = 3                                    # FO_DELETE
    op.pFrom = path + "\0\0"                        # the file list ends with a double NUL
    op.fFlags = 0x0040 | 0x0010 | 0x0400 | 0x0004   # ALLOWUNDO (= Recycle Bin) | NOCONFIRMATION | NOERRORUI | SILENT
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0:
        return False, f"Windows shell error 0x{rc:X}"
    if op.fAnyOperationsAborted:
        return False, "Windows cancelled the operation"
    return True, ""


def recycle_file(path):
    """Moves ONE file to the Recycle Bin and checks that it is really gone. Returns (True, "") or (False, reason).
    Never deletes permanently. Order: send2trash, then (Windows) the shell API, then both again after clearing a
    read-only flag. The path is normalised first (C:/a/b becomes C:\\a\\b), which the Windows APIs require."""
    p = os.path.abspath(path)
    if not os.path.exists(p):
        return False, "the file is already gone (moved or deleted by another program)"
    reasons = []

    def attempt():
        try:
            send2trash(p)
        except Exception as e:
            reasons.append(f"send2trash: {type(e).__name__}: {e}")
        if not os.path.exists(p):
            return True
        if IS_WIN:
            try:
                ok, msg = _shell_recycle(p)
            except Exception as e:
                ok, msg = False, f"{type(e).__name__}: {e}"
            if not ok:
                reasons.append(f"Windows shell: {msg}")
            if not os.path.exists(p):
                return True
        return False

    if attempt():
        return True, ""
    try:                                            # a read-only flag can block the Recycle Bin
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass
    if attempt():
        return True, ""
    return False, "; ".join(dict.fromkeys(reasons)) or "the file is still there after the Recycle Bin call"


def summarize_failures(failed, limit=3):
    """[(path, reason)] -> short readable text, grouped by reason."""
    by = defaultdict(list)
    for p, why in failed:
        by[why].append(p)
    lines = []
    for why, ps in sorted(by.items(), key=lambda kv: -len(kv[1]))[:limit]:
        lines.append(f"- {len(ps)} file(s): {why}\n    e.g. {ps[0]}")
    if len(by) > limit:
        lines.append(f"- ... and {len(by) - limit} other reason(s) (listed in the console)")
    return "\n".join(lines)


def short_name(name, limit):
    if len(name) <= limit:
        return name
    keep = max(4, limit // 2 - 1)
    return name[:keep] + "…" + name[-keep:]


def fmt_res(px):
    return f"{px / 1_000_000:.1f} MP" if px else "N/A"


def flatten_to_gray(img):
    """Grayscale copy; transparent areas are composited on white so alpha images compare correctly."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, rgba)
    return img.convert("L")


def pixel_signature(path):
    """(aspect ratio, gray, local-max, local-min) arrays at SIG_SIZE used for the verification stage.
    The 3x3 local max/min give a +-1 pixel tolerance so harmless resize / re-save edge shifts are not counted."""
    with Image.open(path) as img:
        try:
            img.draft("RGB", (SIG_SIZE * 2, SIG_SIZE * 2))
        except Exception:
            pass
        img = ImageOps.exif_transpose(img)
        w, h = img.size
        g = flatten_to_gray(img).resize((SIG_SIZE, SIG_SIZE), Image.BOX)
        return (w / h if h else 1.0,
                np.asarray(g, dtype=np.uint8),
                np.asarray(g.filter(ImageFilter.MaxFilter(3)), dtype=np.uint8),
                np.asarray(g.filter(ImageFilter.MinFilter(3)), dtype=np.uint8))


def signature_metrics(a, b):
    """(aspect difference, mean difference, worst tile difference, changed-pixel fraction) between two signatures."""
    ra, ga, xa, na = a
    rb, gb, xb, nb = b
    aspect = abs(ra - rb) / max(ra, rb, 1e-9)
    ga16, gb16 = ga.astype(np.int16), gb.astype(np.int16)
    mean_diff = float(np.abs(ga16 - gb16).mean())
    t = SIG_SIZE // GRID
    tile_a = ga16.reshape(GRID, t, GRID, t).mean(axis=(1, 3))
    tile_b = gb16.reshape(GRID, t, GRID, t).mean(axis=(1, 3))
    tile_diff = float(np.abs(tile_a - tile_b).max())
    zero = np.zeros_like(ga16)
    d1 = np.maximum(np.maximum(ga16 - xb, nb.astype(np.int16) - ga16), zero)
    d2 = np.maximum(np.maximum(gb16 - xa, na.astype(np.int16) - gb16), zero)
    changed = float((np.maximum(d1, d2) > BIG_DIFF).mean())
    return aspect, mean_diff, tile_diff, changed


def signatures_match(a, b):
    """True only if the two images are the same picture (resized / re-saved), not merely similar."""
    aspect, mean_diff, tile_diff, changed = signature_metrics(a, b)
    return (aspect <= ASPECT_TOL and mean_diff <= MEAN_TOL
            and tile_diff <= TILE_TOL and changed <= CHANGED_FRAC_TOL)


def find_candidate_clusters(hashes, cancel):
    """Groups paths whose 64-bit phash differs by <= PHASH_MAX_DIST bits.
    Uses 8 byte-chunks (pigeonhole: <= 7 differing bits always leaves one identical chunk), so no all-pairs scan."""
    parent = {p: p for p in hashes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    buckets = defaultdict(list)
    for p, h in hashes.items():
        for i in range(8):
            buckets[(i, (h >> (8 * i)) & 0xFF)].append((p, h))

    for members in buckets.values():
        if cancel.is_set():
            return []
        for i in range(len(members)):
            pi, hi = members[i]
            for j in range(i + 1, len(members)):
                pj, hj = members[j]
                if (hi ^ hj).bit_count() <= PHASH_MAX_DIST:
                    ra, rb = find(pi), find(pj)
                    if ra != rb:
                        parent[ra] = rb

    clusters = defaultdict(list)
    for p in hashes:
        clusters[find(p)].append(p)
    return [c for c in clusters.values() if len(c) > 1]