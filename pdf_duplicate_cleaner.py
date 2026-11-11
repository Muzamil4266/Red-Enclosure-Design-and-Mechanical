"""
PDF Duplicate Detector and Cleaner Mixin for Shoby Red Project.
Finds byte-identical PDF copies (any file name, any sub-folder) without reading every PDF.

Funnel (cheap -> expensive, each step only sees files that survived the previous one):
  1. SIZE CHECK      identical files have identical size, so a PDF with a unique size is never opened.
  2. SAMPLED HASH    (Fast mode, default) hashes 8 chunks of 64 KB spread across the file (+ the size).
                     Reads at most 512 KB per file whatever its size.
  3. FULL HASH       (Exact mode checkbox) only for files that already matched in step 2. 100% proof.

Which copy is kept: the LATEST MODIFIED one (file modified time). Ties: larger file, then shorter path.
File names play no role.
All other copies go ONLY to the OS Recycle Bin (no permanent deletion).

Built for a basic 8 GB laptop: max 2 hashing threads (more threads slow a hard disk), 1 MB streaming reads,
workers never touch Tk (they post to a queue).
"""
import os
import stat
import sys
import queue
import hashlib
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import customtkinter as ctk
from send2trash import send2trash

BTN_FONT = dict(size=13, weight="bold")
C_SELECT, C_SELECT_H = "#1565c0", "#0d47a1"
C_SCAN, C_SCAN_H = "#00a651", "#007a3d"
C_TOGGLE, C_TOGGLE_H = "#8e24aa", "#6a1b82"
C_DELETE, C_DELETE_H = "#d32f2f", "#9a0007"

CHUNK = 64 * 1024               # size of each sampled piece
SAMPLES = 8                     # number of sampled places (start ... end, evenly spaced)
FULL_READ_LIMIT = CHUNK * SAMPLES   # files this small are fully read by the sampled hash already
STREAM = 1024 * 1024            # read size for the full hash
HASH_THREADS = 2                # keep low: better for hard disks and weak CPUs
NAME_W = 46

def fmt_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def short_name(name, limit):
    if len(name) <= limit:
        return name
    keep = max(4, limit // 2 - 1)
    return name[:keep] + "…" + name[-keep:]


def sampled_hash(path, size):
    """Hash of SAMPLES chunks spread evenly over the file (first and last included) plus the size."""
    h = hashlib.blake2b(digest_size=16)
    h.update(size.to_bytes(8, "little"))
    with open(path, "rb", buffering=0) as f:
        if size <= FULL_READ_LIMIT:
            h.update(f.read())
        else:
            step = (size - CHUNK) / (SAMPLES - 1)
            for i in range(SAMPLES):
                f.seek(min(int(i * step), size - CHUNK))
                h.update(f.read(CHUNK))
    return h.hexdigest()


def full_hash(path, cancel):
    """Streams the whole file in 1 MB pieces (constant, tiny RAM use). None if cancelled."""
    h = hashlib.blake2b(digest_size=20)
    with open(path, "rb", buffering=0) as f:
        while True:
            chunk = f.read(STREAM)
            if not chunk:
                break
            if cancel.is_set():
                return None
            h.update(chunk)
    return h.hexdigest()


def keeper_key(path, size, mtime):
    """Sort key: the smallest key is the copy to KEEP -> newest modified first, then larger, then shorter path."""
    return (-mtime, -size, len(path), path)

IS_WIN = sys.platform.startswith("win")


def _shell_recycle(path):
    """Windows only: asks the Windows shell to move one file to the Recycle Bin (works from any thread)."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _pack_ = 1 if ctypes.sizeof(ctypes.c_void_p) == 4 else 8
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    op = SHFILEOPSTRUCTW()
    op.wFunc = 3                                    # FO_DELETE
    op.pFrom = path + "\0\0"                        # the file list ends with a double NUL
    op.fFlags = 0x0040 | 0x0010 | 0x0400 | 0x0004   # ALLOWUNDO (= Recycle Bin) | NOCONFIRMATION | NOERRORUI | SILENT
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0:
        return False, f"Windows shell error 0x{rc:X}"
    if op.fAnyOperationsAborted:
        return False, "Windows cancelled the operation"
    return True, ""


def recycle_file(path):
    """Moves ONE file to the Recycle Bin and checks that it is really gone. Returns (True, "") or (False, reason).
    Never deletes permanently. Order: send2trash, then (Windows) the shell API, then both again after clearing a
    read-only flag. The path is normalised first (C:/a/b becomes C:\\a\\b), which the Windows APIs require."""
    p = os.path.abspath(path)
    if not os.path.exists(p):
        return False, "the file is already gone (moved or deleted by another program)"
    reasons = []

    def attempt():
        try:
            send2trash(p)
        except Exception as e:
            reasons.append(f"send2trash: {type(e).__name__}: {e}")
        if not os.path.exists(p):
            return True
        if IS_WIN:
            try:
                ok, msg = _shell_recycle(p)
            except Exception as e:
                ok, msg = False, f"{type(e).__name__}: {e}"
            if not ok:
                reasons.append(f"Windows shell: {msg}")
            if not os.path.exists(p):
                return True
        return False

    if attempt():
        return True, ""
    try:                                            # a read-only flag can block the Recycle Bin
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass
    if attempt():
        return True, ""
    return False, "; ".join(dict.fromkeys(reasons)) or "the file is still there after the Recycle Bin call"


def summarize_failures(failed, limit=3):
    """[(path, reason)] -> short readable text, grouped by reason."""
    by = defaultdict(list)
    for p, why in failed:
        by[why].append(p)
    lines = []
    for why, ps in sorted(by.items(), key=lambda kv: -len(kv[1]))[:limit]:
        lines.append(f"- {len(ps)} file(s): {why}\n    e.g. {ps[0]}")
    if len(by) > limit:
        lines.append(f"- ... and {len(by) - limit} other reason(s) (listed in the console)")
    return "\n".join(lines)


FAIL_HINT = ("Common causes: the file is open in another program (photo viewer, OneDrive / Dropbox sync), the drive "
             "has no Recycle Bin (USB stick or network drive), or the Recycle Bin is switched off for that drive. "
             "The app never deletes permanently, so those files were left where they are.")


class PdfDuplicateCleanerMixin:
    """Mixin class for integrating the PDF Duplicate Cleaner into CircuitBuilderApp."""

    def init_pdf_duplicate_cleaner(self):
        self._pdf_cleaner_win = None

    def open_pdf_duplicate_cleaner(self):
        """Launches the PDF Duplicate Detector window."""
        if getattr(self, "_pdf_cleaner_win", None) and self._pdf_cleaner_win.winfo_exists():
            self._pdf_cleaner_win.lift()
            self._pdf_cleaner_win.focus_force()
            return
        win = ctk.CTkToplevel(self)
        win.title("PDF Duplicate Detector and Cleaner")
        win.geometry("1180x760")
        win.attributes("-topmost", True)
        self._pdf_cleaner_win = win
        PdfDuplicateCleanerUI(win)


class PdfDuplicateCleanerUI:
    def __init__(self, root):
        self.root = root
        self.selected_folder = ""
        self.groups = []
        self.rows = []              # one dict per listbox line: {"kind": head|keep|dupe|msg, "path": ...}
        self.checked = {}           # duplicate path -> bool (state lives here, not in widgets)
        self.sizes = {}             # path -> size, for the "frees X MB" counter
        self.busy = False
        self.cancel = threading.Event()
        self.msg_q = queue.Queue()
        self.task_running = False
        self._poll_job = None
        self.last_exact = False
        self.setup_ui()
        self.root.bind("<Destroy>", self._on_destroy, add="+")

    # ------------------------------------------------------------------ UI
    def setup_ui(self):
        top = ctk.CTkFrame(self.root, fg_color="#181818", corner_radius=8)
        top.pack(fill="x", padx=12, pady=10)

        self.btn_select = ctk.CTkButton(
            top, text="📁 Select Folder", command=self.select_folder, width=150, height=36,
            fg_color=C_SELECT, hover_color=C_SELECT_H, text_color="white", font=ctk.CTkFont(**BTN_FONT))
        self.btn_select.pack(side="left", padx=10, pady=10)

        self.lbl_folder = ctk.CTkLabel(top, text="No folder selected", text_color="#aaaaaa",
                                       font=ctk.CTkFont(size=12), anchor="w")
        self.lbl_folder.pack(side="left", fill="x", expand=True, padx=5)

        self.btn_start = ctk.CTkButton(
            top, text="⚡ Scan PDFs", command=self.start_scan_thread, width=150, height=36,
            fg_color=C_SCAN, hover_color=C_SCAN_H, text_color="white", font=ctk.CTkFont(**BTN_FONT))
        self.btn_start.pack(side="right", padx=10, pady=10)

        self.exact_var = tk.BooleanVar(master=self.root, value=False)
        self.chk_exact = ctk.CTkCheckBox(top, text="Exact mode (full hash, slower)", variable=self.exact_var,
                                         font=ctk.CTkFont(size=12))
        self.chk_exact.pack(side="right", padx=10)

        status = ctk.CTkFrame(self.root, fg_color="transparent")
        status.pack(fill="x", padx=12, pady=(0, 5))
        self.progress = ttk.Progressbar(status, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x", pady=2)
        self.lbl_status = ctk.CTkLabel(status, text="Status: Ready to scan  (Fast mode: sampled hash)",
                                       text_color="#3498db", font=ctk.CTkFont(size=12, weight="bold"))
        self.lbl_status.pack(anchor="w", pady=2)

        bar = ctk.CTkFrame(self.root, fg_color="#1f1f1f", height=54)
        bar.pack(fill="x", padx=12, pady=5)
        ctk.CTkLabel(bar, text="Results", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left", padx=(12, 8))
        self.lbl_selected = ctk.CTkLabel(bar, text="0 selected", text_color="#f1c40f", width=540, anchor="w",
                                         font=ctk.CTkFont(size=15, weight="bold"))
        self.lbl_selected.pack(side="left", padx=8)

        self.btn_delete = ctk.CTkButton(
            bar, text="🗑️ Move Selected to Recycle Bin", command=self.delete_selected,
            fg_color=C_DELETE, hover_color=C_DELETE_H, text_color="white", height=36, font=ctk.CTkFont(**BTN_FONT))
        self.btn_delete.pack(side="right", padx=10, pady=8)
        self.btn_toggle_all = ctk.CTkButton(
            bar, text="☑ Select / Deselect All", command=self.toggle_all, width=190, height=36,
            fg_color=C_TOGGLE, hover_color=C_TOGGLE_H, text_color="white", font=ctk.CTkFont(**BTN_FONT))
        self.btn_toggle_all.pack(side="right", padx=6, pady=8)

        ctk.CTkLabel(self.root, text="Click a ☑ line to include / exclude it  •  double-click any line to open that PDF",
                     text_color="#888888", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=14)

        outer = ctk.CTkFrame(self.root, fg_color="#121212")
        outer.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self.listbox = tk.Listbox(
            outer, bg="#121212", fg="#dddddd", font=("Consolas", 10), activestyle="none", exportselection=False,
            selectmode="browse", selectbackground="#263238", selectforeground="#ffffff",
            borderwidth=0, highlightthickness=0)
        vbar = ttk.Scrollbar(outer, orient="vertical", command=self.listbox.yview)
        hbar = ttk.Scrollbar(outer, orient="horizontal", command=self.listbox.xview)
        self.listbox.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        vbar.pack(side="right", fill="y")
        hbar.pack(side="bottom", fill="x")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<Button-1>", self.on_click)
        self.listbox.bind("<Double-Button-1>", self.on_double_click)
        self.listbox.bind("<space>", self.on_space)

    def _on_destroy(self, e):
        if e.widget is self.root:
            self.cancel.set()

    # ------------------------------------------------------------------ list helpers
    def add_row(self, row, text, fg, bg="#121212"):
        self.listbox.insert("end", text)
        idx = len(self.rows)
        self.listbox.itemconfigure(idx, fg=fg, bg=bg)
        self.rows.append(row)

    def dupe_text(self, path):
        size, mtime = self.meta[path]
        box = "☑" if self.checked.get(path) else "☐"
        return (f"   {box} Recycle   {short_name(os.path.basename(path), NAME_W).ljust(NAME_W)}  "
                f"{fmt_size(size).rjust(9)}  {datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M}   "
                f"{os.path.dirname(path)}")

    def keep_text(self, path):
        size, mtime = self.meta[path]
        return (f"   ✔ KEEP latest  {short_name(os.path.basename(path), NAME_W).ljust(NAME_W)}  "
                f"{fmt_size(size).rjust(9)}  {datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M}   "
                f"{os.path.dirname(path)}")

    def show_message(self, icon, title, sub=""):
        self.listbox.delete(0, "end")
        self.rows = []
        for text, fg in (("", "#121212"), (f"   {icon}  {title}", "#2ecc71"), (f"      {sub}" if sub else "", "#aaaaaa")):
            self.add_row({"kind": "msg"}, text, fg)

    # ------------------------------------------------------------------ clicks
    def on_click(self, e):
        idx = self.listbox.nearest(e.y)
        box = self.listbox.bbox(idx) if idx >= 0 else None
        if not box or e.y > box[1] + box[3] or idx >= len(self.rows) or self.busy:
            return
        self.toggle_index(idx)

    def on_space(self, e):
        sel = self.listbox.curselection()
        if sel and not self.busy:
            self.toggle_index(sel[0])
        return "break"

    def toggle_index(self, idx):
        row = self.rows[idx]
        if row["kind"] != "dupe":
            return
        p = row["path"]
        self.checked[p] = not self.checked[p]
        frac = self.listbox.yview()[0]
        self.listbox.delete(idx)
        self.listbox.insert(idx, self.dupe_text(p))
        self.listbox.itemconfigure(idx, fg="#ff8a80" if self.checked[p] else "#777777", bg="#241414")
        self.listbox.yview_moveto(frac)
        self.update_selected_count()

    def on_double_click(self, e):
        idx = self.listbox.nearest(e.y)
        if 0 <= idx < len(self.rows) and self.rows[idx].get("path"):
            try:
                os.startfile(self.rows[idx]["path"])
            except Exception:
                pass

    def update_selected_count(self):
        total = len(self.checked)
        n = sum(1 for v in self.checked.values() if v)
        free = sum(self.meta[p][0] for p, v in self.checked.items() if v) if total else 0
        self.lbl_selected.configure(
            text=f"{n} selected" + (f"  (of {total} duplicates in {len(self.groups)} set(s))  \u2022  frees {fmt_size(free)}" if total else ""))

    def toggle_all(self):
        if self.busy or not self.checked:
            return
        value = any(not v for v in self.checked.values())
        for p in self.checked:
            self.checked[p] = value
        self.render_groups()

    # ------------------------------------------------------------------ scan control
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
        self.last_exact = bool(self.exact_var.get())      # read the checkbox on the UI thread
        self.groups, self.checked, self.meta = [], {}, {}
        self.rows = []
        self.listbox.delete(0, "end")
        self.update_selected_count()
        self.progress.configure(value=0, maximum=1)
        self.lbl_status.configure(text="Scanning: listing PDF files...", text_color="#3498db")
        self.task_running = True
        threading.Thread(target=self.scan_worker, args=(self.last_exact,), daemon=True).start()
        self._ensure_polling()

    # ------------------------------------------------------------------ background workers (NO Tk calls here)
    def scan_worker(self, exact):
        try:
            self.msg_q.put(("progress", 0, 1, "Step 1/3: listing PDFs and checking sizes"))
            by_size, meta, total, bad = defaultdict(list), {}, 0, 0
            stack = [self.selected_folder]
            while stack:
                if self.cancel.is_set():
                    return
                try:
                    with os.scandir(stack.pop()) as it:
                        for e in it:
                            try:
                                if e.is_dir(follow_symlinks=False):
                                    stack.append(e.path)
                                elif e.name.lower().endswith(".pdf") and e.is_file(follow_symlinks=False):
                                    st = e.stat()
                                    total += 1
                                    if st.st_size > 0:
                                        by_size[st.st_size].append(e.path)
                                        meta[e.path] = (st.st_size, st.st_mtime)
                            except OSError:
                                bad += 1
                except OSError:
                    bad += 1

            if total == 0:
                self.msg_q.put(("empty",))
                return

            # STEP 1 gate: only files that share their size with another file are ever opened
            candidates = [(p, size) for size, paths in by_size.items() if len(paths) > 1 for p in paths]
            n_cand = len(candidates)
            buckets = defaultdict(list)

            # STEP 2: sampled hash (8 x 64 KB per file)
            if n_cand:
                self.msg_q.put(("progress", 0, n_cand, "Step 2/3: sampled hash"))

                def do_sample(item):
                    path, size = item
                    if self.cancel.is_set():
                        return path, size, None
                    try:
                        return path, size, sampled_hash(path, size)
                    except OSError:
                        return path, size, None

                done = 0
                with ThreadPoolExecutor(max_workers=HASH_THREADS) as ex:
                    for path, size, h in ex.map(do_sample, candidates):
                        if self.cancel.is_set():
                            return
                        done += 1
                        if h is None:
                            bad += 1
                        else:
                            buckets[(size, h)].append(path)
                        if done % 10 == 0 or done == n_cand:
                            self.msg_q.put(("progress", done, n_cand, "Step 2/3: sampled hash"))

            # STEP 3 (Exact mode): full hash, only inside buckets that already matched in step 2
            groups_paths, todo = [], []
            for (size, _), paths in buckets.items():
                if len(paths) < 2:
                    continue
                if exact and size > FULL_READ_LIMIT:      # small files were already fully read in step 2
                    todo.append(paths)
                else:
                    groups_paths.append(paths)

            n_full = sum(len(paths) for paths in todo)
            if n_full:
                self.msg_q.put(("progress", 0, n_full, "Step 3/3: full hash (exact)"))

                def do_full(path):
                    try:
                        return path, full_hash(path, self.cancel)
                    except OSError:
                        return path, None

                done = 0
                with ThreadPoolExecutor(max_workers=HASH_THREADS) as ex:
                    for paths in todo:
                        split = defaultdict(list)
                        for path, h in ex.map(do_full, paths):
                            if self.cancel.is_set():
                                return
                            done += 1
                            if h is None:
                                bad += 1
                            else:
                                split[h].append(path)
                            self.msg_q.put(("progress", done, n_full, "Step 3/3: full hash (exact)"))
                        groups_paths.extend(v for v in split.values() if len(v) > 1)

            groups = []
            for paths in groups_paths:
                ordered = sorted(paths, key=lambda p: keeper_key(p, meta[p][0], meta[p][1]))
                groups.append({"original": ordered[0], "duplicates": ordered[1:]})
            groups.sort(key=lambda g: -meta[g["original"]][0] * len(g["duplicates"]))   # biggest savings first
            self.msg_q.put(("groups", groups, meta, total, n_cand, bad))
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

    # ------------------------------------------------------------------ UI-side queue polling
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
                    last_progress = None          # a stale progress message would overwrite the scan summary
                    self.begin_render(*msg[1:])
                elif kind == "empty":
                    self.task_running = False
                    last_progress = None
                    self.show_message("📄", "No PDF files found in the selected folder.", "Sub-folders are included.")
                    self.lbl_status.configure(text="No PDF files found in selected folder.", text_color="#f1c40f")
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

    # ------------------------------------------------------------------ results
    def begin_render(self, groups, meta, total, n_cand, bad):
        self.groups, self.meta = groups, meta
        self.progress.configure(value=0)
        mode = "Exact" if self.last_exact else "Fast"
        note = f"  ({bad} file(s) could not be read)" if bad else ""
        if not groups:
            self.show_message("✅", "No duplicate PDFs found",
                              f"{total} PDF(s) scanned, only {n_cand} needed their content checked ({mode} mode).")
            self.lbl_status.configure(text=f"Scan Complete: no duplicate PDFs found ({total} scanned).{note}",
                                      text_color="#2ecc71")
            self.busy = False
            return
        self.checked = {p: True for g in groups for p in g["duplicates"]}
        self.lbl_status.configure(
            text=f"Scan Complete ({mode} mode): {len(groups)} set(s), {len(self.checked)} duplicate PDF(s). "
                 f"{total} scanned, {n_cand} content-checked.{note}", text_color="#f1c40f")
        self.render_groups()
        self.busy = False

    def render_groups(self):
        self.listbox.delete(0, "end")
        self.rows = []
        for n, g in enumerate(self.groups, start=1):
            keep = g["original"]
            self.add_row({"kind": "head"},
                         f" Set #{n}   •   {len(g['duplicates']) + 1} identical copies   •   {fmt_size(self.meta[keep][0])} each",
                         "#3498db", "#1a2a3a")
            self.add_row({"kind": "keep", "path": keep}, self.keep_text(keep), "#7dcea0", "#0e2a14")
            for p in g["duplicates"]:
                self.add_row({"kind": "dupe", "path": p}, self.dupe_text(p),
                             "#ff8a80" if self.checked[p] else "#777777", "#241414")
        self.update_selected_count()

    # ------------------------------------------------------------------ delete
    def delete_selected(self):
        if self.busy:
            return
        to_delete = [p for p, v in self.checked.items() if v]
        if not to_delete:
            messagebox.showinfo("Selection Empty", "No duplicate PDFs are checked for recycling.", parent=self.root)
            return
        free = sum(self.meta[p][0] for p in to_delete)
        if not messagebox.askyesno(
                "Confirm Move to Recycle Bin",
                f"Move {len(to_delete)} duplicate PDF(s) ({fmt_size(free)}) to the Recycle Bin?\n\n"
                "They can be restored from the Recycle Bin if needed. The latest-modified copy of every set is kept.",
                parent=self.root):
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
        for g in self.groups:
            g["duplicates"] = [p for p in g["duplicates"] if p not in ok_set]
        self.groups = [g for g in self.groups if g["duplicates"]]
        for p in ok_set:
            self.checked.pop(p, None)
        remaining = len(self.checked)
        self.progress.configure(value=0)
        for p, why in failed:
            print(f"[recycle] could not move {p}: {why}")

        if self.groups:
            self.render_groups()
        else:
            self.show_message("\u2705", "All selected duplicates were moved to the Recycle Bin",
                              "Press 'Scan PDFs' to check the folder again.")
            self.update_selected_count()

        status = f"Moved {len(ok_set)} duplicate PDF(s) to the Recycle Bin"
        if failed:
            status += f", {len(failed)} could not be moved"
        status += f". {remaining} duplicate(s) in {len(self.groups)} set(s) left."
        self.lbl_status.configure(text=status, text_color="#e74c3c" if failed else "#2ecc71")
        if failed:
            messagebox.showwarning(
                "Recycle finished with problems",
                f"Moved {len(ok_set)} duplicate PDF(s) to the Recycle Bin.\n"
                f"Could NOT move {len(failed)} file(s):\n\n{summarize_failures(failed)}\n\n{FAIL_HINT}\n\n"
                f"Those files are still in the list. {remaining} duplicate(s) remain.", parent=self.root)
        else:
            messagebox.showinfo(
                "Recycle Complete",
                f"Moved {len(ok_set)} duplicate PDF(s) to the Recycle Bin.\n\n"
                f"{remaining} duplicate(s) in {len(self.groups)} set(s) remain in the list.", parent=self.root)
