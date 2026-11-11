#!/usr/bin/env python3
"""
Android Touch & Hardware Button Test Window (part 1 of 2)
========================================================
Provides the interactive TouchTestWindow for validating touchscreen grid
coverage, multi-touch points, and hardware buttons (Volume, Power, Home).
"""
import queue
import re
import threading

try:
    import customtkinter as ctk
    import tkinter as tk
    HAVE_GUI = True
except Exception:
    HAVE_GUI = False

from phone_diag_parsers import parse_input_devices

BaseTop = ctk.CTkToplevel if HAVE_GUI else object


class TouchTestWindow(BaseTop):
    WANTED = [("KEY_VOLUMEUP", "Volume Up"), ("KEY_VOLUMEDOWN", "Volume Down"), ("KEY_POWER", "Power button"),
              ("KEY_HOMEPAGE", "Home"), ("KEY_BACK", "Back"), ("KEY_MENU", "Menu"), ("KEY_APPSELECT", "Recents")]
    EVENT_RE = re.compile(r"^(?:(/dev/input/\S+):\s+)?(EV_\w+)\s+(\w+)\s+(\S+)$")

    def __init__(self, app, adb, serial, on_result):
        if HAVE_GUI:
            super().__init__(master=app)
        self.title("Touch & Button Test")
        self.attributes("-topmost", True)
        self.adb, self.serial, self.on_result = adb, serial, on_result
        self.proc, self._stop = None, False
        self.lines = queue.Queue()
        self.cur, self.slots, self.max_fingers, self.touched, self.pressed = 0, {}, 0, set(), set()
        self.protocol("WM_DELETE_WINDOW", self._close)

        devs = parse_input_devices(adb.shell(serial, "getevent -lp", 20))
        touch = [d for d in devs if d["max_x"] and d["max_y"]]
        if not touch:
            ctk.CTkLabel(self, text="No touchscreen input device could be read on this phone.\n"
                                    "(Some phones block 'getevent' for USB debugging.)",
                         font=ctk.CTkFont(size=13)).pack(padx=30, pady=30)
            self.geometry("430x150")
            self.touch = None
            return
        self.touch = max(touch, key=lambda d: (d["max_x"] + 1) * (d["max_y"] + 1))
        self.w_res, self.h_res = self.touch["max_x"] + 1, self.touch["max_y"] + 1
        cw = 300
        ch = int(cw * self.h_res / self.w_res)
        if ch > 640:
            ch = 640
            cw = int(ch * self.w_res / self.h_res)
        self.cw, self.ch, self.cols = cw, ch, 8
        self.rows = max(6, int(round(self.cols * self.h_res / self.w_res)))

        left = ctk.CTkFrame(self, fg_color="transparent")
        left.pack(side="left", padx=12, pady=12)
        self.canvas = tk.Canvas(left, width=cw, height=ch, bg="#111111", highlightthickness=1, highlightbackground="#555555")
        self.canvas.pack()
        self.cells = {}
        for c in range(self.cols):
            for r in range(self.rows):
                x0, y0 = c * cw / self.cols, r * ch / self.rows
                self.cells[(c, r)] = self.canvas.create_rectangle(x0, y0, x0 + cw / self.cols, y0 + ch / self.rows,
                                                                   outline="#2a2a2a", fill="#161616")
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.pack(side="left", fill="y", padx=(0, 12), pady=12)
        ctk.CTkLabel(right, text="Touch test", font=ctk.CTkFont(size=15, weight="bold"), text_color="#e0a070").pack(anchor="w")
        ctk.CTkLabel(right, justify="left", wraplength=260, text_color="#9aa0a6",
                     text="Drag a finger over the whole phone screen until every box turns green. "
                          "Then try 2, 3 and 5 fingers at once. Red boxes after 'Finish' = zones that never responded.").pack(anchor="w", pady=(2, 8))
        self.info = ctk.CTkLabel(right, text="Coverage 0 %   Fingers now 0   Max 0", font=ctk.CTkFont(size=12, weight="bold"))
        self.info.pack(anchor="w", pady=4)
        ctk.CTkLabel(right, text="Hardware buttons", font=ctk.CTkFont(size=14, weight="bold"), text_color="#e0a070").pack(anchor="w", pady=(12, 2))
        present = set().union(*(d["keys"] for d in devs))
        self.btn_labels = {}
        for key, label in self.WANTED:
            if key in present:
                lbl = ctk.CTkLabel(right, text=f"○ {label}  (press it)", anchor="w")
                lbl.pack(anchor="w")
                self.btn_labels[key] = (lbl, label)
        if not self.btn_labels:
            ctk.CTkLabel(right, text="No button events readable", text_color="#7f8c8d").pack(anchor="w")
        ctk.CTkLabel(right, text="Pressing Power may turn the screen off - press it again to continue.",
                     wraplength=260, justify="left", text_color="#7f8c8d", font=ctk.CTkFont(size=10)).pack(anchor="w", pady=(4, 10))
        ctk.CTkButton(right, text="Finish (mark untouched zones)", command=self._finish).pack(fill="x", pady=3)
        ctk.CTkButton(right, text="Reset", fg_color="#555555", command=self._reset).pack(fill="x", pady=3)
        ctk.CTkButton(right, text="Close and save result", fg_color="#8B4513", hover_color="#6B3410", command=self._close).pack(fill="x", pady=3)

        threading.Thread(target=self._reader, daemon=True).start()
        self.after(40, self._poll)

    def _reader(self):
        try:
            self.proc = self.adb.popen(["-s", self.serial, "shell", "getevent", "-l"])
            for raw in iter(self.proc.stdout.readline, b""):
                if self._stop:
                    break
                self.lines.put(raw.decode("utf-8", "replace").strip())
        except Exception as e:
            self.lines.put(f"!ERR {e}")

    def _poll(self):
        if self._stop:
            return
        for _ in range(400):
            try:
                line = self.lines.get_nowait()
            except queue.Empty:
                break
            self._handle(line)
        self.after(40, self._poll)

    def _handle(self, line):
        m = self.EVENT_RE.match(line)
        if not m:
            return
        dev, ev, code, val = m.groups()
        if ev == "EV_KEY" and code in self.btn_labels and val.upper() == "DOWN":
            self.pressed.add(code)
            lbl, label = self.btn_labels[code]
            lbl.configure(text=f"✅ {label}", text_color="#2ecc71")
            return
        if dev and dev != self.touch["path"]:
            return
        if ev == "EV_ABS":
            try:
                v = int(val, 16)
            except ValueError:
                return
            if code == "ABS_MT_SLOT":
                self.cur = v
            elif code == "ABS_MT_TRACKING_ID":
                if v == 0xFFFFFFFF:
                    self.slots.pop(self.cur, None)
                else:
                    self.slots.setdefault(self.cur, {})["id"] = v
            elif code == "ABS_MT_POSITION_X":
                self.slots.setdefault(self.cur, {})["x"] = v
            elif code == "ABS_MT_POSITION_Y":
                self.slots.setdefault(self.cur, {})["y"] = v
        elif ev == "EV_SYN" and code == "SYN_REPORT":
            self._frame()

    def _frame(self):
        active = [s for s in self.slots.values() if "x" in s and "y" in s]
        self.max_fingers = max(self.max_fingers, len(active))
        self.canvas.delete("dot")
        for s in active:
            cx = min(self.cols - 1, max(0, int(s["x"] / self.w_res * self.cols)))
            cy = min(self.rows - 1, max(0, int(s["y"] / self.h_res * self.rows)))
            if (cx, cy) not in self.touched:
                self.touched.add((cx, cy))
                self.canvas.itemconfigure(self.cells[(cx, cy)], fill="#1e8449", outline="#2ecc71")
            px, py = s["x"] / self.w_res * self.cw, s["y"] / self.h_res * self.ch
            self.canvas.create_oval(px - 7, py - 7, px + 7, py + 7, fill="#f1c40f", outline="", tags="dot")
        self.info.configure(text=f"Coverage {self._pct():.0f} %   Fingers now {len(active)}   Max {self.max_fingers}")

    def _pct(self):
        return len(self.touched) * 100 / (self.cols * self.rows)

    def _finish(self):
        for key, item in self.cells.items():
            if key not in self.touched:
                self.canvas.itemconfigure(item, fill="#4a1515", outline="#e74c3c")

    def _reset(self):
        self.touched.clear()
        self.max_fingers = 0
        for item in self.cells.values():
            self.canvas.itemconfigure(item, fill="#161616", outline="#2a2a2a")
        self.info.configure(text="Coverage 0 %   Fingers now 0   Max 0")

    def _close(self):
        self._stop = True
        if self.touch is not None and (self.touched or self.pressed):
            pct = self._pct()
            st = "ok" if pct >= 98 else ("warn" if pct >= 85 else "bad")
            note = "" if pct >= 98 else "  (if you did not sweep the whole screen, repeat the test)"
            result = {"Touch screen": (f"{pct:.0f} % of screen zones responded, up to {self.max_fingers} fingers at once{note}", st)}
            if self.btn_labels:
                parts = [f"{label} {'✅' if key in self.pressed else '❌ not pressed'}" for key, (_, label) in self.btn_labels.items()]
                missing = [k for k in self.btn_labels if k not in self.pressed]
                result["Hardware buttons"] = (", ".join(parts), "ok" if not missing else "warn")
            self.on_result(result)
        try:
            if self.proc:
                self.proc.terminate()
        except Exception:
            pass
        threading.Thread(target=lambda: self.adb.shell(self.serial, "pkill getevent", 8), daemon=True).start()
        self.destroy()