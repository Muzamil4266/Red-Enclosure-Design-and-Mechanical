"""
ocr_gallery.py  (part of the redesigned PaddleOCR panel)

The picture picker and the thumbnail screen used by "Extract text from images" (up to 2000 photos) and
"Images to PDF" (up to 1000 images).

  choose_images(parent, room)   small chooser (Select photos / Select a whole folder) + the normal PC dialog.
                                Returns (paths, folder or None) or None.
  PhotoGallery                  every picture as a numbered thumbnail. Only: rotate, remove (x), add photos,
                                thumbnail zoom (Ctrl + wheel / touchpad pinch / buttons) and Next.
                                No filters, crop or sharpness here.

The grid is drawn on ONE canvas and only the visible tiles are drawn, thumbnails are decoded by a background
thread and kept as small JPEG bytes, so 2000 photos stay light on an 8 GB PC.
"""
import io
import math
import os
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from PIL import Image, ImageOps, ImageTk

from ocr_imaging import load_oriented

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")
IMAGE_TYPES = [("Images", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp")]
MAX_EXTRACT_PHOTOS = 2000
MAX_PDF_IMAGES = 1000
THUMB_SRC = 320                      # longest side of the kept thumbnail
CELL_MIN, CELL_MAX, CELL_DEFAULT = 90, 420, 170
PAD = 10


# ----------------------------------------------------------------------------------------------
# Picking
# ----------------------------------------------------------------------------------------------
def natural_key(path):
    """IMG_2 before IMG_10."""
    name = os.path.basename(path)
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def list_images(folder):
    """Every picture directly inside the folder, in natural order."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    found = [os.path.join(folder, n) for n in names
             if os.path.splitext(n)[1].lower() in IMAGE_EXTS and os.path.isfile(os.path.join(folder, n))]
    return sorted(found, key=natural_key)


def choose_images(parent, room):
    """Windows has no dialog that picks files AND folders, so a small chooser asks which one first."""
    win = ctk.CTkToplevel(parent)
    win.title("Pick photos/folder")
    win.resizable(False, False)
    win.attributes("-topmost", True)
    try:
        win.transient(parent.winfo_toplevel())
    except Exception:
        pass
    parent.update_idletasks()
    w, h = 400, 235
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - w) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 2)
    win.geometry(f"{w}x{h}+{x}+{y}")
    choice = {"v": None}

    def pick(v):
        choice["v"] = v
        win.destroy()

    ctk.CTkLabel(win, text="Pick photos/folder", font=ctk.CTkFont(size=15, weight="bold"),
                 text_color="#3498db").pack(pady=(18, 10))
    for text, v in (("🖼  Select photos", "photos"), ("📁  Select a whole folder", "folder")):
        ctk.CTkButton(win, text=text, width=300, height=40, font=ctk.CTkFont(size=13, weight="bold"),
                      command=lambda v=v: pick(v)).pack(pady=5)
    ctk.CTkLabel(win, text=f"Room for {room} more picture(s). A folder takes every picture directly inside it.",
                 font=ctk.CTkFont(size=10), text_color="#8a8a8a", wraplength=360).pack(pady=(8, 0))
    win.bind("<Escape>", lambda e: win.destroy())

    def grab():
        try:
            win.focus_force()
            win.grab_set()
        except Exception:
            pass

    win.after(180, grab)
    parent.wait_window(win)

    kind = choice["v"]
    folder = None
    if kind == "photos":
        paths = list(filedialog.askopenfilenames(parent=parent, title="Select photos", filetypes=IMAGE_TYPES))
    elif kind == "folder":
        folder = filedialog.askdirectory(parent=parent, title="Select the folder that contains the photos")
        paths = list_images(folder) if folder else []
        if folder and not paths:
            messagebox.showinfo("No pictures", "This folder has no pictures directly inside it.", parent=parent)
            return None
    else:
        return None
    if not paths:
        return None
    if len(paths) > room:
        messagebox.showinfo("Too many pictures", f"Only {room} more picture(s) fit. The first {room} are used.",
                            parent=parent)
        paths = paths[:room]
    return paths, folder


# ----------------------------------------------------------------------------------------------
# Thumbnail gallery
# ----------------------------------------------------------------------------------------------
class PhotoGallery(ctk.CTkFrame):

    def __init__(self, master, paths, limit, on_next):
        super().__init__(master, fg_color="transparent")
        self.limit = limit
        self.on_next = on_next
        self.items = []                 # {"uid", "path", "rot", "jpg", "bad"}
        self._by_uid = {}
        self._uid = 0
        self.cell = CELL_DEFAULT
        self._cache = {}                # (uid, rot, cell) -> PhotoImage, kept in use order
        self._q = queue.Queue()         # (uid, jpeg bytes or None) from the loader thread
        self._todo = queue.Queue()
        self._closed = False
        self._loaded = 0
        self._wanted = 0
        self._redraw_job = None
        self._sr = None
        self._loader = threading.Thread(target=self._loader_run, daemon=True)
        self._loader.start()
        self._build()
        self.add_paths(paths)
        self.after(40, self._pump)

    # ------------------------------------------------------------------ UI
    def _build(self):
        bold = ctk.CTkFont(size=12, weight="bold")
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=15, pady=(2, 4))
        ctk.CTkButton(top, text="＋ Add photos", height=32, fg_color="#27ae60", hover_color="#1e8449", font=bold,
                      command=self.add_photos).pack(side="left")
        self.info = ctk.CTkLabel(top, text="", font=bold, text_color="#f1c40f")
        self.info.pack(side="left", padx=14)
        ctk.CTkButton(top, text="🔍+", width=36, height=30, fg_color="#34495e", hover_color="#2c3e50",
                      command=lambda: self._zoom(1.25)).pack(side="right", padx=2)
        ctk.CTkButton(top, text="🔍−", width=36, height=30, fg_color="#34495e", hover_color="#2c3e50",
                      command=lambda: self._zoom(1 / 1.25)).pack(side="right", padx=2)
        ctk.CTkLabel(top, text="⟳ rotate   × remove   Ctrl + wheel / pinch = zoom", font=ctk.CTkFont(size=11),
                     text_color="#8a8a8a").pack(side="right", padx=10)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=15, pady=(6, 10))
        self.prog = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.prog_lbl = ctk.CTkLabel(bottom, text="", font=ctk.CTkFont(size=11), text_color="#a0a0a0")
        self.next_btn = ctk.CTkButton(bottom, text="Next ▶", width=130, height=38, fg_color="#27ae60",
                                      hover_color="#1e8449", font=ctk.CTkFont(size=14, weight="bold"),
                                      command=self._next)
        self.next_btn.pack(side="right")

        area = ctk.CTkFrame(self, fg_color="transparent")
        area.pack(fill="both", expand=True, padx=15)
        self.canvas = c = tk.Canvas(area, bg="#0d0d0d", highlightthickness=0, yscrollincrement=60)
        self.sb = ctk.CTkScrollbar(area, orientation="vertical", command=c.yview)
        c.configure(yscrollcommand=self._on_yscroll)
        self.sb.pack(side="right", fill="y")
        c.pack(side="left", fill="both", expand=True)
        c.bind("<Configure>", lambda e: self._schedule_redraw())
        c.bind("<Enter>", lambda e: c.focus_set())
        c.bind("<MouseWheel>", self._wheel)
        c.bind("<Control-MouseWheel>", self._wheel_zoom)
        c.tag_bind("g_rot", "<Button-1>", lambda e: self._rotate(self._uid_of_current()))
        c.tag_bind("g_del", "<Button-1>", lambda e: self._remove(self._uid_of_current()))

    def _on_yscroll(self, a, b):
        self.sb.set(a, b)
        self._schedule_redraw()

    # ------------------------------------------------------------------ data
    def count(self):
        return len(self.items)

    def get_items(self):
        return [{"path": it["path"], "rot": it["rot"]} for it in self.items]

    def add_paths(self, paths):
        have = {os.path.normcase(os.path.abspath(it["path"])) for it in self.items}
        room = self.limit - len(self.items)
        added = 0
        for p in paths:
            if added >= room:
                break
            key = os.path.normcase(os.path.abspath(p))
            if key in have:
                continue
            have.add(key)
            self._uid += 1
            it = {"uid": self._uid, "path": p, "rot": 0, "jpg": None, "bad": False}
            self.items.append(it)
            self._by_uid[it["uid"]] = it
            self._todo.put((it["uid"], p))
            added += 1
        self._wanted += added
        self._update_info()
        self._schedule_redraw()
        return added

    def add_photos(self):
        room = self.limit - len(self.items)
        if room <= 0:
            messagebox.showinfo("Limit reached", f"You already have the maximum of {self.limit} pictures.",
                                parent=self)
            return
        res = choose_images(self, room)
        if res:
            self.add_paths(res[0])

    def _update_info(self):
        n = len(self.items)
        self.info.configure(text=f"{n} picture{'s' if n != 1 else ''}  (max {self.limit})")
        self.next_btn.configure(state="normal" if n else "disabled")

    def _loader_run(self):
        while True:
            job = self._todo.get()
            if job is None or self._closed:
                return
            uid, path = job
            if uid not in self._by_uid:                  # removed before it was loaded
                self._q.put((uid, None))
                continue
            try:
                img = load_oriented(path, 0, THUMB_SRC)
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=85)
                self._q.put((uid, buf.getvalue()))
            except Exception:
                self._q.put((uid, None))

    def _pump(self):
        if self._closed:
            return
        changed = False
        for _ in range(15):
            try:
                uid, data = self._q.get_nowait()
            except queue.Empty:
                break
            self._loaded += 1
            it = self._by_uid.get(uid)
            if it is not None:
                it["jpg"], it["bad"] = data, data is None
                changed = True
        if changed:
            self._schedule_redraw()
        if self._loaded < self._wanted:
            self.prog["value"] = 100.0 * self._loaded / max(1, self._wanted)
            self.prog_lbl.configure(text=f"Loading pictures {self._loaded}/{self._wanted}...")
            if not self.prog.winfo_ismapped():
                self.prog_lbl.pack(side="left")
                self.prog.pack(side="left", fill="x", expand=True, padx=(8, 12))
        elif self.prog.winfo_ismapped():
            self.prog.pack_forget()
            self.prog_lbl.pack_forget()
        self.after(40, self._pump)

    # ------------------------------------------------------------------ drawing
    def _schedule_redraw(self):
        if self._redraw_job is None and not self._closed:
            self._redraw_job = self.after(16, self._redraw)

    def _tile_photo(self, it):
        key = (it["uid"], it["rot"], self.cell)
        ph = self._cache.pop(key, None)
        if ph is None:
            img = Image.open(io.BytesIO(it["jpg"])).convert("RGB")
            if it["rot"]:
                img = img.rotate(-it["rot"], expand=True)
            img = ImageOps.contain(img, (self.cell - 10, self.cell - 10), Image.Resampling.BILINEAR)
            ph = ImageTk.PhotoImage(img)
        self._cache[key] = ph                           # re-inserted = most recently used
        while len(self._cache) > 400:
            self._cache.pop(next(iter(self._cache)))
        return ph

    def _redraw(self):
        self._redraw_job = None
        if self._closed:
            return
        c = self.canvas
        W, H = max(240, c.winfo_width()), max(120, c.winfo_height())
        cell = self.cell
        cols = max(1, (W - PAD) // (cell + PAD))
        n = len(self.items)
        rows = max(1, math.ceil(n / cols))
        sr = (0, 0, W, max(rows * (cell + PAD) + PAD, H))
        if sr != self._sr:
            self._sr = sr
            c.configure(scrollregion=sr)
        left = max(PAD, (W - (cols * (cell + PAD) - PAD)) // 2)
        y0 = c.canvasy(0)
        first = max(0, int((y0 - PAD) // (cell + PAD)))
        last = min(rows - 1, int((y0 + H) // (cell + PAD)))
        c.delete("all")
        if n == 0:
            c.create_text(W / 2, H / 2, text="No pictures. Press ＋ Add photos.", fill="#888888",
                          font=("Arial", 13, "bold"))
            return
        for r in range(first, last + 1):
            for k in range(cols):
                idx = r * cols + k
                if idx >= n:
                    break
                self._draw_tile(self.items[idx], idx, left + k * (cell + PAD), PAD + r * (cell + PAD))

    def _draw_tile(self, it, idx, x, y):
        c, cell = self.canvas, self.cell
        c.create_rectangle(x, y, x + cell, y + cell, fill="#1b1b1b", outline="#3a3a3a")
        if it["jpg"]:
            c.create_image(x + cell / 2, y + cell / 2, image=self._tile_photo(it))
        elif it["bad"]:
            c.create_text(x + cell / 2, y + cell / 2, text="Cannot open", fill="#e74c3c", font=("Arial", 11, "bold"))
        else:
            c.create_text(x + cell / 2, y + cell / 2, text="Loading...", fill="#888888", font=("Arial", 11))
        t = c.create_text(x + 12, y + cell - 15, text=str(idx + 1), anchor="w", fill="#f1c40f",
                          font=("Arial", 11, "bold"))
        bb = c.bbox(t)
        if bb:
            c.tag_lower(c.create_rectangle(bb[0] - 4, bb[1] - 2, bb[2] + 4, bb[3] + 2, fill="#000000", outline=""), t)
        u = f"u{it['uid']}"
        c.create_oval(x + 6, y + 6, x + 30, y + 30, fill="white", outline="#888888", tags=("g_rot", u))
        c.create_text(x + 18, y + 18, text="⟳", fill="#222222", font=("Arial", 12, "bold"), tags=("g_rot", u))
        c.create_oval(x + cell - 30, y + 6, x + cell - 6, y + 30, fill="#c0392b", outline="white", tags=("g_del", u))
        c.create_text(x + cell - 18, y + 18, text="×", fill="white", font=("Arial", 13, "bold"), tags=("g_del", u))

    # ------------------------------------------------------------------ actions
    def _uid_of_current(self):
        for t in self.canvas.gettags("current"):
            if t.startswith("u") and t[1:].isdigit():
                return int(t[1:])
        return None

    def _rotate(self, uid):
        it = self._by_uid.get(uid)
        if it is not None:
            it["rot"] = (it["rot"] + 90) % 360
            self._schedule_redraw()

    def _remove(self, uid):
        it = self._by_uid.pop(uid, None)
        if it is None:
            return
        self.items.remove(it)
        self._update_info()
        self._schedule_redraw()

    def _wheel(self, e):
        steps = int(-e.delta / 120 * 3) or (-1 if e.delta > 0 else 1)
        self.canvas.yview_scroll(steps, "units")

    def _wheel_zoom(self, e):
        self._zoom(math.exp(e.delta * 0.0015))
        return "break"

    def _zoom(self, factor):
        new = max(CELL_MIN, min(CELL_MAX, int(round(self.cell * factor))))
        if new == self.cell:
            return
        frac = self.canvas.yview()[0]
        self.cell = new
        if self._redraw_job:
            self.after_cancel(self._redraw_job)
        self._redraw()
        self.canvas.yview_moveto(frac)

    def _next(self):
        if self.items:
            self.on_next()

    def shutdown(self):
        self._closed = True
        self._todo.put(None)
        if self._redraw_job:
            try:
                self.after_cancel(self._redraw_job)
            except Exception:
                pass
