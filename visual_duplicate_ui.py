"""
Visual Duplicate Image UI (part 2 of 3)
=======================================
Virtualised Tkinter canvas interface, thumbnail generator, multi-threaded worker manager,
and interactive card selection grid.
"""
import os
import sys
import queue
import random
import threading
from bisect import bisect_right
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import customtkinter as ctk
from PIL import Image, ImageTk, ImageOps
import imagehash

from visual_dup_engine import (
    FAIL_HINT, recycle_file, summarize_failures, short_name, fmt_res,
    flatten_to_gray, pixel_signature, signatures_match, find_candidate_clusters
)

BTN_FONT = dict(size=13, weight="bold")
C_SELECT, C_SELECT_H = "#1565c0", "#0d47a1"
C_SCAN, C_SCAN_H = "#00a651", "#007a3d"
C_TOGGLE, C_TOGGLE_H = "#8e24aa", "#6a1b82"
C_DELETE, C_DELETE_H = "#d32f2f", "#9a0007"

THUMB_DEFAULT = 220     # image size in pixels (adjustable with the slider)
TITLE_H = 30            # height of the "Duplicate Set #n" title strip
GROUP_GAP = 12          # vertical gap between sets
BUFFER_PX = 700         # build widgets this far above/below the visible area
CACHE_MAX = 400         # thumbnails kept in memory


class VisualDuplicateCleanerUI:
    def __init__(self, root):
        self.root = root
        self.supported_extensions = ('.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff')
        self.selected_folder = ""
        self.duplicate_groups = []
        self.checkbox_vars = {}
        self.thumb = THUMB_DEFAULT
        self.thumb_cache = OrderedDict()

        self.busy = False                 # True while scanning / recycling
        self.cancel = threading.Event()   # set when the window closes so workers stop quickly
        self.msg_q = queue.Queue()
        self.task_running = False
        self._poll_job = None

        # virtual grid state
        self.tops = []          # y of each set
        self.heights = []       # height of each set
        self.visible = {}       # set index -> (canvas window id, frame)
        self.cols = 1
        self.sizes = None       # measured card sizes
        self._relayout_job = None
        self._size_job = None
        self.empty_text = None

        self.setup_ui()

        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.bind(ev, self.on_mousewheel, add="+")
        self.root.bind("<Destroy>", self._on_destroy, add="+")

    def setup_ui(self):
        top_frame = ctk.CTkFrame(self.root, fg_color="#181818", corner_radius=8)
        top_frame.pack(fill="x", padx=12, pady=10)

        self.btn_select = ctk.CTkButton(
            top_frame, text="📁 Select Folder", command=self.select_folder, width=150, height=36,
            fg_color=C_SELECT, hover_color=C_SELECT_H, text_color="white", font=ctk.CTkFont(**BTN_FONT))
        self.btn_select.pack(side="left", padx=10, pady=10)

        self.lbl_folder = ctk.CTkLabel(top_frame, text="No folder selected", text_color="#aaaaaa",
                                       font=ctk.CTkFont(size=12), anchor="w")
        self.lbl_folder.pack(side="left", fill="x", expand=True, padx=5)

        self.btn_start = ctk.CTkButton(
            top_frame, text="⚡ Scan Duplicates", command=self.start_scan_thread, width=170, height=36,
            fg_color=C_SCAN, hover_color=C_SCAN_H, text_color="white", font=ctk.CTkFont(**BTN_FONT))
        self.btn_start.pack(side="right", padx=10, pady=10)

        status_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        status_frame.pack(fill="x", padx=12, pady=(0, 5))
        self.progress = ttk.Progressbar(status_frame, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x", pady=2)
        self.lbl_status = ctk.CTkLabel(status_frame, text="Status: Ready to scan", text_color="#3498db",
                                       font=ctk.CTkFont(size=12, weight="bold"))
        self.lbl_status.pack(anchor="w", pady=2)

        action_bar = ctk.CTkFrame(self.root, fg_color="#1f1f1f", height=54)
        action_bar.pack(fill="x", padx=12, pady=5)

        ctk.CTkLabel(action_bar, text="Results", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left", padx=(12, 8))

        self.lbl_found = ctk.CTkLabel(action_bar, text="", text_color="#3498db", width=250, anchor="w",
                                      font=ctk.CTkFont(size=13, weight="bold"))
        self.lbl_found.pack(side="left", padx=(4, 4))

        self.lbl_selected = ctk.CTkLabel(action_bar, text="0 selected", text_color="#f1c40f", width=170, anchor="w",
                                         font=ctk.CTkFont(size=17, weight="bold"))
        self.lbl_selected.pack(side="left", padx=8)

        ctk.CTkLabel(action_bar, text="Image size", font=ctk.CTkFont(size=11)).pack(side="left", padx=(14, 4))
        self.size_slider = ctk.CTkSlider(action_bar, from_=140, to=340, number_of_steps=10, width=130,
                                         command=self.on_size_slider)
        self.size_slider.set(self.thumb)
        self.size_slider.pack(side="left", padx=4)

        self.btn_delete = ctk.CTkButton(
            action_bar, text="🗑️ Move Selected to Recycle Bin", command=self.delete_selected,
            fg_color=C_DELETE, hover_color=C_DELETE_H, text_color="white", height=36, font=ctk.CTkFont(**BTN_FONT))
        self.btn_delete.pack(side="right", padx=10, pady=8)

        self.btn_toggle_all = ctk.CTkButton(
            action_bar, text="☑ Select / Deselect All", command=self.toggle_all_checkboxes,
            fg_color=C_TOGGLE, hover_color=C_TOGGLE_H, text_color="white", width=190, height=36,
            font=ctk.CTkFont(**BTN_FONT))
        self.btn_toggle_all.pack(side="right", padx=6, pady=8)

        self.grid_outer = ctk.CTkFrame(self.root, fg_color="#121212")
        self.grid_outer.pack(fill="both", expand=True, padx=12, pady=(5, 12))

        self.canvas = tk.Canvas(self.grid_outer, bg="#121212", highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.grid_outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.on_yscroll)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", self.on_canvas_resize)

    def on_yscroll(self, first, last):
        self.scrollbar.set(first, last)
        self.update_visible()

    def _over_grid(self, e):
        try:
            w = self.root.winfo_containing(e.x_root, e.y_root)
        except Exception:
            return False
        while w is not None and not isinstance(w, str):
            if w is self.grid_outer:
                return True
            w = getattr(w, "master", None)
        return False

    def on_mousewheel(self, e):
        if not self._over_grid(e):
            return
        num = getattr(e, "num", None)
        delta = 120 if num == 4 else -120 if num == 5 else e.delta
        pixels = -delta * 4 if sys.platform == "darwin" else -delta / 120 * 90

        sr = str(self.canvas.cget("scrollregion")).split()
        total_h = float(sr[3]) - float(sr[1]) if len(sr) == 4 else 0
        view_h = self.canvas.winfo_height()
        if total_h <= view_h or total_h <= 0:
            return "break"
        top = self.canvas.yview()[0]
        new_top = max(0.0, min(1.0 - view_h / total_h, top + pixels / total_h))
        self.canvas.yview_moveto(new_top)
        return "break"

    def _on_destroy(self, e):
        if e.widget is self.root:
            self.cancel.set()

    def select_folder(self):
        if self.busy:
            return
        folder = filedialog.askdirectory(parent=self.root)
        if folder:
            self.selected_folder = os.path.normpath(folder)
            self.lbl_folder.configure(text=self.selected_folder, text_color="#ffffff")

    def start_scan_thread(self):
        if self.busy:
            return
        if not self.selected_folder:
            messagebox.showinfo("Select a folder", "Please click 'Select Folder' first.", parent=self.root)
            return
        self.busy = True
        self.clear_results()
        self.progress.configure(value=0, maximum=1)
        self.lbl_status.configure(text="Scanning: collecting image files...", text_color="#3498db")
        self.task_running = True
        threading.Thread(target=self.scan_worker, daemon=True).start()
        self._ensure_polling()

    def clear_results(self):
        for idx in list(self.visible):
            self.drop_set(idx)
        self.canvas.delete("all")
        self.canvas.configure(scrollregion=(0, 0, 1, 1))
        self.duplicate_groups = []
        self.checkbox_vars = {}
        self.tops, self.heights = [], []
        self.empty_text = None
        self.thumb_cache.clear()
        self.update_found_label()
        self.update_selected_count()

    def get_image_quality_score(self, file_path):
        try:
            file_size = os.path.getsize(file_path)
            with Image.open(file_path) as img:
                w, h = img.size
            return (w * h, file_size)
        except Exception:
            return (0, 0)

    def hash_one(self, path):
        """Stage 1 fingerprint: 64-bit phash as an int (None if the file cannot be read)."""
        if self.cancel.is_set():
            return path, None
        try:
            with Image.open(path) as img:
                try:
                    img.draft("RGB", (256, 256))
                except Exception:
                    pass
                img = ImageOps.exif_transpose(img)
                return path, int(str(imagehash.phash(flatten_to_gray(img))), 16)
        except Exception:
            return path, None

    def verify_cluster(self, paths):
        """Stage 2: keeps only images that really match the best-quality image of the cluster."""
        scored = {}
        sigs = {}
        for p in paths:
            try:
                sigs[p] = pixel_signature(p)
                scored[p] = self.get_image_quality_score(p)
            except Exception:
                continue
        remaining = sorted(sigs, key=lambda p: scored[p], reverse=True)
        random.shuffle(remaining)
        remaining.sort(key=lambda p: scored[p], reverse=True)
        groups = []
        while remaining:
            anchor = remaining.pop(0)
            members, rest = [anchor], []
            for p in remaining:
                (members if signatures_match(sigs[anchor], sigs[p]) else rest).append(p)
            remaining = rest
            if len(members) > 1:
                groups.append({
                    "original": members[0],
                    "original_score": scored[members[0]],
                    "duplicates": [(p, scored[p]) for p in members[1:]],
                })
        return groups

    def scan_worker(self):
        try:
            image_paths = []
            for root_dir, _, files in os.walk(self.selected_folder):
                if self.cancel.is_set():
                    return
                for file in files:
                    if file.lower().endswith(self.supported_extensions):
                        image_paths.append(os.path.join(root_dir, file))

            total = len(image_paths)
            if total == 0:
                self.msg_q.put(("empty",))
                return
            self.msg_q.put(("progress", 0, total, "Scanning image content"))

            hashes = {}
            workers = max(2, min(6, os.cpu_count() or 2))
            done = 0
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for path, h in ex.map(self.hash_one, image_paths):
                    if self.cancel.is_set():
                        return
                    done += 1
                    if h is not None:
                        hashes[path] = h
                    if done % 5 == 0 or done == total:
                        self.msg_q.put(("progress", done, total, "Scanning image content"))

            clusters = find_candidate_clusters(hashes, self.cancel)
            groups = []
            n_clusters = len(clusters)
            if n_clusters:
                self.msg_q.put(("progress", 0, n_clusters, "Verifying matches"))
            for i, cluster in enumerate(clusters, start=1):
                if self.cancel.is_set():
                    return
                groups.extend(self.verify_cluster(cluster))
                if i % 3 == 0 or i == n_clusters:
                    self.msg_q.put(("progress", i, n_clusters, "Verifying matches"))
            self.msg_q.put(("groups", groups, total))
        except Exception as e:
            self.msg_q.put(("error", str(e)))

    def delete_worker(self, paths):
        ok, failed = [], []
        total = len(paths)
        for i, p in enumerate(paths, start=1):
            if self.cancel.is_set():
                break
            good, why = recycle_file(p)
            if good:
                ok.append(p)
            else:
                failed.append((p, why))
            self.msg_q.put(("progress", i, total, "Moving to Recycle Bin"))
        self.msg_q.put(("del_done", ok, failed))

    def _ensure_polling(self):
        if self._poll_job is None:
            self._poll_job = self.root.after(80, self.poll_queue)

    def poll_queue(self):
        self._poll_job = None
        if self.cancel.is_set() or not self.root.winfo_exists():
            return
        last_progress = None
        try:
            while True:
                msg = self.msg_q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    last_progress = msg
                elif kind == "groups":
                    self.task_running = False
                    last_progress = None
                    self.begin_render(msg[1], msg[2])
                elif kind == "empty":
                    self.task_running = False
                    last_progress = None
                    self.show_message("🖼️", "No images found in the selected folder.",
                                      "Supported types: JPG, PNG, BMP, WEBP, TIFF (sub-folders are included).")
                    self.lbl_status.configure(text="No images found in selected folder.", text_color="#f1c40f")
                    self.progress.configure(value=0)
                    self.busy = False
                elif kind == "error":
                    self.task_running = False
                    last_progress = None
                    self.lbl_status.configure(text=f"Scan failed: {msg[1]}", text_color="#e74c3c")
                    self.busy = False
                elif kind == "del_done":
                    self.task_running = False
                    self.finish_delete(msg[1], msg[2])
                    last_progress = None
        except queue.Empty:
            pass

        if last_progress:
            _, cur, total, label = last_progress
            self.progress.configure(maximum=max(1, total), value=cur)
            self.lbl_status.configure(text=f"{label}: {cur}/{total}", text_color="#3498db")

        if self.task_running:
            self._ensure_polling()

    def show_message(self, icon, title, sub=""):
        self.empty_text = (icon, title, sub)
        self.canvas.delete("empty_msg")
        w = max(self.canvas.winfo_width(), 400)
        h = max(self.canvas.winfo_height(), 300)
        self.canvas.configure(scrollregion=(0, 0, w, h))
        self.canvas.create_text(w / 2, h / 2 - 50, text=icon, font=("Segoe UI Emoji", 46), fill="#2ecc71", tags="empty_msg")
        self.canvas.create_text(w / 2, h / 2 + 20, text=title, font=("Arial", 20, "bold"), fill="#2ecc71",
                                width=w - 80, justify="center", tags="empty_msg")
        if sub:
            self.canvas.create_text(w / 2, h / 2 + 62, text=sub, font=("Arial", 12), fill="#aaaaaa",
                                    width=w - 80, justify="center", tags="empty_msg")

    def begin_render(self, groups, total_scanned):
        self.duplicate_groups = groups
        self.progress.configure(value=0)
        if not groups:
            self.show_message("✅", "No duplicate images found",
                              f"Scanned {total_scanned} image(s) in this folder - every one is unique.")
            self.lbl_status.configure(text=f"Scan Complete: No duplicate images found ({total_scanned} images scanned).",
                                      text_color="#2ecc71")
            self.busy = False
            return

        self.checkbox_vars = {}
        for g in groups:
            for path, _ in g["duplicates"]:
                self.checkbox_vars[path] = tk.BooleanVar(master=self.root, value=True)

        total_dupes = len(self.checkbox_vars)
        self.lbl_status.configure(
            text=f"Scan Complete: Found {len(groups)} set(s) containing {total_dupes} duplicate photo(s) "
                 f"({total_scanned} images scanned).", text_color="#f1c40f")
        self.measure_cards()
        self.relayout(keep_position=False)
        self.update_found_label()
        self.update_selected_count()
        self.busy = False

    def update_found_label(self):
        sets = len(self.duplicate_groups)
        dupes = sum(len(g["duplicates"]) for g in self.duplicate_groups)
        self.lbl_found.configure(text=f"{sets} set(s)  \u2022  {dupes} duplicate(s) found" if sets else "")

    def card_chars(self):
        return max(14, self.thumb // 7)

    def make_thumb_box(self, parent, bg, path, load=True):
        box = tk.Frame(parent, width=self.thumb, height=self.thumb, bg=bg)
        box.pack_propagate(False)
        box.pack(pady=3)
        if load:
            photo = self.get_thumb(path)
            if photo:
                lbl = tk.Label(box, image=photo, bg=bg)
                lbl.image = photo
                lbl.place(relx=0.5, rely=0.5, anchor="center")
        return box

    def make_meta(self, parent, bg, fg, score, name):
        n = self.card_chars()
        text = f"Res: {fmt_res(score[0])} | Size: {score[1] // 1024} KB\n{short_name(name, n)}"
        tk.Label(parent, text=text, font=("Arial", 8), bg=bg, fg=fg, width=n, height=2, justify="center").pack()

    def make_orig_card(self, parent, group, load=True):
        card = tk.Frame(parent, bd=2, relief="solid", bg="#0e2a14", padx=6, pady=6)
        tk.Label(card, text="KEEP (HIGHEST QUALITY)", bg="#27ae60", fg="white", font=("Arial", 8, "bold")).pack(fill="x")
        self.make_thumb_box(card, "#0e2a14", group["original"], load)
        self.make_meta(card, "#0e2a14", "#a3e2d7", group["original_score"], os.path.basename(group["original"]))
        return card

    def make_dupe_card(self, parent, path, score, var, load=True):
        card = tk.Frame(parent, bd=1, relief="groove", bg="#2c1414", padx=6, pady=6)
        tk.Checkbutton(card, text="Recycle Bin", variable=var, command=self.update_selected_count,
                       bg="#2c1414", fg="#ff6b5e", selectcolor="#1a1a1a", activebackground="#2c1414",
                       activeforeground="#ff6b5e", font=("Arial", 9, "bold")).pack(anchor="w")
        self.make_thumb_box(card, "#2c1414", path, load)
        self.make_meta(card, "#2c1414", "#f5b7b1", score, os.path.basename(path))
        return card

    def measure_cards(self):
        """Builds one hidden sample of each card type to learn its exact pixel size."""
        probe = tk.Frame(self.root)
        dummy = {"original": "x.jpg", "original_score": (2_000_000, 512 * 1024)}
        od = self.make_orig_card(probe, dummy, load=False)
        dd = self.make_dupe_card(probe, "x.jpg", (2_000_000, 512 * 1024), tk.BooleanVar(master=self.root), load=False)
        probe.update_idletasks()
        self.sizes = (od.winfo_reqwidth(), od.winfo_reqheight(), dd.winfo_reqwidth(), dd.winfo_reqheight())
        probe.destroy()

    def relayout(self, keep_position=True):
        """Computes every set's height and position for the current window width, then draws what is visible."""
        self._relayout_job = None
        if not self.duplicate_groups or not self.sizes:
            if self.empty_text:
                self.show_message(*self.empty_text)
            return
        frac = self.canvas.yview()[0] if keep_position else 0.0
        for idx in list(self.visible):
            self.drop_set(idx)

        ow, oh, dw, dh = self.sizes
        width = max(self.canvas.winfo_width(), 500)
        cell_w, cell_h = dw + 10, dh + 10
        dupes_x = 12 + ow + 28
        self.cols = max(1, (width - 16 - dupes_x - 12) // cell_w)

        self.tops, self.heights, y = [], [], 6
        for g in self.duplicate_groups:
            rows = -(-len(g["duplicates"]) // self.cols)
            h = TITLE_H + max(oh, rows * cell_h) + 16
            self.tops.append(y)
            self.heights.append(h)
            y += h + GROUP_GAP
        self.canvas.configure(scrollregion=(0, 0, width, y))
        self.canvas.yview_moveto(frac)
        self.update_visible(force=True)

    def on_canvas_resize(self, event):
        if self.duplicate_groups:
            if self._relayout_job:
                self.root.after_cancel(self._relayout_job)
            self._relayout_job = self.root.after(200, self.relayout)
        elif self.empty_text:
            self.show_message(*self.empty_text)

    def update_visible(self, force=False):
        if not self.tops:
            return
        top = self.canvas.canvasy(0)
        bottom = top + self.canvas.winfo_height()
        first = max(0, bisect_right(self.tops, top - BUFFER_PX) - 1)
        last = min(len(self.tops) - 1, bisect_right(self.tops, bottom + BUFFER_PX) - 1)
        want = set(range(first, last + 1))
        have = set(self.visible)
        if not force and want == have:
            return
        for idx in have - want:
            self.drop_set(idx)
        for idx in sorted(want - have):
            self.build_set(idx)

    def drop_set(self, idx):
        item = self.visible.pop(idx, None)
        if item:
            win_id, frame = item
            try:
                self.canvas.delete(win_id)
            except Exception:
                pass
            frame.destroy()

    def build_set(self, idx):
        g = self.duplicate_groups[idx]
        width = max(self.canvas.winfo_width(), 500)
        ow, oh, dw, dh = self.sizes
        frame = tk.Frame(self.canvas, bg="#1a1a1a", highlightbackground="#2d4a66", highlightthickness=1)
        tk.Label(frame, text=f"Duplicate Set #{idx + 1}   •   {len(g['duplicates'])} duplicate(s)",
                 bg="#1a1a1a", fg="#3498db", font=("Arial", 10, "bold")).place(x=12, y=5)
        self.make_orig_card(frame, g).place(x=12, y=TITLE_H)

        dupes_x = 12 + ow + 28
        for n, (path, score) in enumerate(g["duplicates"]):
            r, c = divmod(n, self.cols)
            self.make_dupe_card(frame, path, score, self.checkbox_vars[path]).place(
                x=dupes_x + c * (dw + 10), y=TITLE_H + r * (dh + 10))
        win_id = self.canvas.create_window(8, self.tops[idx], window=frame, anchor="nw",
                                           width=width - 16, height=self.heights[idx])
        self.visible[idx] = (win_id, frame)

    def get_thumb(self, path):
        key = (path, self.thumb)
        photo = self.thumb_cache.get(key)
        if photo is not None:
            self.thumb_cache.move_to_end(key)
            return photo
        try:
            with Image.open(path) as img:
                try:
                    img.draft("RGB", (self.thumb * 2, self.thumb * 2))
                except Exception:
                    pass
                if img.mode not in ("RGB", "RGBA", "L"):
                    img = img.convert("RGB")
                img.thumbnail((self.thumb, self.thumb))
                photo = ImageTk.PhotoImage(img)
        except Exception:
            return None
        self.thumb_cache[key] = photo
        while len(self.thumb_cache) > CACHE_MAX:
            self.thumb_cache.popitem(last=False)
        return photo

    def on_size_slider(self, value):
        if self._size_job:
            self.root.after_cancel(self._size_job)
        self._size_job = self.root.after(250, lambda: self.apply_size(int(float(value))))

    def apply_size(self, size):
        self._size_job = None
        if size == self.thumb:
            return
        self.thumb = size
        self.thumb_cache.clear()
        if self.duplicate_groups:
            self.measure_cards()
            self.relayout()

    def update_selected_count(self):
        total = len(self.checkbox_vars)
        n = sum(1 for v in self.checkbox_vars.values() if v.get())
        self.lbl_selected.configure(text=f"{n} selected" + (f"  (of {total})" if total else ""))

    def toggle_all_checkboxes(self):
        if self.busy or not self.checkbox_vars:
            return
        any_unchecked = any(not var.get() for var in self.checkbox_vars.values())
        for var in self.checkbox_vars.values():
            var.set(any_unchecked)
        self.update_selected_count()

    def delete_selected(self):
        if self.busy:
            return
        to_delete = [path for path, var in self.checkbox_vars.items() if var.get()]
        if not to_delete:
            messagebox.showinfo("Selection Empty", "No duplicate photos are checked for recycling.", parent=self.root)
            return

        confirm = messagebox.askyesno(
            "Confirm Move to Recycle Bin",
            f"Are you sure you want to move {len(to_delete)} selected duplicate image(s) to the Recycle Bin?\n\n"
            "They can be restored from the Recycle Bin if needed. The original highest quality copies will be preserved.",
            parent=self.root)
        if not confirm:
            return

        self.busy = True
        self.progress.configure(value=0, maximum=max(1, len(to_delete)))
        self.lbl_status.configure(text=f"Moving to Recycle Bin: 0/{len(to_delete)}", text_color="#3498db")
        self.task_running = True
        threading.Thread(target=self.delete_worker, args=(to_delete,), daemon=True).start()
        self._ensure_polling()

    def finish_delete(self, ok_paths, failed):
        self.busy = False
        ok_set = set(ok_paths)
        for g in self.duplicate_groups:
            g["duplicates"] = [(p, s) for p, s in g["duplicates"] if p not in ok_set]
        self.duplicate_groups = [g for g in self.duplicate_groups if g["duplicates"]]
        for p in ok_set:
            self.checkbox_vars.pop(p, None)
        remaining = sum(len(g["duplicates"]) for g in self.duplicate_groups)
        self.progress.configure(value=0)
        for p, why in failed:
            print(f"[recycle] could not move {p}: {why}")

        if self.duplicate_groups:
            self.relayout(keep_position=True)
        else:
            for idx in list(self.visible):
                self.drop_set(idx)
            self.canvas.delete("all")
            self.tops, self.heights = [], []
            self.show_message("\u2705", "All selected duplicates were moved to the Recycle Bin",
                              "Press 'Scan Duplicates' to check the folder again.")
        self.update_found_label()
        self.update_selected_count()

        status = f"Moved {len(ok_set)} duplicate(s) to the Recycle Bin"
        if failed:
            status += f", {len(failed)} could not be moved"
        status += f". {remaining} duplicate(s) in {len(self.duplicate_groups)} set(s) left."
        self.lbl_status.configure(text=status, text_color="#e74c3c" if failed else "#2ecc71")
        if failed:
            messagebox.showwarning(
                "Recycle finished with problems",
                f"Moved {len(ok_set)} duplicate photo(s) to the Recycle Bin.\n"
                f"Could NOT move {len(failed)} file(s):\n\n{summarize_failures(failed)}\n\n{FAIL_HINT}\n\n"
                f"Those files are still in the list. {remaining} duplicate(s) remain.", parent=self.root)
        else:
            messagebox.showinfo(
                "Recycle Complete",
                f"Moved {len(ok_set)} duplicate photo(s) to the Recycle Bin.\n\n"
                f"{remaining} duplicate(s) in {len(self.duplicate_groups)} set(s) remain in the list.", parent=self.root)