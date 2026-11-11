"""
Scrcpy Mirror UI & Connection Wizard (part 1 of 2)
===================================================
Manages device discovery, ADB tool verification, connection wizard steps,
wireless IP pairing, and background event queue polling.
"""
import os
import time
import queue
import collections
import atexit
import shutil
import threading
import customtkinter as ctk
import scrcpy_tools as st

HEAD_H = 28


class ScrcpyMirrorUIMixin:

    def init_mirror(self):
        self._mir_q = queue.Queue()
        self._mir_panel = None
        self._mir_body = None
        self._mir_stream_frame = None
        self._mir_tools = None
        self._mir_tools_busy = False
        self._mir_tool_text = "⏳ Verifying scrcpy & ADB installation..."
        self._mir_devs = []
        self._mir_mode = "wireless"
        self._mir_mon_on = False
        self._mir_mon_gen = 0
        self._mir_poll_job = None
        self._mir_proc = None
        self._mir_hwnd = None
        self._mir_w32 = None
        self._wizard_step = 1
        self._act_status = ""
        self._last_rendered_state = None
        self._embed_attempts = 0
        self._tools_installed = False
        self._stream_scale_w = 0.98
        self._stream_scale_h = 0.96
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._last_mir_w = 0
        self._last_mir_h = 0
        self._mir_aspect = None
        self._stream_status = ""
        self._launch_try = 1
        self._launch_dev = None
        self._launch_gen = 0
        self._mir_log = collections.deque(maxlen=40)
        self._mir_title_lbl = None
        self._mir_hook = None
        self._zoom_job = None
        atexit.register(self.mirror_stop)

    def open_mirroring(self):
        if self._mir_panel is not None and self._mir_panel.winfo_exists():
            self._mir_panel.lift()
            return

        host = self.canvas_container
        self._mir_panel = ctk.CTkFrame(host, fg_color="#181818", corner_radius=8, border_width=1, border_color="#333333")
        self._mir_panel.place(relx=0.99, rely=0.08, anchor="ne", relwidth=0.28, relheight=0.62)

        head = ctk.CTkFrame(self._mir_panel, height=HEAD_H, fg_color="#262626", corner_radius=0)
        head.pack(side="top", fill="x")
        ctk.CTkLabel(head, text="📱 Live Mirror Tool", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=8)
        ctk.CTkButton(head, text="✖", width=22, height=20, fg_color="#c0392b", command=self._mir_close_panel).pack(side="right", padx=4)

        self._mir_body = ctk.CTkFrame(self._mir_panel, fg_color="transparent")
        self._mir_body.pack(fill="both", expand=True, padx=8, pady=6)

        self._wizard_step = 1
        self._last_rendered_state = None
        self._verify_scrcpy_installation()
        self._render_wizard()
        self._mir_schedule_poll()

    def _verify_scrcpy_installation(self):
        sys_scrcpy = shutil.which("scrcpy")
        sys_adb = None
        if sys_scrcpy:
            for folder in (os.path.dirname(os.path.realpath(sys_scrcpy)), os.path.dirname(sys_scrcpy)):
                cand = os.path.join(folder, "adb" + st.EXE)
                if os.path.isfile(cand):
                    sys_adb = cand
                    break
        if not sys_adb:
            sys_adb = shutil.which("adb")

        if sys_scrcpy and sys_adb:
            self._mir_tools = {"scrcpy": [sys_scrcpy], "adb": [sys_adb]}
            self._tools_installed = True
            self._mir_tool_text = "✔ scrcpy & ADB found on System PATH!"
            self._mir_after_tools()
        else:
            if self._mir_tools is None and not self._mir_tools_busy:
                self._mir_start_tools()

    def _mir_close_panel(self):
        self.mirror_stop()
        if self._mir_panel is not None:
            try:
                self._mir_panel.destroy()
            except Exception:
                pass
        self._mir_panel = None

    def _render_wizard(self, force=False):
        if not self._mir_body or not self._mir_body.winfo_exists():
            return

        devs = self._mir_ready_devices()
        dev_name = devs[0]["serial"] if devs else ""
        current_state = (self._wizard_step, self._mir_mode, dev_name, self._act_status, self._mir_tool_text, self._tools_installed, self._stream_status)
        
        if not force and self._last_rendered_state == current_state:
            return
        self._last_rendered_state = current_state

        for w in self._mir_body.winfo_children():
            w.destroy()

        p_frame = ctk.CTkFrame(self._mir_body, fg_color="transparent")
        p_frame.pack(fill="x", pady=(0, 4))

        s1_col = "#27ae60" if self._wizard_step >= 1 else "gray40"
        s2_col = "#27ae60" if self._wizard_step >= 2 else "gray40"
        s3_col = "#27ae60" if self._wizard_step >= 3 else "gray40"

        ctk.CTkLabel(p_frame, text="1. Verification", font=ctk.CTkFont(size=9, weight="bold"), text_color=s1_col).pack(side="left", expand=True)
        ctk.CTkLabel(p_frame, text="›", font=ctk.CTkFont(size=9)).pack(side="left")
        ctk.CTkLabel(p_frame, text="2. Connect", font=ctk.CTkFont(size=9, weight="bold"), text_color=s2_col).pack(side="left", expand=True)
        ctk.CTkLabel(p_frame, text="›", font=ctk.CTkFont(size=9)).pack(side="left")
        ctk.CTkLabel(p_frame, text="3. Stream", font=ctk.CTkFont(size=9, weight="bold"), text_color=s3_col).pack(side="left", expand=True)

        if self._wizard_step == 1:
            box = ctk.CTkScrollableFrame(self._mir_body, height=220, fg_color="#202020", corner_radius=6)
            box.pack(fill="both", expand=True, pady=4)

            if self._tools_installed:
                ctk.CTkLabel(box, text="✅ scrcpy is Installed!", text_color="#2ecc71", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=6, pady=(6, 2))
                ctk.CTkLabel(box, text=self._mir_tool_text, justify="left", font=ctk.CTkFont(size=10)).pack(anchor="w", padx=6, pady=2)
                
                info_txt = (
                    "How to use Live Mirror:\n"
                    "1. Connect your phone via USB or Wi-Fi.\n"
                    "2. Turn ON 'USB Debugging' in Android Developer Options.\n"
                    "3. Click Next Step to start video mirroring.\n"
                    "4. Pinch 2 fingers on Touchpad to scale/zoom screen."
                )
                ctk.CTkLabel(box, text=info_txt, justify="left", font=ctk.CTkFont(size=9), text_color="gray70", wraplength=210).pack(anchor="w", padx=6, pady=6)
                
                ctk.CTkButton(self._mir_body, text="Next Step ➔", height=24, font=ctk.CTkFont(size=10, weight="bold"), fg_color="#27ae60", command=self._next_step).pack(anchor="e", pady=6)

            else:
                ctk.CTkLabel(box, text="❌ scrcpy is NOT Installed", text_color="#e74c3c", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=6, pady=(6, 2))
                
                guide_text = (
                    "To use Live Mirror, scrcpy must be installed on your computer.\n\n"
                    "📌 Method 1: Automatic via Command Prompt\n"
                    "Open CMD and run:\n"
                    "  winget install Genymobile.scrcpy\n\n"
                    "📌 Method 2: Manual Download\n"
                    "1. Download scrcpy zip from Github:\n"
                    "   https://github.com/Genymobile/scrcpy\n"
                    "2. Extract folder & add to System PATH.\n\n"
                    "📱 Mobile Setup:\n"
                    "• Enable 'Developer Options' on Android.\n"
                    "• Turn ON 'USB Debugging'."
                )
                ctk.CTkLabel(box, text=guide_text, justify="left", font=ctk.CTkFont(size=9), text_color="gray85", wraplength=210).pack(anchor="w", padx=6, pady=4)
                
                ctk.CTkButton(
                    self._mir_body, text="🔄 Re-check Installation", height=26,
                    font=ctk.CTkFont(size=10, weight="bold"), fg_color="#2980b9",
                    command=self._recheck_installation
                ).pack(fill="x", pady=6)

        elif self._wizard_step == 2:
            box = ctk.CTkFrame(self._mir_body, corner_radius=6)
            box.pack(fill="x", pady=2)
            
            mode_seg = ctk.CTkSegmentedButton(box, values=["Wireless Wi-Fi", "USB Cable"], height=22, font=ctk.CTkFont(size=9), command=self._on_mode_change)
            mode_seg.set("USB Cable" if self._mir_mode == "usb" else "Wireless Wi-Fi")
            mode_seg.pack(fill="x", padx=4, pady=4)

            if self._mir_mode == "usb":
                ctk.CTkLabel(box, text="1. Plug USB Cable.\n2. Turn ON USB Debugging.\n3. Allow prompt on phone.", justify="left", font=ctk.CTkFont(size=9)).pack(anchor="w", padx=6, pady=2)
            else:
                btn_tcp = ctk.CTkButton(box, text="⚡ 1. Switch USB to TCP Mode", height=22, font=ctk.CTkFont(size=9, weight="bold"), fg_color="#8e44ad", command=self._mir_do_quick_wifi)
                btn_tcp.pack(fill="x", padx=4, pady=2)

                prefs = st.load_prefs()
                ip_frame = ctk.CTkFrame(box, fg_color="transparent")
                ip_frame.pack(fill="x", padx=4, pady=2)
                
                self._ip_entry = ctk.CTkEntry(ip_frame, placeholder_text="IP:5555", height=22, font=ctk.CTkFont(size=10), width=110)
                if prefs.get("last_ip"):
                    self._ip_entry.insert(0, prefs["last_ip"] if ":" in prefs["last_ip"] else f"{prefs['last_ip']}:5555")
                self._ip_entry.pack(side="left", padx=(0, 2))
                
                ctk.CTkButton(ip_frame, text="2. Connect", height=22, font=ctk.CTkFont(size=9, weight="bold"), width=60, fg_color="#2980b9", command=lambda: self._mir_do_connect(self._ip_entry.get())).pack(side="left")

            if self._act_status:
                ctk.CTkLabel(box, text=self._act_status, wraplength=180, font=ctk.CTkFont(size=9, weight="bold")).pack(pady=2)

            if devs:
                d = devs[0]
                ctk.CTkLabel(box, text=f"✅ CONNECTED\n{d['model']}", text_color="#2ecc71", font=ctk.CTkFont(size=10, weight="bold")).pack(pady=4)
            else:
                ctk.CTkLabel(box, text="🔴 NO PHONE YET", text_color="#e74c3c", font=ctk.CTkFont(size=10, weight="bold")).pack(pady=4)

            btn_box = ctk.CTkFrame(self._mir_body, fg_color="transparent")
            btn_box.pack(fill="x", pady=6)
            ctk.CTkButton(btn_box, text="← Back", width=55, height=24, font=ctk.CTkFont(size=10), fg_color="#34495e", command=self._prev_step).pack(side="left")
            
            next_btn = ctk.CTkButton(btn_box, text="Next Step ➔", width=75, height=24, font=ctk.CTkFont(size=10, weight="bold"), fg_color="#27ae60", command=self._next_step, state="normal" if devs else "disabled")
            next_btn.pack(side="right")

        elif self._wizard_step == 3:
            devs = self._mir_ready_devices()
            if not devs:
                self._wizard_step = 2
                self._render_wizard(force=True)
                return

            box = ctk.CTkFrame(self._mir_body, corner_radius=6)
            box.pack(fill="x", pady=6)
            ctk.CTkLabel(box, text="Ready to Stream", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=6, pady=(6, 2))
            ctk.CTkLabel(box, text="Open your phone's Camera app.\nUse touchpad pinch to scale frame size.", justify="left", wraplength=180, font=ctk.CTkFont(size=9), text_color="gray70").pack(anchor="w", padx=6, pady=(0, 6))
            if self._stream_status:
                ctk.CTkLabel(box, text=self._stream_status, justify="left", wraplength=190, font=ctk.CTkFont(size=9, weight="bold"), text_color="#e67e22").pack(anchor="w", padx=6, pady=(0, 6))

            btn_box = ctk.CTkFrame(self._mir_body, fg_color="transparent")
            btn_box.pack(fill="x", pady=8)
            ctk.CTkButton(btn_box, text="← Back", width=55, height=26, font=ctk.CTkFont(size=10), fg_color="#34495e", command=self._prev_step).pack(side="left")
            self._btn_start_stream = ctk.CTkButton(btn_box, text="▶ Start Stream", width=105, height=26, fg_color="#27ae60", hover_color="#2ecc71", font=ctk.CTkFont(size=11, weight="bold"), command=self._mir_start)
            self._btn_start_stream.pack(side="right")

    def _recheck_installation(self):
        self._mir_tool_text = "⏳ Re-checking system installation..."
        self._tools_installed = False
        self._verify_scrcpy_installation()
        self._render_wizard(force=True)

    def _next_step(self):
        if self._wizard_step == 1 and self._tools_installed:
            self._wizard_step = 2
            self._render_wizard(force=True)
        elif self._wizard_step == 2 and self._mir_ready_devices():
            self._wizard_step = 3
            self._render_wizard(force=True)

    def _prev_step(self):
        if self._wizard_step > 1:
            self._wizard_step -= 1
            self._render_wizard(force=True)

    def _on_mode_change(self, val):
        self._mir_mode = "usb" if "USB" in val else "wireless"
        self._act_status = ""
        self._render_wizard(force=True)

    def _mir_start_tools(self):
        self._mir_tools_busy = True
        def say(text, frac=None): self._mir_q.put(("tp", text, frac))
        def work():
            try:
                tools = st.ensure_tools(say)
                self._mir_q.put(("tools", tools))
            except Exception as e:
                self._mir_q.put(("terr", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _mir_after_tools(self):
        if self._mir_tools and not self._mir_mon_on:
            self._mir_start_monitor()
        self._render_wizard(force=True)

    def _mir_start_monitor(self):
        if self._mir_mon_on or not self._mir_tools: return
        self._mir_mon_on = True
        self._mir_mon_gen += 1
        gen = self._mir_mon_gen
        adb = st.Adb(self._mir_tools["adb"][0])

        def loop():
            while self._mir_mon_on and gen == self._mir_mon_gen:
                try:
                    self._mir_q.put(("devs", adb.devices()))
                except Exception as e:
                    self._mir_q.put(("adberr", str(e)))
                time.sleep(1.5)
        threading.Thread(target=loop, daemon=True).start()

    def _mir_ready_devices(self):
        return [d for d in self._mir_devs if d["state"] == "device"]

    def _mir_do_connect(self, addr):
        addr = addr.strip()
        if not addr:
            self._act_status = "❌ Type IP Address."
            self._render_wizard(force=True)
            return

        def fn(adb):
            ok, msg = adb.connect(addr)
            if ok:
                clean_ip = addr.split(":")[0]
                prefs = st.load_prefs()
                prefs["last_ip"] = clean_ip
                st.save_prefs(prefs)
            return ok, msg

        self._mir_act(f"Connecting {addr}...", fn)

    def _mir_do_quick_wifi(self):
        def fn(adb):
            usb = [d for d in adb.devices() if d["state"] == "device" and not d["wireless"]]
            if not usb:
                return False, "Plug USB cable first."
            ip = adb.phone_ip(usb[0]["serial"])
            if not ip:
                return False, "Could not get IP."
            ok, msg = adb.tcpip(usb[0]["serial"])
            if not ok:
                return False, "TCP Switch Failed: " + msg
            time.sleep(2.0)
            ok, msg = adb.connect(f"{ip}:5555")
            if ok:
                prefs = st.load_prefs()
                prefs["last_ip"] = ip
                st.save_prefs(prefs)
            return ok, (f"Success! Unplug USB now." if ok else msg)

        self._mir_act("Executing adb tcpip...", fn)

    def _mir_act(self, text, fn):
        self._act_status = "⏳ " + text
        self._render_wizard(force=True)
        adb = st.Adb(self._mir_tools["adb"][0]) if self._mir_tools else None
        if not adb: return

        def work():
            try:
                ok, msg = fn(adb)
                status_text = ("✅ " if ok else "❌ ") + (msg or ("Done" if ok else "Failed"))
            except Exception as e:
                status_text = "❌ Error: " + str(e)
            self._mir_q.put(("act", status_text))
            
        threading.Thread(target=work, daemon=True).start()

    def _mir_schedule_poll(self):
        if self._mir_poll_job is None:
            self._mir_poll_job = self.after(150, self._mir_poll)

    def _mir_poll(self):
        self._mir_poll_job = None
        try:
            while True:
                m = self._mir_q.get_nowait()
                k = m[0]
                if k == "tp":
                    self._mir_tool_text = m[1]
                    if self._wizard_step == 1: self._render_wizard()
                elif k == "tools":
                    self._mir_tools = m[1]
                    self._tools_installed = True
                    self._mir_after_tools()
                elif k == "devs":
                    self._mir_devs = m[1]
                    if self._wizard_step == 2 and not self._mir_proc:
                        self._render_wizard()
                elif k == "act":
                    self._act_status = m[1]
                    if self._wizard_step == 2: self._render_wizard()
                elif k == "launch_ready":
                    if m[4] == self._launch_gen:
                        self._mir_execute_launch(m[1], m[2], m[3])
                elif k == "launch_fail":
                    if m[2] == self._launch_gen:
                        self._stream_status = f"❌ Could not start the stream: {m[1]}"
                        self.mirror_stop(step=3)
        except queue.Empty: pass
        
        if self._mir_panel and self._mir_panel.winfo_exists():
            self._mir_poll_job = self.after(150, self._mir_poll)