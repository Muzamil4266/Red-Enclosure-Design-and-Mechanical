#!/usr/bin/env python3
"""
Android Phone Diagnostics (standalone & mixin) - Shoby Red Project
==================================================================
Main module containing GUI windows, integration mixins, and CLI entry points.
(part 2 of 2 - keeps original name and re-exports all sub-modules for full project compatibility)
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

try:
    import customtkinter as ctk
    from tkinter import filedialog, messagebox
    HAVE_GUI = True
except Exception:  # CLI mode still works without GUI libraries
    HAVE_GUI = False

# Re-export all sub-module functions and constants for full backwards compatibility
from phone_diag_adb import (  # noqa: F401
    Adb, CREATE_NO_WINDOW, SCRIPT_DIR, CONFIG_FILE, PLATFORM_TOOLS_URLS,
    load_config, save_config, find_adb, platform_tools_url, adb_name,
    extract_platform_tools, download_platform_tools, cli_download_adb
)
from phone_diag_parsers import (  # noqa: F401
    STATUS_COLORS, STATUS_ICONS, ANDROID_BY_API, ANDROID_RELEASE, BATT_STATUS,
    BATT_HEALTH, FEATURES, CHECK_BY_HAND, CYCLES_PER_DAY, num, first, R,
    luhn_ok, months_since, months_between, fmt_gb, round_up_class, fmt_age,
    short_age, parse_devices, parse_getprop, parse_kv, parse_sysfs_blocks,
    parse_input_devices, parse_features, decode_parcel_string
)
from phone_diag_sections import (  # noqa: F401
    SYSFS_FILES, BATT_SCRIPT, section_identity, section_battery, section_display,
    section_touch, section_hardware, find_imeis, section_sim, mask_account,
    section_security, section_usage
)
from phone_diag_analysis import (  # noqa: F401
    build_assessment, estimate_age, collect_report, report_to_text
)
from phone_diag_touch import TouchTestWindow  # noqa: F401

BaseTop = ctk.CTkToplevel if HAVE_GUI else object

SETUP_TEXT = (
    "1. adb (Android platform-tools, about 10 MB) is downloaded automatically from Google the first\n"
    "   time you start the app. If that failed, press 'Download adb'.\n\n"
    "2. On the phone: Settings > About phone > tap 'Build number' 7 times.\n"
    "   Then Settings > System > Developer options > turn on 'USB debugging'.\n\n"
    "3. Plug in the cable (use a data cable, not charge-only). Unlock the phone and tap 'Allow'\n"
    "   on the 'Allow USB debugging?' prompt (tick 'Always allow').\n\n"
    "4. If Windows does not see the phone, install the maker's USB driver (Samsung, Xiaomi...)\n"
    "   or the Google USB driver, and set the USB mode to 'File transfer'."
)


class PhoneDiagApp(BaseTop):
    def __init__(self, master=None):
        if HAVE_GUI:
            super().__init__(master=master)
        self.title("Android Phone Diagnostics")
        self.geometry("1080x780")
        self.minsize(860, 560)
        self.adb, self.q = None, queue.Queue()
        self.devices, self._dev_map = [], {}
        self.scanning, self.downloading, self._closing = False, False, False
        self.report, self.interactive = None, {}
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._init_adb()
        self.after(100, self._poll_queue)
        threading.Thread(target=self._device_loop, daemon=True).start()
        if self.adb is None:
            self.after(700, self._offer_download)

    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(top, text="📱 Android Phone Diagnostics", font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        ctk.CTkButton(top, text="Setup help", width=90, fg_color="#555555", command=self._show_help).pack(side="right", padx=3)
        ctk.CTkButton(top, text="Locate adb.exe", width=110, fg_color="#555555", command=self._locate_adb).pack(side="right", padx=3)
        self.dl_btn = ctk.CTkButton(top, text="⬇ Download adb", width=120, fg_color="#555555", command=self.start_download)
        self.dl_btn.pack(side="right", padx=3)
        self.adb_lbl = ctk.CTkLabel(top, text="", text_color="#9aa0a6", font=ctk.CTkFont(size=11))
        self.adb_lbl.pack(side="right", padx=8)

        dev = ctk.CTkFrame(self, fg_color="#1b1b1b", corner_radius=8)
        dev.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(dev, text="Phone:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(12, 6), pady=10)
        self.dev_menu = ctk.CTkOptionMenu(dev, values=["No phone connected"], width=300, command=lambda v: self._update_buttons())
        self.dev_menu.pack(side="left", padx=4)
        self.dev_status = ctk.CTkLabel(dev, text="", font=ctk.CTkFont(size=12), wraplength=560, justify="left")
        self.dev_status.pack(side="left", padx=10)

        act = ctk.CTkFrame(self, fg_color="transparent")
        act.pack(fill="x", padx=12, pady=4)
        self.scan_btn = ctk.CTkButton(act, text="🔍 Scan phone", width=150, height=36, fg_color="#27ae60", hover_color="#1e8449",
                                      font=ctk.CTkFont(size=14, weight="bold"), command=self.start_scan)
        self.scan_btn.pack(side="left", padx=3)
        self.touch_btn = ctk.CTkButton(act, text="👆 Touch & Button Test", width=190, height=36, fg_color="#8B4513",
                                       hover_color="#6B3410", command=self.open_touch_test)
        self.touch_btn.pack(side="left", padx=3)
        self.imei_btn = ctk.CTkButton(act, text="📱 Show IMEI on phone", width=170, height=36, fg_color="#8B4513",
                                      hover_color="#6B3410", command=self.show_imei_on_phone)
        self.imei_btn.pack(side="left", padx=3)
        self.save_btn = ctk.CTkButton(act, text="💾 Save report", width=130, height=36, command=self.save_report)
        self.save_btn.pack(side="left", padx=3)
        self.copy_btn = ctk.CTkButton(act, text="📋 Copy", width=90, height=36, command=self.copy_report)
        self.copy_btn.pack(side="left", padx=3)

        self.progress = ctk.CTkProgressBar(self, mode="indeterminate")
        self.status_lbl = ctk.CTkLabel(self, text="", text_color="#9aa0a6", font=ctk.CTkFont(size=11))
        self.status_lbl.pack(fill="x", padx=14)
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=8, pady=(4, 10))
        self._welcome()
        self._update_buttons()

    def _welcome(self):
        for w in self.body.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.body, justify="left", anchor="w", font=ctk.CTkFont(size=13), text_color="#cfd3d6",
                     text="1. Plug the phone into the PC with a data cable.\n"
                          "2. Turn on USB debugging on the phone and tap 'Allow' when asked (see 'Setup help').\n"
                          "3. Press 'Scan phone'. The full technical sheet appears here.\n\n"
                          "Nothing is installed on or changed on the phone.").pack(anchor="w", padx=14, pady=20)

    def _init_adb(self):
        path = find_adb()
        if path:
            self.adb = Adb(path)
            self.adb_lbl.configure(text=f"adb: {path}")
            threading.Thread(target=self.adb.start_server, daemon=True).start()
        else:
            self.adb_lbl.configure(text="adb not found", text_color="#e74c3c")
            self.dev_status.configure(text="adb (Android platform-tools) was not found. Press '⬇ Download adb' or 'Locate adb.exe'.",
                                      text_color="#e74c3c")

    def _locate_adb(self):
        p = filedialog.askopenfilename(title="Select adb.exe", filetypes=[("adb", "adb.exe adb"), ("All files", "*.*")])
        if p and os.path.isfile(p):
            cfg = load_config()
            cfg["adb_path"] = p
            save_config(cfg)
            self.adb = Adb(p)
            self.adb_lbl.configure(text=f"adb: {p}", text_color="#9aa0a6")
            threading.Thread(target=self.adb.start_server, daemon=True).start()
            self._update_buttons()

    def _show_help(self):
        messagebox.showinfo("Setup help", SETUP_TEXT, parent=self)

    def _offer_download(self):
        if self._closing or self.adb or self.downloading:
            return
        hint = "adb is missing - press '⬇ Download adb' to get it from Google (about 10 MB)."
        if load_config().get("adb_download_declined"):
            self.status_lbl.configure(text=hint)
            return
        ok = messagebox.askyesno(
            "Download adb?",
            "This tool talks to the phone through adb (Android platform-tools).\n\n"
            "Download it now from Google (dl.google.com, about 10 MB) into:\n"
            f"{os.path.join(SCRIPT_DIR, 'platform-tools')}\n\n"
            "Nothing is installed on the phone. You are asked only once.",
            parent=self)
        if ok:
            self.start_download()
        else:
            cfg = load_config()
            cfg["adb_download_declined"] = True
            save_config(cfg)
            self.status_lbl.configure(text=hint)

    def start_download(self):
        if self.downloading or self.scanning or self._closing:
            return
        try:
            self._show_progress("determinate")
        except Exception as e:
            self.status_lbl.configure(text=f"Could not start the download: {e}")
            return
        self.downloading = True
        cfg = load_config()
        if cfg.pop("adb_download_declined", None):
            save_config(cfg)
        self.status_lbl.configure(text="Starting adb download...")
        self.dev_status.configure(text="Downloading adb from Google...", text_color="#f1c40f")
        self._update_buttons()
        threading.Thread(target=self._download_worker, args=(self.adb,), daemon=True).start()

    def _download_worker(self, old_adb):
        try:
            if old_adb:
                old_adb.run(["kill-server"], 10)
            path = download_platform_tools(lambda pct, msg: self.q.put(("dl_progress", (pct, msg))))
            self.q.put(("dl_done", path))
        except RuntimeError as e:
            self.q.put(("dl_error", str(e)))
        except Exception as e:
            self.q.put(("dl_error", f"Unexpected error while downloading adb: {e}"))

    def _show_progress(self, mode):
        self.progress.configure(mode=mode)
        if mode == "determinate":
            self.progress.set(0)
        self.progress.pack(fill="x", padx=14, pady=(2, 0), after=self.status_lbl)

    def _finish_download(self):
        self.downloading = False
        self.progress.pack_forget()
        self.progress.configure(mode="indeterminate")

    def _device_loop(self):
        while not self._closing:
            if self.adb and not self.downloading:
                try:
                    self.q.put(("devices", self.adb.devices()))
                except Exception:
                    pass
            time.sleep(2.5)

    def selected_serial(self):
        return self._dev_map.get(self.dev_menu.get())

    def selected_device(self):
        s = self.selected_serial()
        return next((d for d in self.devices if d["serial"] == s), None)

    def _apply_devices(self, devs):
        self.devices = devs
        cur_serial = self.selected_serial()
        self._dev_map = {}
        labels = []
        for d in devs:
            lab = f"{d['model'] or 'Android device'}  ({d['serial']})  [{d['state']}]"
            self._dev_map[lab] = d["serial"]
            labels.append(lab)
        if not labels:
            self.dev_menu.configure(values=["No phone connected"])
            self.dev_menu.set("No phone connected")
        else:
            self.dev_menu.configure(values=labels)
            keep = next((l for l, s in self._dev_map.items() if s == cur_serial), labels[0])
            self.dev_menu.set(keep)
        self._update_buttons()

    def _update_buttons(self):
        d = self.selected_device()
        ready = bool(d and d["state"] == "device")
        msgs = {
            None: "Plug in the phone with a USB cable and turn on USB debugging.",
            "device": "Phone connected and authorized - ready to scan.",
            "unauthorized": "Unlock the phone and tap 'Allow' on the USB debugging prompt.",
            "offline": "Phone is offline - unplug the cable, plug it in again, unlock the phone.",
            "no permissions": "No permission to use the phone - install the maker's USB driver / run adb as administrator.",
        }
        if self.adb and not self.downloading:
            st = d["state"] if d else None
            self.dev_status.configure(text=msgs.get(st, f"State: {st}"),
                                      text_color="#2ecc71" if ready else "#f1c40f")
        busy = self.scanning or self.downloading
        free = "normal" if (ready and not busy) else "disabled"
        for b in (self.scan_btn, self.touch_btn, self.imei_btn):
            b.configure(state=free)
        self.dl_btn.configure(state="disabled" if busy else "normal")
        has = "normal" if self.report else "disabled"
        self.save_btn.configure(state=has)
        self.copy_btn.configure(state=has)

    def start_scan(self):
        serial = self.selected_serial()
        if not serial or self.scanning or self.downloading or not self.adb:
            return
        self._show_progress("indeterminate")
        self.scanning = True
        self.interactive = {}
        self.progress.start()
        self.status_lbl.configure(text="Scanning...")
        self._update_buttons()
        adb = self.adb

        def worker():
            try:
                rep = collect_report(lambda cmd, timeout=25: adb.shell(serial, cmd, timeout), serial,
                                     lambda m: self.q.put(("progress", m)))
                self.q.put(("done", rep))
            except Exception as e:
                self.q.put(("error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "devices":
                    self._apply_devices(payload)
                elif kind == "progress":
                    self.status_lbl.configure(text=payload)
                elif kind == "done":
                    self._finish_scan()
                    self.report = payload
                    self._render()
                    self.status_lbl.configure(text=f"Scan finished {payload['generated']}")
                    self._update_buttons()
                elif kind == "error":
                    self._finish_scan()
                    self.status_lbl.configure(text="Scan failed.")
                    messagebox.showerror("Scan failed", payload, parent=self)
                    self._update_buttons()
                elif kind == "info":
                    messagebox.showinfo(payload[0], payload[1], parent=self)
                elif kind == "dl_progress":
                    pct, msg = payload
                    self.progress.set(max(0.0, min(1.0, pct / 100)))
                    self.status_lbl.configure(text=msg + (f"  ({pct} %)" if 0 < pct < 100 else ""))
                elif kind == "dl_done":
                    self._finish_download()
                    self.adb = Adb(payload)
                    self.adb_lbl.configure(text=f"adb: {payload}", text_color="#9aa0a6")
                    threading.Thread(target=self.adb.start_server, daemon=True).start()
                    self.status_lbl.configure(text="adb downloaded and ready. Plug in the phone and turn on USB debugging.")
                    self._update_buttons()
                elif kind == "dl_error":
                    self._finish_download()
                    self.status_lbl.configure(text="adb download failed.")
                    if not self.adb:
                        self.dev_status.configure(text="adb is still missing. Press '⬇ Download adb' to try again.",
                                                  text_color="#e74c3c")
                    self._update_buttons()
                    messagebox.showerror("adb download failed", payload, parent=self)
        except queue.Empty:
            pass
        if not self._closing:
            self.after(120, self._poll_queue)

    def _finish_scan(self):
        self.scanning = False
        self.progress.stop()
        self.progress.pack_forget()

    def _extra_sections(self):
        if not self.interactive:
            return []
        return [{"title": "Interactive tests (done by you)", "icon": "🧪",
                 "rows": [R(k, v[0], v[1]) for k, v in self.interactive.items()]}]

    def _render(self):
        for w in self.body.winfo_children():
            w.destroy()
        rep = self.report
        if not rep:
            self._welcome()
            return
        ctk.CTkLabel(self.body, text=rep["name"], font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", padx=10, pady=(4, 0))
        if rep.get("age"):
            ctk.CTkLabel(self.body, text="⏳ " + rep["age"]["line"], text_color="#f1c40f", anchor="w",
                         font=ctk.CTkFont(size=17, weight="bold")).pack(anchor="w", padx=10, pady=(2, 0))
            ctk.CTkLabel(self.body, text=rep["age"]["sub"], text_color="#9aa0a6", anchor="w", justify="left",
                         wraplength=900, font=ctk.CTkFont(size=11)).pack(anchor="w", padx=10, pady=(0, 2))
        tiles = ctk.CTkFrame(self.body, fg_color="transparent")
        tiles.pack(fill="x", padx=4, pady=6)
        for i, t in enumerate(rep["headline"]):
            tiles.grid_columnconfigure(i, weight=1)
            f = ctk.CTkFrame(tiles, fg_color="#1e1e1e", corner_radius=8)
            f.grid(row=0, column=i, padx=4, sticky="nsew")
            ctk.CTkLabel(f, text=t["label"], text_color="#9aa0a6", font=ctk.CTkFont(size=11)).pack(pady=(8, 0))
            ctk.CTkLabel(f, text=t["value"], text_color=STATUS_COLORS[t["status"]],
                         font=ctk.CTkFont(size=22, weight="bold")).pack(pady=(0, 8))
        v = rep["verdict"]
        card = ctk.CTkFrame(self.body, fg_color="#1e1e1e", corner_radius=8)
        card.pack(fill="x", padx=6, pady=6)
        ctk.CTkLabel(card, text=f"{STATUS_ICONS[v['status']]} {v['text']}", text_color=STATUS_COLORS[v["status"]],
                     font=ctk.CTkFont(size=14, weight="bold"), wraplength=900, justify="left").pack(anchor="w", padx=12, pady=(10, 4))
        for it in rep["assessment"]:
            ctk.CTkLabel(card, text=f"{STATUS_ICONS[it['status']]} {it['text']}", text_color=STATUS_COLORS[it["status"]],
                         anchor="w", justify="left", wraplength=900).pack(anchor="w", padx=16)
        ctk.CTkLabel(card, text="Heuristic guide only: the phone reports its own numbers.", text_color="#7f8c8d",
                     font=ctk.CTkFont(size=10)).pack(anchor="w", padx=12, pady=(6, 8))
        for sec in rep["sections"] + self._extra_sections():
            self._card(sec)

    def _card(self, sec):
        card = ctk.CTkFrame(self.body, fg_color="#1e1e1e", corner_radius=8)
        card.pack(fill="x", padx=6, pady=6)
        ctk.CTkLabel(card, text=f"{sec['icon']} {sec['title']}", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#e0a070").pack(anchor="w", padx=12, pady=(8, 4))
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=12, pady=(0, 8))
        grid.grid_columnconfigure(1, weight=1)
        for i, r in enumerate(sec["rows"]):
            ctk.CTkLabel(grid, text=r["label"], anchor="w", text_color="#9aa0a6", width=230,
                         font=ctk.CTkFont(size=12)).grid(row=i, column=0, sticky="nw", pady=1)
            icon = STATUS_ICONS.get(r["status"], "")
            ctk.CTkLabel(grid, text=(icon + " " if icon else "") + r["value"], anchor="w", justify="left", wraplength=640,
                         text_color=STATUS_COLORS.get(r["status"], "#e6e6e6"),
                         font=ctk.CTkFont(size=12)).grid(row=i, column=1, sticky="w", padx=(8, 0), pady=1)

    def open_touch_test(self):
        serial = self.selected_serial()
        if not serial or not self.adb:
            return

        def on_result(res):
            self.interactive.update(res)
            if self.report:
                self._render()

        TouchTestWindow(self, self.adb, serial, on_result)

    def show_imei_on_phone(self):
        serial = self.selected_serial()
        if not serial or not self.adb:
            return
        adb = self.adb

        def worker():
            out = adb.shell(serial, "am start -a android.intent.action.CALL -d 'tel:*%2306%23'", 15)
            if "Exception" in out or "Error" in out or "denied" in out.lower():
                out = adb.shell(serial, "am start -a android.intent.action.DIAL -d 'tel:*%2306%23'", 15)
            self.q.put(("info", ("IMEI", "Look at the phone screen: the IMEI should be displayed.\n"
                                         "If only the dialer opened with *#06# typed in, press the green call button.")))

        threading.Thread(target=worker, daemon=True).start()

    def _full_text(self):
        return report_to_text(self.report, self._extra_sections())

    def save_report(self):
        if not self.report:
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", self.report["name"])
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".txt", initialfile=f"phone_report_{base}_{stamp}",
                                            filetypes=[("Text report", "*.txt"), ("JSON data", "*.json")])
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                data = dict(self.report)
                data["sections"] = self.report["sections"] + self._extra_sections()
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._full_text())
            self.status_lbl.configure(text=f"Saved: {path}  (contains serial / IMEI - do not post it publicly)")
        except OSError as e:
            messagebox.showerror("Save failed", str(e), parent=self)

    def copy_report(self):
        if self.report:
            self.clipboard_clear()
            self.clipboard_append(self._full_text())
            self.status_lbl.configure(text="Report copied to the clipboard.")

    def _on_close(self):
        self._closing = True
        self.destroy()


class PhoneDiagnosticsMixin:
    """Mixin class for integrating Android Phone Diagnostics into CircuitBuilderApp."""

    def init_phone_diagnostics(self):
        self._phone_diag_win = None

    def open_phone_diagnostics(self):
        """Launches the Android Phone Diagnostics window."""
        if getattr(self, "_phone_diag_win", None) and self._phone_diag_win.winfo_exists():
            self._phone_diag_win.lift()
            self._phone_diag_win.focus_force()
            return
        win = PhoneDiagApp(master=self)
        win.attributes("-topmost", True)
        self._phone_diag_win = win


def run_cli():
    path = find_adb()
    if not path:
        if "--no-download" in sys.argv:
            print("adb was not found and --no-download was given. Install Android platform-tools "
                  "(see the top of this file) or run without --no-download.")
            return 2
        print("adb was not found - downloading Android platform-tools from Google (about 10 MB)...", file=sys.stderr)
        path = cli_download_adb()
        if not path:
            return 2
    adb = Adb(path)
    adb.start_server()
    devs = [d for d in adb.devices() if d["state"] == "device"]
    if not devs:
        print("No authorized phone found. Plug it in, enable USB debugging and tap 'Allow' on the phone.")
        return 1
    d = devs[0]
    rep = collect_report(lambda cmd, timeout=25: adb.shell(d["serial"], cmd, timeout), d["serial"],
                         lambda m: print("..", m, file=sys.stderr))
    print(report_to_text(rep))
    return 0


def ensure_gui_libraries():
    if HAVE_GUI:
        return True
    try:
        import tkinter  # noqa: F401
    except Exception:
        print("tkinter (the window toolkit that ships with Python) is missing. Re-run the Python installer, choose "
              "Modify and tick 'tcl/tk and IDLE', or use --cli.")
        return False
    if os.environ.get("PHONE_DIAG_PIP_TRIED"):
        print("customtkinter still could not be loaded after installing it. Run:  pip install customtkinter   (or use --cli)")
        return False
    print("customtkinter is not installed - installing it now with pip (one time)...")
    installed = False
    for extra in ([], ["--user"]):
        try:
            if subprocess.run([sys.executable, "-m", "pip", "install"] + extra + ["customtkinter"]).returncode == 0:
                installed = True
                break
        except OSError:
            pass
    if not installed:
        print("Automatic install failed (no internet?). Run manually:  pip install customtkinter   (or use --cli)")
        return False
    env = dict(os.environ, PHONE_DIAG_PIP_TRIED="1")
    sys.exit(subprocess.run([sys.executable] + sys.argv, env=env).returncode)


if __name__ == "__main__":
    if "--cli" in sys.argv:
        sys.exit(run_cli())
    if not ensure_gui_libraries():
        sys.exit(1)
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root.withdraw()
    app = PhoneDiagApp(master=root)
    def on_standalone_close():
        app._on_close()
        root.destroy()
    app.protocol("WM_DELETE_WINDOW", on_standalone_close)
    root.mainloop()
