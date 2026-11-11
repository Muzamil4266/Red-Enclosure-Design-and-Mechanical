import os
import re
import json
import time
import shutil
import tkinter as tk
import customtkinter as ctk
from send2trash import send2trash
from tkinter import simpledialog, messagebox

PROJECTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saved_projects")
APP_TITLE = "Red Enclosure"
MAX_PROJECTS_BYTES = 4 * 1024 ** 3  # (PCB maps are big lossless PNGs)  # oldest projects are auto-deleted beyond this

PROJ_WIN_W, PROJ_WIN_H = 1000, 800  # Saved Projects window (double of the old 500x400)


def format_time_12h(ts):
  """12-hour clock with AM/PM, e.g. 2026-11-10  3:45 PM"""
  t = time.localtime(ts)
  h12 = t.tm_hour % 12 or 12
  return f"{t.tm_year}-{t.tm_mon:02d}-{t.tm_mday:02d}  {h12}:{t.tm_min:02d} {'AM' if t.tm_hour < 12 else 'PM'}"


class SerializationProjectsMixin:

  # ------------------------------------------------------------------ helpers
  def _project_safe_name(self, name):
    if not name:
      return ""
    return re.sub(r'[^a-zA-Z0-9_\- ]', '', str(name)).strip()

  def _project_paths(self, safe):
    d = os.path.join(PROJECTS_DIR, safe)
    return d, os.path.join(d, "circuit.json")

  @staticmethod
  def _state_is_empty(st):
    """True when the workspace holds nothing the user made (no components, strokes, texts, tables...).
    Generic on purpose: any non-empty list/dict value in the state counts as content."""
    if not isinstance(st, dict):
      return True
    return not any(isinstance(v, (list, dict)) and v for v in st.values())

  def _project_has_content(self, p):
    """Reads a project file (cached by mtime/size) and tells whether it holds anything."""
    cache = getattr(self, "_proj_content_cache", None)
    if cache is None:
      cache = self._proj_content_cache = {}
    try:
      stt = os.stat(p)
      key = (stt.st_mtime_ns, stt.st_size)
      hit = cache.get(p)
      if hit and hit[0] == key:
        return hit[1]
      with open(p, "r", encoding="utf-8") as f:
        st = json.load(f)
      has = not self._state_is_empty(st)
    except Exception:
      return True  # unreadable file: keep it visible, never hide user data
    cache[p] = (key, has)
    return has

  def list_projects(self):
    out = []
    if os.path.isdir(PROJECTS_DIR):
      for d in os.listdir(PROJECTS_DIR):
        p = os.path.join(PROJECTS_DIR, d, "circuit.json")
        if os.path.isfile(p) and os.path.getsize(p) > 0 and self._project_has_content(p):
          out.append({"name": d, "modified": os.path.getmtime(p)})
    return sorted(out, key=lambda x: x["modified"], reverse=True)

  def purge_empty_projects(self):
    """Startup cleanup: removes empty projects created by older versions.
    Only folders that contain nothing but the project file are deleted."""
    if not os.path.isdir(PROJECTS_DIR):
      return
    only_json = {"circuit.json", "circuit.json.tmp"}
    for d in os.listdir(PROJECTS_DIR):
      full = os.path.join(PROJECTS_DIR, d)
      if not os.path.isdir(full):
        continue
      try:
        files = set(os.listdir(full))
        p = os.path.join(full, "circuit.json")
        if not files:
          os.rmdir(full)
        elif files <= only_json:
          if not os.path.isfile(p) or os.path.getsize(p) == 0 or not self._project_has_content(p):
            shutil.rmtree(full, ignore_errors=True)
      except Exception:
        pass

  # ------------------------------------------------------------------ saving
  def save_project(self):
    """Manual 'save as' (no toolbar button any more - projects autosave). Never saves an empty workspace."""
    try:
      st = self.get_state()
      if self._state_is_empty(st):
        if hasattr(self, "toast"):
          self.toast("Workspace is empty - nothing to save")
        return

      def_name = getattr(self, "current_project", None) or "My Enclosure"
      name = simpledialog.askstring("Save Project", "Enter Project Name:", initialvalue=def_name)
      if not name:
        return

      base_safe = self._project_safe_name(name)
      if not base_safe:
        messagebox.showerror("Error", "Please enter a valid project name.")
        return

      safe = base_safe
      counter = 1
      d, p = self._project_paths(safe)
      while os.path.exists(d) and safe != getattr(self, "current_project", None):
        safe = f"{base_safe} ({counter})"
        d, p = self._project_paths(safe)
        counter += 1

      os.makedirs(d, exist_ok=True)
      with open(p, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)
      self.pcb_write_files(d)

      old = getattr(self, "current_project", None)
      if getattr(self, "_auto_project", False) and old and old != safe:
        shutil.rmtree(self._project_paths(old)[0], ignore_errors=True)  # auto-named draft became a named project
      self._auto_project = False
      self._dirty = False
      self._enforce_projects_limit(safe)

      self.current_project = safe
      self.title(f"{APP_TITLE} - {safe}")

      if hasattr(self, "toast"):
        self.toast(f"💾 Saved as '{safe}'!")
      else:
        messagebox.showinfo("Saved", f"Project saved as '{safe}'!")

      if getattr(self, "_proj_win", None) and self._proj_win.winfo_exists():
        self._refresh_projects_window()

    except Exception as e:
      messagebox.showerror("Save Error", f"Failed to save project:\n{e}")

  def _next_auto_name(self):
    n = 1
    while os.path.exists(os.path.join(PROJECTS_DIR, f"Enclosure {n:03d}")) or os.path.exists(os.path.join(PROJECTS_DIR, f"Circuit {n:03d}")):
      n += 1
    return f"Enclosure {n:03d}"

  def _enforce_projects_limit(self, keep=None):
    items, total = [], 0
    if not os.path.isdir(PROJECTS_DIR):
      return
    for d in os.listdir(PROJECTS_DIR):
      full = os.path.join(PROJECTS_DIR, d)
      if not os.path.isdir(full):
        continue
      size = sum(os.path.getsize(os.path.join(r, n)) for r, _, ns in os.walk(full) for n in ns)
      p = os.path.join(full, "circuit.json")
      items.append((os.path.getmtime(p) if os.path.isfile(p) else 0, d, full, size))
      total += size
    for _, d, full, size in sorted(items):
      if total <= MAX_PROJECTS_BYTES:
        break
      if d != keep:
        shutil.rmtree(full, ignore_errors=True)
        total -= size

  def autosave_project(self):
    """Writes the workspace into its saved project (creating 'Enclosure 001', 'Enclosure 002'... if unnamed).
    An EMPTY workspace is never written, so no empty project can ever exist."""
    st = self.get_state()
    if self._state_is_empty(st):
      self._dirty = False
      return False
    safe = getattr(self, "current_project", None)
    if not safe:
      safe = self._next_auto_name()
      self.current_project = safe
      self._auto_project = True
      self.title(f"{APP_TITLE} - {safe}")
    d, p = self._project_paths(safe)
    os.makedirs(d, exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
      json.dump(st, f)
    os.replace(p + ".tmp", p)
    self.pcb_write_files(d)
    self._enforce_projects_limit(safe)
    self._dirty = False
    if getattr(self, "_proj_win", None) and self._proj_win.winfo_exists():
      self._refresh_projects_window()
    return True

  # ------------------------------------------------------------------ new / clear
  def new_project(self):
    if hasattr(self, "flush_autosave"):
      self.flush_autosave()  # nothing the user did is lost
    self.set_state({})
    self.undo_stack.clear()
    self.redo_stack.clear()
    self.pre_state = None
    self._dirty = False
    self._auto_project = False
    self.current_project = None
    self.title(APP_TITLE)
    self.center_view()

  def clear_workspace(self):
    """Toolbar 'Clear Workspace': empties the whole workspace. The work done so far is already
    saved in Saved Projects, and the now-empty workspace is never saved."""
    if self._state_is_empty(self.get_state()):
      if hasattr(self, "toast"):
        self.toast("Workspace is already empty")
      return
    if not messagebox.askyesno(
        "Clear Workspace",
        "Clear everything on the workspace?\n\nYour work so far stays saved in 'Saved Projects'."):
      return
    self.new_project()
    if hasattr(self, "toast"):
      self.toast("🧹 Workspace cleared")

  # ------------------------------------------------------------------ projects window
  def open_projects_window(self):
    win = getattr(self, "_proj_win", None)
    if win and win.winfo_exists():
      win.focus()
      return
    win = self._proj_win = ctk.CTkToplevel(self)
    win.title("📁 Saved Projects")
    self.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    w, h = max(640, min(PROJ_WIN_W, sw - 80)), max(480, min(PROJ_WIN_H, sh - 120))
    x = max(0, min(self.winfo_rootx() + (self.winfo_width() - w) // 2, sw - w - 10))
    y = max(0, min(self.winfo_rooty() + (self.winfo_height() - h) // 2, sh - h - 60))
    win.geometry(f"{w}x{h}+{x}+{y}")
    win.minsize(640, 480)
    win.transient(self)
    win.grab_set()

    top = ctk.CTkFrame(win, fg_color="transparent")
    top.pack(fill="x", padx=18, pady=(16, 6))
    ctk.CTkLabel(top, text="Your Projects", font=ctk.CTkFont(size=24, weight="bold")).pack(side="left")
    self._proj_count_lbl = ctk.CTkLabel(top, text="", font=ctk.CTkFont(size=13), text_color="gray")
    self._proj_count_lbl.pack(side="left", padx=12, pady=(8, 0))
    ctk.CTkButton(top, text="➕ New Project", width=140, height=34, fg_color="#27ae60",
                  command=self._proj_new).pack(side="right")

    bar = ctk.CTkFrame(win, fg_color=("gray85", "gray17"), corner_radius=8)
    bar.pack(fill="x", padx=18, pady=(4, 8))
    self._proj_all_var = tk.BooleanVar(value=False)
    ctk.CTkCheckBox(bar, text="Select All", variable=self._proj_all_var, command=self._proj_toggle_all,
                    font=ctk.CTkFont(size=13, weight="bold")).pack(side="left", padx=14, pady=10)
    self._proj_sel_lbl = ctk.CTkLabel(bar, text="", font=ctk.CTkFont(size=12), text_color="gray")
    self._proj_sel_lbl.pack(side="left", padx=8)
    self._proj_del_btn = ctk.CTkButton(bar, text="🗑️ Delete Selected", width=190, height=32, fg_color="#c0392b",
                                       hover_color="#e74c3c", command=self._proj_delete_selected)
    self._proj_del_btn.pack(side="right", padx=12)

    self._proj_scroll = ctk.CTkScrollableFrame(win)
    self._proj_scroll.pack(fill="both", expand=True, padx=18, pady=(0, 16))
    self._proj_vars = {}
    self._proj_names = []
    self._refresh_projects_window()

  def _proj_new(self):
    self.new_project()
    if getattr(self, "_proj_win", None) and self._proj_win.winfo_exists():
      self._proj_win.destroy()

  def _proj_selected(self):
    return [n for n in getattr(self, "_proj_names", []) if n in self._proj_vars and self._proj_vars[n].get()]

  def _proj_toggle_all(self):
    on = self._proj_all_var.get()
    for n in self._proj_names:
      self._proj_vars[n].set(on)
    self._proj_update_selection()

  def _proj_update_selection(self):
    try:
      sel = self._proj_selected()
      total = len(self._proj_names)
      self._proj_count_lbl.configure(text=f"{total} project{'s' if total != 1 else ''}")
      self._proj_sel_lbl.configure(text=f"{len(sel)} selected" if sel else "")
      self._proj_all_var.set(bool(total) and len(sel) == total)
      self._proj_del_btn.configure(text=f"🗑️ Delete Selected ({len(sel)})" if sel else "🗑️ Delete Selected",
                                   state="normal" if sel else "disabled")
    except Exception:
      pass

  def _proj_delete_selected(self):
    self.delete_projects(self._proj_selected())

  def _refresh_projects_window(self):
    scroll = getattr(self, "_proj_scroll", None)
    try:
      if scroll is None or not scroll.winfo_exists():
        return
      for w in scroll.winfo_children():
        w.destroy()
    except Exception:
      return
    projs = self.list_projects()
    self._proj_names = [p["name"] for p in projs]
    if not hasattr(self, "_proj_vars"):
      self._proj_vars = {}
    for n in list(self._proj_vars):
      if n not in self._proj_names:
        del self._proj_vars[n]  # selection of deleted projects is dropped, the rest is kept
    if not projs:
      ctk.CTkLabel(scroll, text="No saved projects found.", font=ctk.CTkFont(size=15), text_color="gray").pack(pady=40)
      self._proj_update_selection()
      return
    for p in projs:
      safe = p["name"]
      var = self._proj_vars.get(safe)
      if var is None:
        var = self._proj_vars[safe] = tk.BooleanVar(value=False)
      f = ctk.CTkFrame(scroll, fg_color=("gray90", "gray15"), corner_radius=8)
      f.pack(fill="x", pady=5, padx=4)
      ctk.CTkCheckBox(f, text="", variable=var, width=28, command=self._proj_update_selection).pack(side="left", padx=(14, 2), pady=14)
      btns = ctk.CTkFrame(f, fg_color="transparent")
      btns.pack(side="right", padx=12)
      ctk.CTkButton(btns, text="📂 Open", width=90, height=34, fg_color="#2980b9",
                    command=lambda s=safe: self.open_project(s)).pack(side="left", padx=4)
      ctk.CTkButton(btns, text="🗑️", width=40, height=34, fg_color="#c0392b", hover_color="#e74c3c",
                    command=lambda s=safe: self.delete_projects([s])).pack(side="left")
      inf = ctk.CTkFrame(f, fg_color="transparent")
      inf.pack(side="left", fill="x", expand=True, padx=8, pady=10)
      ctk.CTkLabel(inf, text=safe, font=ctk.CTkFont(size=16, weight="bold"), anchor="w", justify="left", wraplength=520).pack(fill="x")
      ctk.CTkLabel(inf, text=f"Last modified: {format_time_12h(p['modified'])}", font=ctk.CTkFont(size=12),
                   text_color="gray", anchor="w").pack(fill="x")
    self._proj_update_selection()

  # ------------------------------------------------------------------ delete / open
  def delete_project(self, safe, name=None):
    self.delete_projects([safe])

  def delete_projects(self, names):
    """Moves one or many projects to the Recycle Bin (not permanent)."""
    names = list(names)
    if not names:
      return
    parent = self._proj_win if getattr(self, "_proj_win", None) and self._proj_win.winfo_exists() else self
    if len(names) == 1:
      msg = f"Move project '{names[0]}' to the Recycle Bin?"
    else:
      msg = f"Move {len(names)} selected projects to the Recycle Bin?"
    if not messagebox.askyesno("Delete", msg, parent=parent):
      return
    cur = getattr(self, "current_project", None)
    failed = []
    for safe in names:
      d, _ = self._project_paths(safe)
      try:
        send2trash(os.path.abspath(d))  # Recycle Bin, not permanent; the folder also holds PCB map images and backups
      except Exception as e:
        failed.append(f"{safe}: {e}")
        continue
      self._proj_vars.pop(safe, None)
      if cur == safe:
        job = getattr(self, "_autosave_job", None)
        if job:
          try:
            self.after_cancel(job)
          except Exception:
            pass
          self._autosave_job = None
        self._dirty = False
        self.current_project = None
        self._auto_project = False
        self.title(APP_TITLE)
    self._refresh_projects_window()
    if failed:
      messagebox.showerror("Error", "Delete failed for:\n" + "\n".join(failed), parent=parent)

  def open_project(self, safe):
    d, p = self._project_paths(safe)
    if not os.path.exists(p) or os.path.getsize(p) == 0:
      messagebox.showerror("Error", "Project file is corrupted or empty.")
      self._refresh_projects_window()
      return
    try:
      if hasattr(self, "flush_autosave"):
        self.flush_autosave()  # save what is on the workspace before replacing it
      with open(p, "r", encoding="utf-8") as f:
        st = json.load(f)
      self._state_dir = d  # PCB map images are read from the project folder
      try:
        self.set_state(st)
      finally:
        self._state_dir = None
      self.undo_stack.clear()
      self.redo_stack.clear()
      self.pre_state = None
      self._dirty = False
      if hasattr(self, "schedule_aa_all"):
        self.schedule_aa_all()
      self._auto_project = False
      self.current_project = safe
      self.title(f"{APP_TITLE} - {safe}")
      if getattr(self, "_proj_win", None) and self._proj_win.winfo_exists():
        self._proj_win.destroy()
    except Exception as e:
      messagebox.showerror("Error", f"Open failed: {e}")
