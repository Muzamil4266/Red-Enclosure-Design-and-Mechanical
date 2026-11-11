"""
settings_logic.py  (part 2 of 3 of Settings Mixin)

Core state management, project auto-save wrapping, backup copy generation,
canvas scroll & border event handling, theme application, and background ticks.
"""
import gc
import json
import os
import shutil
import time
from tkinter import messagebox

import customtkinter as ctk

import serialization_projects as _sp
from canvas_ui_setup import SCROLL_Y_DIR, BTN_BLUE
from settings_schema import (
    SETTINGS_FILE, CANVAS_W, CENTER_X, SCREEN_MODES, THEME_PALETTES,
    DEFAULT_TOOLBAR_BG, BTN_COLORS, BORDER_COLORS, AUTOSAVE_GAP_SECONDS,
    MANUAL_SAVE_GAP_SECONDS, SETTINGS_SCHEMA, UI_SCALE, LOW_MEMORY_UNDO_CAP,
    coerce_setting
)


class SettingsLogicMixin:

    # ------------------------------------------------------------------ setup / storage
    def init_settings(self):
        """Call BEFORE the toolbar is built, so wrapped functions are the ones the buttons bind to."""
        self._st_schema = {s["key"]: s for s in SETTINGS_SCHEMA}
        self.app_settings = {k: s["default"] for k, s in self._st_schema.items()}
        self._st_quiet = False
        self._st_win = None
        self._docs_help_win = None
        self._st_widgets = {}
        self._st_save_job = None
        self._st_ticking = False
        self._st_tick_n = 0
        self._st_cfg_job = None
        self._st_cfg_bound = False
        self._st_btn_fg = BTN_BLUE
        self._st_btn_hover = BTN_COLORS["Blue"][1]
        self._st_last_backup = {}
        self._st_default_projects_dir = _sp.PROJECTS_DIR
        self._themed_buttons = []  # toolbar buttons register themselves here
        self._st_load()
        self._st_wrap_toast()
        self._st_wrap_new_project()
        self._st_wrap_highlight()
        self._st_wrap_autosave()
        self._st_wrap_project_io()
        self.protocol("WM_DELETE_WINDOW", self._st_on_close_request)

    def _st_load(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key in self._st_schema:
                    if key in data:
                        self.app_settings[key] = coerce_setting(self._st_schema, key, data[key])
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            print(f"[Settings] could not read settings.json, using defaults: {e}")

    def _st_save_now(self):
        self._st_save_job = None
        try:
            tmp = SETTINGS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.app_settings, f, indent=2)
            os.replace(tmp, SETTINGS_FILE)
        except OSError as e:
            print(f"[Settings] could not save settings.json: {e}")

    def _st_schedule_save(self):
        if self._st_save_job is not None:
            try:
                self.after_cancel(self._st_save_job)
            except Exception:
                pass
        self._st_save_job = self.after(400, self._st_save_now)

    def _st_coerce(self, key, v):
        return coerce_setting(self._st_schema, key, v)

    # ------------------------------------------------------------------ public API
    def get_setting(self, key, default=None):
        return getattr(self, "app_settings", {}).get(key, default)

    def set_setting(self, key, value):
        if key not in self._st_schema:
            return
        self.app_settings[key] = self._st_coerce(key, value)
        self._st_schedule_save()
        self._st_apply(key)

    def reset_setting(self, key):
        if key in self._st_schema:
            self.set_setting(key, self._st_schema[key]["default"])

    def reset_all_settings(self):
        for key, s in self._st_schema.items():
            if s["kind"] not in ("info", "hidden"):
                self.set_setting(key, s["default"])

    def apply_all_settings(self):
        """Call after the canvas/toolbar exist (just before init_pipe_sim)."""
        self._st_quiet = True
        try:
            for key in self._st_schema:
                self._st_apply(key)
        finally:
            self._st_quiet = False
        if self.app_settings["remember_last_project"]:
            self.after(900, self._st_open_last_project)
        if not self._st_ticking:
            self._st_ticking = True
            self.after(2000, self._st_tick)

    def _st_apply(self, key):
        fn = getattr(self, "_st_apply_" + key, None)
        if fn is None:
            return
        try:
            fn(self.app_settings[key])
        except Exception as e:
            print(f"[Settings] could not apply '{key}': {e}")

    # ------------------------------------------------------------------ live appliers: look & feel
    def _st_apply_theme(self, v):
        prev = getattr(self, "_st_theme_now", None)
        self._st_theme_now = v
        if v in THEME_PALETTES:
            ctk.set_appearance_mode("Dark")
            win_bg, tb_bg = THEME_PALETTES[v]
        else:
            ctk.set_appearance_mode(v)
            if prev not in THEME_PALETTES:
                return
            win_bg = ctk.ThemeManager.theme["CTk"]["fg_color"]
            tb_bg = DEFAULT_TOOLBAR_BG
        self.configure(fg_color=win_bg)
        tb = getattr(self, "top_scroll_toolbar", None)
        if tb is not None:
            tb.configure(fg_color=tb_bg)

    def _st_apply_toolbar_btn_color(self, v):
        fg, hover = BTN_COLORS.get(v, BTN_COLORS["Blue"])
        old = self._st_btn_fg
        self._st_btn_fg, self._st_btn_hover = fg, hover
        self._st_retint_buttons(old)

    def _st_retint_buttons(self, old=None):
        fg, hover = self._st_btn_fg, self._st_btn_hover
        targets = {BTN_BLUE.lower()}
        if old:
            targets.add(old.lower())
        if fg.lower() == BTN_BLUE.lower() and len(targets) == 1:
            return
        for b in list(getattr(self, "_themed_buttons", [])):
            try:
                if not b.winfo_exists():
                    continue
                cur = b.cget("fg_color")
                if isinstance(cur, str) and cur.lower() in targets:
                    b.configure(fg_color=fg, hover_color=hover)
            except Exception:
                pass

    def _st_wrap_highlight(self):
        orig = getattr(self, "highlight_toolbar_button", None)
        if not callable(orig):
            return

        def highlight(*a, **k):
            r = orig(*a, **k)
            self._st_retint_buttons()
            return r

        self.highlight_toolbar_button = highlight

    def _st_apply_canvas_bg(self, v):
        if getattr(self, "canvas", None) is not None:
            self.canvas.configure(bg=v)
            self.canvas.delete("bg_grid")
        if getattr(self, "canvas_container", None) is not None:
            self.canvas_container.configure(fg_color=v)
        self.current_bg_type = "solid"

    # ------------------------------------------------------------------ screen border
    def _st_border_px(self):
        if not self.app_settings.get("screen_border"):
            return 0
        return int(self.app_settings.get("border_width", 3))

    def _st_refresh_border(self):
        c = getattr(self, "canvas", None)
        if c is None:
            return
        width = self._st_border_px()
        color = BORDER_COLORS.get(self.app_settings.get("border_color"), BORDER_COLORS["Green"])
        c.configure(highlightthickness=width, highlightbackground=color, highlightcolor=color)
        try:
            self._st_resize_refresh()
        except Exception as e:
            print(f"[Settings] border refresh error: {e}")

    def _st_apply_screen_border(self, v):
        self._st_refresh_border()

    def _st_apply_border_color(self, v):
        self._st_refresh_border()

    def _st_apply_border_width(self, v):
        self._st_refresh_border()

    def _st_apply_ui_size(self, v):
        factor = UI_SCALE.get(v, 1.0)
        if factor == getattr(self, "_st_scale_now", 1.0):
            return
        self._st_scale_now = factor
        self.after(600, lambda: self._st_set_scale(factor))

    def _st_set_scale(self, factor):
        try:
            self.update_idletasks()
            geo = self.wm_geometry()
            ctk.set_widget_scaling(factor)
            self.after(150, lambda: self.wm_geometry(geo))
        except Exception as e:
            print(f"[Settings] could not change interface size: {e}")

    # ------------------------------------------------------------------ live appliers: drawing
    def _st_apply_pen_color(self, v):
        if str(getattr(self, "pen_color", "")).lower() == v:
            return
        fn = getattr(self, "set_pen_color", None)
        if callable(fn):
            fn(v)
        else:
            self.pen_color = v

    def _st_apply_pen_size(self, v):
        self.tool_size = v
        sl = getattr(self, "size_slider", None)
        if sl is not None:
            sl.set(v)
        fn = getattr(self, "update_tool_size", None)
        if callable(fn):
            fn(v)

    def _st_apply_eraser_size(self, v):
        self.eraser_size = v

    def _st_apply_default_zoom(self, v):
        sl = getattr(self, "univ_zoom_slider", None)
        if sl is None or int(round(sl.get())) == v:
            return
        sl.set(float(v))
        self._on_zoom_slider(float(v))

    def _st_apply_scroll_speed(self, v):
        self._st_bind_scroll()

    def _st_apply_invert_scroll(self, v):
        self._st_bind_scroll()

    # ------------------------------------------------------------------ screen modes
    def _st_mode(self):
        try:
            return SCREEN_MODES.index(self.app_settings["screen_mode"]) + 1
        except ValueError:
            return 2

    def _st_apply_screen_mode(self, v):
        if getattr(self, "canvas", None) is None:
            return
        self._st_bind_scroll()
        self._st_update_scrollregion(center=True)

    def _st_viewport(self):
        c = self.canvas
        t = self._st_border_px()
        w = (c.winfo_width() if c.winfo_width() > 1 else 1480) - 2 * t
        h = (c.winfo_height() if c.winfo_height() > 1 else 760) - 2 * t
        return max(1, w), max(1, h)

    def _st_update_scrollregion(self, center=False):
        c = self.canvas
        mode = self._st_mode()
        w, h = self._st_viewport()
        if mode == 1:
            region = (CENTER_X - w / 2, 0, CENTER_X + w / 2, h)
        elif mode == 2:
            region = (0, 0, CANVAS_W, h)
        else:
            region = (0, 0, CANVAS_W, CANVAS_W)
        c.configure(scrollregion=region)
        self._st_show_scrollbars(mode)
        if center:
            self._st_center_view()

    def _st_show_scrollbars(self, mode):
        hb = getattr(self, "h_scrollbar", None)
        vb = getattr(self, "v_scrollbar", None)
        if hb is not None:
            if mode == 1:
                hb.grid_remove()
            else:
                hb.grid(row=1, column=0, sticky="ew")
        if vb is not None:
            if mode == 3:
                vb.grid(row=0, column=1, sticky="ns")
            else:
                vb.grid_remove()

    def _st_center_view(self):
        c = self.canvas
        self.update_idletasks()
        mode = self._st_mode()
        w = c.winfo_width() if c.winfo_width() > 1 else 1480
        if mode == 1:
            c.xview_moveto(0)
            c.yview_moveto(0)
        elif mode == 2:
            c.xview_moveto(max(0.0, (CENTER_X - w / 2) / CANVAS_W))
            c.yview_moveto(0)
        else:
            c.xview_moveto(max(0.0, (CENTER_X - w / 2) / CANVAS_W))
            c.yview_moveto(0)

    def _st_on_canvas_configure(self, e):
        if self._st_cfg_job is not None:
            try:
                self.after_cancel(self._st_cfg_job)
            except Exception:
                pass
        self._st_cfg_job = self.after(80, self._st_resize_refresh)

    def _st_resize_refresh(self):
        self._st_cfg_job = None
        try:
            mode = self._st_mode()
            if mode in (1, 2):
                self._st_update_scrollregion(center=(mode == 1))
        except Exception as e:
            print(f"[Settings] resize refresh error: {e}")

    # ------------------------------------------------------------------ scrolling
    def _st_bind_scroll(self):
        c = getattr(self, "canvas", None)
        if c is None:
            return
        c.bind("<MouseWheel>", self._st_wheel)
        c.bind("<Shift-MouseWheel>", self._st_shift_wheel)
        if not self._st_cfg_bound:
            c.bind("<Configure>", self._st_on_canvas_configure, add="+")
            self._st_cfg_bound = True

    def _st_scroll_factor(self):
        f = self.app_settings["scroll_speed"] / 100.0
        return -f if self.app_settings["invert_scroll"] else f

    def _st_wheel(self, e):
        mode = self._st_mode()
        if mode == 1:
            return
        f = self._st_scroll_factor()
        if mode == 2:
            self._st_scroll_h(e, f)
        else:
            self.canvas.yview_moveto(
                self.canvas.yview()[0] + SCROLL_Y_DIR * f * e.delta / 120 * 40 / CANVAS_W)

    def _st_shift_wheel(self, e):
        if self._st_mode() != 1:
            self._st_scroll_h(e, self._st_scroll_factor())

    def _st_scroll_h(self, e, f):
        view = self.canvas.xview
        view("moveto", max(0, view()[0] - f * e.delta / 120 * 40 / CANVAS_W))

    # ------------------------------------------------------------------ projects
    def _st_apply_projects_folder(self, v):
        target = v if v else self._st_default_projects_dir
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as e:
            print(f"[Settings] projects folder '{target}' is not usable, using default: {e}")
            target = self._st_default_projects_dir
            os.makedirs(target, exist_ok=True)
        _sp.PROJECTS_DIR = target

    def _st_apply_autosave_enabled(self, v):
        if not v:
            job = getattr(self, "_autosave_job", None)
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                self._autosave_job = None

    def _st_wrap_autosave(self):
        orig = getattr(self, "_schedule_autosave", None)
        if not callable(orig):
            return

        def schedule(*a, **k):
            if not self.app_settings.get("autosave_enabled", True):
                return None
            minutes = self.app_settings.get("autosave_minutes", 0)
            if minutes <= 0:
                return orig(*a, **k)
            if getattr(self, "_autosave_job", None):
                return None
            self._autosave_job = self.after(minutes * 60000, self._autosave)
            return None

        self._schedule_autosave = schedule

    def _st_wrap_project_io(self):
        orig_auto = getattr(self, "autosave_project", None)
        if callable(orig_auto):
            def autosave_project(*a, **k):
                self._st_backup_project(AUTOSAVE_GAP_SECONDS)
                return orig_auto(*a, **k)
            self.autosave_project = autosave_project

        orig_save = getattr(self, "save_project", None)
        if callable(orig_save):
            def save_project(*a, **k):
                self._st_backup_project(MANUAL_SAVE_GAP_SECONDS)
                return orig_save(*a, **k)
            self.save_project = save_project

    def _st_backup_project(self, min_gap):
        try:
            keep = self.app_settings.get("backup_copies", 0)
            safe = getattr(self, "current_project", None)
            if keep <= 0 or not safe:
                return
            d, p = self._project_paths(safe)
            if not os.path.isfile(p) or os.path.getsize(p) == 0:
                return
            now = time.time()
            if now - self._st_last_backup.get(safe, 0) < min_gap:
                return
            bdir = os.path.join(d, "backups")
            os.makedirs(bdir, exist_ok=True)
            name = time.strftime("enclosure_%Y%m%d_%H%M%S.json")
            shutil.copy2(p, os.path.join(bdir, name))
            self._st_last_backup[safe] = now
            files = sorted(f for f in os.listdir(bdir) if f.endswith(".json"))
            for old in files[:-keep]:
                os.remove(os.path.join(bdir, old))
        except Exception as e:
            print(f"[Settings] backup skipped: {e}")

    def _st_open_last_project(self):
        name = self.app_settings.get("last_project", "")
        if not name or getattr(self, "current_project", None):
            return
        try:
            _, p = self._project_paths(name)
            if os.path.isfile(p) and os.path.getsize(p) > 0:
                self.open_project(name)
                if hasattr(self, "toast"):
                    self.toast(f"📂 Opened last project: {name}")
        except Exception as e:
            print(f"[Settings] could not open last project: {e}")

    # ------------------------------------------------------------------ wrappers
    def _st_wrap_toast(self):
        orig = getattr(self, "toast", None)
        if not callable(orig):
            return

        def toast(*a, **k):
            if self.app_settings.get("toast_enabled", True) and not self._st_quiet:
                return orig(*a, **k)

        self.toast = toast

    def _st_wrap_new_project(self):
        orig = getattr(self, "new_project", None)
        if not callable(orig):
            return

        def new_project(*a, **k):
            if self.app_settings.get("confirm_new_project"):
                if self.app_settings.get("autosave_enabled", True):
                    msg = "Start a new project? Your current project stays in the Saved Projects list."
                else:
                    msg = "Start a new project? Changes you have not saved will be lost."
                if not messagebox.askyesno("Start new project", msg, parent=self):
                    return None
            return orig(*a, **k)

        self.new_project = new_project

    def _st_on_close_request(self):
        if self.app_settings.get("confirm_exit") and not messagebox.askyesno(
                "Close", "Close Red Enclosure?", parent=self):
            return
        job = getattr(self, "_autosave_job", None)
        if job and self.app_settings.get("autosave_enabled", True):
            try:
                self.after_cancel(job)
            except Exception:
                pass
            self._autosave_job = None
            try:
                self._autosave()
            except Exception as e:
                print(f"[Settings] final auto-save failed: {e}")
        if self._st_save_job is not None:
            self._st_save_now()
        self.destroy()

    # ------------------------------------------------------------------ background tick
    def _st_tick(self):
        try:
            limit = self.app_settings["undo_limit"]
            low = self.app_settings["low_memory"]
            if low:
                limit = min(limit, LOW_MEMORY_UNDO_CAP)
            stack = getattr(self, "undo_stack", None)
            if isinstance(stack, list) and len(stack) > limit:
                del stack[:len(stack) - limit]

            self._st_tick_n += 1
            if low and self._st_tick_n % 15 == 0:
                gc.collect()

            cur = getattr(self, "current_project", None)
            if cur and cur != self.app_settings.get("last_project"):
                self.app_settings["last_project"] = cur
                self._st_schedule_save()
        except Exception as e:
            print(f"[Settings] tick error: {e}")
        finally:
            try:
                self.after(2000, self._st_tick)
            except Exception:
                pass
