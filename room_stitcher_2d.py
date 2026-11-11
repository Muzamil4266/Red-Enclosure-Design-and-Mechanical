"""
Red Circuit - 2D Room Panorama Stitcher Module
UI + canvas placement. All image processing lives in room_stitcher_core.py
(keep both files in the same folder).
"""

import io
import time
import math
import base64
import threading
import queue

import cv2
import customtkinter as ctk
from tkinter import filedialog
from PIL import Image

from room_stitcher_core import stitch_room, natural_key

MODES = {
    "Auto (recommended)": "auto",
    "360° room (ceiling + floor included)": "spherical",
    "Wall view (flat, clean rectangle)": "cylindrical",
}
QUALITY_HINT = {
    "Auto": "Auto (recommended - picks by photo count & free RAM)",
    "Fast": "Fast (neighbour matching, ~10 MP result)",
    "Balanced": "Balanced (recommended, ~30 MP result)",
    "Maximum": "Maximum (slowest, ~60 MP, needs lots of RAM)",
}
CANVAS_JPEG_QUALITY = 95     # image placed on the workspace (kept at full resolution)
LIBRARY_MAX_SIDE = 5000      # copy saved to the component library is capped to this size
LIBRARY_JPEG_QUALITY = 90


class RoomStitcher2DMixin:
    """Mixin class for 2D room stitching and canvas placement with real-time progress tracking."""

    def init_room_stitcher(self):
        self._stitch_busy = False

    def open_room_stitcher_dialog(self):
        if getattr(self, "_stitch_busy", False):
            if hasattr(self, "toast"):
                self.toast("⚠️ A 2D Room Stitching process is already running!")
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("2D Room Photo Stitcher")
        dialog.geometry("480x500")
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        dialog.geometry(f"+{max(10, self.winfo_pointerx() - 240)}+{max(10, self.winfo_pointery() - 200)}")

        ctk.CTkLabel(dialog, text="🖼️ 2D Room Composite Builder",
                     font=ctk.CTkFont(size=14, weight="bold"), text_color="#3498db"
                     ).pack(anchor="w", padx=16, pady=(14, 6))

        tips = (
            "For a clean result:\n"
            "• Stand in ONE spot and turn slowly (don't walk between photos)\n"
            "• Overlap neighbouring photos by 30-50%\n"
            "• Shoot 2-3 rows: eye level, a bit up, a bit down\n"
            "• Avoid extreme ceiling/floor shots; keep exposure steady\n"
            "• Photos named in capture order stitch fastest"
        )
        ctk.CTkLabel(dialog, text=tips, font=ctk.CTkFont(size=11), text_color="gray70",
                     justify="left").pack(padx=16, pady=(0, 8), anchor="w")

        selected = []
        status_lbl = ctk.CTkLabel(dialog, text="No photos selected.",
                                  font=ctk.CTkFont(size=11, weight="bold"), text_color="#f1c40f")
        status_lbl.pack(pady=4)

        def pick():
            files = filedialog.askopenfilenames(
                master=dialog, title="Select Overlapping Room Photos",
                filetypes=[("Image Files", "*.jpg *.jpeg *.png *.bmp *.webp")])
            if files:
                selected[:] = sorted(files, key=natural_key)
                status_lbl.configure(text=f"✔ Selected {len(selected)} photos", text_color="#2ecc71")

        ctk.CTkButton(dialog, text="📂 Select Overlapping Photos", width=260, height=32,
                      fg_color="#1f6aa5", font=ctk.CTkFont(size=11, weight="bold"),
                      command=pick).pack(pady=6)

        ctk.CTkLabel(dialog, text="Result type", font=ctk.CTkFont(size=11, weight="bold")).pack(pady=(8, 0))
        mode_var = ctk.StringVar(value=list(MODES)[0])
        ctk.CTkOptionMenu(dialog, values=list(MODES), variable=mode_var, width=300).pack(pady=3)

        ctk.CTkLabel(dialog, text="Quality", font=ctk.CTkFont(size=11, weight="bold")).pack(pady=(8, 0))
        q_var = ctk.StringVar(value=QUALITY_HINT["Auto"])
        ctk.CTkOptionMenu(dialog, values=list(QUALITY_HINT.values()), variable=q_var, width=300).pack(pady=3)

        def start():
            if len(selected) < 2:
                if hasattr(self, "toast"):
                    self.toast("Please select at least 2 overlapping photos!")
                return
            quality = next(k for k, v in QUALITY_HINT.items() if v == q_var.get())
            mode = MODES[mode_var.get()]
            dialog.destroy()
            self._run_2d_stitching_thread(list(selected), mode, quality)

        ctk.CTkButton(dialog, text="⚡ Stitch & Place on Workspace", width=260, height=36,
                      fg_color="#27ae60", hover_color="#2ecc71",
                      font=ctk.CTkFont(size=12, weight="bold"), command=start).pack(pady=(16, 6))

    def _run_2d_stitching_thread(self, photo_paths, mode="auto", quality="Auto"):
        self._stitch_busy = True
        q = queue.Queue()

        frame = ctk.CTkFrame(self.canvas_container, fg_color="#1b2631", corner_radius=10,
                             border_width=1, border_color="#3498db")
        frame.place(relx=0.5, rely=0.88, anchor="center")
        status_lbl = ctk.CTkLabel(frame, text="🖼️ Step 1/6: Loading photos (0%)...", text_color="#f1c40f",
                                  font=ctk.CTkFont(size=12, weight="bold"))
        status_lbl.pack(padx=20, pady=(10, 4))
        pbar = ctk.CTkProgressBar(frame, width=420, height=14, progress_color="#2ecc71", fg_color="#2c3e50")
        pbar.set(0.0)
        pbar.pack(padx=20, pady=(0, 12))

        stage = {"active": False, "start": 0.0, "end": 0.0, "text": "", "t0": 0.0, "tau": 20.0}

        def work():
            try:
                bgr, info = stitch_room(photo_paths, mode, quality, emit=lambda *m: q.put(m))
                q.put(("done", Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)), info))
            except MemoryError:
                q.put(("err", "Out of memory - choose a lower quality setting."))
            except Exception as e:
                msg = str(e)
                if "Insufficient memory" in msg:
                    msg = "Out of memory - choose a lower quality setting."
                q.put(("err", msg))

        def poll():
            try:
                while True:
                    msg = q.get_nowait()
                    kind = msg[0]
                    if kind == "p":
                        stage["active"] = False
                        pbar.set(min(1.0, msg[1]))
                        status_lbl.configure(text=f"🖼️ {msg[2]}")
                    elif kind == "stage":
                        stage.update(active=True, start=msg[1], end=msg[2], text=msg[3], tau=msg[4], t0=time.time())
                    elif kind == "stage_end":
                        stage["active"] = False
                    elif kind == "err":
                        self._stitch_busy = False
                        frame.destroy()
                        if hasattr(self, "toast"):
                            self.toast(f"❌ 2D Stitching Error: {msg[1]}", 7000)
                        return
                    elif kind == "done":
                        self._stitch_busy = False
                        frame.destroy()
                        info = msg[2]
                        extra = f" (skipped {info['total'] - info['used']} unusable)" if info["used"] < info["total"] else ""
                        kind = "360° room" if info["mode"] == "spherical" else "wall view"
                        warn = ""
                        if info["used"] < 0.7 * info["total"]:
                            warn = " ⚠ many photos could not be linked - add overlap / shoot from one spot"
                        if hasattr(self, "toast"):
                            self.toast(f"✔ Stitched {info['used']}/{info['total']} photos{extra} as {kind} → "
                                       f"{info['width']}×{info['height']} px{warn}", 8000)
                        self._place_stitched_room_on_canvas(msg[1])
                        return
            except queue.Empty:
                pass

            if stage["active"]:
                el = time.time() - stage["t0"]
                val = stage["start"] + (stage["end"] - stage["start"]) * (1 - math.exp(-el / stage["tau"])) * 0.97
                pbar.set(val)
                status_lbl.configure(text=f"🖼️ {stage['text']} ({int(val * 100)}%, {int(el)}s)...")
            self.after(100, poll)

        threading.Thread(target=work, daemon=True).start()
        poll()

    def _place_stitched_room_on_canvas(self, pil_img):
        """Places the panorama on the canvas at full resolution; saves a size-capped copy to the library."""
        try:
            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=CANVAS_JPEG_QUALITY, subsampling=0)
            img_bytes = buf.getvalue()

            name = "Stitched Room Panorama"
            if hasattr(self, "save_component_to_module"):
                lib = pil_img
                if max(lib.size) > LIBRARY_MAX_SIDE:
                    s = LIBRARY_MAX_SIDE / max(lib.size)
                    lib = lib.resize((int(lib.width * s), int(lib.height * s)), Image.LANCZOS)
                lb = io.BytesIO()
                lib.save(lb, format="JPEG", quality=LIBRARY_JPEG_QUALITY)
                self.save_component_to_module(name, base64.b64encode(lb.getvalue()).decode("utf-8"))

            if hasattr(self, "spawn_embedded_component"):
                cx = self.canvas.canvasx(self.canvas.winfo_width() / 2)
                cy = self.canvas.canvasy(self.canvas.winfo_height() / 2)
                item_id = self.spawn_embedded_component(name, img_bytes, x=cx, y=cy)
                if item_id and hasattr(self, "selected_item"):
                    self.selected_item = item_id
                    if hasattr(self, "show_handles"):
                        self.show_handles()
        except Exception as e:
            print(f"Error placing stitched image on canvas: {e}")
