"""
aria2 Fast Downloader Module & Integration Mixin - Red Enclosure
==============================================================
Provides high-speed multi-connection downloading via aria2c.
Can run standalone or integrated into Red Enclosure via Aria2DownloaderMixin.
"""
import os
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from collections import deque
from tkinter import filedialog, messagebox
from urllib.parse import unquote, urlparse

import customtkinter as ctk

PROGRESS_RE = re.compile(
    r"\[#\w+\s+(?P<done>\S+)/(?P<total>\S+)\((?P<pct>\d+)%\)"
    r"(?:\s+CN:(?P<cn>\d+))?(?:\s+DL:(?P<dl>\S+))?(?:\s+ETA:(?P<eta>\S+))?"
)
SIZE_RE = re.compile(r"([\d.]+)\s*([KMGT]?)i?B", re.I)
RETRYABLE = ("Pending", "Stopped", "Failed")
STATUS_COLORS = {
    "Pending": "gray60",
    "Downloading": "#3b8ed0",
    "Done": "#2fa572",
    "Stopped": "#e0a030",
    "Failed": "#d9534f",
}
ROW_BG = ("gray88", "gray20")
ROW_SEL = ("#c9d6ea", "#2b4a73")
CARD_BG = "#1e1e1e"
BLUE = "#3b8ed0"
GREEN = "#2fa572"
GRAPH_POINTS = 60


def parse_size(text):
    m = SIZE_RE.match(text or "")
    if not m:
        return 0.0
    mult = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}[m.group(2).upper()]
    return float(m.group(1)) * mult


def fmt_bytes(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n:.0f} B"
        n /= 1024


def spaced(text):
    return re.sub(r"(\d)([A-Za-z])", r"\1 \2", text or "-")


def file_label(url):
    name = os.path.basename(unquote(urlparse(url).path))
    return name or url


class DownloaderApp(ctk.CTkToplevel):
    def __init__(self, master=None):
        super().__init__(master=master)
        self.title("aria2 Fast Downloader")
        self.geometry("720x900")
        self.minsize(600, 780)

        self.jobs = []
        self.current = None
        self.proc = None
        self.running = False
        self.stop_requested = False
        self.attempted = set()
        self.speed_hist = deque(maxlen=GRAPH_POINTS)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        bold = ctk.CTkFont(size=14, weight="bold")

        # --- Add links ---
        ctk.CTkLabel(self, text="Add link(s)", font=bold).pack(anchor="w", padx=16, pady=(14, 2))
        add_row = ctk.CTkFrame(self, fg_color="transparent")
        add_row.pack(fill="x", padx=16)
        self.url_entry = ctk.CTkEntry(add_row, placeholder_text="Paste one or many links (space or new line separated)")
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_entry.bind("<Return>", lambda e: self.add_links())
        ctk.CTkButton(add_row, text="Paste", width=70, command=self.paste).pack(side="left", padx=(8, 0))
        ctk.CTkButton(add_row, text="Add", width=70, command=self.add_links).pack(side="left", padx=(8, 0))

        # --- Save folder ---
        ctk.CTkLabel(self, text="Save to", font=bold).pack(anchor="w", padx=16, pady=(10, 2))
        dir_row = ctk.CTkFrame(self, fg_color="transparent")
        dir_row.pack(fill="x", padx=16)
        self.dir_entry = ctk.CTkEntry(dir_row)
        self.dir_entry.insert(0, os.path.join(os.path.expanduser("~"), "Downloads"))
        self.dir_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(dir_row, text="Browse", width=90, command=self.browse).pack(side="left", padx=(8, 0))

        # --- Queue ---
        ctk.CTkLabel(self, text="Queue", font=bold).pack(anchor="w", padx=16, pady=(10, 2))
        self.list_frame = ctk.CTkScrollableFrame(self, height=140)
        self.list_frame.pack(fill="both", expand=True, padx=16)

        q_btns = ctk.CTkFrame(self, fg_color="transparent")
        q_btns.pack(fill="x", padx=16, pady=(8, 0))
        ctk.CTkButton(q_btns, text="Remove selected", width=130, fg_color="gray35",
                      hover_color="gray25", command=self.remove_selected).pack(side="left")
        ctk.CTkButton(q_btns, text="Clear done", width=100, fg_color="gray35",
                      hover_color="gray25", command=self.clear_done).pack(side="left", padx=8)

        # --- Controls ---
        ctl = ctk.CTkFrame(self, fg_color="transparent")
        ctl.pack(fill="x", padx=16, pady=(10, 0))
        ctk.CTkLabel(ctl, text="Connections").pack(side="left")
        self.conn_menu = ctk.CTkOptionMenu(ctl, values=["1", "2", "4", "8", "16"], width=80)
        self.conn_menu.set("16")
        self.conn_menu.pack(side="left", padx=8)
        ctk.CTkButton(ctl, text="Light/Dark", width=90, fg_color="gray35", hover_color="gray25",
                      command=self.toggle_theme).pack(side="left", padx=8)
        self.start_btn = ctk.CTkButton(ctl, text="Start queue", width=120, command=self.start_queue)
        self.start_btn.pack(side="right")
        self.stop_btn = ctk.CTkButton(ctl, text="Stop", width=80, fg_color="#d9534f",
                                      hover_color="#b52b27", state="disabled", command=self.stop)
        self.stop_btn.pack(side="right", padx=8)

        # --- Live progress panel ---
        panel = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=14)
        panel.pack(fill="x", padx=16, pady=(14, 0))

        head = ctk.CTkFrame(panel, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 0))
        self.file_lbl = ctk.CTkLabel(head, text="No active download", anchor="w",
                                     font=ctk.CTkFont(size=14, weight="bold"), text_color="white")
        self.file_lbl.pack(side="left", fill="x", expand=True)
        self.item_lbl = ctk.CTkLabel(head, text="", text_color="gray60")
        self.item_lbl.pack(side="right")

        big = ctk.CTkFrame(panel, fg_color="transparent")
        big.pack(fill="x", padx=16, pady=(4, 0))
        self.pct_lbl = ctk.CTkLabel(big, text="0%", text_color=BLUE,
                                    font=ctk.CTkFont(size=64, weight="bold"))
        self.pct_lbl.pack(side="left")
        speed_box = ctk.CTkFrame(big, fg_color="transparent")
        speed_box.pack(side="right")
        self.speed_lbl = ctk.CTkLabel(speed_box, text="0 B/s", text_color="white",
                                      font=ctk.CTkFont(size=30, weight="bold"))
        self.speed_lbl.pack(anchor="e")
        ctk.CTkLabel(speed_box, text="LIVE SPEED", text_color="gray60",
                     font=ctk.CTkFont(size=11)).pack(anchor="e")

        self.bar = ctk.CTkProgressBar(panel, height=24, corner_radius=12,
                                      progress_color=BLUE, fg_color="#333333")
        self.bar.set(0)
        self.bar.pack(fill="x", padx=16, pady=(6, 10))

        stats = ctk.CTkFrame(panel, fg_color="transparent")
        stats.pack(fill="x", padx=16)
        self.size_val = self.make_stat(stats, "DOWNLOADED")
        self.eta_val = self.make_stat(stats, "TIME LEFT")
        self.conn_val = self.make_stat(stats, "CONNECTIONS")

        self.graph = tk.Canvas(panel, height=64, bg=CARD_BG, highlightthickness=0)
        self.graph.pack(fill="x", padx=16, pady=(10, 14))

        self.status_lbl = ctk.CTkLabel(self, text="Idle", anchor="w")
        self.status_lbl.pack(fill="x", padx=16, pady=(6, 0))
        self.log = ctk.CTkTextbox(self, height=70, state="disabled")
        self.log.pack(fill="x", padx=16, pady=(4, 14))

    def make_stat(self, parent, title):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.pack(side="left", expand=True, fill="x")
        ctk.CTkLabel(box, text=title, text_color="gray60", font=ctk.CTkFont(size=11)).pack(anchor="w")
        val = ctk.CTkLabel(box, text="-", text_color="white", font=ctk.CTkFont(size=16, weight="bold"))
        val.pack(anchor="w")
        return val

    def toggle_theme(self):
        ctk.set_appearance_mode("light" if ctk.get_appearance_mode() == "Dark" else "dark")

    def paste(self):
        try:
            text = self.clipboard_get().strip()
        except Exception:
            return
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, text)

    def browse(self):
        folder = filedialog.askdirectory(initialdir=self.dir_entry.get())
        if folder:
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, folder)

    def write_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_running(self, running):
        self.running = running
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    def set_status(self, job, status, text=None):
        job["status"] = status
        job["status_lbl"].configure(text=text or status, text_color=STATUS_COLORS[status])

    def reset_panel(self, job):
        self.speed_hist.clear()
        self.file_lbl.configure(text=file_label(job["url"]))
        self.item_lbl.configure(text=f"Item {self.jobs.index(job) + 1} of {len(self.jobs)}")
        self.pct_lbl.configure(text="0%", text_color=BLUE)
        self.speed_lbl.configure(text="0 B/s")
        self.bar.configure(progress_color=BLUE)
        self.bar.set(0)
        self.size_val.configure(text="-")
        self.eta_val.configure(text="-")
        self.conn_val.configure(text="-")
        self.graph.delete("all")

    def add_links(self):
        links = self.url_entry.get().split()
        if not links:
            try:
                links = self.clipboard_get().split()
            except Exception:
                links = []
        for link in links:
            self.add_row(link)
        if links:
            self.url_entry.delete(0, "end")

    def add_row(self, url):
        frame = ctk.CTkFrame(self.list_frame, fg_color=ROW_BG, corner_radius=8)
        frame.pack(fill="x", padx=2, pady=3)
        short = url if len(url) <= 54 else url[:51] + "..."
        link_lbl = ctk.CTkLabel(frame, text=short, anchor="w")
        link_lbl.pack(side="left", fill="x", expand=True, padx=10, pady=6)
        status_lbl = ctk.CTkLabel(frame, text="Pending", width=90, text_color=STATUS_COLORS["Pending"])
        status_lbl.pack(side="right", padx=10)
        job = {"url": url, "status": "Pending", "selected": False,
               "frame": frame, "status_lbl": status_lbl}
        for w in (frame, link_lbl, status_lbl):
            w.bind("<Button-1>", lambda e, j=job: self.toggle_select(j))
        self.jobs.append(job)

    def toggle_select(self, job):
        job["selected"] = not job["selected"]
        job["frame"].configure(fg_color=ROW_SEL if job["selected"] else ROW_BG)

    def remove_job(self, job):
        job["frame"].destroy()
        self.jobs.remove(job)

    def remove_selected(self):
        for job in [j for j in self.jobs if j["selected"] and j is not self.current]:
            self.remove_job(job)

    def clear_done(self):
        for job in [j for j in self.jobs if j["status"] == "Done"]:
            self.remove_job(job)

    def start_queue(self):
        if self.running:
            return
        if not shutil.which("aria2c"):
            messagebox.showerror(
                "aria2c not found",
                "aria2c is not on PATH.\nInstall it with:\nwinget install aria2.aria2\nthen restart this app.",
                parent=self,
            )
            return
        os.makedirs(self.dir_entry.get(), exist_ok=True)
        self.stop_requested = False
        self.attempted = set()
        self.set_running(True)
        self.status_lbl.configure(text="Running...")
        self.next_job()

    def next_job(self):
        if self.stop_requested:
            return self.finish_queue("Stopped")
        for job in self.jobs:
            if id(job) not in self.attempted and job["status"] in RETRYABLE:
                self.attempted.add(id(job))
                self.current = job
                self.set_status(job, "Downloading", "0%")
                self.reset_panel(job)
                threading.Thread(target=self.run_download, args=(job["url"],), daemon=True).start()
                return
        self.finish_queue("Queue finished")

    def job_done(self, job, code):
        if code == 0:
            self.set_status(job, "Done")
            self.pct_lbl.configure(text="100%", text_color=GREEN)
            self.bar.configure(progress_color=GREEN)
            self.bar.set(1)
            self.speed_lbl.configure(text="Complete")
            self.eta_val.configure(text="0s")
        elif self.stop_requested:
            self.set_status(job, "Stopped")
        else:
            self.set_status(job, "Failed")
        self.current = None
        self.next_job()

    def finish_queue(self, msg):
        self.current = None
        self.status_lbl.configure(text=msg)
        self.set_running(False)

    def stop(self):
        self.stop_requested = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.write_log("Stopping... click Start queue to resume.")

    def run_download(self, url):
        job = self.current
        n = int(self.conn_menu.get())
        cmd = [
            "aria2c", f"-x{n}", f"-s{n}", "-k1M", "-c",
            "--file-allocation=none", "--summary-interval=1",
            "--console-log-level=warn", "-d", self.dir_entry.get(), url,
        ]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, creationflags=flags,
            )
            for line in self.proc.stdout:
                line = line.strip()
                m = PROGRESS_RE.search(line)
                if m:
                    self.after(0, self.update_progress, job, m)
                elif line and not line.startswith(("***", "===", "FILE:", "Status Legend", "(OK)")):
                    self.after(0, self.write_log, line)
            code = self.proc.wait()
        except Exception as e:
            self.after(0, self.write_log, f"Error: {e}")
            code = -1
        self.after(0, self.job_done, job, code)

    def update_progress(self, job, m):
        pct = int(m.group("pct"))
        dl = m.group("dl") or "0B"
        self.pct_lbl.configure(text=f"{pct}%")
        self.bar.set(pct / 100)
        self.speed_lbl.configure(text=f"{spaced(dl)}/s")
        self.size_val.configure(text=f"{spaced(m.group('done'))} / {spaced(m.group('total'))}")
        self.eta_val.configure(text=m.group("eta") or "-")
        self.conn_val.configure(text=m.group("cn") or "-")
        self.set_status(job, "Downloading", f"{pct}%")
        self.speed_hist.append(parse_size(dl))
        self.draw_graph()

    def draw_graph(self):
        c = self.graph
        c.delete("all")
        data = list(self.speed_hist)
        w, h = c.winfo_width(), c.winfo_height()
        if len(data) < 2 or w < 10:
            return
        peak = max(data) or 1
        step = w / (GRAPH_POINTS - 1)
        x0 = w - (len(data) - 1) * step
        pts = []
        for i, v in enumerate(data):
            pts += [x0 + i * step, h - 4 - (v / peak) * (h - 16)]
        c.create_polygon([x0, h] + pts + [w, h], fill="#1b3a57", outline="")
        c.create_line(pts, fill=BLUE, width=2)
        c.create_text(6, 8, anchor="w", fill="gray60", font=("Segoe UI", 8),
                      text=f"peak {fmt_bytes(peak)}/s")

    def _on_close(self):
        self.stop()
        self.destroy()


class Aria2DownloaderMixin:
    """Mixin class for integrating aria2 Fast Downloader into CircuitBuilderApp."""

    def init_aria2_downloader(self):
        self._aria2_win = None

    def open_aria2_downloader(self):
        """Launches the aria2 Fast Downloader window."""
        if getattr(self, "_aria2_win", None) and self._aria2_win.winfo_exists():
            self._aria2_win.lift()
            self._aria2_win.focus_force()
            return
        win = DownloaderApp(master=self)
        win.attributes("-topmost", True)
        self._aria2_win = win


if __name__ == "__main__":
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root.withdraw()
    app = DownloaderApp(master=root)
    app.protocol("WM_DELETE_WINDOW", lambda: (app._on_close(), root.destroy()))
    root.mainloop()
