"""
PaddleOCR Integration Mixin for Red Circuit / Visual Circuit Builder Pro.
Embedded directly inside the main workspace screen (No separate OS window/taskbar item).

(part 5 of 5 - this file keeps the ORIGINAL name, so existing imports keep working.
 It holds PaddleOCRMixin and re-exports every name the old single file had.)

  ocr_imaging.py      settings + crop / sharpen / filter helpers
  ocr_support.py      SmoothProgress, PdfWriter, open_file, _bench_image
  ocr_workers.py      scan_worker_process, _build_ocr, ocr_engine_process
  ocr_scan_editor.py  ScanEditor
  pdf_split_worker.py PDF -> page images (own process, used by the PDF text screen)
  pdf_text_extractor.py  PdfTextExtractor: the "Extract text from Pdf" screen, format_duration
  paddle_ocr.py       PaddleOCRMixin (this file)

WORKFLOW
  1. User clicks "Select Photos & Scan"  -> normal PC file dialog opens, user selects photos (up to 500).
  2. All selected photos are loaded into the page editor (left list: drag the divider to widen it,
     "+" adds more photos, "x" removes a page).
  3. Scan editor: Original / Auto / B&W / Vibrant / Soft Tone / Sharp Black / OCV B&W filters,
     manual crop handles, Rotate, and "Apply filter to all pages".
  4. User presses "Extract Text" -> every page is exported as a scanned image,
     and ONLY THEN is the text extracted from the scanned images.
     When it is done the screen shows "Time taken" and "Number of words in txt".
  The PaddleOCR model is loaded in the background the moment the OCR panel opens, so there is
  no waiting for it after scanning.

  5. "Extract text from Pdf" -> file picker (one or many PDFs) -> every PDF is broken into page images,
     every page is auto-scanned (Auto mode, Medium sharpness) and read by the same OCR engine.
     That screen lives in pdf_text_extractor.py so this one stays simple.
"""
import os
import queue as pyqueue
import shutil
import tempfile
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, scrolledtext, messagebox, ttk
import customtkinter as ctk
import multiprocessing as mp

# ---- every name the old single-file module exposed (so `from paddle_ocr import X` still works) ----
from ocr_imaging import (  # noqa: F401
    MAX_PHOTOS, IMAGE_TYPES, PREVIEW_MAX, START_FULL, THUMB, FINAL_MAX_SIDE, DET_MAX_SIDE,
    USE_MKLDNN, USE_MOBILE_MODELS, FULL_QUAD, FILTERS, DEFAULT_FILTER, GRAY_FILTERS,
    SHARPEN_LEVELS, DEFAULT_SHARPEN,
    load_oriented, order_quad, is_full_quad, crop_to_quad, enhance, _shade_fix, _color_filter,
    apply_filter, process_page, build_scanned, draw_crop_overlay)
from ocr_support import SmoothProgress, PdfWriter, open_file, _bench_image  # noqa: F401
from ocr_workers import scan_worker_process, _build_ocr, ocr_engine_process  # noqa: F401
from ocr_scan_editor import ScanEditor  # noqa: F401
from pdf_text_extractor import PdfTextExtractor, format_duration, reflow_text, PDF_TYPES  # noqa: F401


# ----------------------------------------------------------------------------------------------
# Mixin
# ----------------------------------------------------------------------------------------------
class PaddleOCRMixin:

    def init_paddle_ocr(self):
        self._ocr_panel = None
        self._ocr_engine = None
        self._ocr_tmp = None
        self._ocr_rid = 0
        self._pdf_extract_active = False

    def _ocr_next_rid(self):
        """One shared, ever-growing run number for every OCR run (photos or PDFs). The engine stops a run when
        'cancel value >= run number', so a stopped run can never cancel a later one."""
        self._ocr_rid = getattr(self, "_ocr_rid", 0) + 1
        return self._ocr_rid

    # ---- persistent OCR engine (model is loaded once, in the background) ----
    def _ocr_start_engine(self):
        eng = getattr(self, "_ocr_engine", None)
        if eng is not None and eng["proc"].is_alive():
            return eng
        task_q, out_q, cancel = mp.Queue(), mp.Queue(), mp.Value("i", 0)
        proc = mp.Process(target=ocr_engine_process, args=(task_q, out_q, cancel), daemon=True)
        proc.start()
        self._ocr_engine = {"proc": proc, "task_q": task_q, "out_q": out_q, "cancel": cancel,
                            "ready": False, "error": None, "bench": None}
        return self._ocr_engine

    def _ocr_stop_engine(self):
        eng = getattr(self, "_ocr_engine", None)
        if eng is None:
            return
        try:
            eng["task_q"].put(None)
            eng["proc"].join(timeout=0.3)
            if eng["proc"].is_alive():
                eng["proc"].terminate()
        except Exception:
            pass
        self._ocr_engine = None

    def open_paddle_ocr(self):
        """Displays the AI Offline OCR panel directly inside the main screen workspace."""
        if hasattr(self, "_ocr_panel") and self._ocr_panel is not None and self._ocr_panel.winfo_exists():
            self._ocr_panel.lift()
            return

        eng0 = self._ocr_start_engine()

        # Embedded frame attached directly to main canvas container (single main screen)
        panel = ctk.CTkFrame(
            self.canvas_container,
            fg_color="#181818",
            corner_radius=12,
            border_width=2,
            border_color="#3498db"
        )
        full = {"on": START_FULL}

        def place_panel():
            if full["on"]:
                panel.place(relx=0, rely=0, anchor="nw", relwidth=1, relheight=1)
            else:
                panel.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.82, relheight=0.9)
            panel.lift()

        place_panel()
        self._ocr_panel = panel
        self._ocr_tmp = None
        editor_ref = {"ed": None}
        pdf_ref = {"scr": None}      # the "Extract text from Pdf" screen while it is open
        # phase: idle | scanning | pdf | ocr
        run = {"phase": "idle", "total": 0, "done": 0, "sq": None, "proc": None, "id": 0, "prog": None,
               "pdf_path": None, "t0": 0.0, "words": 0, "pages": []}

        def stop_scan():
            p = run["proc"]
            if p is not None:
                try:
                    if p.is_alive():
                        p.terminate()
                except Exception:
                    pass
            run["proc"] = None
            run["sq"] = None

        def cleanup_tmp():
            if getattr(self, "_ocr_tmp", None):
                shutil.rmtree(self._ocr_tmp, ignore_errors=True)
            self._ocr_tmp = None

        # Header bar: [← back]  title ........ [⛶] [✖]
        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(12, 6))

        title_lbl = ctk.CTkLabel(
            header,
            text="🤖 AI Offline OCR — PaddleOCR",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#3498db"
        )
        title_lbl.pack(side="left")

        back_btn = ctk.CTkButton(
            header, text="← Back to pages", width=120, height=28, corner_radius=14,
            fg_color="#34495e", hover_color="#2c3e50", font=ctk.CTkFont(size=12, weight="bold"),
            command=lambda: go_back()
        )  # shown only on the results screen while the page editor still exists

        def close_panel():
            if pdf_ref["scr"] is not None:
                pdf_ref["scr"].shutdown()
            if editor_ref["ed"] is not None:
                editor_ref["ed"].shutdown()
            stop_scan()
            self._ocr_stop_engine()
            cleanup_tmp()
            panel.destroy()
            self._ocr_panel = None
            self._pdf_extract_active = False

        ctk.CTkButton(
            header,
            text="✖",
            width=28,
            height=28,
            corner_radius=14,
            fg_color="#c0392b",
            hover_color="#e74c3c",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=close_panel
        ).pack(side="right")

        def toggle_full():
            full["on"] = not full["on"]
            full_btn.configure(text="❐" if full["on"] else "⛶")
            place_panel()

        full_btn = ctk.CTkButton(
            header,
            text="❐" if START_FULL else "⛶",
            width=28,
            height=28,
            corner_radius=14,
            fg_color="#1f6aa5",
            hover_color="#144870",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=toggle_full
        )
        full_btn.pack(side="right", padx=(0, 6))

        # Content area: either the results view, the scan editor or the PDF text screen
        content = ctk.CTkFrame(panel, fg_color="transparent")
        content.pack(fill="both", expand=True)
        results = ctk.CTkFrame(content, fg_color="transparent")
        results.pack(fill="both", expand=True)

        # Top Action Bar
        top_bar = ctk.CTkFrame(results, fg_color="transparent")
        top_bar.pack(fill="x", padx=15, pady=6)

        select_btn = ctk.CTkButton(
            top_bar,
            text="📁 Select Photos & Scan",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#1f6aa5",
            hover_color="#144870",
            height=32,
            command=lambda: pick_and_scan()
        )
        select_btn.pack(side="left")

        pdf_text_btn = ctk.CTkButton(
            top_bar,
            text="📄 Extract text from Pdf",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#16a085",
            hover_color="#117a65",
            height=32,
            command=lambda: pick_pdfs()
        )
        pdf_text_btn.pack(side="left", padx=(8, 0))

        open_pdf_btn = ctk.CTkButton(
            top_bar, text="📂 Open PDF", font=ctk.CTkFont(size=12, weight="bold"), fg_color="#d35400",
            hover_color="#a04000", height=32, command=lambda: open_file(run["pdf_path"])
        )  # appears after a PDF has been saved

        # Progress bar (0..100 %) + percentage label
        prog_row = ctk.CTkFrame(results, fg_color="transparent")
        prog_row.pack(fill="x", padx=15, pady=(4, 4))
        pct_label = ctk.CTkLabel(prog_row, text="", width=48, anchor="e",
                                 font=ctk.CTkFont(size=13, weight="bold"), text_color="#ecf0f1")
        pct_label.pack(side="right", padx=(8, 0))
        progress = ttk.Progressbar(prog_row, mode="determinate", maximum=100)
        progress.pack(side="left", fill="x", expand=True)

        def set_bar(v):
            progress["value"] = v
            pct_label.configure(text=f"{int(v)}%")

        # Status Label
        status_label = ctk.CTkLabel(
            results,
            text=("✅ OCR engine ready. Select photos to begin." if eng0["ready"]
                  else "Starting the OCR engine in the background — you can select photos right away."),
            font=ctk.CTkFont(size=11),
            text_color="#2ecc71" if eng0["ready"] else "#f1c40f",
            anchor="w"
        )
        status_label.pack(fill="x", padx=15, pady=(0, 6))

        # Bottom Action Bar (packed before the text box so it is never squeezed out)
        bottom_bar = ctk.CTkFrame(results, fg_color="transparent")
        bottom_bar.pack(side="bottom", pady=(4, 12))

        # Text Output Area (Ctrl + wheel / touchpad pinch changes the text size)
        text_font = tkfont.Font(family="Consolas", size=10)
        text_box = scrolledtext.ScrolledText(
            results,
            wrap=tk.WORD,
            bg="#111111",
            fg="#ffffff",
            insertbackground="#ffffff",
            font=text_font,
            borderwidth=1,
            relief="solid"
        )
        text_box.pack(fill=tk.BOTH, expand=True, padx=15, pady=6)

        # "Time taken" and "Number of words" line, shown after the text has been extracted
        stats_label = ctk.CTkLabel(results, text="", anchor="w", font=ctk.CTkFont(size=15, weight="bold"),
                                   text_color="#f1c40f")

        def set_stats(text):
            if text:
                stats_label.configure(text=text)
                stats_label.pack(fill="x", padx=15, pady=(0, 4), before=text_box)
            else:
                stats_label.pack_forget()

        # "Join lines": the OCR engine returns one line per printed line; joined, the text uses the full width
        join_var = tk.BooleanVar(value=True)

        def page_block(name, text):
            body = reflow_text(text) if join_var.get() else text
            return f"--- {name} ---\n{body}\n\n"

        def rerender():
            text_box.delete("1.0", tk.END)
            for name, page_text in run["pages"]:
                text_box.insert(tk.END, page_block(name, page_text))

        def zoom_text(event):
            step = 1 if event.delta > 0 else -1
            text_font.configure(size=max(6, min(40, text_font.cget("size") + step)))
            return "break"

        text_box.bind("<Control-MouseWheel>", zoom_text)

        def copy_text():
            content_txt = text_box.get("1.0", tk.END).strip()
            if not content_txt:
                messagebox.showwarning("Nothing to Copy", "No text extracted yet.")
                return
            self.clipboard_clear()
            self.clipboard_append(content_txt)
            if hasattr(self, "toast"):
                self.toast("📋 Copied to clipboard!")
            else:
                messagebox.showinfo("Copied", "All extracted text copied to clipboard!")

        def save_text():
            content_txt = text_box.get("1.0", tk.END).strip()
            if not content_txt:
                messagebox.showwarning("Nothing to Save", "No extracted text available to save.")
                return
            path = filedialog.asksaveasfilename(
                defaultextension=".txt",
                filetypes=[("Text File", "*.txt")],
                title="Save Extracted Text As"
            )
            if not path:
                return
            with open(path, "w", encoding="utf-8") as f:
                f.write(content_txt)
            if hasattr(self, "toast"):
                self.toast("💾 Text saved to file!")
            else:
                messagebox.showinfo("Saved", f"Text saved to:\n{path}")

        def send_to_canvas():
            content_txt = text_box.get("1.0", tk.END).strip()
            if not content_txt:
                messagebox.showwarning("Nothing to Insert", "No extracted text available to place on workspace canvas.")
                return
            self.begin_change()
            cx = self.canvas.canvasx(self.canvas.winfo_width() / 2)
            cy = self.canvas.canvasy(self.canvas.winfo_height() / 2)
            self.canvas.create_text(
                cx, cy, text=content_txt, anchor="nw", fill=getattr(self, "pen_color", "#e74c3c"),
                tags="text_note", font=("Arial", max(8, int(12 * getattr(self, "current_zoom_scale", 1.0))), "bold")
            )
            self.commit_change()
            if hasattr(self, "update_workspace_counter"):
                self.update_workspace_counter()
            if hasattr(self, "toast"):
                self.toast("✏️ Text inserted onto canvas workspace!")

        ctk.CTkCheckBox(
            bottom_bar, text="↔ Join lines (use full width)", variable=join_var, command=rerender,
            font=ctk.CTkFont(size=12)
        ).pack(side="left", padx=(0, 14))

        ctk.CTkButton(
            bottom_bar, text="📋 Copy Text", width=120, fg_color="#27ae60", hover_color="#1e8449", command=copy_text
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            bottom_bar, text="💾 Save as .txt", width=120, fg_color="#8e44ad", hover_color="#6c3483", command=save_text
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            bottom_bar, text="📌 Put on Canvas Workspace", width=170, fg_color="#d35400", hover_color="#a04000", command=send_to_canvas
        ).pack(side="left", padx=5)

        # ---------------- screens: results <-> page editor <-> PDF text screen ----------------
        def show_results():
            ed = editor_ref["ed"]
            if ed is not None:
                ed.pack_forget()                 # hidden, NOT destroyed: all crop / filter edits are kept
            results.pack(fill="both", expand=True)
            if ed is not None:
                back_btn.pack(side="left", padx=(0, 10), before=title_lbl)
            else:
                back_btn.pack_forget()

        def show_editor():
            back_btn.pack_forget()
            results.pack_forget()
            editor_ref["ed"].pack(fill="both", expand=True)

        def discard_editor():
            ed = editor_ref["ed"]
            if ed is not None:
                ed.shutdown()
                ed.destroy()
                editor_ref["ed"] = None
            show_results()

        def go_back():
            if run["phase"] != "idle":
                if not messagebox.askyesno("Go back?", "This is still running.\nStop it and go back to the pages?"):
                    return
                cancel_run()
            if editor_ref["ed"] is not None:
                show_editor()

        def close_pdf_screen():
            scr = pdf_ref["scr"]
            pdf_ref["scr"] = None
            if scr is not None:
                scr.shutdown()
                scr.destroy()
            show_results()

        def pick_pdfs():
            """'Extract text from Pdf': file picker (one or many PDFs), then the PDF text screen takes over."""
            if run["phase"] != "idle":
                return
            paths = filedialog.askopenfilenames(
                title="Select PDF(s) to extract text from",
                filetypes=PDF_TYPES
            )
            if not paths:
                return
            self._ocr_start_engine()  # no-op while it is alive; restarts it if it ever stopped
            old = pdf_ref["scr"]
            if old is not None:
                old.shutdown()
                old.destroy()
            scr = PdfTextExtractor(content, self, list(paths), on_back=close_pdf_screen)
            pdf_ref["scr"] = scr
            back_btn.pack_forget()
            results.pack_forget()
            if editor_ref["ed"] is not None:
                editor_ref["ed"].pack_forget()
            scr.pack(fill="both", expand=True)

        # ---------------- STEP 1: choose photos, STEP 2: load + edit them ----------------
        def pick_and_scan():
            paths = filedialog.askopenfilenames(
                title=f"Select up to {MAX_PHOTOS} photos",
                filetypes=IMAGE_TYPES
            )
            if not paths:
                return
            if len(paths) > MAX_PHOTOS:
                messagebox.showinfo("Too many photos", f"Only the first {MAX_PHOTOS} photos will be used.")
            paths = list(paths[:MAX_PHOTOS])
            self._ocr_start_engine()  # no-op while it is alive; restarts it if it ever stopped
            if editor_ref["ed"] is not None:
                editor_ref["ed"].shutdown()
                editor_ref["ed"].destroy()
            ed = ScanEditor(content, paths, on_done=start_extraction, on_cancel=discard_editor, on_pdf=start_pdf)
            editor_ref["ed"] = ed
            show_editor()

        # ---------------- runs ----------------
        def finish_run(cleanup=True):
            run["phase"] = "idle"
            select_btn.configure(state="normal")
            pdf_text_btn.configure(state="normal")
            stop_scan()
            if cleanup:
                cleanup_tmp()

        def cancel_run():
            eng = self._ocr_engine
            if run["phase"] == "ocr" and eng is not None:
                eng["cancel"].value = run["id"]          # engine stops after the page it is reading now
            stop_scan()
            if run["phase"] == "pdf" and run["pdf_path"]:
                try:
                    os.remove(run["pdf_path"])
                except Exception:
                    pass
            run["id"] = self._ocr_next_rid()              # results of the stopped run are ignored from now on
            run["prog"] = None
            set_bar(0)
            pct_label.configure(text="")
            set_stats("")
            status_label.configure(text="Stopped. Change the pages if you like and start again.", text_color="#f1c40f")
            finish_run(cleanup=False)

        def begin_run(total, phase, with_ocr):
            eng = self._ocr_start_engine()
            run["id"] = self._ocr_next_rid()
            run.update(phase=phase, total=total, done=0, words=0, pages=[], t0=time.perf_counter())
            run["prog"] = SmoothProgress(total, with_ocr=with_ocr, ocr_prior=eng.get("bench"))
            select_btn.configure(state="disabled")
            pdf_text_btn.configure(state="disabled")
            open_pdf_btn.pack_forget()
            set_bar(0)
            set_stats("")
            stop_scan()
            cleanup_tmp()

        def start_extraction(jobs):
            show_results()
            text_box.delete("1.0", tk.END)
            total = len(jobs)
            begin_run(total, "scanning", True)
            status_label.configure(text=f"Saving scanned pages 0/{total}...", text_color="#f1c40f")
            self._ocr_tmp = tempfile.mkdtemp(prefix="ocr_scan_")
            sq = mp.Queue()
            proc = mp.Process(target=scan_worker_process, args=(jobs, self._ocr_tmp, sq), daemon=True)
            proc.start()
            run["sq"], run["proc"] = sq, proc

        def start_pdf(jobs):
            path = filedialog.asksaveasfilename(
                defaultextension=".pdf",
                filetypes=[("PDF document", "*.pdf")],
                initialfile="scanned_pages.pdf",
                title="Save scanned pages as PDF"
            )
            if not path:
                return                                     # stay on the page editor
            show_results()
            total = len(jobs)
            begin_run(total, "pdf", False)
            run["pdf_path"] = path
            status_label.configure(text=f"Creating PDF 0/{total}...", text_color="#f1c40f")
            sq = mp.Queue()
            proc = mp.Process(target=scan_worker_process, args=(jobs, "", sq, path), daemon=True)
            proc.start()
            run["sq"], run["proc"] = sq, proc

        def poll():
            if self._ocr_panel is None or not self._ocr_panel.winfo_exists():
                return
            eng = self._ocr_engine
            prog = run["prog"]

            # --- messages from the scan / PDF process ---
            sq = run["sq"]
            if sq is not None:
                try:
                    while True:
                        kind, payload = sq.get_nowait()
                        if kind == "scan_progress":
                            if prog:
                                prog.scan_progress(payload)
                            what = "Creating PDF" if run["phase"] == "pdf" else "Saving scanned pages"
                            status_label.configure(text=f"{what} {payload}/{run['total']}...", text_color="#f1c40f")
                        elif kind == "scan_done":
                            stop_scan()
                            eng = self._ocr_start_engine()
                            run["phase"] = "ocr"
                            if prog:
                                prog.set_prior(eng.get("bench"))
                                prog.scan_finished(eng["ready"])
                            eng["task_q"].put(("ocr", run["id"], payload))
                            if eng["ready"]:
                                status_label.configure(text=f"Extracting text 0/{run['total']}...", text_color="#3498db")
                            else:
                                status_label.configure(
                                    text="All pages scanned. Waiting for the OCR engine to finish starting...",
                                    text_color="#f1c40f")
                            break
                        elif kind == "pdf_done":
                            pdf_file, ok, bad = payload
                            if prog:
                                prog.scan_finished(False)
                            run["pdf_path"] = pdf_file
                            msg = f"✅ PDF saved with {ok} page(s): {pdf_file}"
                            if bad:
                                msg += f"   ⚠ {bad} page(s) could not be read and were skipped."
                            status_label.configure(text=msg, text_color="#2ecc71")
                            open_pdf_btn.pack(side="left", padx=(8, 0))
                            if hasattr(self, "toast"):
                                self.toast("📄 PDF saved!")
                            finish_run()
                            break
                        elif kind == "scan_error":
                            status_label.configure(text=f"Error while scanning pages: {payload}", text_color="#e74c3c")
                            run["prog"] = prog = None
                            set_bar(0)
                            pct_label.configure(text="")
                            finish_run()
                            break
                except pyqueue.Empty:
                    pass

            # --- messages from the (already loaded) OCR engine ---
            # (while the "Extract text from Pdf" screen is open it reads these messages itself)
            if eng is not None and not getattr(self, "_pdf_extract_active", False):
                try:
                    while True:
                        kind, payload = eng["out_q"].get_nowait()
                        if kind == "engine_ready":
                            eng["ready"], eng["bench"] = True, payload
                            if prog:
                                prog.set_prior(payload)
                                prog.engine_became_ready()
                            if run["phase"] == "idle":
                                status_label.configure(text="✅ OCR engine ready. Select photos to begin.",
                                                       text_color="#2ecc71")
                            elif run["phase"] == "ocr":
                                status_label.configure(text=f"Extracting text {run['done']}/{run['total']}...",
                                                       text_color="#3498db")
                        elif kind == "engine_error":
                            eng["error"] = payload
                            status_label.configure(text=f"Error initializing PaddleOCR: {payload}", text_color="#e74c3c")
                            if run["phase"] in ("ocr", "scanning"):
                                run["prog"] = prog = None
                                set_bar(0)
                                pct_label.configure(text="")
                                finish_run()
                        elif kind == "result":
                            rid, path, text = payload
                            if rid != run["id"] or run["phase"] != "ocr":
                                continue                                 # result of a stopped run
                            run["pages"].append((os.path.basename(path), text))
                            text_box.insert(tk.END, page_block(os.path.basename(path), text))
                            text_box.see(tk.END)
                            run["done"] += 1
                            if text and not text.startswith("[Error"):
                                run["words"] += len(text.split())
                            if prog:
                                prog.ocr_result()
                            status_label.configure(text=f"Extracting text {run['done']}/{run['total']}...",
                                                   text_color="#3498db")
                        elif kind == "done":
                            if payload == run["id"] and run["phase"] == "ocr":
                                if prog:
                                    prog.finish()
                                elapsed = time.perf_counter() - run["t0"]
                                status_label.configure(
                                    text=f"✅ Done — extracted text from {run['total']} scanned page(s).",
                                    text_color="#2ecc71")
                                set_stats(f"⏱ Time taken = {format_duration(elapsed)}      "
                                          f"📝 Number of words in txt = {run['words']}")
                                finish_run()
                except pyqueue.Empty:
                    pass
                if run["phase"] == "ocr" and not eng["error"] and not eng["proc"].is_alive():
                    status_label.configure(text="The OCR engine stopped unexpectedly. Please try again.",
                                           text_color="#e74c3c")
                    self._ocr_engine = None
                    run["prog"] = prog = None
                    set_bar(0)
                    pct_label.configure(text="")
                    finish_run()

            # --- smooth 0 -> 100 % bar ---
            if prog is not None:
                v = prog.tick()
                set_bar(v)
                if prog.phase == "done" and v >= 100.0:
                    run["prog"] = None

            self.after(60, poll)

        # Start loading PaddleOCR right now, in the background (hides the loading time)
        self._ocr_start_engine()
        poll()
