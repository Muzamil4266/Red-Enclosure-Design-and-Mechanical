"""
Scrcpy Embedded Streaming Engine (part 2 of 2)
==============================================
Manages scrcpy process launching, embedded canvas window positioning,
touchpad pinch-to-zoom events, Win32 window docking, and termination.
"""
import os
import time
import threading
import subprocess
import tkinter as tk
import customtkinter as ctk
import scrcpy_tools as st
from scrcpy_mirror_win32 import _Win32, _WheelHook

HEAD_H = 28
IS_WIN = os.name == "nt"
MAX_TRIES = 3          # automatic connection attempts before giving up
LINK_WAIT = 25         # seconds the phone link may take to become usable before an attempt


class ScrcpyMirrorStreamMixin:

    def _mir_start(self):
        devs = self._mir_ready_devices()
        if not devs or not self._mir_tools: return
        d = devs[0]

        if hasattr(self, "_btn_start_stream"):
            self._btn_start_stream.configure(state="disabled", text="⏳ Connecting...")

        self._mir_mon_on = False

        self._launch_try = 1
        self._launch_dev = d
        self._stream_status = ""
        self._mir_preflight(d)

    def _mir_preflight(self, d):
        s, a = self._mir_tools["scrcpy"][0], self._mir_tools["adb"][0]
        gen = self._launch_gen

        def work():
            try:
                chosen = self._mir_prepare_link(d, a)
                self._mir_q.put(("launch_ready", chosen, s, a, gen))
            except Exception as e:
                self._mir_q.put(("launch_fail", str(e), gen))
        threading.Thread(target=work, daemon=True).start()

    def _mir_prepare_link(self, d, adb_path):
        adb = st.Adb(adb_path)
        serial, deadline = d["serial"], time.time() + LINK_WAIT

        def current():
            try:
                devs = adb.devices()
            except Exception:
                return None
            same = [x for x in devs if x["serial"] == serial] or [x for x in devs if x["model"] == d["model"]]
            ready = [x for x in same if x["state"] == "device"]
            return ready[0] if ready else (same[0] if same else None)

        while time.time() < deadline:
            cur = current()
            if cur and cur["state"] == "device":
                code, out, err = adb.run("shell", "echo", "ready", serial=cur["serial"], timeout=6)
                if out.strip() == "ready":
                    return cur
            target = cur["serial"] if cur else serial
            if ":" in target and not target.startswith("adb-"):
                adb.connect(target)
            time.sleep(1.0)
        raise RuntimeError("the phone did not answer. Keep its screen on, check the cable / Wi-Fi, and tap Allow if asked.")

    def _mir_build_stream_ui(self, d):
        host = self.canvas_container
        self._mir_stream_frame = ctk.CTkFrame(host, fg_color="#000000", corner_radius=6, border_width=2, border_color="#1abc9c")
        self._mir_stream_frame.place(
            relx=0.5, rely=0.50, anchor="center",
            relwidth=self._stream_scale_w, relheight=self._stream_scale_h
        )

        bar = ctk.CTkFrame(self._mir_stream_frame, height=HEAD_H, fg_color="#1a1a1a", corner_radius=0)
        bar.pack(side="top", fill="x")

        ctk.CTkButton(bar, text="⏹ Stop Mirror", width=95, height=22, font=ctk.CTkFont(size=10, weight="bold"), fg_color="#c0392b", hover_color="#e74c3c", command=self.mirror_stop).pack(side="right", padx=6, pady=2)

        self._mir_title_lbl = ctk.CTkLabel(bar, text=f"🟡 Connecting to {d['model']}...", font=ctk.CTkFont(size=11, weight="bold"), text_color="#f1c40f")
        self._mir_title_lbl.pack(side="left", padx=10)
        self._mir_title_lbl.bind("<Button-1>", self._on_drag_start)
        self._mir_title_lbl.bind("<B1-Motion>", self._on_drag_motion)

        self._mir_body_frame = tk.Frame(self._mir_stream_frame, bg="black", highlightthickness=0)
        self._mir_body_frame.pack(fill="both", expand=True)
        self._mir_body_frame.bind("<Configure>", lambda e: self._mir_on_resize())

        for ev in ("<Control-MouseWheel>", "<Control-Button-4>", "<Control-Button-5>"):
            self._mir_stream_frame.bind(ev, self._on_stream_pinch_zoom)
            self._mir_body_frame.bind(ev, self._on_stream_pinch_zoom)

    def _mir_set_title(self, text, color):
        lbl = self._mir_title_lbl
        try:
            if lbl is not None and lbl.winfo_exists():
                lbl.configure(text=text, text_color=color)
        except Exception:
            pass

    def _mir_execute_launch(self, d, s, a):
        self._launch_dev = d
        self._mir_title = f"VCB Mirror {os.getpid()}"
        self._mir_log.clear()

        if self._mir_panel and self._mir_panel.winfo_exists():
            self._mir_panel.place_forget()

        if not (self._mir_stream_frame and self._mir_stream_frame.winfo_exists()):
            self._mir_build_stream_ui(d)
            self.update_idletasks()
        suffix = f" (attempt {self._launch_try}/{MAX_TRIES})" if self._launch_try > 1 else ""
        self._mir_set_title(f"🟡 Connecting to {d['model']}...{suffix}", "#f1c40f")
        self._embed_attempts = 0
        self._last_mir_w = 0
        self._last_mir_h = 0

        try:
            cmd = st.mirror_command(s, d["serial"], self._mir_title, "screen", False, "0", embedded=True)
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, errors="replace",
                env=dict(os.environ, ADB=a),
                creationflags=st.NOWIN
            )
        except Exception as e:
            self._mir_attempt_failed("Launch error: " + str(e))
            return

        self._mir_proc = proc
        self._mir_started = time.time()
        threading.Thread(target=lambda: [self._mir_log.append(l.rstrip()) for l in proc.stdout], daemon=True).start()
        self._mir_start_zoom()
        self.after(400, self._mir_tick)

    def _on_drag_start(self, event):
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _on_drag_motion(self, event):
        if not self._mir_stream_frame: return
        dx = event.x - self._drag_start_x
        dy = event.y - self._drag_start_y
        info = self._mir_stream_frame.place_info()
        cur_x = float(info.get("relx", 0.5))
        cur_y = float(info.get("rely", 0.50))
        new_x = max(0.1, min(0.9, cur_x + dx / 1500))
        new_y = max(0.1, min(0.9, cur_y + dy / 950))
        self._mir_stream_frame.place(relx=new_x, rely=new_y)

    def _on_stream_pinch_zoom(self, event):
        delta = getattr(event, "delta", 0)
        if delta == 0:
            if getattr(event, "num", 0) == 4: delta = 120
            elif getattr(event, "num", 0) == 5: delta = -120
        self._mir_zoom_by(delta)
        return "break"

    def _mir_zoom_by(self, delta):
        if not delta:
            return
        f = 1.0 + min(0.25, abs(delta) / 120.0 * 0.06)
        f = f if delta > 0 else 1.0 / f
        if self._mir_aspect:
            self._stream_scale_h = max(0.35, min(0.98, self._stream_scale_h * f))
        else:
            self._stream_scale_w = max(0.40, min(0.98, self._stream_scale_w * f))
            self._stream_scale_h = max(0.40, min(0.98, self._stream_scale_h * f))
        self._mir_apply_layout()

    def _mir_apply_layout(self):
        fr = self._mir_stream_frame
        if not (fr and fr.winfo_exists()):
            return
        cont = self.canvas_container
        cw, ch = max(300, cont.winfo_width()), max(300, cont.winfo_height())
        if self._mir_aspect:
            pad = 4
            frame_h = max(0.35, min(0.98, self._stream_scale_h)) * ch
            body_h = frame_h - HEAD_H - pad
            body_w = body_h * self._mir_aspect
            frame_w = max(300.0, body_w + pad)
            if frame_w > 0.98 * cw:
                frame_w = 0.98 * cw
                body_h = (frame_w - pad) / self._mir_aspect
                frame_h = body_h + HEAD_H + pad
            self._stream_scale_h = min(0.98, frame_h / ch)
            self._stream_scale_w = min(0.98, frame_w / cw)
        fr.place(relwidth=self._stream_scale_w, relheight=self._stream_scale_h)
        self.update_idletasks()
        self._mir_on_resize()

    def _mir_start_zoom(self):
        if not IS_WIN or self._mir_hook is not None:
            return
        try:
            self._mir_hook = _WheelHook()
            self._mir_hook.start()
        except Exception as e:
            print(f"Pinch hook unavailable: {e}")
            self._mir_hook = None
        if self._zoom_job is None:
            self._zoom_job = self.after(20, self._mir_zoom_poll)

    def _mir_stop_zoom(self):
        if self._mir_hook is not None:
            try: self._mir_hook.stop()
            except Exception: pass
        self._mir_hook = None
        if self._zoom_job is not None:
            try: self.after_cancel(self._zoom_job)
            except Exception: pass
        self._zoom_job = None

    def _mir_zoom_poll(self):
        self._zoom_job = None
        fr = self._mir_stream_frame
        if not (fr and fr.winfo_exists()):
            return
        hook = self._mir_hook
        if hook is not None:
            x, y = fr.winfo_rootx(), fr.winfo_rooty()
            hook.rect = (x, y, x + fr.winfo_width(), y + fr.winfo_height())
            hook.frame_hwnd = int(fr.winfo_id())
            total = 0
            while hook.deltas:
                total += hook.deltas.popleft()
            if total:
                self._mir_zoom_by(total)
        self._zoom_job = self.after(20, self._mir_zoom_poll)

    def _mir_follow_shape(self):
        hwnd, w32, body = self._mir_hwnd, self._mir_w32, self._mir_body_frame
        try:
            if not (hwnd and w32 and body and body.winfo_exists() and w32.alive(hwnd)):
                return
            cw, chh = w32.size(hwnd, client=True)
            if cw > 100 and chh > 100 and (abs(cw - body.winfo_width()) > 6 or abs(chh - body.winfo_height()) > 6):
                a = cw / chh
                if self._mir_aspect is None or abs(a / self._mir_aspect - 1.0) > 0.04:
                    self._mir_aspect = a
                    self._last_mir_w = 0
                    self._last_mir_h = 0
                    self._mir_apply_layout()
        except Exception:
            pass

    def _mir_on_resize(self):
        if self._mir_hwnd and self._mir_body_frame and self._mir_body_frame.winfo_exists():
            w = self._mir_body_frame.winfo_width()
            h = self._mir_body_frame.winfo_height()
            if w > 50 and h > 50:
                last_w = getattr(self, "_last_mir_w", 0)
                last_h = getattr(self, "_last_mir_h", 0)
                if w != last_w or h != last_h:
                    self._mir_w32.fit(self._mir_hwnd, w, h)
                    self._last_mir_w = w
                    self._last_mir_h = h

    def _mir_tick(self):
        p = self._mir_proc
        if p is None:
            return

        if p.poll() is not None:
            self._mir_ended()
            return

        if IS_WIN and self._mir_hwnd is None:
            self._embed_attempts += 1
            self._mir_w32 = self._mir_w32 or _Win32()
            hwnd = self._mir_w32.find(self._mir_title)

            if hwnd and self._mir_body_frame and self._mir_body_frame.winfo_exists() and self._mir_w32.ready(hwnd):
                parent_id = int(self._mir_body_frame.winfo_id())
                if parent_id > 0:
                    w0, h0 = self._mir_w32.size(hwnd)
                    success = self._mir_w32.embed(hwnd, parent_id)
                    if success:
                        self._mir_hwnd = hwnd
                        self._mir_aspect = w0 / h0 if w0 > 100 and h0 > 100 else self._mir_aspect
                        self._last_mir_w = 0
                        self._last_mir_h = 0
                        d = self._launch_dev or {}
                        self._mir_set_title(f"🟢 Live Mirror Stream • {d.get('model', 'phone')} (Pinch touchpad to Zoom)", "#2ecc71")
                        self._mir_apply_layout()

            if self._embed_attempts > 60 and self._mir_hwnd is None:
                self._mir_attempt_failed("scrcpy started but its picture window never appeared")
                return
        elif self._mir_hwnd:
            self._mir_follow_shape()
            self._mir_on_resize()

        self.after(300, self._mir_tick)

    def _mir_explain(self, tail):
        text = " ".join(tail[-8:]).lower()
        if "unauthorized" in text:
            return "the phone has not allowed this PC - unlock it and tap Allow"
        if "offline" in text:
            return "the phone link went offline - keep the phone screen on and check Wi-Fi / cable"
        if "doesn't match this client" in text or "server version" in text:
            return "two different adb versions are clashing - close other Android tools and try again"
        if "no devices" in text or "could not find any adb device" in text or "not found" in text:
            return "the phone disconnected while starting"
        if "server connection failed" in text or "connection refused" in text or "closed" in text:
            return "the phone closed the connection while starting"
        return tail[-1][:140] if tail else "scrcpy closed immediately"

    def _mir_dump_log(self):
        try:
            os.makedirs(st.TOOLS_DIR, exist_ok=True)
            with open(os.path.join(st.TOOLS_DIR, "last_mirror.log"), "w", encoding="utf-8") as f:
                f.write("\n".join(self._mir_log))
        except Exception:
            pass

    def _mir_attempt_failed(self, reason):
        p = self._mir_proc
        self._mir_proc = None
        self._mir_hwnd = None
        if p is not None:
            try: p.terminate()
            except Exception: pass
        if self._launch_try < MAX_TRIES and self._launch_dev:
            self._launch_try += 1
            d = self._launch_dev
            self._mir_set_title(f"🟡 Reconnecting to {d['model']}... (attempt {self._launch_try}/{MAX_TRIES})", "#f1c40f")
            self._mir_preflight(d)
        else:
            self._mir_dump_log()
            self._stream_status = f"❌ Could not start the stream ({MAX_TRIES} tries): {reason}."
            self.mirror_stop(step=3)

    def _mir_ended(self):
        tail = [l for l in list(self._mir_log) if l.strip()]
        if self._mir_hwnd is None:
            self._mir_attempt_failed(self._mir_explain(tail))
            return
        reason = self._mir_explain(tail) if tail else ""
        self.mirror_stop()
        if reason and hasattr(self, "toast"):
            self.toast("📱 Mirror ended: " + reason, 4500)

    def mirror_stop(self, step=2):
        self._launch_gen += 1
        self._mir_stop_zoom()
        if self._mir_proc is not None:
            try: self._mir_proc.terminate()
            except Exception: pass
        self._mir_proc = None
        self._mir_hwnd = None
        self._embed_attempts = 0
        self._launch_try = 1
        self._mir_aspect = None
        self._mir_title_lbl = None

        if self._mir_stream_frame and self._mir_stream_frame.winfo_exists():
            self._mir_stream_frame.destroy()
        self._mir_stream_frame = None

        if self._mir_panel and self._mir_panel.winfo_exists():
            self._mir_panel.place(relx=0.99, rely=0.08, anchor="ne", relwidth=0.28, relheight=0.62)
            self._wizard_step = step
            if self._tools_installed and not self._mir_mon_on:
                self._mir_start_monitor()
            self._render_wizard(force=True)