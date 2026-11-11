"""
Canvas UI Controls Mixin for Red Circuit.
Handles tool sizes, pen colors, background selection dialogs, eraser size popups,
and toolbar button state highlighting.
"""
import customtkinter as ctk
import math
from tkinter import colorchooser
from PIL import Image, ImageColor, ImageDraw, ImageTk

GRID_BASE = 60        # grid cell size (px) at 100% zoom
GRID_MIN_PX = 40      # a cell is always kept between 40 and 80 px on screen, at any zoom
GRID_MARGIN = 300     # extra pixels drawn around the view so scrolling never shows an edge
GRID_LINE = "#222222"
GRID_DOT = "#383838"

ERASER_DEFAULT = 15
ERASER_SIZES = [("Tiny", 4), ("Small", 8), ("Medium (default)", 15), ("Large", 25), ("Huge", 40)]


class CanvasUIControlsMixin:

    def show_eraser_options(self, event=None):
        popup = ctk.CTkToplevel(self)
        popup.title("Eraser Size")
        popup.geometry("200x250")
        popup.resizable(False, False)
        popup.attributes("-topmost", True)
        popup.geometry(f"+{max(10, self.winfo_pointerx() - 100)}+{max(10, self.winfo_pointery() + 10)}")

        def pick(px):
            self.eraser_size = px
            popup.destroy()

        head = ctk.CTkFrame(popup, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(head, text="Eraser size", font=ctk.CTkFont(size=13, weight="bold"), text_color="#3498db").pack(side="left")
        ctk.CTkButton(head, text="↺", width=28, height=24, command=lambda: pick(ERASER_DEFAULT)).pack(side="right")
        for name, px in ERASER_SIZES:
            cur = px == self.eraser_size
            ctk.CTkButton(popup, text=("✔ " if cur else "") + name, width=170, height=28,
                          fg_color="#d35400" if cur else "#1f6aa5", command=lambda p=px: pick(p)).pack(pady=3)

    def highlight_toolbar_button(self, active_mode):
        for mode, btn in self.toolbar_buttons.items():
            if mode == active_mode:
                btn.configure(
                    width=78, height=30, corner_radius=15,
                    fg_color="#006400",
                    font=ctk.CTkFont(size=12, weight="bold")
                )
            else:
                btn.configure(
                    width=62, height=26, corner_radius=6,
                    fg_color="#1f6aa5",
                    font=ctk.CTkFont(size=11, weight="normal")
                )

    def update_tool_size(self, value):
        self.tool_size = int(float(value))

    def set_pen_color(self, color):
        self.pen_color = color
        self.color_btn.configure(fg_color=color)

    def change_pen_color(self):
        color = colorchooser.askcolor(title="Choose Pen / Line Color", color=self.pen_color)[1]
        if color:
            self.set_pen_color(color)

    def change_background_color(self):
        """Opens dialog to select background options including Absolute Dark Mode."""
        popup = ctk.CTkToplevel(self)
        popup.title("Background Options")
        popup.geometry("240x230")
        popup.resizable(False, False)
        popup.attributes("-topmost", True)

        x = max(10, self.winfo_pointerx() - 120)
        y = max(10, self.winfo_pointery() - 115)
        popup.geometry(f"+{x}+{y}")

        ctk.CTkLabel(popup, text="Select Background Style", font=ctk.CTkFont(size=13, weight="bold"), text_color="#3498db").pack(pady=(12, 8))

        def choose_solid():
            popup.destroy()
            color = colorchooser.askcolor(title="Choose Solid Background Color", color=self.canvas.cget("bg"))[1]
            if color:
                self.begin_change()
                self.canvas.configure(bg=color)
                self.canvas.delete("bg_grid")
                self.current_bg_type = "solid"
                self.commit_change()

        def choose_line_grid():
            popup.destroy()
            self.begin_change()
            self.canvas.configure(bg="#121212")
            self.draw_background_grid("line")
            self.current_bg_type = "line_grid"
            self.commit_change()

        def choose_dot_grid():
            popup.destroy()
            self.begin_change()
            self.canvas.configure(bg="#121212")
            self.draw_background_grid("dot")
            self.current_bg_type = "dot_grid"
            self.commit_change()

        def choose_absolute_dark():
            popup.destroy()
            self.begin_change()
            self.set_absolute_dark_mode()
            self.commit_change()

        # 4 Background Style Options
        ctk.CTkButton(popup, text="🎨 Solid Color...", width=200, height=30, fg_color="#9b59b6", command=choose_solid).pack(pady=3)
        ctk.CTkButton(popup, text="📐 Line Grid Pattern", width=200, height=30, fg_color="#2980b9", command=choose_line_grid).pack(pady=3)
        ctk.CTkButton(popup, text="📍 Dot Matrix Grid", width=200, height=30, fg_color="#27ae60", command=choose_dot_grid).pack(pady=3)
        ctk.CTkButton(popup, text="🖤 Absolute Dark Mode", width=200, height=30, fg_color="#111111", hover_color="#222222", command=choose_absolute_dark).pack(pady=3)

    # ------------------------------------------------------------------ background grid
    # The grid is ONE image that is drawn only for the part of the workspace you can see (plus a margin), so it can
    # never break up, never covers only a small area and costs nothing while zooming: the cell size is kept
    # between GRID_MIN_PX and 2 x GRID_MIN_PX on screen at every zoom level.
    def _grid_spacing(self):
        s = GRID_BASE * max(1e-6, getattr(self, "current_zoom_scale", 1.0))
        while s < GRID_MIN_PX:
            s *= 2
        while s >= GRID_MIN_PX * 2:
            s /= 2
        return s

    def draw_background_grid(self, grid_type):
        self.current_bg_type = "line_grid" if grid_type == "line" else "dot_grid"
        self.canvas.delete("bg_grid")
        self._grid_item = None
        self._grid_key = None
        if not hasattr(self, "_grid_origin"):
            self._grid_origin = [0.0, 0.0]  # moves with the zoom anchor so the grid zooms together with the project
        if not getattr(self, "_grid_polling", False):
            self._grid_polling = True
            self._grid_poll()
        self.grid_refresh(force=True)

    def _grid_poll(self):
        try:
            self.grid_refresh()
        except Exception as e:
            print(f"Grid error: {e}")
        self.after(40, self._grid_poll)  # scrolling / resizing the window has no hook, so it is checked here (cheap)

    def grid_refresh(self, force=False):
        c = self.canvas
        kind = {"line_grid": "line", "dot_grid": "dot"}.get(getattr(self, "current_bg_type", "solid"))
        item = getattr(self, "_grid_item", None)
        if item is not None and not c.type(item):
            item = self._grid_item = None
        if kind is None:  # solid / dark mode: no grid
            if item is not None:
                c.delete(item)
                self._grid_item = None
            self._grid_key = None
            return
        vw, vh = c.winfo_width(), c.winfo_height()
        if vw < 2 or vh < 2:
            return
        margin = 0 if getattr(self, "_zoom_job", None) else GRID_MARGIN  # no margin while zooming = faster frames
        vx, vy = c.canvasx(0), c.canvasy(0)
        s = self._grid_spacing()
        ox, oy = self._grid_origin
        bg = c.cget("bg")
        key = (kind, round(s, 4), round(ox, 3), round(oy, 3), bg, vw, vh, margin)
        rect = getattr(self, "_grid_rect", None)
        inside = bool(rect) and item is not None and rect[0] <= vx and rect[1] <= vy and vx + vw <= rect[2] and vy + vh <= rect[3]
        if not force and inside and key == getattr(self, "_grid_key", None):
            return

        rx0, ry0 = vx - margin, vy - margin
        W, H = int(vw + 2 * margin), int(vh + 2 * margin)
        try:
            bgc = ImageColor.getrgb(bg)
        except ValueError:
            bgc = (18, 18, 18)
        img = Image.new("RGB", (W, H), bgc)
        d = ImageDraw.Draw(img)
        nx0, nx1 = math.ceil((rx0 - ox) / s), math.floor((rx0 + W - ox) / s)
        ny0, ny1 = math.ceil((ry0 - oy) / s), math.floor((ry0 + H - oy) / s)
        if kind == "line":
            for n in range(nx0, nx1 + 1):
                px = int(round(ox + n * s - rx0))
                d.line((px, 0, px, H), fill=GRID_LINE, width=1)
            for n in range(ny0, ny1 + 1):
                py = int(round(oy + n * s - ry0))
                d.line((0, py, W, py), fill=GRID_LINE, width=1)
        else:
            xs = [int(round(ox + n * s - rx0)) for n in range(nx0, nx1 + 1)]
            for n in range(ny0, ny1 + 1):
                py = int(round(oy + n * s - ry0))
                for px in xs:
                    d.ellipse((px - 1, py - 1, px + 1, py + 1), fill=GRID_DOT)

        self._grid_tk = ImageTk.PhotoImage(img)
        if item is None:
            item = c.create_image(rx0, ry0, anchor="nw", image=self._grid_tk, tags="bg_grid", state="disabled")
            self._grid_item = item
        else:
            c.coords(item, rx0, ry0)
            c.itemconfig(item, image=self._grid_tk)
        c.tag_lower(item)
        self._grid_pos = (rx0, ry0)
        self._grid_rect = (rx0, ry0, rx0 + W, ry0 + H)
        self._grid_key = key
