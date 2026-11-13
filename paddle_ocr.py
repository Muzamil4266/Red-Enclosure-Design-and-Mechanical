"""
PaddleOCR Integration Mixin for Red Circuit / Visual Circuit Builder Pro.
Embedded directly inside the main workspace screen (No separate OS window/taskbar item).

(part 5 of 5 - this file keeps the ORIGINAL name, so existing imports keep working.
 It holds PaddleOCRMixin and re-exports every name the old single file had.)

  ocr_imaging.py         settings + crop / sharpen / filter helpers
  ocr_support.py         SmoothProgress, PdfWriter, open_file, _bench_image
  ocr_workers.py         scan_worker_process, _build_ocr, ocr_engine_process
  ocr_gallery.py         choose_images, PhotoGallery (numbered thumbnails: rotate / remove / add / zoom)
  ocr_image_extractor.py ImageTextExtractor ("Extract text from images" work screen)
  ocr_pdf_maker.py       PdfOptionsScreen, PdfBuildScreen ("Images to PDF")
  pdf_text_extractor.py  PdfTextExtractor ("Extract text from PDF")
  ocr_scan_editor.py     ScanEditor (old editor, no longer used by the panel, kept so old imports work)
  paddle_ocr.py          PaddleOCRMixin (this file)

SCREENS
  Home           [Extract text from PDF] [Extract text from images] [Images to PDF]
  Extract text from images -> one sub button "Pick photos/folder" -> gallery (up to 2000 photos) -> Next ->
                  Auto filter + Medium sharpness are applied automatically and the text is extracted
  Images to PDF  -> "Pick photos/folder" -> gallery (up to 1000 images) -> Next -> 2 option steps with an example
                  -> PDF saved next to the pictures
The PaddleOCR model is loaded in the background the moment the OCR panel opens.
"""
import os
import queue as pyqueue
import tkinter as tk  # noqa: F401
from tkinter import filedialog, messagebox
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

from ocr_gallery import PhotoGallery, choose_images, MAX_EXTRACT_PHOTOS, MAX_PDF_IMAGES
from ocr_image_extractor import ImageTextExtractor
from ocr_pdf_maker import PdfOptionsScreen, PdfBuildScreen, default_pdf_path
from pdf_text_extractor import PdfTextExtractor, PDF_TYPES


# ----------------------------------------------------------------------------------------------
# Mixin
# ----------------------------------------------------------------------------------------------
class PaddleOCRMixin:

    def init_paddle_ocr(self):
        self._ocr_panel = None
        self._ocr_engine = None
        self._ocr_tmp = None
        self._ocr_rid = 0
        self._pdf_extract_active = False   # True while a work screen reads the engine's messages itself
        self._ocr_close_fn = None

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

    def _ocr_next_rid(self):
        """Run ids only ever grow: stopping run N (cancel = N) can never stop a later run."""
        self._ocr_rid = getattr(self, "_ocr_rid", 0) + 1
        return self._ocr_rid

    def shutdown_paddle_ocr(self):
        fn = getattr(self, "_ocr_close_fn", None)
        if fn is not None:
            try:
                fn()
            except Exception:
                pass
        self._ocr_stop_engine()

    # ---- the panel ----
    def open_paddle_ocr(self):
        """Displays the AI Offline OCR panel directly inside the main screen workspace."""
        if getattr(self, "_ocr_panel", None) is not None and self._ocr_panel.winfo_exists():
            self._ocr_panel.lift()
            return

        self._ocr_start_engine()
        panel = ctk.CTkFrame(self.canvas_container, fg_color="#181818", corner_radius=12,
                             border_width=2, border_color="#3498db")
        full = {"on": START_FULL}

        def place_panel():
            if full["on"]:
                panel.place(relx=0, rely=0, anchor="nw", relwidth=1, relheight=1)
            else:
                panel.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.82, relheight=0.9)
            panel.lift()

        place_panel()
        self._ocr_panel = panel
        # gallery = kept while the user moves between gallery / extractor / options; work = the current work screen
        S = {"cur": None, "back": None, "gallery": None, "work": None, "mode": None, "folder": None, "status": ""}

        # ---------------- header: [← Back] title ........ [⛶] [✖] ----------------
        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(12, 6))
        title_lbl = ctk.CTkLabel(header, text="🤖 AI Offline OCR — PaddleOCR",
                                 font=ctk.CTkFont(size=14, weight="bold"), text_color="#3498db")
        title_lbl.pack(side="left")
        back_btn = ctk.CTkButton(header, text="← Back", width=90, height=28, corner_radius=14, fg_color="#34495e",
                                 hover_color="#2c3e50", font=ctk.CTkFont(size=12, weight="bold"),
                                 command=lambda: do_back())

        def close_panel():
            discard_gallery()
            drop_work()
            self._ocr_stop_engine()
            self._ocr_close_fn = None
            try:
                panel.destroy()
            except Exception:
                pass
            self._ocr_panel = None

        self._ocr_close_fn = close_panel
        ctk.CTkButton(header, text="✖", width=28, height=28, corner_radius=14, fg_color="#c0392b",
                      hover_color="#e74c3c", font=ctk.CTkFont(size=12, weight="bold"),
                      command=close_panel).pack(side="right")

        def toggle_full():
            full["on"] = not full["on"]
            full_btn.configure(text="❐" if full["on"] else "⛶")
            place_panel()

        full_btn = ctk.CTkButton(header, text="❐" if START_FULL else "⛶", width=28, height=28, corner_radius=14,
                                 fg_color="#1f6aa5", hover_color="#144870", font=ctk.CTkFont(size=14, weight="bold"),
                                 command=toggle_full)
        full_btn.pack(side="right", padx=(0, 6))

        content = ctk.CTkFrame(panel, fg_color="transparent")
        content.pack(fill="both", expand=True)

        # ---------------- screen helpers ----------------
        def later(fn):
            panel.after(10, fn)       # never destroy a screen from inside its own button callback

        def hide_all():
            for f in (home, images_menu, S["gallery"], S["work"]):
                if f is not None:
                    try:
                        f.pack_forget()
                    except Exception:
                        pass

        def show(name, frame, back):
            hide_all()
            S["cur"], S["back"] = name, back
            frame.pack(fill="both", expand=True)
            if back:
                back_btn.pack(side="left", padx=(0, 10), before=title_lbl)
            else:
                back_btn.pack_forget()

        def do_back():
            if S["back"]:
                S["back"]()

        def drop_work():
            w, S["work"] = S["work"], None
            if w is not None:
                try:
                    w.shutdown()
                except Exception:
                    pass
                try:
                    w.destroy()
                except Exception:
                    pass

        def discard_gallery():
            g, S["gallery"] = S["gallery"], None
            if g is not None:
                try:
                    g.shutdown()
                except Exception:
                    pass
                try:
                    g.destroy()
                except Exception:
                    pass

        # ---------------- HOME ----------------
        home = ctk.CTkFrame(content, fg_color="transparent")
        ctk.CTkLabel(home, text="What do you want to do?", font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="#ecf0f1").pack(pady=(34, 14))

        def big(text, sub, cmd, color):
            ctk.CTkButton(home, text=text, width=400, height=54, fg_color=color, hover_color="#144870",
                          font=ctk.CTkFont(size=15, weight="bold"), command=cmd).pack(pady=(10, 0))
            ctk.CTkLabel(home, text=sub, font=ctk.CTkFont(size=11), text_color="#8a8a8a").pack(pady=(3, 4))

        def go_home():
            drop_work()
            discard_gallery()
            show("home", home, None)

        # --- 1. Extract text from PDF (already existed) ---
        def go_pdf_text():
            paths = filedialog.askopenfilenames(title="Select PDF(s) to extract text from", filetypes=PDF_TYPES)
            if not paths:
                return
            drop_work()
            ex = PdfTextExtractor(content, self, list(paths), on_back=lambda: later(go_home))
            S["work"] = ex
            show("pdf_text", ex, None)           # it has its own "Back to OCR" button

        # --- 2. Extract text from images ---
        def go_images_menu():
            show("images_menu", images_menu, go_home)

        # --- 3. Images to PDF ---
        def go_images_to_pdf():
            pick("pdf")

        big("📄 Extract text from PDF", "Read the text of one or many PDF files", go_pdf_text, "#1f6aa5")
        big("🖼 Extract text from images", "Photos or a whole folder, up to 2000 photos", go_images_menu, "#1f6aa5")
        big("📑 Images to PDF", "Photos or a whole folder, up to 1000 images. The PDF is saved next to the pictures",
            go_images_to_pdf, "#d35400")
        status_lbl = ctk.CTkLabel(home, text="", font=ctk.CTkFont(size=11), text_color="#f1c40f")
        status_lbl.pack(pady=(24, 0))

        # ---------------- IMAGES MENU (only one sub button) ----------------
        images_menu = ctk.CTkFrame(content, fg_color="transparent")
        ctk.CTkLabel(images_menu, text="🖼 Extract text from images", font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="#ecf0f1").pack(pady=(40, 18))
        ctk.CTkButton(images_menu, text="📁 Pick photos/folder", width=340, height=56, fg_color="#1f6aa5",
                      hover_color="#144870", font=ctk.CTkFont(size=15, weight="bold"),
                      command=lambda: pick("extract")).pack()
        ctk.CTkLabel(images_menu, text="Pick several photos or a whole folder (up to 2000).",
                     font=ctk.CTkFont(size=11), text_color="#8a8a8a").pack(pady=(8, 0))

        # ---------------- pick -> gallery ----------------
        def pick(mode):
            limit = MAX_EXTRACT_PHOTOS if mode == "extract" else MAX_PDF_IMAGES
            res = choose_images(panel, limit)
            if not res:
                return
            paths, folder = res
            drop_work()
            discard_gallery()
            g = PhotoGallery(content, paths, limit, on_next=gallery_next)
            S.update(gallery=g, mode=mode, folder=folder)
            show("gallery", g, gallery_back)

        def gallery_back():
            g = S["gallery"]
            if g is not None and g.count() > 0:
                if not messagebox.askyesno("Go back?", "Remove the picked pictures and go back?", parent=panel):
                    return
            mode = S["mode"]
            discard_gallery()
            if mode == "extract":
                go_images_menu()
            else:
                show("home", home, None)

        def back_to_gallery():
            drop_work()
            if S["gallery"] is not None:
                show("gallery", S["gallery"], gallery_back)
            else:
                go_home()

        def gallery_next():
            g = S["gallery"]
            if g is None or g.count() == 0:
                return
            items = g.get_items()
            drop_work()
            if S["mode"] == "extract":
                ex = ImageTextExtractor(content, self, items, on_back=lambda: later(back_to_gallery))
                S["work"] = ex
                show("extract", ex, None)        # it has its own "Back to photos" button
            else:
                opt = PdfOptionsScreen(content, items, on_ok=lambda auto, sharp: later(lambda: build_pdf(items, auto, sharp)),
                                       on_back=lambda: later(back_to_gallery))
                S["work"] = opt
                show("pdf_options", opt, None)

        def build_pdf(items, auto, sharp):
            out = default_pdf_path(S["folder"], items[0]["path"])
            drop_work()
            b = PdfBuildScreen(content, self, items, out, auto, sharp,
                               on_back=lambda: later(back_to_gallery), on_home=lambda: later(go_home))
            S["work"] = b
            show("pdf_build", b, None)

        # ---------------- idle engine messages (ready / error) while no work screen owns the engine ----------------
        def refresh_status():
            eng = self._ocr_engine
            if eng is None:
                text, color = "OCR engine is not running. It starts when you pick something.", "#f1c40f"
            elif eng.get("error"):
                text, color = f"Error initializing PaddleOCR: {eng['error']}", "#e74c3c"
            elif eng["ready"]:
                text, color = "✅ OCR engine ready.", "#2ecc71"
            else:
                text, color = "Starting the OCR engine in the background — you can already pick PDFs or pictures.", "#f1c40f"
            if text != S["status"]:
                S["status"] = text
                status_lbl.configure(text=text, text_color=color)

        def poll():
            if self._ocr_panel is None or not self._ocr_panel.winfo_exists():
                return
            eng = self._ocr_engine
            if eng is not None and not self._pdf_extract_active:
                try:
                    while True:
                        kind, payload = eng["out_q"].get_nowait()
                        if kind == "engine_ready":
                            eng["ready"], eng["bench"] = True, payload
                        elif kind == "engine_error":
                            eng["error"] = payload
                        # results / done of a stopped run are simply dropped
                except pyqueue.Empty:
                    pass
            refresh_status()
            self.after(150, poll)

        show("home", home, None)
        self._ocr_start_engine()     # start loading PaddleOCR right now, in the background
        poll()
