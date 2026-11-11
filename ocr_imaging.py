"""
ocr_imaging.py  (part 1 of 5 of the PaddleOCR scanner)

Settings + page-image helpers: orient, crop, sharpen, filters, crop overlay.
No tkinter / customtkinter in here, so the background worker processes can import it cheaply.
"""
import math

from PIL import Image, ImageOps, ImageEnhance, ImageFilter, ImageDraw

# ----------------------------------------------------------------------------------------------
# SETTINGS (all tunable numbers of the OCR scanner live here)
# ----------------------------------------------------------------------------------------------
MAX_PHOTOS = 500         # maximum number of photos in the page list
IMAGE_TYPES = [("Images", "*.png *.jpg *.jpeg *.bmp *.tiff *.webp")]
PREVIEW_MAX = 2200       # longest side of the page preview used in the editor (higher = crisper text)
START_FULL = False       # True = the OCR panel opens full size; the ⛶ button toggles it anyway
THUMB = 96               # page list / filter thumbnail source size
FINAL_MAX_SIDE = 3000    # longest side of the scanned image that goes to OCR
DET_MAX_SIDE = 1600      # text DETECTION works at this size; text READING still uses the full-size page
USE_MKLDNN = True        # Intel CPU acceleration; falls back automatically if it fails
USE_MOBILE_MODELS = True # True = faster light models (downloaded ONE time, then reused from disk)

FULL_QUAD = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]  # TL, TR, BR, BL (normalised 0..1)

FILTERS = [
    ("original", "Original"),
    ("auto", "Auto"),
    ("bw", "B&W"),
    ("vibrant", "Vibrant"),
    ("soft", "Soft Tone"),
    ("sharp", "Sharp Black"),
    ("ocv", "OCV B&W"),
]
DEFAULT_FILTER = "auto"

GRAY_FILTERS = ("bw", "sharp", "ocv")


# ----------------------------------------------------------------------------------------------
# Scanning helpers (module level so the OCR worker process can use them too)
# ----------------------------------------------------------------------------------------------
def load_oriented(path, rot=0, max_side=None):
    """Opens a photo, fixes phone EXIF orientation, optionally shrinks it and rotates it clockwise."""
    img = Image.open(path)
    if max_side and img.format == "JPEG":
        img.draft("RGB", (max_side, max_side))  # very fast JPEG down-scaling on load
    img = ImageOps.exif_transpose(img).convert("RGB")
    if max_side and max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    if rot % 360:
        img = img.rotate(-rot, expand=True)
    return img


def order_quad(pts):
    """Orders 4 points as TL, TR, BR, BL."""
    tl = min(pts, key=lambda p: p[0] + p[1])
    br = max(pts, key=lambda p: p[0] + p[1])
    tr = min(pts, key=lambda p: p[1] - p[0])
    bl = max(pts, key=lambda p: p[1] - p[0])
    return [(float(p[0]), float(p[1])) for p in (tl, tr, br, bl)]


def is_full_quad(quad):
    return all(abs(a - b) < 0.003 for p, f in zip(quad, FULL_QUAD) for a, b in zip(p, f))


def crop_to_quad(img, quad):
    """Cuts the page out of the photo and straightens it."""
    if is_full_quad(quad):
        return img
    w, h = img.size
    tl, tr, br, bl = [(x * w, y * h) for x, y in quad]
    out_w = int(max(math.dist(tl, tr), math.dist(bl, br)))
    out_h = int(max(math.dist(tl, bl), math.dist(tr, br)))
    if out_w < 8 or out_h < 8:
        return img
    return img.transform((out_w, out_h), Image.Transform.QUAD, (*tl, *bl, *br, *tr), Image.Resampling.BICUBIC)


SHARPEN_LEVELS = [("Off", 0), ("Low", 1), ("Medium", 2), ("High", 3)]
DEFAULT_SHARPEN = 2


def enhance(img, level):
    """STEP 1 (before any filter): brighten the paper and sharpen the text. level: 0 off .. 3 high."""
    if level <= 0:
        return img
    # brightness: lift the paper white point (98th percentile of brightness -> ~240)
    hist = img.convert("L").histogram()
    total, acc, p98 = sum(hist), 0, 255
    for v, cnt in enumerate(hist):
        acc += cnt
        if acc >= 0.98 * total:
            p98 = v
            break
    if 20 < p98 < 235:
        gain = min(1.55, 240.0 / p98)
        img = img.point(lambda v: min(255, int(v * gain + 0.5)))
    # sharpness: radius follows image size so thumbnails, preview and final scan look alike
    r = max(0.8, max(img.size) / 1500.0)
    pct = {1: 70, 2: 120, 3: 190}[min(3, level)]
    img = img.filter(ImageFilter.UnsharpMask(radius=r, percent=pct, threshold=2))
    if level >= 2:  # second, wider pass = local contrast ("clarity")
        img = img.filter(ImageFilter.UnsharpMask(radius=r * 5, percent=15 + 12 * level, threshold=2))
    return img


def _shade_fix(cv2, np, L):
    """Removes uneven phone lighting / shadows from the brightness channel (float32 0..255)."""
    h, w = L.shape
    small = cv2.resize(L, (max(8, w // 8), max(8, h // 8)), interpolation=cv2.INTER_AREA)
    small = cv2.dilate(small, np.ones((7, 7), np.uint8))          # ignore thin dark text
    bg = cv2.GaussianBlur(small, (0, 0), max(2.0, min(small.shape) * 0.10))
    bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)
    target = float(np.percentile(bg, 92))
    return np.clip(L * (target / np.maximum(bg, 20.0)), 0, 255)


def _color_filter(img, white_pct, gamma, clahe, sat, contrast=1.0, warm=0.0, gray=False):
    try:
        import cv2
        import numpy as np
    except ImportError:
        out = ImageOps.autocontrast(ImageOps.grayscale(img) if gray else img, cutoff=3)
        return (out.convert("RGB") if gray else ImageEnhance.Color(out).enhance(sat))
    lab = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2LAB)
    L = _shade_fix(cv2, np, lab[..., 0].astype(np.float32))
    sm = cv2.resize(L, (max(8, L.shape[1] // 4), max(8, L.shape[0] // 4)), interpolation=cv2.INTER_AREA)
    lo = float(np.percentile(sm, 1.0))
    hi = max(float(np.percentile(sm, white_pct)), lo + 40.0)
    L = np.clip((L - lo) * 255.0 / (hi - lo), 0, 255)           # paper -> white, ink -> dark
    L = 255.0 * np.power(L / 255.0, gamma)
    if contrast != 1.0:
        L = np.clip((L - 128.0) * contrast + 128.0, 0, 255)
    L8 = L.astype(np.uint8)
    if clahe > 0:
        c = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(L8)
        L8 = cv2.addWeighted(L8, 1.0 - clahe, c, clahe, 0)
    if gray:
        a = b = np.full_like(L8, 128)
    else:
        a = np.clip(128.0 + (lab[..., 1].astype(np.float32) - 128.0) * sat, 0, 255).astype(np.uint8)
        b = np.clip(128.0 + (lab[..., 2].astype(np.float32) - 128.0) * sat + warm, 0, 255).astype(np.uint8)
    return Image.fromarray(cv2.cvtColor(cv2.merge([L8, a, b]), cv2.COLOR_LAB2RGB))


def apply_filter(img, name):
    """STEP 2: scanner-style filters. Every filter first removes shadows / uneven light."""
    if name == "original":
        return img
    if name == "auto":
        return _color_filter(img, white_pct=93, gamma=1.0, clahe=0.2, sat=1.12, contrast=1.1)
    if name == "bw":
        return _color_filter(img, white_pct=92, gamma=1.2, clahe=0.3, sat=0.0, contrast=1.2, gray=True)
    if name == "vibrant":
        return _color_filter(img, white_pct=92, gamma=0.95, clahe=0.35, sat=1.75, contrast=1.25)
    if name == "soft":
        return _color_filter(img, white_pct=97, gamma=0.8, clahe=0.0, sat=0.9, contrast=0.95, warm=4.0)
    if name in ("sharp", "ocv"):
        try:
            import cv2
            import numpy as np
        except ImportError:
            return ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=3).convert("RGB")
        gray = np.array(ImageOps.grayscale(img))
        h, w = gray.shape
        if name == "sharp":
            # paper -> pure white, text -> deep black, but keeps smooth edges (readable, not binary)
            sm = cv2.resize(gray, (max(8, w // 4), max(8, h // 4)), interpolation=cv2.INTER_AREA)
            sm = cv2.dilate(sm, np.ones((5, 5), np.uint8))
            sm = cv2.GaussianBlur(sm, (0, 0), max(3.0, min(sm.shape) / 25.0))
            bg = cv2.resize(sm, (w, h), interpolation=cv2.INTER_LINEAR)
            norm = cv2.divide(gray, bg, scale=255).astype(np.float32)
            lv = np.clip((norm - 85.0) * (255.0 / (205.0 - 85.0)), 0, 255)
            out = (255.0 * np.power(lv / 255.0, 1.35)).astype(np.uint8)
        else:
            # pure black & white (OpenCV adaptive threshold)
            blk = max(21, (min(h, w) // 18) | 1)
            g = cv2.medianBlur(cv2.GaussianBlur(gray, (3, 3), 0), 3)
            out = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, blk, 14)
        return Image.fromarray(out).convert("RGB")
    return img


def process_page(img, sharpen, filt):
    """Full pipeline on an already cropped page: sharpen/brighten first, THEN the filter."""
    return apply_filter(enhance(img, sharpen), filt)


def build_scanned(path, quad, filt, rot, sharpen=DEFAULT_SHARPEN, max_side=None):
    """Original photo -> cropped -> sharpened -> filtered scanned page."""
    return process_page(crop_to_quad(load_oriented(path, rot, max_side), quad), sharpen, filt)


# ----------------------------------------------------------------------------------------------
# Crop-area view helper
# ----------------------------------------------------------------------------------------------
def draw_crop_overlay(img, quad, color="#00e5ff", width=3):
    """Whole photo with a visible border around the area that will be kept; the rest is darkened."""
    w, h = img.size
    pts = [(x * w, y * h) for x, y in quad]
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    out = Image.composite(img, ImageEnhance.Brightness(img).enhance(0.4), mask)
    ImageDraw.Draw(out).line(pts + [pts[0]], fill=color, width=width)
    return out
