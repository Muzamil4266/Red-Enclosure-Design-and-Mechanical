"""
Insert Picture Mixin for Red Enclosure.

A toolbar button opens the normal PC file picker. One or many photos can be selected and they are
placed directly on the workspace canvas.

The pictures live ONLY on the workspace. They are never added to the components list: that list is
separate and this module never touches it.

Pictures are manipulated exactly like components (same handles, same maths):
    select + drag       move
    corner / side arrows  crop
    right / bottom arrows  stretch width / height
    rotate handle       rotate
    x handle            delete
    Ctrl + wheel        resize the selected picture

Hooks for the rest of the app:
    make_insert_picture_button(parent)          builds the toolbar button
    init_insert_picture()                       registry + right-click menu (safe to call more than once)
    open_insert_picture()                       opens the picker and places the chosen photos
    is_picture(item)                            True when the canvas item is an inserted picture
    pick_picture(x, y, tol)                     topmost picture under the point (used by pick_item)
    draw_picture_handles(item)                  handles of the selected picture (used by show_handles)
    picture_rotation(item)                      current rotation in degrees (used when a rotate drag starts)
    picture_handle_drag(x, y)                   rotate / stretch / crop drag; True when it handled the drag
    scale_picture(item, factor)                 Ctrl + wheel resize of the selected picture
    rescale_inserted_pictures(fast, visible_only)  after every zoom change; returns the off-screen ids when visible_only
    rescale_picture(item_id, fast)              one picture (used by the background zoom pass)
    get_inserted_pictures_snapshot()            cheap in-memory copy for undo / redo (history.py)
    restore_inserted_pictures_snapshot(list)    puts an undo / redo copy back
    get_inserted_pictures_state()               base64 list for saving a project
    restore_inserted_pictures_state(state)      puts the saved pictures back when loading
    clear_inserted_pictures()                   for Clear Workspace
"""
import io
import base64
import math
import os
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
from PIL import Image, ImageOps, ImageTk

from canvas_graphics_geometry import CORNERS

PICTURE_TAG = "inserted_picture"
PICTURE_FILETYPES = [
    ("Pictures", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff *.ico"),
    ("All files", "*.*"),
]
MAX_STORED_SIDE = 2048      # the kept original is never larger than this (saves memory)
MAX_SHOWN_SIDE = 6000       # a picture is never drawn larger than this on screen
START_FRACTION = 0.40       # a new picture starts at most 40 % of the visible area
EDGE_MARGIN = 40
GAP = 24
SCALE_MIN, SCALE_MAX = 0.01, 20.0
HQ_DELAY_MS = 160           # full quality is drawn this long after the last edit
MENU_BG = "#1b1b1b"
MENU_ACTIVE = "#8B4513"


class InsertPictureMixin:

    def init_insert_picture(self):
        """Safe to call more than once: creates the registry and binds the right-click menu once."""
        if not hasattr(self, "_picture_items"):
            # canvas item id -> {"pil", "scale", "crop", "sx", "sy", "rot", "photo", "key", "hq", "cache"}
            self._picture_items = {}
        if getattr(self, "_picture_bound", False) or not hasattr(self, "canvas"):
            return
        for ev in ("<Button-3>", "<Button-2>"):
            self.canvas.tag_bind(PICTURE_TAG, ev, self._on_picture_right_click)
        self._picture_bound = True

    def is_picture(self, item):
        return item is not None and item in getattr(self, "_picture_items", {})

    # ------------------------------------------------------------------ toolbar button
    def make_insert_picture_button(self, parent, **button_kwargs):
        """Returns the toolbar button. Pass the same width/height/colours the other toolbar buttons use."""
        options = dict(text="🖼 Insert Picture", command=self.open_insert_picture)
        options.update(button_kwargs)
        return ctk.CTkButton(parent, **options)

    # ------------------------------------------------------------------ picker + placement
    def open_insert_picture(self):
        paths = filedialog.askopenfilenames(title="Select picture(s) to insert", filetypes=PICTURE_FILETYPES)
        if not paths:
            return
        loaded, failed = [], []
        for path in paths:
            try:
                loaded.append((path, self._load_picture(path)))
            except Exception as e:
                failed.append(f"{os.path.basename(path)}: {e}")
        if loaded:
            self._place_pictures([img for _, img in loaded])
            if hasattr(self, "toast"):
                self.toast(f"🖼 Inserted {len(loaded)} picture(s)")
        if failed:
            messagebox.showwarning("Insert Picture", "These files could not be opened:\n\n" + "\n".join(failed))

    @staticmethod
    def _load_picture(path):
        with Image.open(path) as src:
            img = ImageOps.exif_transpose(src)
            if getattr(src, "is_animated", False):
                src.seek(0)
                img = src.copy()
            img = img.convert("RGBA")
        longest = max(img.size)
        if longest > MAX_STORED_SIDE:
            k = MAX_STORED_SIDE / longest
            img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
        return img

    def _zoom_now(self):
        return max(0.01, float(getattr(self, "current_zoom_scale", 1.0) or 1.0))

    def _place_pictures(self, images):
        self.init_insert_picture()
        canvas = self.canvas
        zoom = self._zoom_now()
        view_w = max(200, canvas.winfo_width())
        view_h = max(200, canvas.winfo_height())
        left = canvas.canvasx(0) + EDGE_MARGIN
        top = canvas.canvasy(0) + EDGE_MARGIN
        right = canvas.canvasx(0) + view_w - EDGE_MARGIN
        max_w = view_w * START_FRACTION
        max_h = view_h * START_FRACTION

        if hasattr(self, "begin_change"):
            self.begin_change()

        x, y, row_h = left, top, 0
        for img in images:
            k = min(1.0, max_w / img.width, max_h / img.height)
            shown_w = max(1, int(img.width * k))
            shown_h = max(1, int(img.height * k))
            if x + shown_w > right and x > left:           # next row
                x = left
                y += row_h + GAP
                row_h = 0
            scale = (shown_w / zoom) / img.width           # screen pixels per source pixel at zoom 1.0
            self._add_picture_item(img, x + shown_w / 2, y + shown_h / 2, scale)
            x += shown_w + GAP
            row_h = max(row_h, shown_h)

        if hasattr(self, "commit_change"):
            self.commit_change()
        if hasattr(self, "update_workspace_counter"):
            self.update_workspace_counter()

    def _send_picture_to_back(self, item_id):
        """Pictures sit behind everything the user draws, just above the background grid."""
        c = self.canvas
        c.tag_lower(item_id)
        try:
            grid = getattr(self, "_grid_item", None)
            if grid is not None and c.type(grid):
                c.tag_raise(item_id, grid)
            if c.find_withtag("bg_grid"):
                c.tag_raise(item_id, "bg_grid")
        except tk.TclError:
            pass

    def _add_picture_item(self, pil_image, cx, cy, scale, crop=None, sx=1.0, sy=1.0, rot=0, cache=None):
        """(cx, cy) = centre of the picture on the canvas. scale = screen pixels per source pixel at zoom 1.0.
        cache is shared with history snapshots."""
        self.init_insert_picture()
        item_id = self.canvas.create_image(cx, cy, anchor="center", tags=(PICTURE_TAG,))
        self._picture_items[item_id] = {
            "pil": pil_image, "scale": float(scale), "crop": list(crop) if crop else [0.0, 0.0, 0.0, 0.0],
            "sx": float(sx), "sy": float(sy), "rot": int(rot) % 360,
            "photo": None, "key": None, "hq": False, "cache": cache if cache is not None else {},
        }
        self._send_picture_to_back(item_id)
        self._render_picture(item_id)
        return item_id

    # ------------------------------------------------------------------ geometry (same maths as components)
    def _picture_size(self, rec, zoom=None):
        """(cropped source w, cropped source h, drawn w, drawn h) at the current zoom, before rotation."""
        zoom = self._zoom_now() if zoom is None else zoom
        W0, H0 = rec["pil"].size
        l, t, r, b = rec["crop"]
        cw0 = max(1, int((1 - r) * W0) - int(l * W0))
        ch0 = max(1, int((1 - b) * H0) - int(t * H0))
        s = rec["scale"] * zoom
        tw = max(2, int(cw0 * s * rec["sx"]))
        th = max(2, int(ch0 * s * rec["sy"]))
        longest = max(tw, th)
        if longest > MAX_SHOWN_SIDE:
            k = MAX_SHOWN_SIDE / longest
            tw, th = max(2, int(tw * k)), max(2, int(th * k))
        return cw0, ch0, tw, th

    def _picture_key(self, rec):
        _, _, tw, th = self._picture_size(rec)
        return (tw, th, tuple(rec["crop"]), rec["rot"])

    def picture_rotation(self, item):
        rec = self._picture_items.get(item)
        return rec["rot"] if rec else 0

    def picture_corner_point(self, item, sx, sy):
        rec = self._picture_items[item]
        s = rec["scale"] * self._zoom_now()
        w, h = rec["pil"].size
        l, t, r, b = rec["crop"]
        lx = sx * w * (1 - l - r) * s * rec["sx"] / 2
        ly = sy * h * (1 - t - b) * s * rec["sy"] / 2
        a = math.radians(rec["rot"])
        cx, cy = self.canvas.coords(item)
        return cx + lx * math.cos(a) + ly * math.sin(a), cy - lx * math.sin(a) + ly * math.cos(a)

    def pick_picture(self, x, y, tol=5):
        """Topmost picture whose (rotated, cropped, stretched) rectangle contains the point."""
        c = self.canvas
        zoom = self._zoom_now()
        for item_id in reversed(c.find_withtag(PICTURE_TAG)):
            rec = getattr(self, "_picture_items", {}).get(item_id)
            if rec is None:
                continue
            try:
                cx, cy = c.coords(item_id)[:2]
            except ValueError:
                continue
            w, h = rec["pil"].size
            l, t, r, b = rec["crop"]
            s = rec["scale"] * zoom
            hw = w * (1 - l - r) * s * rec["sx"] / 2 + tol
            hh = h * (1 - t - b) * s * rec["sy"] / 2 + tol
            a = math.radians(rec["rot"])
            dx, dy = x - cx, y - cy
            lx = dx * math.cos(a) - dy * math.sin(a)
            ly = dx * math.sin(a) + dy * math.cos(a)
            if abs(lx) <= hw and abs(ly) <= hh:
                return item_id
        return None

    # ------------------------------------------------------------------ drawing the picture
    def _render_picture(self, item_id, fast=False):
        rec = self._picture_items.get(item_id)
        if rec is None:
            return
        pil = rec["pil"]
        W0, H0 = pil.size
        l, t, r, b = rec["crop"]
        _, _, tw, th = self._picture_size(rec)
        src = pil
        if any(rec["crop"]):
            src = pil.crop((int(l * W0), int(t * H0), int((1 - r) * W0), int((1 - b) * H0)))
        img = src.resize((tw, th), Image.Resampling.BILINEAR if fast else Image.Resampling.LANCZOS)
        if rec["rot"]:
            img = img.rotate(rec["rot"], expand=True,
                             resample=Image.Resampling.NEAREST if fast else Image.Resampling.BICUBIC)
        photo = ImageTk.PhotoImage(img)
        self.canvas.itemconfig(item_id, image=photo)
        rec["photo"], rec["key"], rec["hq"] = photo, self._picture_key(rec), not fast

    def _picture_on_screen(self, item_id, rec, zoom):
        """Cheap test from the coordinates and the size the picture should have (the drawn image may be stale)."""
        c = self.canvas
        try:
            cx, cy = c.coords(item_id)[:2]
        except (tk.TclError, ValueError):
            return True
        _, _, tw, th = self._picture_size(rec, zoom)
        rad = math.hypot(tw, th) / 2 + 4
        x0, y0 = c.canvasx(0), c.canvasy(0)
        return (cx + rad >= x0 and cx - rad <= x0 + c.winfo_width() and
                cy + rad >= y0 and cy - rad <= y0 + c.winfo_height())

    def rescale_picture(self, item_id, fast=False):
        """Re-draws one picture at the current zoom. fast=True uses cheaper filtering (while zooming / editing)."""
        rec = self._picture_items.get(item_id)
        if rec is None:
            return
        try:
            exists = self.canvas.type(item_id) == "image"
        except tk.TclError:
            exists = False
        if not exists:
            del self._picture_items[item_id]
            return
        if self._picture_key(rec) == rec["key"] and (rec["hq"] or fast):
            return
        self._render_picture(item_id, fast)

    def rescale_inserted_pictures(self, fast=False, visible_only=False):
        """Re-draws the inserted pictures at the current zoom.
        visible_only=True skips the ones off screen and returns their ids so they can be done later."""
        skipped = []
        if not getattr(self, "_picture_items", None):
            return skipped
        zoom = self._zoom_now()
        for item_id in list(self._picture_items):
            rec = self._picture_items.get(item_id)
            if rec is None:
                continue
            if visible_only and not self._picture_on_screen(item_id, rec, zoom):
                skipped.append(item_id)
                continue
            self.rescale_picture(item_id, fast)
        return skipped

    # ------------------------------------------------------------------ handles (copy of the component handles)
    def draw_picture_handles(self, item):
        if not self.is_picture(item):
            return
        mid = lambda a, b: ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        F = self.draw_frame([self.picture_corner_point(item, sx, sy) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
        self.draw_handle(*F[0], "×", "delete")
        self.draw_handle(*F[1], "↻", "img_rotate")
        self.draw_handle(*F[3], "↙", "cropBL")
        self.draw_handle(*F[2], "↘", "cropBR")
        self.draw_handle(*mid(F[0], F[3]), "↔", "cropL", 9)
        self.draw_handle(*mid(F[1], F[2]), "↔", "cropR", 9)
        self.draw_handle(*mid(F[0], F[1]), "↕", "cropT", 9)
        self.draw_handle(*mid(F[3], F[2]), "↕", "cropB", 9)

        cc = mid(F[0], F[2])
        mr, mb = mid(F[1], F[2]), mid(F[3], F[2])
        dr = math.hypot(mr[0] - cc[0], mr[1] - cc[1]) or 1
        db = math.hypot(mb[0] - cc[0], mb[1] - cc[1]) or 1
        self.draw_handle(mr[0] + (mr[0] - cc[0]) / dr * 24, mr[1] + (mr[1] - cc[1]) / dr * 24, "⇔", "stretchH", 9)
        self.draw_handle(mb[0] + (mb[0] - cc[0]) / db * 24, mb[1] + (mb[1] - cc[1]) / db * 24, "⇕", "stretchV", 9)

    # ------------------------------------------------------------------ editing (copy of the component controls)
    def _picture_edited(self, item_id):
        """Quick redraw while the user is still editing, full quality shortly after the last edit."""
        self._render_picture(item_id, fast=True)
        job = getattr(self, "_picture_hq_job", None)
        if job:
            self.after_cancel(job)
        self._picture_hq_job = self.after(HQ_DELAY_MS, self._picture_hq_pass, item_id)
        self.show_handles()

    def _picture_hq_pass(self, item_id):
        self._picture_hq_job = None
        rec = getattr(self, "_picture_items", {}).get(item_id)
        if rec is not None and not rec["hq"]:
            self.rescale_picture(item_id)

    def picture_handle_drag(self, x, y):
        """Rotate / stretch / crop drag of the selected picture. Returns True when it handled the drag."""
        item = self.selected_item
        if not self.is_picture(item):
            return False
        if self.box_op == "img_rotate":
            self._picture_rotate_drag(item, x, y)
        elif self.box_op in ("stretch_h", "stretch_v"):
            self._picture_stretch_drag(item, x - self.last_x, y - self.last_y)
        elif self.crop_corner:
            self._picture_crop_drag(item, x - self.last_x, y - self.last_y)
        else:
            return False
        return True

    def _picture_rotate_drag(self, item, x, y):
        rec = self._picture_items[item]
        cx, cy = self.canvas.coords(item)
        a = math.atan2(y - cy, x - cx)
        new = round(self.rot_start - math.degrees(a - self.box_ang)) % 360
        if new != rec["rot"]:
            rec["rot"] = new
            self._picture_edited(item)

    def _picture_stretch_drag(self, item, dx, dy):
        """Stretches along one local axis only (right edge = width, bottom edge = height)."""
        rec = self._picture_items[item]
        a = math.radians(rec["rot"])
        s = rec["scale"] * self._zoom_now()
        w, h = rec["pil"].size
        l, t, r, b = rec["crop"]
        if self.box_op == "stretch_h":
            k = rec["sx"]
            cur = w * (1 - l - r) * s * k
            new = max(10.0, cur + dx * math.cos(a) - dy * math.sin(a))
            rec["sx"] = k * new / cur
            m = (new - cur) / 2
            self.canvas.move(item, m * math.cos(a), -m * math.sin(a))
        else:
            k = rec["sy"]
            cur = h * (1 - t - b) * s * k
            new = max(10.0, cur + dx * math.sin(a) + dy * math.cos(a))
            rec["sy"] = k * new / cur
            m = (new - cur) / 2
            self.canvas.move(item, m * math.sin(a), m * math.cos(a))
        self._picture_edited(item)

    def _picture_crop_drag(self, item, dx, dy):
        rec = self._picture_items[item]
        sxd, syd, _ = CORNERS.get(self.crop_corner, (0, 0, ""))
        a = math.radians(rec["rot"])
        lx, ly = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
        s = rec["scale"] * self._zoom_now()
        sw, sh = s * rec["sx"], s * rec["sy"]
        w, h = rec["pil"].size
        old = rec["crop"]
        l, t, r, b = old
        if sxd < 0: l += lx / (w * sw)
        elif sxd > 0: r -= lx / (w * sw)
        if syd < 0: t += ly / (h * sh)
        elif syd > 0: b -= ly / (h * sh)
        l = max(0, min(l, 0.9 - r)); r = max(0, min(r, 0.9 - l))
        t = max(0, min(t, 0.9 - b)); b = max(0, min(b, 0.9 - t))
        ax = ((l - old[0]) - (r - old[2])) * w * sw / 2
        ay = ((t - old[1]) - (b - old[3])) * h * sh / 2
        self.canvas.move(item, ax * math.cos(a) + ay * math.sin(a), -ax * math.sin(a) + ay * math.cos(a))
        rec["crop"] = [l, t, r, b]
        self._picture_edited(item)

    def scale_picture(self, item, factor):
        """Ctrl + wheel resize of the selected picture (the centre stays where it is)."""
        rec = self._picture_items.get(item)
        if rec is None:
            return
        rec["scale"] = max(SCALE_MIN, min(SCALE_MAX, rec["scale"] * factor))
        self._picture_edited(item)

    # ------------------------------------------------------------------ right-click: delete
    def _on_picture_right_click(self, event):
        items = self.canvas.find_withtag("current")
        if not items or items[0] not in getattr(self, "_picture_items", {}):
            return
        item_id = items[0]
        menu = tk.Menu(self.canvas, tearoff=0, bg=MENU_BG, fg="white",
                       activebackground=MENU_ACTIVE, activeforeground="white")
        menu.add_command(label="🗑 Delete picture", command=lambda: self._delete_picture(item_id))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _delete_picture(self, item_id):
        if item_id not in self._picture_items:
            return
        if hasattr(self, "begin_change"):
            self.begin_change()
        try:
            self.canvas.delete(item_id)
        except tk.TclError:
            pass
        self._picture_items.pop(item_id, None)
        if getattr(self, "selected_item", None) == item_id:
            self.selected_item = None
            self.show_handles()
        if hasattr(self, "commit_change"):
            self.commit_change()

    # ------------------------------------------------------------------ undo / redo (in memory, no encoding)
    def get_inserted_pictures_snapshot(self):
        snap = []
        for item_id, rec in list(getattr(self, "_picture_items", {}).items()):
            try:
                x, y = self.canvas.coords(item_id)[:2]
            except (tk.TclError, ValueError):
                continue
            snap.append({"pil": rec["pil"], "x": x, "y": y, "scale": rec["scale"], "crop": list(rec["crop"]),
                         "sx": rec["sx"], "sy": rec["sy"], "rot": rec["rot"], "cache": rec["cache"]})
        return snap

    def restore_inserted_pictures_snapshot(self, snap):
        """x / y (the picture centre) were already converted to the current zoom by history._apply_zoom_xf."""
        self.clear_inserted_pictures()
        for p in snap or []:
            try:
                self._add_picture_item(p["pil"], p["x"], p["y"], p["scale"], p.get("crop"),
                                       p.get("sx", 1.0), p.get("sy", 1.0), p.get("rot", 0), p.get("cache"))
            except Exception:
                continue

    # ------------------------------------------------------------------ save / restore (disk)
    @staticmethod
    def _encode_picture(pil_image):
        buf = io.BytesIO()
        has_alpha = pil_image.getchannel("A").getextrema()[0] < 255
        if has_alpha:
            pil_image.save(buf, format="PNG")
            kind = "png"
        else:
            pil_image.convert("RGB").save(buf, format="JPEG", quality=92)
            kind = "jpeg"
        return kind, base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def _decode_picture(data):
        return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGBA")

    def get_inserted_pictures_state(self):
        """Serialisable list of every inserted picture (stamped with the zoom they were saved at).
        Each picture is encoded only once; autosaves reuse the cached text."""
        state = []
        zoom = self._zoom_now()
        for item_id, rec in list(getattr(self, "_picture_items", {}).items()):
            try:
                x, y = self.canvas.coords(item_id)[:2]
            except (tk.TclError, ValueError):
                continue
            if "enc" not in rec["cache"]:
                rec["cache"]["enc"] = self._encode_picture(rec["pil"])
            kind, data = rec["cache"]["enc"]
            state.append({"x": x, "y": y, "anchor": "center", "zoom": zoom,
                          "scale": rec["scale"], "crop": list(rec["crop"]), "sx": rec["sx"], "sy": rec["sy"],
                          "rot": rec["rot"],
                          "base_w": rec["pil"].width * rec["scale"], "base_h": rec["pil"].height * rec["scale"],
                          "format": kind, "data": data})
        return state

    def restore_inserted_pictures_state(self, state):
        """Replaces all inserted pictures with the saved ones (project load).
        Pictures saved by the older version (no "anchor": x / y was the top-left corner) are converted."""
        self.clear_inserted_pictures()
        zoom = self._zoom_now()
        for rec in state or []:
            try:
                pil = self._decode_picture(rec["data"])
                z0 = max(0.01, float(rec.get("zoom", zoom)))
                ratio = zoom / z0
                scale = float(rec["scale"]) if "scale" in rec else rec["base_w"] / pil.width
                x, y = rec["x"], rec["y"]
                if rec.get("anchor") != "center":
                    x += rec["base_w"] * z0 / 2
                    y += rec["base_h"] * z0 / 2
                self._add_picture_item(pil, x * ratio, y * ratio, scale, rec.get("crop"),
                                       rec.get("sx", 1.0), rec.get("sy", 1.0), rec.get("rot", 0),
                                       {"enc": (rec.get("format", "png"), rec["data"])})
            except Exception:
                continue

    def clear_inserted_pictures(self):
        old = list(getattr(self, "_picture_items", {}))
        for item_id in old:
            try:
                self.canvas.delete(item_id)
            except tk.TclError:
                pass
        if getattr(self, "selected_item", None) in old:
            self.selected_item = None
        self._picture_items = {}
