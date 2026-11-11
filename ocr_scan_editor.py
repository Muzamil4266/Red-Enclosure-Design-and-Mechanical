"""
ocr_scan_editor.py  (part 4 of 5 of the PaddleOCR scanner)

ScanEditor: the page editor that sits inside the OCR panel (page list, crop handles, rotate,
filters, sharpen, zoom / pan, "Extract Text" and "Images to PDF" buttons).
"""
import math
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from PIL import Image, ImageOps, ImageTk

from ocr_imaging import (MAX_PHOTOS, IMAGE_TYPES, PREVIEW_MAX, THUMB, FULL_QUAD, FILTERS, DEFAULT_FILTER,
                         SHARPEN_LEVELS, DEFAULT_SHARPEN, load_oriented, order_quad, crop_to_quad,
                         process_page, draw_crop_overlay)


# ----------------------------------------------------------------------------------------------
# Scan editor (embedded inside the OCR panel)
# ----------------------------------------------------------------------------------------------
class ScanEditor(ctk.CTkFrame):
    """Loads all selected photos, then lets the user fix crop (manual) / sharpen / filter before OCR.

    Sidebar: drag the divider to make it wider, "+" adds photos, "×" removes a page.
    Zoom: touchpad pinch (Ctrl+MouseWheel), Ctrl+wheel, or the - / + / Fit buttons.
    Pan : two-finger scroll, or drag the page with the mouse.
    """

    def __init__(self, master, paths, on_done, on_cancel, on_pdf=None):
        super().__init__(master, fg_color="transparent")
        self.on_pdf = on_pdf
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.pages = [self._new_page(p) for p in paths]
        self.cur = -1
        self.mode = "crop"          # "crop" = whole photo + visible crop border, "scan" = scanned result
        self.base = None            # current page, oriented + rotated, max PREVIEW_MAX px
        self._photo = None
        self._geom = (0.0, 0.0, 1.0, 1.0)
        self._drag = None
        self._pan_from = None
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self._busy = True
        self._closed = False
        self._cfg_job = None
        self._hq_job = None
        self._filter_btns = {}
        self._filter_imgs = {}
        self.apply_all = tk.BooleanVar(value=True)
        self.sharpen = DEFAULT_SHARPEN   # global auto-sharpen level, applied to ALL pages before the filter
        self._src_cache = (None, None)
        self._build()
        self.after(60, self._scan_step, 0)

    @staticmethod
    def _new_page(path, filt=DEFAULT_FILTER):
        return {"path": path, "quad": list(FULL_QUAD), "filter": filt, "rot": 0,
                "raw": None, "full": None, "row": None, "btn": None, "ci": None}

    def _idx(self, pg):
        for i, p in enumerate(self.pages):
            if p is pg:
                return i
        return -1

    # ---------------- UI ----------------
    def _build(self):
        self.status = ctk.CTkLabel(self, text="Loading pages...", anchor="w", text_color="#f1c40f",
                                   font=ctk.CTkFont(size=12, weight="bold"), wraplength=1000, justify="left")
        self.status.pack(fill="x", padx=15, pady=(2, 2))
        self.progress = ttk.Progressbar(self, mode="determinate", maximum=len(self.pages))
        self.progress.pack(fill="x", padx=15, pady=(0, 6))

        self.body = ctk.CTkFrame(self, fg_color="transparent")  # shown when loading finishes

        # draggable divider between the page list and the editor
        self.paned = tk.PanedWindow(self.body, orient="horizontal", sashwidth=8, sashrelief="flat",
                                    sashcursor="sb_h_double_arrow", bg="#3a3a3a", bd=0, opaqueresize=True)
        self.paned.pack(fill="both", expand=True)
        left = ctk.CTkFrame(self.paned, fg_color="transparent")
        main = ctk.CTkFrame(self.paned, fg_color="transparent")
        self.paned.add(left, minsize=130, width=150, stretch="never")
        self.paned.add(main, minsize=420, stretch="always")

        # sidebar: "+" button on top, page list below
        ctk.CTkButton(left, text="＋  Add photos", height=30, fg_color="#27ae60", hover_color="#1e8449",
                      font=ctk.CTkFont(size=12, weight="bold"), command=self.add_photos
                      ).pack(side="top", fill="x", padx=(0, 4), pady=(0, 6))
        self.listbox = ctk.CTkScrollableFrame(left, width=100, fg_color="#111111")
        self.listbox.pack(side="top", fill="both", expand=True, padx=(0, 4))

        # top: navigation + zoom + page tools
        nav = ctk.CTkFrame(main, fg_color="transparent")
        nav.pack(side="top", fill="x", pady=(0, 4))
        ctk.CTkButton(nav, text="◀", width=32, height=28, command=lambda: self._step_page(-1)).pack(side="left")
        self.page_lbl = ctk.CTkLabel(nav, text="Page - / -", width=84, font=ctk.CTkFont(size=12, weight="bold"))
        self.page_lbl.pack(side="left", padx=2)
        ctk.CTkButton(nav, text="▶", width=32, height=28, command=lambda: self._step_page(1)).pack(side="left")
        ctk.CTkButton(nav, text="🔍−", width=36, height=28, fg_color="#34495e", hover_color="#2c3e50",
                      command=lambda: self._zoom_center(1 / 1.3)).pack(side="left", padx=(12, 2))
        self.zoom_lbl = ctk.CTkLabel(nav, text="100%", width=46, font=ctk.CTkFont(size=11))
        self.zoom_lbl.pack(side="left")
        ctk.CTkButton(nav, text="🔍+", width=36, height=28, fg_color="#34495e", hover_color="#2c3e50",
                      command=lambda: self._zoom_center(1.3)).pack(side="left", padx=2)
        ctk.CTkButton(nav, text="Fit", width=36, height=28, fg_color="#34495e", hover_color="#2c3e50",
                      command=self.zoom_fit).pack(side="left", padx=2)
        ctk.CTkButton(nav, text="↺ Reset Crop", width=84, height=28, fg_color="#7f8c8d", hover_color="#626d6e",
                      command=self.reset_crop).pack(side="right", padx=2)
        ctk.CTkButton(nav, text="⟳ Rotate", width=70, height=28, command=self.rotate_page).pack(side="right", padx=2)
        self.crop_btn = ctk.CTkButton(nav, text="👁 Show Scanned Result", width=150, height=28,
                                      fg_color="#27ae60", hover_color="#1e8449", command=self.toggle_crop)
        self.crop_btn.pack(side="right", padx=2)

        # bottom (packed bottom-up): action row, options row, filter row
        actions = ctk.CTkFrame(main, fg_color="transparent")
        actions.pack(side="bottom", fill="x", pady=(6, 0))
        ctk.CTkButton(actions, text="✖ Cancel", width=90, height=34, fg_color="#c0392b", hover_color="#e74c3c",
                      command=self.on_cancel).pack(side="left")
        self.extract_btn = ctk.CTkButton(actions, text="✔ Done — Extract Text", height=34, fg_color="#27ae60",
                                         hover_color="#1e8449", font=ctk.CTkFont(size=13, weight="bold"),
                                         state="disabled", command=self._finish)
        self.extract_btn.pack(side="right")
        self.pdf_btn = ctk.CTkButton(actions, text="📄 Images to PDF", height=34, fg_color="#d35400",
                                     hover_color="#a04000", font=ctk.CTkFont(size=13, weight="bold"),
                                     state="disabled", command=self._finish_pdf)
        if self.on_pdf:
            self.pdf_btn.pack(side="right", padx=(0, 8))

        opts = ctk.CTkFrame(main, fg_color="transparent")
        opts.pack(side="bottom", fill="x", pady=(4, 0))
        ctk.CTkCheckBox(opts, text="Apply filter to all pages", variable=self.apply_all,
                        command=self._on_apply_all_toggle, font=ctk.CTkFont(size=12)).pack(side="left")
        self.sharpen_seg = ctk.CTkSegmentedButton(opts, values=[n for n, _ in SHARPEN_LEVELS],
                                                  command=self.set_sharpen, height=26)
        self.sharpen_seg.set(dict((v, n) for n, v in SHARPEN_LEVELS)[DEFAULT_SHARPEN])
        self.sharpen_seg.pack(side="right")
        ctk.CTkLabel(opts, text="✨ Auto Sharpen (all pages):", font=ctk.CTkFont(size=12)).pack(side="right", padx=(0, 6))

        frow = ctk.CTkScrollableFrame(main, orientation="horizontal", height=100, fg_color="transparent")
        frow.pack(side="bottom", fill="x", pady=(6, 0))
        for key, label in FILTERS:
            b = ctk.CTkButton(frow, text=label, compound="top", width=76, height=86, fg_color="#1f6aa5",
                              font=ctk.CTkFont(size=11), command=lambda k=key: self.set_filter(k))
            b.pack(side="left", padx=3)
            self._filter_btns[key] = b

        # centre: page canvas
        self.canvas = c = tk.Canvas(main, bg="#0d0d0d", highlightthickness=0)
        c.pack(side="top", fill="both", expand=True)
        c.bind("<Configure>", self._on_resize)
        c.bind("<Enter>", lambda e: c.focus_set())
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._move)
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<Double-Button-1>", lambda e: self.zoom_fit() if self.mode == "scan" else None)
        # touchpad pinch (Windows sends it as Ctrl + MouseWheel) and Ctrl + wheel
        c.bind("<Control-MouseWheel>", self._wheel_zoom)
        c.bind("<Control-Button-4>", lambda e: self._zoom_at(e.x, e.y, 1.15))
        c.bind("<Control-Button-5>", lambda e: self._zoom_at(e.x, e.y, 1 / 1.15))
        # two-finger scroll = pan
        c.bind("<MouseWheel>", self._wheel_pan)
        c.bind("<Shift-MouseWheel>", lambda e: self._wheel_pan(e, horizontal=True))
        c.bind("<Button-4>", lambda e: self._pan_by(0, 60))
        c.bind("<Button-5>", lambda e: self._pan_by(0, -60))
        for seq in ("<Control-plus>", "<Control-equal>"):
            c.bind(seq, lambda e: self._zoom_center(1.3))
        c.bind("<Control-minus>", lambda e: self._zoom_center(1 / 1.3))
        c.bind("<Control-Key-0>", lambda e: self.zoom_fit())

    # ---------------- phase 1: load every page ----------------
    def _load_thumbs(self, pg):
        try:
            self._set_raw(pg, load_oriented(pg["path"], 0, 300))
        except Exception:
            pass  # page stays usable (full-image crop)

    def _scan_step(self, i):
        if self._closed:
            return
        n = len(self.pages)
        if i >= n:
            self._finish_scan()
            return
        self._load_thumbs(self.pages[i])
        self.progress["value"] = i + 1
        self.status.configure(text=f"Loading pages {i + 1}/{n}...", text_color="#f1c40f")
        self.after(1, self._scan_step, i + 1)

    def _finish_scan(self):
        self._busy = False
        self.progress.pack_forget()
        n = len(self.pages)
        self.status.configure(
            text=f"✅ Loaded {n} page(s). The cyan border shows the area that will be kept (outside is darkened). "
                 f"Drag the corners to crop, then press 'Show Scanned Result'.",
            text_color="#2ecc71")
        self.body.pack(fill="both", expand=True, padx=15, pady=(0, 6))
        for pg in self.pages:
            self._make_row(pg)
        self._set_actions("normal")
        self._update_counts()
        self.after(120, lambda: self.select_page(0))

    # ---------------- sidebar rows: add / delete ----------------
    def _make_row(self, pg):
        row = ctk.CTkFrame(self.listbox, fg_color="transparent")
        row.pack(fill="x", pady=3)
        btn = ctk.CTkButton(row, text=str(self._idx(pg) + 1), image=self._page_image(pg), compound="top",
                            width=80, height=112, fg_color="#222222", hover_color="#333333",
                            font=ctk.CTkFont(size=11), command=lambda pg=pg: self.select_page(self._idx(pg)))
        btn.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="×", width=22, height=22, corner_radius=11, fg_color="#c0392b",
                      hover_color="#e74c3c", font=ctk.CTkFont(size=14, weight="bold"),
                      command=lambda pg=pg: self.remove_page(pg)).pack(side="right", anchor="n", padx=(3, 0))
        pg["row"], pg["btn"] = row, btn

    def _renumber(self):
        for i, pg in enumerate(self.pages):
            if pg["btn"] is not None:
                pg["btn"].configure(text=str(i + 1))

    def _update_counts(self):
        n = len(self.pages)
        self.extract_btn.configure(text=f"✔ Extract Text ({n} page{'s' if n != 1 else ''})")
        if self.cur >= 0:
            self.page_lbl.configure(text=f"Page {self.cur + 1} / {n}")

    def remove_page(self, pg):
        if self._busy:
            return
        i = self._idx(pg)
        if i < 0:
            return
        if len(self.pages) == 1:
            if messagebox.askyesno("Remove last page?", "This is the last page.\nRemove it and close the page editor?"):
                self.after(10, self.on_cancel)
            return
        if pg["row"] is not None:
            pg["row"].destroy()
        pg["btn"] = pg["row"] = pg["ci"] = None
        self.pages.pop(i)
        self._renumber()
        if i == self.cur:                       # removed the page that is open -> open the neighbour
            self.cur, self.base = -1, None
            self._update_counts()
            self.select_page(min(i, len(self.pages) - 1))
        else:
            if i < self.cur:
                self.cur -= 1
            self._update_counts()

    def add_photos(self):
        if self._busy:
            return
        room = MAX_PHOTOS - len(self.pages)
        if room <= 0:
            messagebox.showinfo("Limit reached", f"You already have the maximum of {MAX_PHOTOS} photos.")
            return
        paths = filedialog.askopenfilenames(title=f"Add photos (room for {room} more)", filetypes=IMAGE_TYPES)
        if not paths:
            return
        if len(paths) > room:
            messagebox.showinfo("Too many photos", f"Only {room} more photo(s) fit (limit {MAX_PHOTOS}). "
                                                   f"The first {room} will be added.")
        filt = self.pages[self.cur]["filter"] if (self.apply_all.get() and self.cur >= 0) else DEFAULT_FILTER
        new = [self._new_page(p, filt) for p in list(paths)[:room]]
        self.pages.extend(new)
        self._busy = True
        self._set_actions("disabled")
        self._add_step(new, 0)

    def _add_step(self, new, k):
        if self._closed:
            return
        if k >= len(new):
            self._busy = False
            self._set_actions("normal")
            self._update_counts()
            self.status.configure(text=f"➕ Added {len(new)} page(s). Total: {len(self.pages)}.",
                                  text_color="#2ecc71")
            if self.cur < 0:
                self.select_page(0)
            return
        pg = new[k]
        self._load_thumbs(pg)
        self._make_row(pg)
        self.status.configure(text=f"Adding pages {k + 1}/{len(new)}...", text_color="#f1c40f")
        self.after(1, self._add_step, new, k + 1)

    # ---------------- thumbnails ----------------
    def _set_raw(self, pg, img):
        pg["raw"] = ImageOps.contain(crop_to_quad(img, pg["quad"]), (THUMB, THUMB), Image.Resampling.BILINEAR)
        pg["full"] = ImageOps.contain(img, (THUMB, THUMB), Image.Resampling.BILINEAR)

    def _page_image(self, pg):
        if self.mode == "crop" and pg["full"] is not None:
            img = draw_crop_overlay(pg["full"], pg["quad"])
        else:
            raw = pg["raw"] or Image.new("RGB", (THUMB, THUMB), "#444444")
            img = process_page(raw, self.sharpen, pg["filter"])
        ci = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
        pg["ci"] = ci
        return ci

    def _update_page_btn(self, pg):
        if pg["btn"] is not None:
            pg["btn"].configure(image=self._page_image(pg))

    def _update_all_btns(self):
        for pg in self.pages:
            self._update_page_btn(pg)

    def _refresh_filter_ui(self):
        if self.cur < 0:
            return
        pg = self.pages[self.cur]
        raw = pg["raw"] or Image.new("RGB", (THUMB, THUMB), "#444444")
        small = ImageOps.contain(raw, (64, 64))
        for key, _ in FILTERS:
            img = process_page(small, self.sharpen, key)
            ci = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self._filter_imgs[key] = ci
            self._filter_btns[key].configure(image=ci, fg_color="#d35400" if key == pg["filter"] else "#1f6aa5")

    # ---------------- view mode ----------------
    def _set_mode(self, mode):
        self.mode = mode
        if mode == "crop":
            self.crop_btn.configure(text="👁 Show Scanned Result", fg_color="#27ae60")
        else:
            self.crop_btn.configure(text="✂ Show Crop Area", fg_color="#1f6aa5")
        self._update_all_btns()
        self._render()

    def toggle_crop(self):
        if self._busy or self.base is None:
            return
        self._set_mode("scan" if self.mode == "crop" else "crop")

    # ---------------- page navigation ----------------
    def _step_page(self, d):
        if not self._busy and self.cur >= 0:
            self.select_page(max(0, min(len(self.pages) - 1, self.cur + d)))

    def select_page(self, i):
        if self._busy or self._closed or not (0 <= i < len(self.pages)):
            return
        pg = self.pages[i]
        try:
            self.base = load_oriented(pg["path"], pg["rot"], PREVIEW_MAX)
        except Exception as e:
            self.base = None
            self.status.configure(text=f"Could not open page {i + 1}: {e}", text_color="#e74c3c")
            return
        self.cur = i
        self._src_cache = (None, None)
        self.zoom, self.pan = 1.0, [0.0, 0.0]
        self.page_lbl.configure(text=f"Page {i + 1} / {len(self.pages)}")
        for p in self.pages:
            if p["btn"] is not None:
                p["btn"].configure(fg_color="#d35400" if p is pg else "#222222")
        self._render()
        self._refresh_filter_ui()

    # ---------------- drawing ----------------
    def _on_resize(self, event=None):
        if self._cfg_job:
            self.after_cancel(self._cfg_job)
        self._cfg_job = self.after(80, self._render)

    def _clamp_pan(self, dw, dh, cw, ch):
        if self.zoom <= 1.001:
            self.pan = [0.0, 0.0]
            return
        mx, my = max(0.0, (dw - cw) / 2) + 40, max(0.0, (dh - ch) / 2) + 40
        self.pan[0] = max(-mx, min(mx, self.pan[0]))
        self.pan[1] = max(-my, min(my, self.pan[1]))

    def _render(self, fast=False):
        self._cfg_job = None
        if self._closed or self.base is None or self.cur < 0:
            return
        c = self.canvas
        pg = self.pages[self.cur]
        if self.mode == "crop":
            src = self.base
        else:
            key = (id(self.base), tuple(pg["quad"]), self.sharpen, pg["filter"])
            if self._src_cache[0] == key:
                src = self._src_cache[1]
            else:
                src = process_page(crop_to_quad(self.base, pg["quad"]), self.sharpen, pg["filter"])
                self._src_cache = (key, src)
        cw, ch = max(60, c.winfo_width()), max(60, c.winfo_height())
        s = min((cw - 20) / src.width, (ch - 20) / src.height) * self.zoom
        dw, dh = src.width * s, src.height * s
        self._clamp_pan(dw, dh, cw, ch)
        ox, oy = cw / 2 + self.pan[0] - dw / 2, ch / 2 + self.pan[1] - dh / 2
        c.delete("all")
        ix0, iy0, ix1, iy1 = max(0.0, ox), max(0.0, oy), min(float(cw), ox + dw), min(float(ch), oy + dh)
        if ix1 - ix0 >= 1 and iy1 - iy0 >= 1:
            box = (max(0.0, (ix0 - ox) / s), max(0.0, (iy0 - oy) / s),
                   min(float(src.width), (ix1 - ox) / s), min(float(src.height), (iy1 - oy) / s))
            res = Image.Resampling.BILINEAR if fast else (Image.Resampling.LANCZOS if s < 1 else Image.Resampling.BICUBIC)
            tile = src.resize((max(1, int(round(ix1 - ix0))), max(1, int(round(iy1 - iy0)))), res, box=box)
            self._photo = ImageTk.PhotoImage(tile)
            c.create_image(int(round(ix0)), int(round(iy0)), anchor="nw", image=self._photo)
        self._geom = (ox, oy, dw, dh)
        self.zoom_lbl.configure(text=f"{int(round(self.zoom * 100))}%")
        self._draw_overlay()

    def _draw_overlay(self):
        c = self.canvas
        c.delete("overlay")
        if self.mode != "crop" or self.cur < 0:
            return
        ox, oy, dw, dh = self._geom
        pts = [(ox + x * dw, oy + y * dh) for x, y in self.pages[self.cur]["quad"]]
        if sys.platform != "darwin":  # darken everything outside the crop area (Tk ignores stipple on macOS)
            rect = [(ox, oy), (ox + dw, oy), (ox + dw, oy + dh), (ox, oy + dh)]
            for i in range(4):
                j = (i + 1) % 4
                c.create_polygon(*rect[i], *rect[j], *pts[j], *pts[i], fill="black", stipple="gray50",
                                 outline="", tags="overlay")
        flat = [v for p in pts for v in p]
        c.create_polygon(*flat, outline="white", fill="", width=5, tags="overlay")   # visible border, all 4 sides
        c.create_polygon(*flat, outline="#00e5ff", fill="", width=2, tags="overlay")
        for i in range(4):  # edge markers
            mx, my = (pts[i][0] + pts[(i + 1) % 4][0]) / 2, (pts[i][1] + pts[(i + 1) % 4][1]) / 2
            c.create_rectangle(mx - 5, my - 5, mx + 5, my + 5, fill="#00e5ff", outline="white", tags="overlay")
        for x, y in pts:  # draggable corners
            c.create_oval(x - 11, y - 11, x + 11, y + 11, fill="#00e5ff", outline="white", width=2, tags="overlay")

    # ---------------- zoom / pan ----------------
    def _hq_later(self):
        if self._hq_job:
            try:
                self.after_cancel(self._hq_job)
            except Exception:
                pass
        self._hq_job = self.after(160, self._render)   # crisp re-render after the gesture stops

    def _zoom_at(self, mx, my, factor):
        if self.base is None or self.cur < 0:
            return
        new = min(8.0, max(1.0, self.zoom * factor))
        if abs(new - self.zoom) < 1e-6:
            return
        ox, oy, dw, dh = self._geom
        r = new / self.zoom
        nox, noy = mx - (mx - ox) * r, my - (my - oy) * r      # keep the point under the fingers fixed
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.pan = [nox + dw * r / 2 - cw / 2, noy + dh * r / 2 - ch / 2]
        self.zoom = new
        self._render(fast=True)
        self._hq_later()

    def _zoom_center(self, factor):
        self._zoom_at(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2, factor)

    def zoom_fit(self):
        self.zoom, self.pan = 1.0, [0.0, 0.0]
        self._render()

    def _wheel_zoom(self, e):
        d = e.delta * (40 if sys.platform == "darwin" else 1)
        self._zoom_at(e.x, e.y, math.exp(d * 0.0015))
        return "break"

    def _pan_by(self, dx, dy):
        if self.zoom <= 1.001:
            return
        self.pan[0] += dx
        self.pan[1] += dy
        self._render(fast=True)
        self._hq_later()

    def _wheel_pan(self, e, horizontal=False):
        d = e.delta * (40 if sys.platform == "darwin" else 1) * 0.6
        self._pan_by(d if horizontal else 0, 0 if horizontal else d)

    # ---------------- mouse: crop handles + drag-pan ----------------
    def _press(self, e):
        self._drag = None
        self._pan_from = None
        if self.cur < 0:
            return
        if self.mode == "crop":
            ox, oy, dw, dh = self._geom
            best, bd = None, 28
            for i, (x, y) in enumerate(self.pages[self.cur]["quad"]):
                d = math.hypot(ox + x * dw - e.x, oy + y * dh - e.y)
                if d < bd:
                    best, bd = i, d
            self._drag = best
        if self._drag is None and self.zoom > 1.001:
            self._pan_from = (e.x, e.y, self.pan[0], self.pan[1])

    def _move(self, e):
        if self._drag is not None:
            ox, oy, dw, dh = self._geom
            x = min(1.0, max(0.0, (e.x - ox) / dw))
            y = min(1.0, max(0.0, (e.y - oy) / dh))
            self.pages[self.cur]["quad"][self._drag] = (x, y)
            self._draw_overlay()
        elif self._pan_from is not None:
            x0, y0, p0, p1 = self._pan_from
            self.pan = [p0 + e.x - x0, p1 + e.y - y0]
            self._render(fast=True)
            self._hq_later()

    def _release(self, e):
        self._pan_from = None
        if self._drag is None:
            return
        self._drag = None
        pg = self.pages[self.cur]
        self._set_raw(pg, self.base)
        self._update_page_btn(pg)
        self._refresh_filter_ui()

    # ---------------- tools ----------------
    def reset_crop(self):
        if self._busy or self.base is None:
            return
        pg = self.pages[self.cur]
        pg["quad"] = list(FULL_QUAD)
        self._set_raw(pg, self.base)
        self._update_page_btn(pg)
        self._refresh_filter_ui()
        self._render()

    def rotate_page(self):
        if self._busy or self.base is None:
            return
        pg = self.pages[self.cur]
        pg["rot"] = (pg["rot"] + 90) % 360
        pg["quad"] = order_quad([(1 - y, x) for x, y in pg["quad"]])  # same 90° clockwise turn for the corners
        self.base = self.base.rotate(-90, expand=True)
        self._src_cache = (None, None)
        self._set_raw(pg, self.base)
        self._update_page_btn(pg)
        self._refresh_filter_ui()
        self.zoom_fit()

    def set_sharpen(self, label):
        """Auto sharpen: always applies to every page; the filters then work on the sharpened pages."""
        if self._busy:
            return
        self.sharpen = dict(SHARPEN_LEVELS)[label]
        if self.mode == "crop":
            self.mode = "scan"
            self.crop_btn.configure(text="✂ Show Crop Area", fg_color="#1f6aa5")
        self._update_all_btns()
        self._refresh_filter_ui()
        self._render()

    def set_filter(self, key):
        if self._busy or self.cur < 0:
            return
        if self.mode == "crop":
            self.mode = "scan"
            self.crop_btn.configure(text="✂ Show Crop Area", fg_color="#1f6aa5")
            self._update_all_btns()
        targets = self.pages if self.apply_all.get() else [self.pages[self.cur]]
        for pg in targets:
            pg["filter"] = key
            self._update_page_btn(pg)
        self._refresh_filter_ui()
        self._render()

    def _on_apply_all_toggle(self):
        if self.apply_all.get() and self.cur >= 0:
            self.set_filter(self.pages[self.cur]["filter"])

    # ---------------- finish ----------------
    def _set_actions(self, state):
        self.extract_btn.configure(state=state)
        self.pdf_btn.configure(state=state)

    def _jobs(self):
        return [{"path": p["path"], "quad": [tuple(q) for q in p["quad"]], "filter": p["filter"], "rot": p["rot"],
                 "sharpen": self.sharpen} for p in self.pages]

    def _finish(self):                     # ✔ Extract Text
        if not self._busy:
            self.on_done(self._jobs())

    def _finish_pdf(self):                 # 📄 Images to PDF (only converts the scanned pages to a PDF)
        if not self._busy and self.on_pdf:
            self.on_pdf(self._jobs())

    def shutdown(self):
        self._closed = True
        for job in (self._cfg_job, self._hq_job):
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
