"""
🎥 Screen Recorder Mixin for Red Enclosure
Full-Screen Workspace Recording with:
  * a LIVE recording indicator in the top toolbar (blinking dot, timer, Pause/Resume and Stop)
  * real-time video timing (the video always lasts as long as the recording really did, pauses are cut out)
  * a big Saved Screen Recordings screen (Total recordings, Select All, multi-select delete, 12-hour dates)
  * Detailed path auto-save, single-window overlay panels (Settings, Recordings List, built-in video player)
  * 10 GB auto-cleanup storage limit
"""
import os
import re
import time
import glob
import threading
import subprocess
from datetime import datetime
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
from PIL import Image, ImageTk, ImageGrab
import cv2
import numpy as np

RECORDINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")
MAX_STORAGE_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB limit

PANEL_BG = "#181818"
PANEL_HEAD = "#262626"
PANEL_BORDER = "#e67e22"
ROW_BG = "#242424"
REC_RED = "#e74c3c"
REC_RED_DIM = "#5a1d18"
REC_AMBER = "#f1c40f"
UI_TICK_MS = 250          # indicator refresh (blink, timer, capture area)
MAX_ERRORS_IN_A_ROW = 20  # capture errors in a row before the recording is stopped


def _fmt_clock(seconds):
  seconds = int(max(0, seconds))
  mins, secs = divmod(seconds, 60)
  hrs, mins = divmod(mins, 60)
  return f"{hrs:02d}:{mins:02d}:{secs:02d}" if hrs > 0 else f"{mins:02d}:{secs:02d}"


def _fmt_modified(timestamp):
  """12-hour format with AM / PM, e.g. '10 Nov 2026, 3:45 PM'."""
  d = datetime.fromtimestamp(timestamp)
  hour12 = d.hour % 12 or 12
  return f"{d.day} {d.strftime('%b %Y')}, {hour12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def _fmt_size(nbytes):
  if nbytes >= 1024 ** 3:
    return f"{nbytes / 1024 ** 3:.2f} GB"
  return f"{nbytes / 1024 ** 2:.2f} MB"


def _safe_mtime(path):
  try:
    return os.path.getmtime(path)
  except OSError:
    return 0.0


class ScreenRecorderMixin:

  def init_recorder(self):
    self._rec_active = False
    self._rec_paused = False
    self._rec_paused_total = 0.0
    self._rec_pause_started = 0.0
    self._rec_thread = None
    self._rec_start_time = 0
    self._rec_timer_job = None
    self._rec_fps = 30
    self._rec_mode = "full"
    self._rec_bbox = None
    self._rec_error = None
    self._rec_tick_n = 0
    self._rec_btn_key = None
    self._rec_btn_orig = None
    self._rec_ind = None
    self._rec_dot = None
    self._rec_time_lbl = None
    self._rec_pause_btn = None
    self._rec_list_refresh = None
    self._current_recording_path = None
    self._rec_panel = None
    self._player_panel = None
    self._player_cap = None
    self._player_job = None
    self._player_is_playing = False
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    self._cleanup_recordings_storage()

  # ------------------------------------------------------------------ storage
  def _cleanup_recordings_storage(self):
    """Enforces 10 GB storage limit by removing oldest files when limit is exceeded."""
    try:
      files = glob.glob(os.path.join(RECORDINGS_DIR, "*.mp4")) + glob.glob(os.path.join(RECORDINGS_DIR, "*.avi"))
      files.sort(key=_safe_mtime)

      total_size = sum(os.path.getsize(f) for f in files if os.path.isfile(f))

      while total_size > MAX_STORAGE_BYTES and files:
        oldest = files.pop(0)
        try:
          size = os.path.getsize(oldest)
          os.remove(oldest)
          total_size -= size
          print(f"🧹 Storage Auto-Cleanup: Removed older recording '{os.path.basename(oldest)}'")
        except Exception as e:
          print(f"Error purging old recording {oldest}: {e}")
    except Exception as e:
      print(f"Auto-cleanup storage check failed: {e}")

  def _recording_files(self):
    """Saved recordings, newest first. The file that is being recorded right now is not listed."""
    files = glob.glob(os.path.join(RECORDINGS_DIR, "*.mp4")) + glob.glob(os.path.join(RECORDINGS_DIR, "*.avi"))
    active = self._current_recording_path if self._rec_active else None
    if active:
      active = os.path.abspath(active)
      files = [f for f in files if os.path.abspath(f) != active]
    files = [f for f in files if os.path.isfile(f)]
    files.sort(key=_safe_mtime, reverse=True)
    return files

  # ------------------------------------------------------------------ panels
  def _close_recorder_panel(self):
    self._rec_list_refresh = None
    if self._rec_panel is not None and self._rec_panel.winfo_exists():
      self._rec_panel.destroy()
    self._rec_panel = None

  def open_recorder_settings(self, event=None):
    """Right-click settings panel embedded directly into main application window."""
    if self._rec_panel is not None and self._rec_panel.winfo_exists():
      self._rec_panel.lift()
      return

    host = self.canvas_container
    self._rec_panel = ctk.CTkFrame(host, fg_color=PANEL_BG, corner_radius=10, border_width=2, border_color=PANEL_BORDER)
    self._rec_panel.place(relx=0.99, rely=0.08, anchor="ne", relwidth=0.34, relheight=0.62)

    head = ctk.CTkFrame(self._rec_panel, height=40, fg_color=PANEL_HEAD, corner_radius=0)
    head.pack(side="top", fill="x")
    ctk.CTkLabel(head, text="⚙️ Recorder Settings", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left", padx=12, pady=6)
    ctk.CTkButton(head, text="✖", width=34, height=28, fg_color="#c0392b", hover_color="#e74c3c",
                  font=ctk.CTkFont(size=13, weight="bold"), command=self._close_recorder_panel).pack(side="right", padx=8)

    body = ctk.CTkFrame(self._rec_panel, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=14, pady=10)

    # FPS Selector
    fps_frame = ctk.CTkFrame(body, fg_color="transparent")
    fps_frame.pack(fill="x", pady=6)
    ctk.CTkLabel(fps_frame, text="Frame Rate (FPS):", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
    fps_seg = ctk.CTkSegmentedButton(fps_frame, values=["15 FPS", "30 FPS", "60 FPS"], height=34, font=ctk.CTkFont(size=12))
    fps_seg.set(f"{self._rec_fps} FPS")
    fps_seg.configure(command=lambda val: setattr(self, "_rec_fps", int(val.split()[0])))
    fps_seg.pack(fill="x", pady=4)

    # Target Selector
    mode_frame = ctk.CTkFrame(body, fg_color="transparent")
    mode_frame.pack(fill="x", pady=6)
    ctk.CTkLabel(mode_frame, text="Capture Target Area:", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
    mode_seg = ctk.CTkSegmentedButton(mode_frame, values=["Full Window", "Canvas Only"], height=34, font=ctk.CTkFont(size=12))
    mode_seg.set("Full Window" if self._rec_mode == "full" else "Canvas Only")
    mode_seg.configure(command=lambda val: setattr(self, "_rec_mode", "full" if "Full" in val else "canvas"))
    mode_seg.pack(fill="x", pady=4)

    # Recordings List Navigation
    ctk.CTkButton(
        body, text="📼 Recordings List", height=40, fg_color="#e67e22", hover_color="#d35400",
        font=ctk.CTkFont(size=14, weight="bold"),
        command=self.open_recordings_list
    ).pack(fill="x", pady=(14, 6))

    # Folder Action
    def open_folder():
      if os.name == "nt":
        os.startfile(RECORDINGS_DIR)
      else:
        subprocess.Popen(["xdg-open", RECORDINGS_DIR])

    ctk.CTkButton(body, text="📂 Open Recordings Folder", height=40, fg_color="#8e44ad", hover_color="#9b59b6",
                  font=ctk.CTkFont(size=14, weight="bold"), command=open_folder).pack(fill="x", pady=4)

  def open_recordings_list(self):
    """Big embedded screen for the saved recordings: Total recordings, Select All, multi-select delete."""
    self._close_recorder_panel()

    host = self.canvas_container
    panel = ctk.CTkFrame(host, fg_color=PANEL_BG, corner_radius=12, border_width=2, border_color=PANEL_BORDER)
    panel.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.66, relheight=0.92)
    self._rec_panel = panel
    panel.update_idletasks()
    name_wrap = max(240, int(panel.winfo_width() * 0.42))

    # ---- header
    head = ctk.CTkFrame(panel, height=48, fg_color=PANEL_HEAD, corner_radius=0)
    head.pack(side="top", fill="x")
    ctk.CTkLabel(head, text="📼 Saved Screen Recordings", font=ctk.CTkFont(size=19, weight="bold")).pack(side="left", padx=14, pady=8)
    ctk.CTkButton(head, text="✖", width=40, height=32, fg_color="#c0392b", hover_color="#e74c3c",
                  font=ctk.CTkFont(size=15, weight="bold"), command=self._close_recorder_panel).pack(side="right", padx=10)

    # ---- totals
    stats = ctk.CTkFrame(panel, fg_color="transparent")
    stats.pack(side="top", fill="x", padx=16, pady=(12, 2))
    total_lbl = ctk.CTkLabel(stats, text="Total recordings: 0", font=ctk.CTkFont(size=20, weight="bold"), text_color="#f5b041")
    total_lbl.pack(side="left")
    size_lbl = ctk.CTkLabel(stats, text="", font=ctk.CTkFont(size=13), text_color="gray65")
    size_lbl.pack(side="left", padx=(16, 0), pady=(4, 0))

    # ---- selection controls
    controls = ctk.CTkFrame(panel, fg_color="transparent")
    controls.pack(side="top", fill="x", padx=16, pady=(6, 6))
    select_all_var = ctk.BooleanVar(value=False)
    select_all_cb = ctk.CTkCheckBox(controls, text="Select All", variable=select_all_var,
                                    font=ctk.CTkFont(size=15, weight="bold"),
                                    checkbox_width=26, checkbox_height=26, command=lambda: on_select_all())
    select_all_cb.pack(side="left")
    sel_lbl = ctk.CTkLabel(controls, text="Selected: 0", font=ctk.CTkFont(size=14), text_color="gray70")
    sel_lbl.pack(side="left", padx=(18, 0))
    delete_btn = ctk.CTkButton(controls, text="🗑️ Delete Selected (0)", height=38, width=210,
                               fg_color="#c0392b", hover_color="#e74c3c", state="disabled",
                               font=ctk.CTkFont(size=14, weight="bold"), command=lambda: delete_selected())
    delete_btn.pack(side="right")

    scroll_box = ctk.CTkScrollableFrame(panel, fg_color="#141414", corner_radius=8)
    scroll_box.pack(fill="both", expand=True, padx=14, pady=(2, 14))

    vars_by_path = {}

    def update_selection_state():
      total = len(vars_by_path)
      n_sel = sum(1 for v in vars_by_path.values() if v.get())
      sel_lbl.configure(text=f"Selected: {n_sel}")
      delete_btn.configure(text=f"🗑️ Delete Selected ({n_sel})", state="normal" if n_sel else "disabled")
      select_all_var.set(total > 0 and n_sel == total)

    def on_select_all():
      value = select_all_var.get()
      for v in vars_by_path.values():
        v.set(value)
      update_selection_state()

    def delete_selected():
      paths = [p for p, v in vars_by_path.items() if v.get()]
      if not paths:
        return
      word = "recording" if len(paths) == 1 else "recordings"
      if not messagebox.askyesno("Delete recordings", f"Permanently delete {len(paths)} {word}?\n\nThis cannot be undone.", parent=self):
        return
      failed = 0
      for p in paths:
        try:
          os.remove(p)
        except Exception as e:
          failed += 1
          print(f"Delete failed: {p}: {e}")
      if hasattr(self, "toast"):
        done = len(paths) - failed
        self.toast(f"🗑️ Deleted {done} {word}" + (f" ({failed} could not be deleted)" if failed else ""))
      render_list()

    def delete_file(path):
      try:
        os.remove(path)
      except Exception as e:
        print(f"Delete failed: {e}")
      render_list()

    def show_inline_rename(path, row_frame):
      for child in row_frame.winfo_children():
        child.destroy()

      old_name = os.path.basename(path)
      entry = ctk.CTkEntry(row_frame, height=36, font=ctk.CTkFont(size=14))
      entry.insert(0, old_name)
      entry.pack(side="left", fill="x", expand=True, padx=8, pady=8)
      entry.focus_set()

      def do_save(event=None):
        new_name = re.sub(r'[<>:"/\\|?*]', "_", entry.get().strip())
        if new_name:
          if not new_name.lower().endswith((".mp4", ".avi")):
            new_name += ".mp4"
          new_path = os.path.join(RECORDINGS_DIR, new_name)
          if os.path.abspath(new_path) != os.path.abspath(path):
            if os.path.exists(new_path):
              if hasattr(self, "toast"):
                self.toast("⚠️ A recording with that name already exists")
              return
            try:
              os.rename(path, new_path)
            except Exception as e:
              print(f"Rename error: {e}")
        render_list()

      entry.bind("<Return>", do_save)
      entry.bind("<Escape>", lambda e: render_list())
      ctk.CTkButton(row_frame, text="✔ Save", width=84, height=36, fg_color="#27ae60", hover_color="#2ecc71",
                    font=ctk.CTkFont(size=13, weight="bold"), command=do_save).pack(side="right", padx=(2, 8))
      ctk.CTkButton(row_frame, text="✖ Cancel", width=92, height=36, fg_color="#7f8c8d", hover_color="#95a5a6",
                    font=ctk.CTkFont(size=13, weight="bold"), command=render_list).pack(side="right", padx=2)

    def render_list():
      if self._rec_panel is not panel or not panel.winfo_exists():
        return
      previous = {p: v.get() for p, v in vars_by_path.items()}
      for child in scroll_box.winfo_children():
        child.destroy()
      vars_by_path.clear()

      files = self._recording_files()
      total_bytes = 0
      for p in files:
        vars_by_path[p] = ctk.BooleanVar(value=previous.get(p, False))
        try:
          total_bytes += os.path.getsize(p)
        except OSError:
          pass

      total_lbl.configure(text=f"Total recordings: {len(files)}")
      size_lbl.configure(text=f"Total size: {_fmt_size(total_bytes)}" if files else "")
      select_all_cb.configure(state="normal" if files else "disabled")

      if not files:
        ctk.CTkLabel(scroll_box, text="No recordings available yet.", font=ctk.CTkFont(size=16), text_color="gray60").pack(pady=40)
        update_selection_state()
        return

      for fpath in files:
        fname = os.path.basename(fpath)
        try:
          fsize = os.path.getsize(fpath)
        except OSError:
          fsize = 0

        row = ctk.CTkFrame(scroll_box, fg_color=ROW_BG, corner_radius=8)
        row.pack(fill="x", pady=4, padx=2)

        ctk.CTkCheckBox(row, text="", variable=vars_by_path[fpath], width=30, checkbox_width=26, checkbox_height=26,
                        command=update_selection_state).pack(side="left", padx=(12, 4), pady=10)

        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=6, pady=6)
        ctk.CTkLabel(info, text=fname, font=ctk.CTkFont(size=14, weight="bold"), anchor="w", justify="left",
                     wraplength=name_wrap).pack(anchor="w")
        ctk.CTkLabel(info, text=f"{_fmt_modified(_safe_mtime(fpath))}   •   {_fmt_size(fsize)}",
                     font=ctk.CTkFont(size=12), text_color="gray65", anchor="w").pack(anchor="w")

        btn_box = ctk.CTkFrame(row, fg_color="transparent")
        btn_box.pack(side="right", padx=8)

        ctk.CTkButton(
            btn_box, text="▶ Play", width=84, height=36, font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#27ae60", hover_color="#2ecc71", command=lambda p=fpath: self.play_recording_in_app(p)
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            btn_box, text="✏️ Rename", width=104, height=36, font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#2980b9", hover_color="#3498db", command=lambda p=fpath, r=row: show_inline_rename(p, r)
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            btn_box, text="🗑️ Delete", width=96, height=36, font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#c0392b", hover_color="#e74c3c", command=lambda p=fpath: delete_file(p)
        ).pack(side="left", padx=2)

      update_selection_state()

    self._rec_list_refresh = render_list
    render_list()

  # ------------------------------------------------------------------ in-app player
  def play_recording_in_app(self, video_path):
    """Plays video directly inside the main app canvas container using an embedded frame."""
    self.stop_in_app_player()
    self._close_recorder_panel()

    host = self.canvas_container
    self._player_panel = ctk.CTkFrame(host, fg_color="#000000", corner_radius=6, border_width=2, border_color="#e67e22")
    self._player_panel.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.88, relheight=0.88)

    head = ctk.CTkFrame(self._player_panel, height=40, fg_color="#1a1a1a", corner_radius=0)
    head.pack(side="top", fill="x")

    ctk.CTkButton(
        head, text="⏹ Stop Playing", width=140, height=30,
        font=ctk.CTkFont(size=13, weight="bold"), fg_color="#c0392b", hover_color="#e74c3c",
        command=self.stop_in_app_player
    ).pack(side="right", padx=8, pady=5)

    ctk.CTkLabel(head, text=f"▶ Playing: {os.path.basename(video_path)}", font=ctk.CTkFont(size=13, weight="bold"),
                 text_color="#f39c12").pack(side="left", padx=10)

    self._player_disp = tk.Label(self._player_panel, bg="black")
    self._player_disp.pack(fill="both", expand=True)

    self._player_cap = cv2.VideoCapture(video_path)
    self._player_is_playing = True
    self._player_fps = self._player_cap.get(cv2.CAP_PROP_FPS) or 30.0
    self._player_tick()

  def _player_tick(self):
    if not (self._player_panel and self._player_panel.winfo_exists() and self._player_is_playing):
      return

    ret, frame = self._player_cap.read()
    if ret:
      w_win = max(200, self._player_disp.winfo_width())
      h_win = max(150, self._player_disp.winfo_height())

      frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
      img = Image.fromarray(frame_rgb)
      img.thumbnail((w_win, h_win), Image.Resampling.BILINEAR)

      self._player_tk = ImageTk.PhotoImage(img)
      self._player_disp.configure(image=self._player_tk)

      delay = int(1000 / self._player_fps)
      self._player_job = self.after(delay, self._player_tick)
    else:
      self.stop_in_app_player()

  def stop_in_app_player(self):
    self._player_is_playing = False
    if self._player_job:
      self.after_cancel(self._player_job)
      self._player_job = None

    if self._player_cap and self._player_cap.isOpened():
      self._player_cap.release()
    self._player_cap = None

    if self._player_panel and self._player_panel.winfo_exists():
      self._player_panel.destroy()
    self._player_panel = None

  # ------------------------------------------------------------------ live indicator (top toolbar)
  def _rec_build_indicator(self):
    """Small pill in the top toolbar row: blinking dot, timer, Pause/Resume, Stop. Shown only while recording."""
    ind = self._rec_ind
    if ind is not None and ind.winfo_exists():
      return ind
    try:  # let the canvas span the extra column so it never shrinks when the pill is shown
      self.canvas.master.grid_configure(columnspan=2)
    except Exception:
      pass
    ind = ctk.CTkFrame(self.canvas_container, fg_color="#181818", corner_radius=8, border_width=1, border_color="#7a2a2a")
    self._rec_dot = ctk.CTkLabel(ind, text="●", width=20, font=ctk.CTkFont(size=18), text_color=REC_RED)
    self._rec_dot.pack(side="left", padx=(10, 0))
    self._rec_time_lbl = ctk.CTkLabel(ind, text="REC 00:00", font=ctk.CTkFont(size=13, weight="bold"), text_color="#f2f2f2")
    self._rec_time_lbl.pack(side="left", padx=(4, 8))
    self._rec_pause_btn = ctk.CTkButton(ind, text="⏸", width=34, height=28, font=ctk.CTkFont(size=14),
                                        fg_color="#e67e22", hover_color="#d35400", command=self.toggle_pause_recording)
    self._rec_pause_btn.pack(side="left", padx=2, pady=6)
    ctk.CTkButton(ind, text="⏹", width=34, height=28, font=ctk.CTkFont(size=14),
                  fg_color="#c0392b", hover_color="#e74c3c", command=self.stop_screen_recording).pack(side="left", padx=(2, 10), pady=6)
    self._rec_ind = ind
    return ind

  def _rec_show_indicator(self):
    ind = self._rec_build_indicator()
    self._rec_dot.configure(text_color=REC_RED)
    self._rec_time_lbl.configure(text="REC 00:00")
    self._rec_pause_btn.configure(text="⏸")
    ind.grid(row=0, column=1, padx=(8, 0), pady=(0, 6))

  def _rec_hide_indicator(self):
    ind = self._rec_ind
    if ind is not None and ind.winfo_exists():
      ind.grid_remove()

  def _rec_style_button(self, text, fg_color, hover_color):
    btn = getattr(self, "recorder_btn", None)
    if not btn:
      return
    try:
      btn.configure(text=text, fg_color=fg_color, hover_color=hover_color)
    except Exception:
      pass

  # ------------------------------------------------------------------ recording control
  def toggle_screen_recording(self):
    if self._rec_active:
      self.stop_screen_recording()
    else:
      self.start_screen_recording()

  def _rec_elapsed(self):
    """Seconds recorded so far (time spent paused is not counted)."""
    now = self._rec_pause_started if self._rec_paused else time.time()
    return max(0.0, now - self._rec_start_time - self._rec_paused_total)

  def _rec_refresh_bbox(self):
    """Runs on the main thread: Tk is not called from the capture thread."""
    try:
      if self.state() == "iconic":
        self._rec_bbox = None  # window minimized: do not film whatever is on the desktop
        return
      target = self.canvas if (self._rec_mode == "canvas" and getattr(self, "canvas", None)) else self
      rx, ry, rw, rh = target.winfo_rootx(), target.winfo_rooty(), target.winfo_width(), target.winfo_height()
      if rw > 50 and rh > 50 and rx > -10000:
        self._rec_bbox = (rx, ry, rx + rw, ry + rh)
    except Exception:
      pass

  def start_screen_recording(self):
    if self._rec_active:
      return

    self._cleanup_recordings_storage()
    self._rec_start_time = time.time()
    self._rec_paused = False
    self._rec_paused_total = 0.0
    self._rec_pause_started = 0.0
    self._rec_error = None
    self._rec_tick_n = 0
    self._rec_btn_key = None
    self._rec_bbox = None
    self._rec_refresh_bbox()

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    self._current_recording_path = os.path.join(RECORDINGS_DIR, f"Recording_{timestamp}.mp4")

    # remember how the More Tools card / button looked so it can be restored exactly afterwards
    btn = getattr(self, "recorder_btn", None)
    if btn:
      try:
        fg = btn.cget("fg_color")
      except Exception:
        fg = "#1f6aa5"
      try:
        hv = btn.cget("hover_color")
      except Exception:
        hv = "#2980b9"
      self._rec_btn_orig = (fg, hv)

    self._rec_active = True
    self._rec_show_indicator()

    if hasattr(self, "toast"):
      self.toast("🔴 Screen Recording Started!")

    self._rec_thread = threading.Thread(target=self._record_loop, args=(self._current_recording_path,), daemon=True)
    self._rec_thread.start()
    self._rec_ui_tick()

  def toggle_pause_recording(self):
    if not self._rec_active:
      return
    if self._rec_paused:
      self._rec_paused_total += time.time() - self._rec_pause_started
      self._rec_paused = False
      if self._rec_pause_btn:
        self._rec_pause_btn.configure(text="⏸")
      if hasattr(self, "toast"):
        self.toast("▶ Recording resumed")
    else:
      self._rec_pause_started = time.time()
      self._rec_paused = True
      if self._rec_pause_btn:
        self._rec_pause_btn.configure(text="▶")
      if hasattr(self, "toast"):
        self.toast("⏸ Recording paused")

  def _rec_ui_tick(self):
    """Every 250 ms: blink, timer, capture area, More Tools card text, and a check that the capture thread is alive."""
    self._rec_timer_job = None
    if not self._rec_active:
      return

    self._rec_tick_n += 1
    self._rec_refresh_bbox()
    clock = _fmt_clock(self._rec_elapsed())
    paused = self._rec_paused

    if self._rec_dot is not None:
      if paused:
        self._rec_dot.configure(text_color=REC_AMBER)
      else:
        self._rec_dot.configure(text_color=REC_RED if (self._rec_tick_n // 2) % 2 == 0 else REC_RED_DIM)
    if self._rec_time_lbl is not None:
      self._rec_time_lbl.configure(text=f"{'PAUSED' if paused else 'REC'} {clock}")

    key = (clock, paused)
    if key != self._rec_btn_key:
      self._rec_btn_key = key
      if paused:
        self._rec_style_button(f"⏸ Paused ({clock})", "#b9770e", "#d68910")
      else:
        self._rec_style_button(f"⏹ Recording ({clock})", "#c0392b", "#e74c3c")

    if self._rec_thread is not None and not self._rec_thread.is_alive():
      self.stop_screen_recording(error=self._rec_error or "the capture stopped unexpectedly")
      return

    self._rec_timer_job = self.after(UI_TICK_MS, self._rec_ui_tick)

  def _record_loop(self, out_path):
    """Capture thread. Frames are written by REAL time, so the video lasts exactly as long as the recording did
    (slow screen grabs repeat the last frame instead of making the video play too fast); paused time is skipped."""
    fps = float(self._rec_fps)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = None
    size = None
    written = 0
    errors = 0

    try:
      while self._rec_active:
        start_time = time.time()

        bbox = self._rec_bbox
        if self._rec_paused or bbox is None:
          time.sleep(0.05)
          continue

        try:
          img = ImageGrab.grab(bbox=bbox)
          frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
          h, w = frame.shape[:2]

          if writer is None:
            size = (w, h)
            writer = cv2.VideoWriter(out_path, fourcc, fps, size)
            if not writer.isOpened():
              raise RuntimeError("the video file could not be created (mp4 codec missing?)")
          elif (w, h) != size:
            frame = cv2.resize(frame, size)  # window was resized while recording

          target = int(self._rec_elapsed() * fps) + 1
          repeat = max(1, min(target - written, int(fps)))
          for _ in range(repeat):
            writer.write(frame)
          written += repeat
          errors = 0
        except Exception as e:
          errors += 1
          print(f"Recording frame error: {e}")
          if errors >= MAX_ERRORS_IN_A_ROW:
            self._rec_error = str(e)
            break

        elapsed = time.time() - start_time
        time.sleep(max(0.001, (1.0 / fps) - elapsed))

    except Exception as e:
      self._rec_error = str(e)
      print(f"Recording loop error: {e}")

    finally:
      if writer:
        writer.release()
      self._cleanup_recordings_storage()

  def stop_screen_recording(self, error=None):
    if not self._rec_active:
      return

    self._rec_active = False

    if self._rec_timer_job:
      self.after_cancel(self._rec_timer_job)
      self._rec_timer_job = None

    thread = self._rec_thread
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
      thread.join(timeout=3.0)  # lets the video writer finish so the file is complete
    self._rec_thread = None
    self._rec_paused = False

    self._rec_hide_indicator()
    orig_fg, orig_hv = self._rec_btn_orig or ("#1f6aa5", "#2980b9")
    self._rec_style_button("🎥 Screen Recorder", orig_fg, orig_hv)

    full_path = self._current_recording_path or RECORDINGS_DIR
    has_video = os.path.isfile(full_path) and os.path.getsize(full_path) > 0
    if hasattr(self, "toast"):
      if error or not has_video:
        self.toast(f"❌ Recording failed: {error or 'no video was captured'}", 6000)
      else:
        self.toast(f"💾 Saved to: {full_path}", 6000)
    else:
      print(f"Saved recording to: {full_path}")

    refresh = self._rec_list_refresh  # the Saved Screen Recordings screen updates itself if it is open
    if refresh:
      try:
        refresh()
      except Exception:
        pass

  def shutdown_recorder(self):
    """Called when the app closes: finishes a running recording so the video file is complete, stops the player."""
    try:
      self.stop_screen_recording()
    except Exception:
      pass
    try:
      self.stop_in_app_player()
    except Exception:
      pass
