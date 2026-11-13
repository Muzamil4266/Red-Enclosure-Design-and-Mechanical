"""
ocr_image_extractor.py  (the "Extract text from images" work screen of the PaddleOCR panel)

Starts working as soon as it is created (the pictures were already chosen in the gallery). No choices here:
every page gets the SAME Auto filter and Medium sharpness the old scanner screen used.

PIPELINE (in chunks of CHUNK pages, so 2000 photos never fill the disk with scanned images)
  1. a chunk of pages is auto-scanned                (ocr_workers.scan_worker_process, own process)
  2. the scanned chunk is read by the PaddleOCR engine (the engine already loaded in the background)
  3. the chunk's temporary images are deleted, the next chunk starts
  4. text + Copy Text + Save as .txt + Put on Canvas + "Time taken" + "Number of words in txt"
Every chunk has its own run id (app._ocr_next_rid), so a stopped run can never disturb the next one.
"""
import os
import shutil
import tempfile
import time
import queue as pyqueue
import multiprocessing as mp
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, scrolledtext, messagebox, ttk

import customtkinter as ctk

from ocr_imaging import FULL_QUAD, SHARPEN_LEVELS
from ocr_support import SmoothProgress
from ocr_workers import scan_worker_process
from pdf_text_extractor import format_duration

TEXT_FILTER = "auto"                                  # the Auto filter of the scanner
TEXT_SHARPEN = dict(SHARPEN_LEVELS)["Medium"]         # the Medium sharpness of the scanner
CHUNK = 50                                            # pages scanned and read together


class ImageTextExtractor(ctk.CTkFrame):

    def __init__(self, master, app, items, on_back):
        super().__init__(master, fg_color="transparent")
        self.app = app                  # the main window: owns the shared OCR engine
        self.on_back = on_back
        self.items = list(items)        # [{"path", "rot"}]
        self.n_total = len(self.items)
        self.chunks = [self.items[i:i + CHUNK] for i in range(0, len(self.items), CHUNK)]
        self.ci = 0
        self.closed = False
        self.stage = "idle"             # idle | scan | ocr | done
        self.rid = 0
        self.t0 = 0.0
        self.words = 0
        self.pages_done = 0
        self.ocr_in_chunk = 0
        self.scan_proc = self.scan_q = None
        self.tmp = None
        self.sp = None
        self.shown = 0.0
        self._notice_frame = None
        app._pdf_extract_active = True  # the OCR panel stops reading the engine's messages while this screen is open
        self._build()
        self.after(60, self._poll)
        self._show_notice()
        self.start()

    # ------------------------------------------------------------------ UI
    def _build(self):
        bold12 = ctk.CTkFont(size=12, weight="bold")
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=15, pady=(2, 4))
        ctk.CTkButton(top, text="← Back to photos", width=130, height=30, fg_color="#34495e", hover_color="#2c3e50",
                      font=bold12, command=self._back).pack(side="left")
        ctk.CTkLabel(top, text="🖼 Extract text from images", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#3498db").pack(side="left", padx=12)
        self.stop_btn = ctk.CTkButton(top, text="⏹ Stop", width=80, height=30, fg_color="#c0392b",
                                      hover_color="#962d22", font=bold12, command=self._stop)
        self.stop_btn.pack(side="right")

        prog_row = ctk.CTkFrame(self, fg_color="transparent")
        prog_row.pack(fill="x", padx=15, pady=(6, 4))
        self.pct = ctk.CTkLabel(prog_row, text="", width=48, anchor="e",
                                font=ctk.CTkFont(size=13, weight="bold"), text_color="#ecf0f1")
        self.pct.pack(side="right", padx=(8, 0))
        self.bar = ttk.Progressbar(prog_row, mode="determinate", maximum=100)
        self.bar.pack(side="left", fill="x", expand=True)

        self.status = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=1000,
                                   font=ctk.CTkFont(size=11), text_color="#f1c40f")
        self.status.pack(fill="x", padx=15, pady=(0, 6))

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(side="bottom", pady=(4, 12))
        ctk.CTkButton(bottom, text="📋 Copy Text", width=120, fg_color="#27ae60", hover_color="#1e8449",
                      command=self._copy).pack(side="left", padx=5)
        ctk.CTkButton(bottom, text="💾 Save as .txt", width=120, fg_color="#8e44ad", hover_color="#6c3483",
                      command=self._save).pack(side="left", padx=5)
        ctk.CTkButton(bottom, text="📌 Put on Canvas Workspace", width=170, fg_color="#d35400",
                      hover_color="#a04000", command=self._to_canvas).pack(side="left", padx=5)

        self.stats = ctk.CTkLabel(self, text="", anchor="w", font=ctk.CTkFont(size=15, weight="bold"),
                                  text_color="#f1c40f")

        self.font = tkfont.Font(family="Consolas", size=10)
        self.box = scrolledtext.ScrolledText(self, wrap=tk.WORD, bg="#111111", fg="#ffffff",
                                             insertbackground="#ffffff", font=self.font, borderwidth=1,
                                             relief="solid")
        self.box.pack(fill=tk.BOTH, expand=True, padx=15, pady=6)
        self.box.bind("<Control-MouseWheel>", self._zoom_text)

    def _show_notice(self):
        """The popup that tells the user which enhancement is applied (there is no choice)."""
        n = ctk.CTkFrame(self, fg_color="#16324a", border_width=2, border_color="#3498db", corner_radius=10)
        ctk.CTkLabel(n, text="✨ Auto filter and medium sharpness applied to enhance images\n"
                             "for better text extraction", justify="left",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(padx=18, pady=(14, 8))
        ctk.CTkButton(n, text="OK", width=70, command=self._close_notice).pack(pady=(0, 12))
        n.place(relx=0.5, rely=0.1, anchor="n")
        n.lift()
        self._notice_frame = n
        self.after(7000, self._close_notice)

    def _close_notice(self):
        n, self._notice_frame = self._notice_frame, None
        if n is not None:
            try:
                n.destroy()
            except Exception:
                pass

    def _zoom_text(self, event):
        step = 1 if event.delta > 0 else -1
        self.font.configure(size=max(6, min(40, self.font.cget("size") + step)))
        return "break"

    def _set_bar(self, v):
        self.bar["value"] = v
        self.pct.configure(text=f"{int(v)}%" if v > 0 else "")

    def _set_stats(self, text):
        if text:
            self.stats.configure(text=text)
            self.stats.pack(fill="x", padx=15, pady=(0, 4), before=self.box)
        else:
            self.stats.pack_forget()

    # ------------------------------------------------------------------ copy / save / canvas
    def _text(self):
        return self.box.get("1.0", tk.END).strip()

    def _copy(self):
        txt = self._text()
        if not txt:
            messagebox.showwarning("Nothing to Copy", "No text extracted yet.", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(txt)
        if hasattr(self.app, "toast"):
            self.app.toast("📋 Copied to clipboard!")

    def _save(self):
        txt = self._text()
        if not txt:
            messagebox.showwarning("Nothing to Save", "No extracted text available to save.", parent=self)
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text File", "*.txt")],
                                            initialfile="extracted_text.txt", title="Save Extracted Text As")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(txt)
        if hasattr(self.app, "toast"):
            self.app.toast("💾 Text saved to file!")

    def _to_canvas(self):
        txt = self._text()
        if not txt:
            messagebox.showwarning("Nothing to Insert", "No extracted text available to place on the workspace.",
                                   parent=self)
            return
        app = self.app
        app.begin_change()
        cx = app.canvas.canvasx(app.canvas.winfo_width() / 2)
        cy = app.canvas.canvasy(app.canvas.winfo_height() / 2)
        app.canvas.create_text(cx, cy, text=txt, anchor="nw", fill=getattr(app, "pen_color", "#e74c3c"),
                               tags="text_note",
                               font=("Arial", max(8, int(12 * getattr(app, "current_zoom_scale", 1.0))), "bold"))
        app.commit_change()
        if hasattr(app, "update_workspace_counter"):
            app.update_workspace_counter()
        if hasattr(app, "toast"):
            app.toast("✏️ Text inserted onto canvas workspace!")

    # ------------------------------------------------------------------ run control
    def _kill_scan(self):
        p = self.scan_proc
        if p is not None:
            try:
                if p.is_alive():
                    p.terminate()
            except Exception:
                pass
        self.scan_proc = self.scan_q = None

    def _cleanup_tmp(self):
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp = None

    def _cancel_engine(self):
        eng = getattr(self.app, "_ocr_engine", None)
        if eng is not None and self.stage == "ocr":
            eng["cancel"].value = self.rid      # run ids only ever grow, so this can never stop a later run

    def start(self):
        self.app._ocr_start_engine()            # no-op while it is alive
        self.t0 = time.perf_counter()
        self.words = self.pages_done = self.ocr_in_chunk = 0
        self.ci = 0
        self.shown = 0.0
        self.box.delete("1.0", tk.END)
        self._set_stats("")
        self.stop_btn.configure(state="normal")
        self._begin_chunk()

    def _begin_chunk(self):
        chunk = self.chunks[self.ci]
        jobs = [{"path": it["path"], "quad": list(FULL_QUAD), "filter": TEXT_FILTER, "rot": it["rot"],
                 "sharpen": TEXT_SHARPEN} for it in chunk]
        eng = self.app._ocr_start_engine()
        self.sp = SmoothProgress(len(jobs), with_ocr=True, ocr_prior=eng.get("bench"))
        self.tmp = tempfile.mkdtemp(prefix="ocr_imgs_")
        q = mp.Queue()
        p = mp.Process(target=scan_worker_process, args=(jobs, self.tmp, q), daemon=True)
        p.start()
        self.scan_q, self.scan_proc = q, p
        self.stage = "scan"
        self.ocr_in_chunk = 0
        self.status.configure(text=f"Scanning pages {self.pages_done}/{self.n_total} (Auto filter, Medium sharpness)...",
                              text_color="#f1c40f")

    def _fail(self, msg):
        self._cancel_engine()
        self._kill_scan()
        self._cleanup_tmp()
        self.stage = "idle"
        self.sp = None
        self.stop_btn.configure(state="disabled")
        self._set_bar(0)
        self.status.configure(text=f"❌ {msg}", text_color="#e74c3c")

    def _stop(self):
        if self.stage in ("scan", "ocr"):
            self._cancel_engine()
            self._kill_scan()
            self._cleanup_tmp()
            self.stage = "idle"
            self.sp = None
            self.stop_btn.configure(state="disabled")
            self._set_bar(0)
            self.status.configure(text="Stopped. Go back to the photos and start again if you like.",
                                  text_color="#f1c40f")

    def _back(self):
        if self.stage in ("scan", "ocr"):
            if not messagebox.askyesno("Go back?", "This is still running.\nStop it and go back to the photos?",
                                       parent=self):
                return
            self._stop()
        self.on_back()

    def shutdown(self):
        """Called when the screen (or the whole OCR panel) closes."""
        if self.closed:
            return
        self._cancel_engine()
        self.closed = True
        self._kill_scan()
        self._cleanup_tmp()
        self.app._pdf_extract_active = False

    # ------------------------------------------------------------------ progress
    def _target(self):
        if self.stage == "done":
            return 100.0
        if self.stage == "idle":
            return self.shown
        cur = self.sp.target() if self.sp else 0.0
        n_cur = len(self.chunks[self.ci]) if self.ci < len(self.chunks) else 0
        return min(99.9, (self.pages_done * 100.0 + cur * n_cur) / max(1, self.n_total))

    def _tick(self):
        g = self._target()
        if g > self.shown:
            self.shown += (g - self.shown) * 0.3
        if self.stage == "done":
            self.shown += (100.0 - self.shown) * 0.35
            if 100.0 - self.shown < 0.5:
                self.shown = 100.0
        return self.shown

    # ------------------------------------------------------------------ message pumps
    def _poll(self):
        if self.closed:
            return
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._pump_scan()
        self._pump_engine()
        if self.stage != "idle":
            self._set_bar(self._tick())
        self.after(60, self._poll)

    def _pump_scan(self):
        q = self.scan_q
        if q is None:
            return
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "scan_progress":
                    if self.sp:
                        self.sp.scan_progress(payload)
                    self.status.configure(
                        text=f"Scanning pages {self.pages_done + payload}/{self.n_total} "
                             f"(Auto filter, Medium sharpness)...", text_color="#f1c40f")
                elif kind == "scan_done":
                    p, self.scan_proc, self.scan_q = self.scan_proc, None, None
                    if p is not None:
                        try:
                            p.join(timeout=0.5)
                        except Exception:
                            pass
                    self.stage = "ocr"
                    eng = self.app._ocr_start_engine()
                    self.rid = self.app._ocr_next_rid()
                    if self.sp:
                        self.sp.set_prior(eng.get("bench"))
                        self.sp.scan_finished(eng["ready"])
                    eng["task_q"].put(("ocr", self.rid, payload))
                    if eng["ready"]:
                        self.status.configure(text=f"Extracting text {self.pages_done}/{self.n_total}...",
                                              text_color="#3498db")
                    else:
                        self.status.configure(text="Pages scanned. Waiting for the OCR engine to finish starting...",
                                              text_color="#f1c40f")
                    return
                elif kind == "scan_error":
                    self._fail(f"Error while scanning pages: {payload}")
                    return
        except pyqueue.Empty:
            pass

    def _pump_engine(self):
        eng = getattr(self.app, "_ocr_engine", None)
        if eng is None:
            return
        try:
            while True:
                kind, payload = eng["out_q"].get_nowait()
                if kind == "engine_ready":
                    eng["ready"], eng["bench"] = True, payload
                    if self.sp:
                        self.sp.set_prior(payload)
                        self.sp.engine_became_ready()
                    if self.stage == "ocr":
                        self.status.configure(
                            text=f"Extracting text {self.pages_done + self.ocr_in_chunk}/{self.n_total}...",
                            text_color="#3498db")
                elif kind == "engine_error":
                    eng["error"] = payload
                    if self.stage in ("scan", "ocr"):
                        self._fail(f"Error initializing PaddleOCR: {payload}")
                elif kind == "result":
                    rid, path, text = payload
                    if rid == self.rid and self.stage == "ocr":
                        self._on_result(path, text)
                elif kind == "done":
                    if payload == self.rid and self.stage == "ocr":
                        self._chunk_done()
        except pyqueue.Empty:
            pass
        if self.stage == "ocr" and not eng["error"] and not eng["proc"].is_alive():
            self.app._ocr_engine = None
            self._fail("The OCR engine stopped unexpectedly. Please try again.")

    # ------------------------------------------------------------------ results
    def _on_result(self, path, text):
        self.box.insert(tk.END, f"--- {os.path.basename(path)} ---\n{text}\n\n")
        self.box.see(tk.END)
        if text and not text.startswith("[Error"):
            self.words += len(text.split())
        self.ocr_in_chunk += 1
        if self.sp:
            self.sp.ocr_result()
        self.status.configure(text=f"Extracting text {self.pages_done + self.ocr_in_chunk}/{self.n_total}...",
                              text_color="#3498db")

    def _chunk_done(self):
        self.pages_done += len(self.chunks[self.ci])
        self.ocr_in_chunk = 0
        self._cleanup_tmp()                     # the scanned images of this chunk are no longer needed
        self.ci += 1
        if self.ci < len(self.chunks):
            self._begin_chunk()
        else:
            self._finish_ok()

    def _finish_ok(self):
        elapsed = time.perf_counter() - self.t0
        self.stage = "done"
        self.stop_btn.configure(state="disabled")
        self.status.configure(text=f"✅ Done — extracted text from {self.n_total} picture(s).",
                              text_color="#2ecc71")
        self._set_stats(f"⏱ Time taken = {format_duration(elapsed)}      "
                        f"📝 Number of words in txt = {self.words}")
        if hasattr(self.app, "toast"):
            self.app.toast("🖼 Text extracted from images!")
