"""
ocr_pdf_maker.py  (the "Images to PDF" screens of the PaddleOCR panel)

  PdfOptionsScreen   step 1: "Apply auto scanning to all pages" with an example of the user's FIRST page
                     step 2: "Apply medium sharpness to all pages" with an example, then OK
  PdfBuildScreen     builds the PDF with the same scanner process the old screen used (pages are streamed one by
                     one, so 1000 images need no extra memory) and shows 0..100 % progress
  default_pdf_path   the PDF is saved NEXT TO the pictures (the picked folder, or the folder of the first photo):
                     "<folder name>.pdf", numbered if that name already exists. The user is never asked where.
"""
import os
import queue as pyqueue
import multiprocessing as mp
import tkinter as tk
from tkinter import messagebox, ttk

import customtkinter as ctk
from PIL import ImageOps

from ocr_imaging import FULL_QUAD, SHARPEN_LEVELS, load_oriented, process_page
from ocr_support import SmoothProgress, open_file
from ocr_workers import scan_worker_process

MEDIUM = dict(SHARPEN_LEVELS)["Medium"]
PREVIEW_SIDE = 900          # the example is made from a 900 px copy of the first page


def default_pdf_path(source_folder, first_path):
    folder = source_folder if (source_folder and os.path.isdir(source_folder)) else os.path.dirname(first_path)
    base = os.path.basename(os.path.normpath(folder)) or "Images_to_PDF"
    path = os.path.join(folder, base + ".pdf")
    n = 1
    while os.path.exists(path):
        path = os.path.join(folder, f"{base} ({n}).pdf")
        n += 1
    return path


# ----------------------------------------------------------------------------------------------
# Options (2 steps)
# ----------------------------------------------------------------------------------------------
class PdfOptionsScreen(ctk.CTkFrame):

    def __init__(self, master, items, on_ok, on_back):
        super().__init__(master, fg_color="transparent")
        self.items, self.on_ok, self.on_back = items, on_ok, on_back
        self.step = 1
        self.auto = tk.BooleanVar(value=True)
        self.sharp = tk.BooleanVar(value=True)
        self.base = None
        self._variants = {}
        self._build()
        self.after(80, self._load_base)

    def _build(self):
        bold = ctk.CTkFont(size=12, weight="bold")
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=15, pady=(2, 4))
        self.back_btn = ctk.CTkButton(top, text="← Back to photos", width=130, height=30, fg_color="#34495e",
                                      hover_color="#2c3e50", font=bold, command=self._back)
        self.back_btn.pack(side="left")
        ctk.CTkLabel(top, text=f"📑 Images to PDF — {len(self.items)} image(s)",
                     font=ctk.CTkFont(size=14, weight="bold"), text_color="#3498db").pack(side="left", padx=12)
        self.step_lbl = ctk.CTkLabel(self, text="", anchor="w", font=ctk.CTkFont(size=14, weight="bold"),
                                     text_color="#f1c40f")
        self.step_lbl.pack(fill="x", padx=15, pady=(6, 0))
        self.desc = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=1000,
                                 font=ctk.CTkFont(size=12), text_color="#cccccc")
        self.desc.pack(fill="x", padx=15, pady=(2, 6))

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=15, pady=(6, 12))
        self.chk = ctk.CTkCheckBox(bottom, text="", font=ctk.CTkFont(size=13, weight="bold"), command=self._render)
        self.chk.pack(side="left")
        self.next_btn = ctk.CTkButton(bottom, text="Next ▶", width=190, height=38, fg_color="#27ae60",
                                      hover_color="#1e8449", font=ctk.CTkFont(size=14, weight="bold"),
                                      command=self._next)
        self.next_btn.pack(side="right")

        mid = ctk.CTkFrame(self, fg_color="transparent")
        mid.pack(fill="both", expand=True, padx=15)
        left = ctk.CTkFrame(mid, fg_color="transparent")
        right = ctk.CTkFrame(mid, fg_color="transparent")
        left.pack(side="left", expand=True, fill="both", padx=6)
        right.pack(side="left", expand=True, fill="both", padx=6)
        self.lbl_before = ctk.CTkLabel(left, text="Loading example...", compound="top",
                                       font=ctk.CTkFont(size=12, weight="bold"))
        self.lbl_before.pack(expand=True)
        self.lbl_after = ctk.CTkLabel(right, text="", compound="top", font=ctk.CTkFont(size=12, weight="bold"))
        self.lbl_after.pack(expand=True)

    def _load_base(self):
        try:
            it = self.items[0]
            self.base = load_oriented(it["path"], it["rot"], PREVIEW_SIDE)
        except Exception as e:
            self.lbl_before.configure(text=f"Could not make the example: {e}")
            self.base = None
        self._show_step(1)

    def _variant(self, auto, sharp):
        key = (auto, sharp)
        if key not in self._variants:
            self._variants[key] = process_page(self.base, MEDIUM if sharp else 0, "auto" if auto else "original")
        return self._variants[key]

    def _show_step(self, step):
        self.step = step
        if step == 1:
            self.step_lbl.configure(text="Step 1 of 2 — Auto scanning")
            self.desc.configure(text="Auto scanning removes shadows and uneven light, turns the paper white and makes "
                                     "the text clear. This is how YOUR first page looks:")
            self.chk.configure(text="Apply auto scanning to all pages", variable=self.auto)
            self.next_btn.configure(text="Next ▶")
            self.back_btn.configure(text="← Back to photos")
        else:
            self.step_lbl.configure(text="Step 2 of 2 — Medium sharpness")
            self.desc.configure(text="Medium sharpness brightens the paper and makes the letters crisper. "
                                     "This is how YOUR first page looks:")
            self.chk.configure(text="Apply medium sharpness to all pages", variable=self.sharp)
            self.next_btn.configure(text="✔ OK — Create PDF")
            self.back_btn.configure(text="← Previous step")
        self._render()

    def _fit(self):
        h = max(300, self.winfo_height() - 250)
        w = max(260, (self.winfo_width() - 90) // 2)
        return w, h

    def _set_img(self, label, pil, caption):
        img = ImageOps.contain(pil, self._fit())
        ci = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
        label.configure(image=ci, text=caption)

    def _render(self):
        if self.base is None:
            return
        auto = bool(self.auto.get())
        if self.step == 1:
            self._set_img(self.lbl_before, self.base, "Original photo")
            self._set_img(self.lbl_after, self._variant(auto, False),
                          "With auto scanning" if auto else "Not applied (pages stay as photographed)")
        else:
            sharp = bool(self.sharp.get())
            self._set_img(self.lbl_before, self._variant(auto, False), "Without extra sharpness")
            self._set_img(self.lbl_after, self._variant(auto, sharp),
                          "With medium sharpness" if sharp else "Not applied")

    def _next(self):
        if self.step == 1:
            self._show_step(2)
        else:
            self.on_ok(bool(self.auto.get()), bool(self.sharp.get()))

    def _back(self):
        if self.step == 2:
            self._show_step(1)
        else:
            self.on_back()

    def shutdown(self):
        pass


# ----------------------------------------------------------------------------------------------
# Building the PDF
# ----------------------------------------------------------------------------------------------
class PdfBuildScreen(ctk.CTkFrame):

    def __init__(self, master, app, items, out_path, auto, sharp, on_back, on_home):
        super().__init__(master, fg_color="transparent")
        self.app, self.items, self.out_path = app, items, out_path
        self.auto, self.sharp = auto, sharp
        self.on_back, self.on_home = on_back, on_home
        self.total = len(items)
        self.q = self.proc = self.sp = None
        self.running = False
        self.finished = False
        self.closed = False
        self._build()
        self.after(60, self._poll)
        self.start()

    def _build(self):
        bold = ctk.CTkFont(size=12, weight="bold")
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=15, pady=(2, 4))
        ctk.CTkButton(top, text="← Back to photos", width=130, height=30, fg_color="#34495e", hover_color="#2c3e50",
                      font=bold, command=self._back).pack(side="left")
        ctk.CTkLabel(top, text="📑 Creating the PDF", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#3498db").pack(side="left", padx=12)
        self.stop_btn = ctk.CTkButton(top, text="⏹ Stop", width=80, height=30, fg_color="#c0392b",
                                      hover_color="#962d22", font=bold, command=self._stop)
        self.stop_btn.pack(side="right")

        ctk.CTkLabel(self, text=f"{self.total} image(s)  →  {self.out_path}", anchor="w", justify="left",
                     wraplength=1000, font=ctk.CTkFont(size=11), text_color="#a0a0a0").pack(fill="x", padx=15, pady=(8, 0))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=15, pady=(8, 4))
        self.pct = ctk.CTkLabel(row, text="", width=48, anchor="e", font=ctk.CTkFont(size=13, weight="bold"),
                                text_color="#ecf0f1")
        self.pct.pack(side="right", padx=(8, 0))
        self.bar = ttk.Progressbar(row, mode="determinate", maximum=100)
        self.bar.pack(side="left", fill="x", expand=True)
        self.status = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=1000,
                                   font=ctk.CTkFont(size=12), text_color="#f1c40f")
        self.status.pack(fill="x", padx=15, pady=(2, 8))
        self.done_row = ctk.CTkFrame(self, fg_color="transparent")
        ctk.CTkButton(self.done_row, text="📂 Open PDF", fg_color="#d35400", hover_color="#a04000", font=bold,
                      command=lambda: open_file(self.out_path)).pack(side="left", padx=4)
        ctk.CTkButton(self.done_row, text="📁 Open folder", fg_color="#1f6aa5", hover_color="#144870", font=bold,
                      command=lambda: open_file(os.path.dirname(self.out_path))).pack(side="left", padx=4)
        ctk.CTkButton(self.done_row, text="🏠 Finish", fg_color="#27ae60", hover_color="#1e8449", font=bold,
                      command=self.on_home).pack(side="left", padx=4)

    def _set_bar(self, v):
        self.bar["value"] = v
        self.pct.configure(text=f"{int(v)}%" if v > 0 else "")

    def start(self):
        jobs = [{"path": it["path"], "quad": list(FULL_QUAD), "filter": "auto" if self.auto else "original",
                 "rot": it["rot"], "sharpen": MEDIUM if self.sharp else 0} for it in self.items]
        self.sp = SmoothProgress(self.total, with_ocr=False)
        q = mp.Queue()
        p = mp.Process(target=scan_worker_process, args=(jobs, "", q, self.out_path), daemon=True)
        p.start()
        self.q, self.proc, self.running = q, p, True
        self.status.configure(text=f"Creating PDF 0/{self.total}...", text_color="#f1c40f")

    def _kill(self, remove_partial=True):
        p = self.proc
        if p is not None:
            try:
                if p.is_alive():
                    p.terminate()
            except Exception:
                pass
        self.proc = self.q = None
        if remove_partial and not self.finished:
            try:
                p and p.join(timeout=0.5)
                if os.path.exists(self.out_path):
                    os.remove(self.out_path)
            except Exception:
                pass

    def _stop(self):
        if not self.running:
            return
        self.running = False
        self._kill()
        self.sp = None
        self._set_bar(0)
        self.stop_btn.configure(state="disabled")
        self.status.configure(text="Stopped. The unfinished PDF was removed.", text_color="#f1c40f")

    def _back(self):
        if self.running:
            if not messagebox.askyesno("Go back?", "The PDF is still being created.\nStop it and go back?",
                                       parent=self):
                return
            self._stop()
        self.on_back()

    def _finish(self, ok, bad):
        self.running = False
        p, self.proc, self.q = self.proc, None, None
        if p is not None:
            try:
                p.join(timeout=0.5)
            except Exception:
                pass
        self.stop_btn.configure(state="disabled")
        if ok == 0:
            try:
                os.remove(self.out_path)
            except Exception:
                pass
            self.sp = None
            self._set_bar(0)
            self.status.configure(text="❌ None of the pictures could be read, so no PDF was made.",
                                  text_color="#e74c3c")
            return
        self.finished = True
        if self.sp:
            self.sp.scan_finished(False)
        msg = f"✅ PDF saved with {ok} page(s): {self.out_path}"
        if bad:
            msg += f"   ⚠ {bad} picture(s) could not be read and were skipped."
        self.status.configure(text=msg, text_color="#2ecc71")
        self.done_row.pack(anchor="w", padx=15, pady=4)
        if hasattr(self.app, "toast"):
            self.app.toast("📄 PDF saved!")

    def _poll(self):
        if self.closed:
            return
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        q = self.q
        if q is not None:
            try:
                while True:
                    kind, payload = q.get_nowait()
                    if kind == "scan_progress":
                        if self.sp:
                            self.sp.scan_progress(payload)
                        self.status.configure(text=f"Creating PDF {payload}/{self.total}...", text_color="#f1c40f")
                    elif kind == "pdf_done":
                        _, ok, bad = payload
                        self._finish(ok, bad)
                        break
                    elif kind == "scan_error":
                        self.running = False
                        self._kill()
                        self.sp = None
                        self._set_bar(0)
                        self.stop_btn.configure(state="disabled")
                        self.status.configure(text=f"❌ Error while creating the PDF: {payload}", text_color="#e74c3c")
                        break
            except pyqueue.Empty:
                pass
        if self.sp is not None:
            v = self.sp.tick()
            self._set_bar(v)
            if self.sp.phase == "done" and v >= 100.0:
                self.sp = None
        self.after(60, self._poll)

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        if self.running:
            self.running = False
            self._kill()
