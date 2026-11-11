"""
ocr_support.py  (part 2 of 5 of the PaddleOCR scanner)

Small self-contained helpers: smooth 0..100 % progress, streaming PDF writer, open a file with the
system viewer, and the synthetic page used to warm up / time the OCR engine.
"""
import io
import math
import os
import subprocess
import sys
import time

from PIL import Image, ImageDraw, ImageFont


# ----------------------------------------------------------------------------------------------
# Smooth progress (0 -> 100 %), PDF writer, small helpers
# ----------------------------------------------------------------------------------------------
class SmoothProgress:
    """One 0..100 % bar for the whole job (scan + OCR). Never freezes at 99 %:
    between finished pages it creeps forward using the real measured page speed, and it only
    reaches 100 % when the job is truly finished."""
    W_SCAN, W_OCR = 1.0, 3.0          # a page costs 1 part scanning + 3 parts OCR
    DEFAULT_SCAN, DEFAULT_OCR = 0.8, 10.0

    def __init__(self, total, with_ocr=True, ocr_prior=None, clock=time.monotonic, with_scan=True):
        self.clock = clock
        self.with_scan = with_scan
        self.total, self.with_ocr, self.ocr_prior = max(1, total), with_ocr, ocr_prior
        self.phase = "scan"                       # scan -> wait -> ocr -> done
        self.scan_done = self.ocr_done = 0
        self.scan_marks, self.ocr_marks = [clock()], []
        self.shown = 0.0

    @staticmethod
    def _pace(marks):
        d = [b - a for a, b in zip(marks, marks[1:])]
        if len(d) >= 3:
            d = d[1:]
        d = d[-8:]
        return sum(d) / len(d) if d else None

    def scan_progress(self, n):
        self.scan_done = n
        self.scan_marks.append(self.clock())

    def scan_finished(self, engine_ready):
        if not self.with_ocr:
            self.finish()
        elif engine_ready:
            self.phase, self.ocr_marks = "ocr", [self.clock()]
        else:
            self.phase = "wait"

    def engine_became_ready(self):
        if self.phase == "wait":
            self.phase, self.ocr_marks = "ocr", [self.clock()]

    def start_ocr_only(self, engine_ready):
        self.scan_done = self.total
        if engine_ready:
            self.phase, self.ocr_marks = "ocr", [self.clock()]
        else:
            self.phase = "wait"

    def set_prior(self, sec):
        if sec:
            self.ocr_prior = sec

    def ocr_result(self):
        self.engine_became_ready()
        self.ocr_done += 1
        self.ocr_marks.append(self.clock())

    def finish(self):
        self.phase = "done"

    def target(self):
        if self.phase == "done":
            return 100.0
        ws, wo = (self.W_SCAN if self.with_scan else 0.0), (self.W_OCR if self.with_ocr else 0.0)
        tot = self.total * (ws + wo)
        done = self.scan_done * ws + self.ocr_done * wo
        now = self.clock()
        if self.phase == "scan" and self.scan_done < self.total:
            pace = self._pace(self.scan_marks) or self.DEFAULT_SCAN
            done += ws * 0.97 * (1 - math.exp(-(now - self.scan_marks[-1]) / pace))
        elif self.phase == "ocr" and self.ocr_done < self.total and self.ocr_marks:
            pace = self._pace(self.ocr_marks) or self.ocr_prior or self.DEFAULT_OCR
            t = now - self.ocr_marks[-1]
            done += wo * 0.95 * t / (t + pace)
        return min(99.9, 100.0 * done / tot)

    def tick(self):
        """Call every ~60-100 ms. Returns the value to show (never goes backwards)."""
        g = self.target()
        if g > self.shown:
            self.shown += (g - self.shown) * 0.3
        if self.phase == "done":
            self.shown += (100.0 - self.shown) * 0.35
            if 100.0 - self.shown < 0.5:
                self.shown = 100.0
        return self.shown


class PdfWriter:
    """Tiny streaming PDF writer (one JPEG per page). No extra libraries, pages are written one by one."""

    def __init__(self, path):
        self.f = open(path, "wb")
        self.f.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        self.offs, self.pages, self.n = {}, [], 2   # object 1 = catalog, 2 = page tree (written at the end)

    def _obj(self, oid, body):
        self.offs[oid] = self.f.tell()
        self.f.write(f"{oid} 0 obj\n".encode() + body + b"\nendobj\n")

    def add_jpeg(self, jpeg, w, h, gray):
        k = 11.69 * 72 / max(w, h)          # longest side = A4 length, aspect ratio kept
        pw, ph = w * k, h * k
        page, cont, imid = self.n + 1, self.n + 2, self.n + 3
        self.n += 3
        cs = "/DeviceGray" if gray else "/DeviceRGB"
        self._obj(imid, (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace {cs} "
                         f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpeg)} >>\nstream\n").encode()
                  + jpeg + b"\nendstream")
        content = f"q {pw:.2f} 0 0 {ph:.2f} 0 0 cm /Im0 Do Q".encode()
        self._obj(cont, f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
        self._obj(page, (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.2f} {ph:.2f}] "
                         f"/Resources << /XObject << /Im0 {imid} 0 R >> >> /Contents {cont} 0 R >>").encode())
        self.pages.append(page)

    def add_image(self, img, gray=False):
        im = img.convert("L") if gray else img.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=90, optimize=True)
        self.add_jpeg(buf.getvalue(), im.width, im.height, gray)

    def close(self):
        kids = " ".join(f"{p} 0 R" for p in self.pages)
        self._obj(2, f"<< /Type /Pages /Kids [{kids}] /Count {len(self.pages)} >>".encode())
        self._obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        xref, size = self.f.tell(), self.n + 1
        self.f.write(f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n")
        for i in range(1, size):
            self.f.write(f"{self.offs[i]:010d} 00000 n \n".encode())
        self.f.write(f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
        self.f.close()


def open_file(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def _bench_image():
    """A dense synthetic text page used to measure the OCR speed of this PC (engine warm-up)."""
    img = Image.new("RGB", (1800, 2400), "white")
    d = ImageDraw.Draw(img)
    font = None
    for name in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            font = ImageFont.truetype(name, 34)
            break
        except Exception:
            pass
    if font is None:
        try:
            font = ImageFont.load_default(size=34)
        except TypeError:
            font = ImageFont.load_default()
    words = ("the beggar was shaking out his upper cloth and tightening his dhoti he raised his eyes and "
             "looked fearfully at the road gleaming in the afternoon heat needs something on his feet").split()
    y, i = 120, 0
    while y < 2300:
        d.text((140, y), " ".join(words[(i + k) % len(words)] for k in range(11)), fill=(25, 25, 25), font=font)
        y += 62
        i += 3
    return img
