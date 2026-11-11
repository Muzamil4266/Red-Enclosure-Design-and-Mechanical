"""
pdf_text_extractor.py  (the "Extract text from Pdf" screen of the PaddleOCR panel)

Kept separate from the photo screen so nothing gets mixed up. It sits inside the OCR panel (same single main
screen) and uses the SAME OCR engine process, the SAME page scanner, the SAME Auto filter and Medium sharpness
that the "Select Photos & Scan" screen uses.

PIPELINE
  1. PDF(s) are broken into one image per page            (pdf_split_worker.py, own process)
  2. every page image is auto-scanned: Auto + Medium       (ocr_workers.scan_worker_process, own process)
  3. every scanned page is read by the PaddleOCR engine    (the engine that is already loaded in the background)
  4. text + Copy Text + Save as .txt + "Time taken" + "Number of words in txt"
"""
import os
import re
import time
import shutil
import tempfile
import importlib.util
import queue as pyqueue
import multiprocessing as mp
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, scrolledtext, messagebox, ttk
import customtkinter as ctk

from ocr_imaging import FULL_QUAD, SHARPEN_LEVELS
from ocr_support import SmoothProgress
from ocr_workers import scan_worker_process
from pdf_split_worker import pdf_split_process

PDF_FILTER = "auto"                                   # the Auto filter of the photo scanner
PDF_SHARPEN = dict(SHARPEN_LEVELS)["Medium"]          # the Medium sharpness of the photo scanner
PDF_TYPES = [("PDF documents", "*.pdf"), ("All files", "*.*")]
SPLIT_WEIGHT = 0.5                                    # splitting a page costs 0.5 parts (scan 1 + OCR 3 per page)


def format_duration(seconds):
    """10 mins, 45 secs, 1 hr 5 mins ..."""
    s = int(round(seconds))
    if s < 1:
        return "less than 1 sec"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} hr" + ("s" if h != 1 else ""))
    if m:
        parts.append(f"{m} min" + ("s" if m != 1 else ""))
    if sec and not h:
        parts.append(f"{sec} sec" + ("s" if sec != 1 else ""))
    return " ".join(parts)


_SENT_END = (".", "?", "!", ":", ";", '"', "”", "…")
_BULLET_START = ("•", "·", "●", "▪", "■")
_LIST_RE = re.compile(r"^(\(?\d{1,3}[.)]|\(?[A-Za-z][.)])\s")


def reflow_text(text):
    """OCR gives one line per printed line. This joins them into flowing paragraphs so the text uses the full
    width. A paragraph ends after a short line that ends a sentence, before bullets / numbered items, and after
    short heading lines. A word split by a hyphen at the end of a line is joined again."""
    lines = [ln.strip() for ln in text.split("\n")]
    lengths = [len(ln) for ln in lines if ln]
    if len(lengths) < 2:
        return text.strip()
    ref = sorted(lengths)[int(0.85 * (len(lengths) - 1))]      # length of a full line on this page
    paras, cur, prev = [], "", ""
    for line in lines:
        if not line:
            if cur:
                paras.append(cur)
            cur, prev = "", ""
            continue
        if not cur:
            cur, prev = line, line
            continue
        new_para = (line[0] in _BULLET_START or line.startswith(("- ", "– ", "— ", "* "))
                    or bool(_LIST_RE.match(line))
                    or len(prev) < 0.45 * ref                                   # heading / last line
                    or (prev.endswith(_SENT_END) and len(prev) < 0.9 * ref))    # sentence ended before the line was full
        if new_para:
            paras.append(cur)
            cur = line
        elif cur.endswith("-") and line[:1].islower():
            cur = cur[:-1] + line
        else:
            cur += " " + line
        prev = line
    if cur:
        paras.append(cur)
    return "\n\n".join(paras)


class PdfTextExtractor(ctk.CTkFrame):
    """The PDF text screen. Starts working as soon as it is created (the PDFs were already picked)."""

    def __init__(self, master, app, pdf_paths, on_back):
        super().__init__(master, fg_color="transparent")
        self.app = app                  # the main window: owns the shared OCR engine
        self.on_back = on_back
        self.pdfs = []
        self.closed = False
        self.stage = "idle"             # idle | split | scan | ocr | done
        self.rid = 0
        self.t0 = 0.0
        self.words = 0
        self.n_total = 0
        self.split_done = 0
        self.ocr_done = 0
        self.entries, self.ptr, self.jobs = [], 0, []
        self._last_pdf = None
        self.log = []                   # raw pieces of the text box (re-drawn when 'Join lines' is toggled)
        self.split_proc = self.scan_proc = None
        self.split_q = self.scan_q = None
        self.tmp_pages = self.tmp_scan = None
        self.sp = None
        self.shown = 0.0
        app._pdf_extract_active = True  # the OCR panel stops reading the engine's messages while this screen is open
        self._build()
        self.after(60, self._poll)
        self.start(pdf_paths)

    # ------------------------------------------------------------------ UI
    def _build(self):
        bold12 = ctk.CTkFont(size=12, weight="bold")
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=15, pady=(2, 4))
        self.back_btn = ctk.CTkButton(top, text="← Back to OCR", width=120, height=30, fg_color="#34495e",
                                      hover_color="#2c3e50", font=bold12, command=self._back)
        self.back_btn.pack(side="left")
        ctk.CTkLabel(top, text="📄 Extract text from Pdf", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#3498db").pack(side="left", padx=12)
        self.again_btn = ctk.CTkButton(top, text="📁 Choose other PDFs", height=30, fg_color="#1f6aa5",
                                       hover_color="#144870", font=bold12, command=self._choose_again)
        self.again_btn.pack(side="right")
        self.stop_btn = ctk.CTkButton(top, text="⏹ Stop", width=80, height=30, fg_color="#c0392b",
                                      hover_color="#962d22", font=bold12, state="disabled", command=self._stop)
        self.stop_btn.pack(side="right", padx=(0, 8))

        self.info = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=1000,
                                 font=ctk.CTkFont(size=11), text_color="#a0a0a0")
        self.info.pack(fill="x", padx=15)

        prog_row = ctk.CTkFrame(self, fg_color="transparent")
        prog_row.pack(fill="x", padx=15, pady=(4, 4))
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
        self.join_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(bottom, text="↔ Join lines (use full width)", variable=self.join_var,
                        command=self._rerender, font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 16))
        ctk.CTkButton(bottom, text="📋 Copy Text", width=120, fg_color="#27ae60", hover_color="#1e8449",
                      command=self._copy).pack(side="left", padx=5)
        ctk.CTkButton(bottom, text="💾 Save as .txt", width=120, fg_color="#8e44ad", hover_color="#6c3483",
                      command=self._save).pack(side="left", padx=5)

        self.stats = ctk.CTkLabel(self, text="", anchor="w", font=ctk.CTkFont(size=15, weight="bold"),
                                  text_color="#f1c40f")  # shown after the text is ready

        self.font = tkfont.Font(family="Consolas", size=10)
        self.box = scrolledtext.ScrolledText(self, wrap=tk.WORD, bg="#111111", fg="#ffffff",
                                             insertbackground="#ffffff", font=self.font, borderwidth=1,
                                             relief="solid")
        self.box.pack(fill=tk.BOTH, expand=True, padx=15, pady=6)
        self.box.bind("<Control-MouseWheel>", self._zoom_text)

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

    def _set_busy(self, busy):
        self.stop_btn.configure(state="normal" if busy else "disabled")
        self.again_btn.configure(state="disabled" if busy else "normal")

    def _append(self, text):
        self.box.insert(tk.END, text)
        self.box.see(tk.END)

    def _render_item(self, item):
        kind = item[0]
        if kind == "note":
            return f"{item[1]}\n\n"
        if kind == "head":
            return f"{item[2]}===== {item[1]} =====\n\n"
        body = reflow_text(item[2]) if self.join_var.get() else item[2]       # ("page", number, text)
        return f"--- Page {item[1]} ---\n{body}\n\n"

    def _add(self, *item):
        self.log.append(item)
        self._append(self._render_item(item))

    def _rerender(self):
        self.box.delete("1.0", tk.END)
        for item in self.log:
            self.box.insert(tk.END, self._render_item(item))

    # ------------------------------------------------------------------ copy / save
    def _copy(self):
        txt = self.box.get("1.0", tk.END).strip()
        if not txt:
            messagebox.showwarning("Nothing to Copy", "No text extracted yet.", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(txt)
        if hasattr(self.app, "toast"):
            self.app.toast("📋 Copied to clipboard!")

    def _save(self):
        txt = self.box.get("1.0", tk.END).strip()
        if not txt:
            messagebox.showwarning("Nothing to Save", "No extracted text available to save.", parent=self)
            return
        stem = os.path.splitext(os.path.basename(self.pdfs[0]))[0] if len(self.pdfs) == 1 else "extracted_text"
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text File", "*.txt")],
                                            initialfile=f"{stem}.txt", title="Save Extracted Text As")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(txt)
        if hasattr(self.app, "toast"):
            self.app.toast("💾 Text saved to file!")

    # ------------------------------------------------------------------ run control
    def _kill_procs(self):
        for name in ("split_proc", "scan_proc"):
            p = getattr(self, name)
            if p is not None:
                try:
                    if p.is_alive():
                        p.terminate()
                except Exception:
                    pass
            setattr(self, name, None)
        self.split_q = self.scan_q = None

    def _cleanup_tmp(self):
        for name in ("tmp_pages", "tmp_scan"):
            d = getattr(self, name)
            if d:
                shutil.rmtree(d, ignore_errors=True)
            setattr(self, name, None)

    def _cancel_engine(self):
        eng = getattr(self.app, "_ocr_engine", None)
        if eng is not None and self.stage == "ocr":
            eng["cancel"].value = self.rid      # run ids only ever grow, so this can never stop a later run

    def _reset_run(self):
        self._cancel_engine()
        self._kill_procs()
        self._cleanup_tmp()
        self.stage = "idle"
        self.sp = None
        self.shown = 0.0
        self.n_total = self.split_done = self.ocr_done = self.words = 0
        self.entries, self.ptr, self.jobs = [], 0, []
        self._last_pdf = None
        self.log = []
        self.box.delete("1.0", tk.END)
        self._set_stats("")
        self._set_bar(0)

    def start(self, paths):
        self._reset_run()
        self.pdfs = [p for p in paths if os.path.isfile(p)]
        if not self.pdfs:
            self._fail("None of the selected files could be found.")
            return
        if importlib.util.find_spec("fitz") is None:
            self._fail("PyMuPDF is not installed. Open More Tools -> 'Scan & Auto-Install Missing Packages' "
                       "to install it, then try again.")
            return
        names = ", ".join(os.path.basename(p) for p in self.pdfs)
        self.info.configure(text=f"{len(self.pdfs)} PDF(s): {names}")
        self.app._ocr_start_engine()                    # no-op while it is alive
        self.rid = self.app._ocr_next_rid()
        self.t0 = time.perf_counter()
        self.stage = "split"
        self._set_busy(True)
        self.status.configure(text="Breaking the PDF(s) into page images...", text_color="#f1c40f")
        self.tmp_pages = tempfile.mkdtemp(prefix="pdf_pages_")
        q = mp.Queue()
        p = mp.Process(target=pdf_split_process, args=(self.pdfs, self.tmp_pages, q), daemon=True)
        p.start()
        self.split_q, self.split_proc = q, p

    def _fail(self, msg):
        self._cancel_engine()
        self._kill_procs()
        self._cleanup_tmp()
        self.stage = "idle"
        self.sp = None
        self._set_busy(False)
        self._set_bar(0)
        self.status.configure(text=f"❌ {msg}", text_color="#e74c3c")

    def _stop(self):
        if self.stage in ("split", "scan", "ocr"):
            self._cancel_engine()
            self._kill_procs()
            self._cleanup_tmp()
            self.stage = "idle"
            self.sp = None
            self._set_busy(False)
            self._set_bar(0)
            self.status.configure(text="Stopped. You can choose other PDFs and start again.", text_color="#f1c40f")

    def _choose_again(self):
        if self.stage in ("split", "scan", "ocr"):
            return
        paths = filedialog.askopenfilenames(title="Select PDF(s) to extract text from", filetypes=PDF_TYPES)
        if paths:
            self.start(list(paths))

    def _back(self):
        if self.stage in ("split", "scan", "ocr"):
            if not messagebox.askyesno("Go back?", "This is still running.\nStop it and go back?", parent=self):
                return
        self.on_back()

    def shutdown(self):
        """Called when the screen (or the whole OCR panel) closes."""
        if self.closed:
            return
        self._cancel_engine()
        self.closed = True
        self._kill_procs()
        self._cleanup_tmp()
        self.app._pdf_extract_active = False

    # ------------------------------------------------------------------ progress
    def _target(self):
        if self.stage == "done":
            return 100.0
        if self.stage == "idle":
            return self.shown
        n = max(1, self.n_total)
        done = self.split_done * SPLIT_WEIGHT
        if self.sp is not None and self.jobs:
            done += self.sp.target() / 100.0 * len(self.jobs) * 4.0   # 1 part scan + 3 parts OCR per page
        return min(99.9, 100.0 * done / (n * (SPLIT_WEIGHT + 4.0)))

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
        self._pump_split()
        self._pump_scan()
        self._pump_engine()
        if self.stage != "idle":
            self._set_bar(self._tick())
        self.after(60, self._poll)

    def _pump_split(self):
        q = self.split_q
        if q is None:
            return
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "split_total":
                    total, skipped = payload
                    self.n_total = total
                    for name, why in skipped:
                        self._add("note", f"⚠ Skipped {name}: {why}")
                    if total == 0:
                        self._fail("No readable pages were found in the selected PDF(s).")
                        return
                    self.status.configure(text=f"Breaking the PDF(s) into page images 0/{total}...",
                                          text_color="#f1c40f")
                elif kind == "split_progress":
                    self.split_done = payload
                    self.status.configure(text=f"Breaking the PDF(s) into page images {payload}/{self.n_total}...",
                                          text_color="#f1c40f")
                elif kind == "split_done":
                    self._begin_scan(payload)
                    return
                elif kind == "split_error":
                    self._fail(payload)
                    return
        except pyqueue.Empty:
            pass

    def _begin_scan(self, pages):
        self.entries, self.ptr = pages, 0
        self.jobs = [{"path": e["path"], "quad": list(FULL_QUAD), "filter": PDF_FILTER, "rot": 0,
                      "sharpen": PDF_SHARPEN} for e in pages if e["path"]]
        if not self.jobs:
            self._fail("None of the PDF pages could be turned into images.")
            return
        eng = self.app._ocr_start_engine()
        self.sp = SmoothProgress(len(self.jobs), with_ocr=True, ocr_prior=eng.get("bench"))
        self.tmp_scan = tempfile.mkdtemp(prefix="pdf_scan_")
        old = self.split_proc
        if old is not None:
            try:
                old.join(timeout=0.5)
            except Exception:
                pass
        self.split_q = self.split_proc = None
        q = mp.Queue()
        p = mp.Process(target=scan_worker_process, args=(self.jobs, self.tmp_scan, q), daemon=True)
        p.start()
        self.scan_q, self.scan_proc = q, p
        self.stage = "scan"
        self.status.configure(text=f"Auto-scanning pages 0/{len(self.jobs)} (Auto mode, Medium sharpness)...",
                              text_color="#f1c40f")

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
                        text=f"Auto-scanning pages {payload}/{len(self.jobs)} (Auto mode, Medium sharpness)...",
                        text_color="#f1c40f")
                elif kind == "scan_done":
                    p, self.scan_proc, self.scan_q = self.scan_proc, None, None
                    if p is not None:
                        try:
                            p.join(timeout=0.5)
                        except Exception:
                            pass
                    if self.tmp_pages:                       # the rendered page images are no longer needed
                        shutil.rmtree(self.tmp_pages, ignore_errors=True)
                        self.tmp_pages = None
                    self.stage = "ocr"
                    eng = self.app._ocr_start_engine()
                    if self.sp:
                        self.sp.set_prior(eng.get("bench"))
                        self.sp.scan_finished(eng["ready"])
                    eng["task_q"].put(("ocr", self.rid, payload))
                    if eng["ready"]:
                        self.status.configure(text=f"Extracting text 0/{len(self.jobs)}...", text_color="#3498db")
                    else:
                        self.status.configure(text="All pages scanned. Waiting for the OCR engine to finish "
                                                   "starting...", text_color="#f1c40f")
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
                        self.status.configure(text=f"Extracting text {self.ocr_done}/{len(self.jobs)}...",
                                              text_color="#3498db")
                elif kind == "engine_error":
                    eng["error"] = payload
                    if self.stage in ("split", "scan", "ocr"):
                        self._fail(f"Error initializing PaddleOCR: {payload}")
                elif kind == "result":
                    rid, path, text = payload
                    if rid == self.rid and self.stage == "ocr":
                        self._on_result(text)
                elif kind == "done":
                    if payload == self.rid and self.stage == "ocr":
                        self._finish_ok()
        except pyqueue.Empty:
            pass
        if self.stage == "ocr" and not eng["error"] and not eng["proc"].is_alive():
            self.app._ocr_engine = None
            self._fail("The OCR engine stopped unexpectedly. Please try again.")

    # ------------------------------------------------------------------ results
    def _emit(self, entry, text):
        if len(self.pdfs) > 1 and entry["pdf"] != self._last_pdf:
            gap = "" if self._last_pdf is None else "\n"
            self._add("head", os.path.basename(self.pdfs[entry["pdf"]]), gap)
        self._last_pdf = entry["pdf"]
        self._add("page", entry["page"], text)

    def _on_result(self, text):
        while self.ptr < len(self.entries):
            e = self.entries[self.ptr]
            self.ptr += 1
            if e["path"] is None:                           # a page that could not be turned into an image
                self._emit(e, f"[Could not read this page: {e['err']}]")
                continue
            self._emit(e, text if text.strip() else "(no text found on this page)")
            if text and not text.startswith("[Error"):
                self.words += len(text.split())
            break
        self.ocr_done += 1
        if self.sp:
            self.sp.ocr_result()
        self.status.configure(text=f"Extracting text {self.ocr_done}/{len(self.jobs)}...", text_color="#3498db")

    def _finish_ok(self):
        elapsed = time.perf_counter() - self.t0
        while self.ptr < len(self.entries):                 # unreadable pages at the very end
            e = self.entries[self.ptr]
            self.ptr += 1
            if e["path"] is None:
                self._emit(e, f"[Could not read this page: {e['err']}]")
        self.stage = "done"
        self._set_busy(False)
        self.status.configure(
            text=f"✅ Done — extracted text from {len(self.entries)} page(s) of {len(self.pdfs)} PDF(s).",
            text_color="#2ecc71")
        self._set_stats(f"⏱ Time taken = {format_duration(elapsed)}      "
                        f"📝 Number of words in txt = {self.words}")
        self._kill_procs()
        self._cleanup_tmp()
        if hasattr(self.app, "toast"):
            self.app.toast("📄 PDF text extracted!")
