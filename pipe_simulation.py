"""
Pipe Simulation Fun mixin for Red Circuit.
========================================
Main integration mixin class managing Tkinter canvas lifecycle, event bindings,
toolbar controls, and multi-layer rendering updates.
"""
import math
import time
import traceback

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFilter, ImageTk

try:
    import numpy as np
except ImportError:
    np = None

from pipe_sim_engine import (
    TAG, BIND_TAG, G, N_PTS, SEG, RAD, POOL, Z_PERSP, FRAME_MS, DROP_COLORS, JK, FK,
    init_pipe_state, step_pipe, update_flow, emit_water,
    step_jet, step_droplets, alloc_wet, step_wet, get_jet_widths
)


class PipeSimulationMixin:

    def init_pipe_sim(self):
        """Call once from main.py after the other init_...() calls."""
        self._ps_active = False
        self._ps = None
        self._ps_bar = None
        self._ps_pin_btn = None
        self._ps_rng = None

        c = self.canvas
        if BIND_TAG not in c.bindtags():
            c.bindtags((BIND_TAG,) + tuple(c.bindtags()))
        bc = c.bind_class
        bc(BIND_TAG, "<ButtonPress-1>", lambda e: self._ps_on_press(e, False))
        bc(BIND_TAG, "<ButtonPress-2>", lambda e: self._ps_on_press(e, True))
        bc(BIND_TAG, "<ButtonPress-3>", lambda e: self._ps_on_press(e, True))
        bc(BIND_TAG, "<Motion>", self._ps_on_motion)
        bc(BIND_TAG, "<B1-Motion>", self._ps_on_motion)
        bc(BIND_TAG, "<B2-Motion>", self._ps_on_motion)
        bc(BIND_TAG, "<B3-Motion>", self._ps_on_motion)
        bc(BIND_TAG, "<ButtonRelease-1>", self._ps_on_release)
        bc(BIND_TAG, "<ButtonRelease-2>", self._ps_on_release)
        bc(BIND_TAG, "<ButtonRelease-3>", self._ps_on_release)
        bc(BIND_TAG, "<MouseWheel>", self._ps_on_wheel)
        bc(BIND_TAG, "<Button-4>", self._ps_on_wheel)
        bc(BIND_TAG, "<Button-5>", self._ps_on_wheel)
        bc(BIND_TAG, "<Double-Button-1>", lambda e: "break" if self._ps_active else None)

        if not getattr(self, "_ps_wrapped", False):
            self._ps_wrapped = True
            for name in ("set_mode", "export_to_pdf", "center_all_components", "new_project",
                         "open_projects_window", "export_component_photos"):
                orig = getattr(self, name, None)
                if callable(orig):
                    setattr(self, name, self._ps_make_wrapper(orig))

    def _ps_make_wrapper(self, orig):
        def wrapped(*args, **kwargs):
            if self._ps_active:
                self.close_pipe_simulation()
            return orig(*args, **kwargs)
        return wrapped

    def _ps_make_tk(self, img, k):
        """Send Tk a SMALL image and let Tk enlarge it (zoom runs in C, far cheaper than Python upload)."""
        base = ImageTk.PhotoImage(img)
        if k > 1:
            ph = getattr(base, "_PhotoImage__photo", None)
            if ph is not None:
                try:
                    return ph.zoom(k)
                except Exception:
                    pass
            return ImageTk.PhotoImage(img.resize((img.width * k, img.height * k), Image.Resampling.BILINEAR))
        return base

    def _ps_say(self, msg):
        if hasattr(self, "toast"):
            try:
                self.toast(msg)
                return
            except Exception:
                pass
        print(msg)

    def _ps_set_button(self, active):
        btn = getattr(self, "pipe_sim_btn", None)
        if btn is not None:
            try:
                btn.configure(text="❌ Close Pipe Simulation" if active else "🚿 Pipe Simulation Fun",
                              fg_color="#c0392b" if active else "#16a085")
            except Exception:
                pass

    def toggle_pipe_simulation(self):
        if self._ps_active:
            self.close_pipe_simulation()
        else:
            self.open_pipe_simulation()

    def open_pipe_simulation(self):
        if np is None:
            self._ps_say("Pipe Simulation needs NumPy:  pip install numpy")
            return
        if self._ps_active:
            return
        c = self.canvas
        try:
            if hasattr(self, "_flush_dot"):
                self._flush_dot()
            if hasattr(self, "finish_text_edit"):
                self.finish_text_edit()
            self.selected_item = None
            self.show_handles()
        except Exception:
            pass
        self.update_idletasks()
        W, H = max(300, c.winfo_width()), max(200, c.winfo_height())

        S = init_pipe_state(W, H)
        self._ps = S
        self._ps_rng = S["rng"]

        it = S["it"]
        it["wet"] = c.create_image(0, 0, anchor="nw", state="hidden", tags=TAG)
        it["out"] = [c.create_line(0, 0, 0, 0, width=12, fill="#0b0f13", capstyle="round", tags=TAG)
                     for _ in range(N_PTS - 1)]
        it["body"] = [c.create_line(0, 0, 0, 0, width=8, fill="#7d8a99", capstyle="round", tags=TAG)
                      for _ in range(N_PTS - 1)]
        it["inlet"] = c.create_oval(0, 0, 1, 1, fill="#4a5663", outline="#0b0f13", width=2, tags=TAG)
        it["outlet"] = c.create_oval(0, 0, 1, 1, fill="#05080b", outline="#c3d0dc", width=3, tags=TAG)
        it["water"] = [c.create_line(0, 0, 0, 0, width=5, fill="#1f7fd6", capstyle="round",
                                     state="hidden", tags=TAG) for _ in range(N_PTS - 1)]
        it["hi"] = [c.create_line(0, 0, 0, 0, width=2, fill="#dbe6f0", capstyle="round", tags=TAG)
                    for _ in range(N_PTS - 1)]
        it["jet"] = c.create_image(0, 0, anchor="nw", state="hidden", tags=TAG)
        it["drops"] = [c.create_line(0, 0, 0, 0, width=3, fill=DROP_COLORS[1], capstyle="round",
                                     state="hidden", tags=TAG) for _ in range(POOL)]
        it["ring"] = c.create_oval(0, 0, 1, 1, outline="#f1c40f", width=2, state="hidden", tags=TAG)

        self._ps_build_bar()
        try:
            c.configure(cursor="hand2")
        except Exception:
            pass
        self._ps_active = True
        self._ps_set_button(True)
        S["last"] = time.perf_counter()
        S["job"] = self.after(FRAME_MS, self._ps_tick)
        self._ps_say("🚿 Pipe Simulation on - grab the pipe!")

    def close_pipe_simulation(self):
        S = self._ps
        self._ps_active = False
        if S and S.get("job"):
            try:
                self.after_cancel(S["job"])
            except Exception:
                pass
        try:
            self.canvas.delete(TAG)
            self.canvas.configure(cursor="")
        except Exception:
            pass
        if self._ps_bar is not None:
            try:
                self._ps_bar.destroy()
            except Exception:
                pass
            self._ps_bar = None
        self._ps = None
        self._ps_set_button(False)

    def _ps_build_bar(self):
        c = self.canvas
        bar = ctk.CTkFrame(c.master, fg_color="#181818", corner_radius=8)
        row = ctk.CTkFrame(bar, fg_color="transparent")
        row.pack(padx=8, pady=(6, 0))
        ctk.CTkLabel(row, text="🚿", font=ctk.CTkFont(size=14)).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(row, text="Pressure", font=ctk.CTkFont(size=11)).pack(side="left", padx=(2, 2))
        sl = ctk.CTkSlider(row, from_=700, to=1400, width=90,
                           command=lambda v: self._ps_set_pressure(v))
        sl.set(1000)
        sl.pack(side="left", padx=2)
        self._ps_pin_btn = ctk.CTkButton(row, text="📌 Pin tap: OFF", width=105, height=26,
                                         font=ctk.CTkFont(size=11, weight="bold"),
                                         command=self._ps_toggle_pin)
        self._ps_pin_btn.pack(side="left", padx=3)
        for txt, cmd, w in (("➖", lambda: self._ps_resize(1 / 1.15), 32), ("➕", lambda: self._ps_resize(1.15), 32),
                            ("💦 Dry screen", self._ps_dry, 100), ("✖ Close", self.close_pipe_simulation, 70)):
            ctk.CTkButton(row, text=txt, width=w, height=26, font=ctk.CTkFont(size=11, weight="bold"),
                          fg_color="#c0392b" if "Close" in txt else "#1f6aa5", command=cmd).pack(side="left", padx=2)
        ctk.CTkLabel(bar, text="Drag = bend / swing   •   Right-drag or Shift+drag = move whole pipe   •   Wheel = resize",
                     font=ctk.CTkFont(size=10), text_color="#aaaaaa").pack(padx=8, pady=(2, 6))
        bar.place(in_=c, relx=0.5, y=8, anchor="n")
        bar.lift()
        self._ps_bar = bar

    def _ps_set_pressure(self, v):
        if self._ps_active:
            self._ps["pressure"] = float(v)

    def _ps_toggle_pin(self):
        if not self._ps_active:
            return
        S = self._ps
        S["pinned"] = not S["pinned"]
        S["anchor"] = S["pos"][0, :2].copy()
        if self._ps_pin_btn is not None:
            self._ps_pin_btn.configure(text="📌 Pin tap: ON" if S["pinned"] else "📌 Pin tap: OFF",
                                         fg_color="#d35400" if S["pinned"] else "#1f6aa5")

    def _ps_dry(self):
        if self._ps_active:
            self._ps["wet"][:] = 0.0

    def _ps_resize(self, ratio):
        if not self._ps_active:
            return
        S = self._ps
        new = min(2.2, max(0.5, S["scale"] * ratio))
        ratio = new / S["scale"]
        if abs(ratio - 1.0) < 1e-6:
            return
        S["scale"] = new
        ctr = S["pos"].mean(axis=0)
        S["pos"][:] = ctr + (S["pos"] - ctr) * ratio
        S["prev"][:] = ctr + (S["prev"] - ctr) * ratio
        if S["pinned"]:
            S["anchor"] = ctr[:2] + (S["anchor"] - ctr[:2]) * ratio

    def _ps_on_press(self, ev, whole):
        if not self._ps_active:
            return None
        S = self._ps
        if ev.state & 0x0001:
            whole = True
        pts = S["pos"][:, :2]
        d = np.hypot(pts[:, 0] - ev.x, pts[:, 1] - ev.y)
        i = int(d.argmin())
        if d[i] <= RAD * S["scale"] * 1.9 + 12:
            S["grab"] = i
            S["mode"] = "whole" if whole else "point"
            S["off"] = pts[i] - np.array([ev.x, ev.y], float)
            S["target"] = pts[i].copy()
            S["cur"] = pts[i].copy()
            S["mouse"] = (ev.x, ev.y)
            S["mv"] = [0.0, 0.0]
            S["pend"] = [0.0, 0.0]
            S["mtime"] = time.perf_counter()
            S["phase"] = float(S["rng"].uniform(0, 6.28))
        return "break"

    def _ps_on_motion(self, ev):
        if not self._ps_active:
            return None
        S = self._ps
        if S["grab"] is not None:
            now = time.perf_counter()
            dtm = max(0.004, now - S["mtime"])
            dx, dy = ev.x - S["mouse"][0], ev.y - S["mouse"][1]
            S["mv"] = [0.6 * S["mv"][0] + 0.4 * dx / dtm, 0.6 * S["mv"][1] + 0.4 * dy / dtm]
            S["mtime"] = now
            S["mouse"] = (ev.x, ev.y)
            if S["mode"] == "point":
                S["target"] = np.array([ev.x, ev.y], float) + S["off"]
            else:
                S["pend"][0] += dx
                S["pend"][1] += dy
            kick = min(math.hypot(dx, dy) / dtm, 3000.0) * 0.0035
            z = S["pos"][:, 2]
            z += kick * np.sin(np.arange(N_PTS) * 0.8 + S["phase"])
            np.clip(z, -90, 90, out=z)
        return "break"

    def _ps_on_release(self, ev):
        if not self._ps_active:
            return None
        S = self._ps
        if S["grab"] is not None and S["mode"] == "whole":
            if time.perf_counter() - S["mtime"] < 0.08:
                v = np.array(S["mv"], float) / 90.0
                sp = float(np.hypot(v[0], v[1]))
                if sp > 40.0:
                    v *= 40.0 / sp
                S["prev"][:, 0] = S["pos"][:, 0] - v[0]
                S["prev"][:, 1] = S["pos"][:, 1] - v[1]
            S["mv"] = [0.0, 0.0]
        S["grab"] = None
        S["mode"] = None
        if hasattr(self, "stop_history"):
            try:
                self.stop_history()
            except Exception:
                pass
        return "break"

    def _ps_on_wheel(self, ev):
        if not self._ps_active:
            return None
        d = getattr(ev, "delta", 0)
        num = getattr(ev, "num", 0)
        if d > 0 or num == 4:
            self._ps_resize(1.08)
        elif d < 0 or num == 5:
            self._ps_resize(1 / 1.08)
        return "break"

    def _ps_tick(self):
        if not self._ps_active:
            return
        S = self._ps
        t0 = time.perf_counter()
        dt = min(0.05, max(0.008, t0 - S["last"]))
        S["last"] = t0
        try:
            if not self.canvas.type(S["it"]["wet"]):
                self.close_pipe_simulation()
                return
            self._ps_viewport()
            step_pipe(S, dt)
            update_flow(S, dt)
            emit_water(S, dt)
            step_jet(S, dt)
            step_droplets(S, dt)
            if step_wet(S, dt):
                self._ps_wet_draw()
            self._ps_render()
        except Exception:
            print("Pipe simulation stopped because of an error:")
            traceback.print_exc()
            self.close_pipe_simulation()
            return

        work = (time.perf_counter() - t0) * 1000.0
        S["ema"] = S["ema"] * 0.9 + work * 0.1
        S["fcount"] += 1
        if S["fcount"] % 15 == 0:
            e = S["ema"]
            if e > 24:
                S["max_active"] = max(120, int(S["max_active"] * 0.85))
                S["wet_every"] = min(8, S["wet_every"] + 1)
                S["jet_every"] = min(5, S["jet_every"] + 1)
                if S["jet_every"] >= 4:
                    S["jk"] = min(5, S["jk"] + 1)
            elif e < 12:
                S["max_active"] = min(POOL, int(S["max_active"] * 1.08) + 1)
                S["wet_every"] = max(4, S["wet_every"] - 1)
                S["jet_every"] = max(2, S["jet_every"] - 1)
                S["jk"] = max(3, S["jk"] - 1)
        S["job"] = self.after(max(1, int(FRAME_MS - work)), self._ps_tick)

    def _ps_viewport(self):
        c, S = self.canvas, self._ps
        W, H = max(300, c.winfo_width()), max(200, c.winfo_height())
        if abs(W - S["W"]) > 1 or abs(H - S["H"]) > 1:
            S["W"], S["H"] = W, H
            alloc_wet(S, W, H)
            if "wet" in S["it"] and self.canvas.type(S["it"]["wet"]):
                self.canvas.itemconfigure(S["it"]["wet"], state="hidden")
        S["ox"], S["oy"] = c.canvasx(0), c.canvasy(0)

    def _ps_jet_render(self):
        S, c, J, Fm = self._ps, self.canvas, self._ps["j"], self._ps["foam"]
        a, fa = J["alive"], Fm["alive"]
        if not a.any() and not fa.any():
            if S["jet_vis"]:
                c.itemconfigure(S["it"]["jet"], state="hidden")
                S["jet_vis"] = False
            return
        if S["frame"] % S["jet_every"]:
            return
        W, H = S["W"], S["H"]
        w = get_jet_widths(S)
        idx = np.flatnonzero(a)
        fi = np.flatnonzero(fa)
        xs_, ys_ = [], []
        if idx.size:
            pad = float(w[idx].max()) * 0.6 + 16
            xs_ += [J["x"][idx].min() - pad, J["x"][idx].max() + pad]
            ys_ += [J["y"][idx].min() - pad, J["y"][idx].max() + pad]
        if fi.size:
            rp = float(Fm["fr"][fi].max()) * 1.6 + 8
            xs_ += [Fm["fx"][fi].min() - rp, Fm["fx"][fi].max() + rp]
            ys_ += [Fm["fy"][fi].min() - rp, Fm["fy"][fi].max() + rp]
        x0, y0 = max(0, int(min(xs_))), max(0, int(min(ys_)))
        x1, y1 = min(W, int(max(xs_)) + 1, x0 + 1990), min(H, int(max(ys_)) + 1, y0 + 1990)
        if x1 - x0 < 6 or y1 - y0 < 6:
            return
        jk = S["jk"]
        q = 1.0 / jk
        w2, h2 = (x1 - x0) // jk + 1, (y1 - y0) // jk + 1

        M = Image.new("L", (w2, h2), 0)
        dm = ImageDraw.Draw(M)
        px = ((J["x"] - x0) * q).tolist()
        py = ((J["y"] - y0) * q).tolist()
        wl = (w * q).tolist()
        sg = J["segid"].tolist()
        prev = None
        for i in idx[np.argsort(J["born"][idx])].tolist():
            wi = max(1.0, wl[i])
            if prev is not None and sg[i] == sg[prev]:
                dm.line([(px[prev], py[prev]), (px[i], py[i])], fill=255, width=max(1, int((wi + wprev) * 0.5)))
            dm.ellipse((px[i] - wi * 0.5, py[i] - wi * 0.5, px[i] + wi * 0.5, py[i] + wi * 0.5), fill=255)
            prev, wprev = i, wi
        Fi = Image.new("L", (w2, h2), 0)
        df = ImageDraw.Draw(Fi)
        for i in fi.tolist():
            v = int(255 * min(1.0, (Fm["life"][i] / Fm["lmax"][i]) ** 0.6))
            fx, fy = (Fm["fx"][i] - x0) * q, (Fm["fy"][i] - y0) * q
            rx, ry = Fm["fr"][i] * Fm["sx"][i] * q, Fm["fr"][i] * Fm["sy"][i] * q
            df.ellipse((fx - rx, fy - ry, fx + rx, fy + ry), fill=v)
        core = np.asarray(M.filter(ImageFilter.GaussianBlur(4)), np.float32) * (1 / 255)
        m = np.asarray(M.filter(ImageFilter.GaussianBlur(1.6)), np.float32) * (1 / 255)
        f = np.asarray(Fi.filter(ImageFilter.GaussianBlur(2.5)), np.float32) * (1 / 255)

        nz, nf = S["noise"], S["noff"]
        ax, ay = int(x0 * q + nf[0]) % 1024, int(y0 * q + nf[1]) % 1024
        bx, by = int(x0 * 0.35 - nf[0] * 0.6 + 512) % 1024, int(y0 * 0.35 - nf[1] * 0.6 + 300) % 1024
        turb = (nz[ay:ay + h2, ax:ax + w2].astype(np.float32) * 0.6 +
                nz[by:by + h2, bx:bx + w2].astype(np.float32) * 0.4) * (1 / 255)
        alpha = np.clip(m * (0.30 + 0.95 * turb), 0.0, 0.9)
        ff = np.clip(f * (0.55 + 1.0 * turb), 0.0, 1.0)
        edge = np.clip(1.0 - core * 1.15, 0.0, 1.0) * m
        white = np.clip(edge * 0.9 + np.clip((turb - 0.48) * 3.0, 0, 1) * 0.95 * m + ff, 0.0, 1.0)
        buf = np.empty((h2, w2, 4), np.uint8)
        for ch, (bc, wc) in enumerate(((88, 238), (165, 247), (228, 255))):
            buf[..., ch] = bc * (1.0 - white) + wc * white
        buf[..., 3] = np.clip(np.maximum(alpha + white * 0.15 * m, np.clip(ff * 1.7, 0.0, 0.95)), 0.0, 0.95) * 255
        S["jet_tk"] = self._ps_make_tk(Image.fromarray(buf), jk)
        c.itemconfigure(S["it"]["jet"], image=S["jet_tk"], state="normal")
        c.coords(S["it"]["jet"], x0 + S["ox"], y0 + S["oy"])
        S["jet_vis"] = True
        nf += S["fdir"] * S["exit"] * q * (1 / 30.0) * 0.6 * S["jet_every"]

    def _ps_wet_draw(self):
        S, c = self._ps, self.canvas
        w = S["wet"]
        if w.max() < 0.03:
            if S["wet_vis"]:
                c.itemconfigure(S["it"]["wet"], state="hidden")
                S["wet_vis"] = False
            return
        buf = S["buf"]
        thick = np.clip(w * (1.0 / 3.0), 0.0, 1.0)
        edge = np.clip(1.0 - np.abs(w - 1.1) / 0.7, 0.0, 1.0)
        buf[..., 0] = np.clip(70 - 45 * thick + 85 * edge, 0, 255)
        buf[..., 1] = np.clip(170 - 70 * thick + 55 * edge, 0, 255)
        buf[..., 2] = np.clip(255 - 30 * thick, 0, 255)
        buf[..., 3] = np.clip(w * 0.6, 0.0, 0.8) * 255
        img = Image.fromarray(buf).resize(S["wsize"], Image.Resampling.BILINEAR)
        S["wet_tk"] = self._ps_make_tk(img, S["cell"] // 2)
        c.itemconfigure(S["it"]["wet"], image=S["wet_tk"], state="normal")
        S["wet_vis"] = True

    def _ps_render(self):
        S, c = self._ps, self.canvas
        it, ox, oy = S["it"], S["ox"], S["oy"]
        pos, rad = S["pos"], S["rad"]
        X = (pos[:, 0] + ox).tolist()
        Y = (pos[:, 1] + oy).tolist()
        R = rad.tolist()
        reach = S["reach"]
        c.coords(it["wet"], ox, oy)

        for k in range(N_PTS - 1):
            x0, y0, x1, y1 = X[k], Y[k], X[k + 1], Y[k + 1]
            r = (R[k] + R[k + 1]) * 0.5
            c.coords(it["out"][k], x0, y0, x1, y1)
            c.coords(it["body"][k], x0, y0, x1, y1)
            wr = round(r, 1)
            if wr != S["wc"][k]:
                S["wc"][k] = wr
                c.itemconfigure(it["out"][k], width=2 * r + 4)
                c.itemconfigure(it["body"][k], width=2 * r)
                c.itemconfigure(it["water"][k], width=r * 1.7)
                c.itemconfigure(it["hi"][k], width=max(1.5, r * 0.4))
            if reach >= k + 1:
                wx, wy, show = x1, y1, True
            elif reach > k:
                t = reach - k
                wx, wy, show = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, True
            else:
                wx = wy = 0.0
                show = False
            if show:
                c.coords(it["water"][k], x0, y0, wx, wy)
                if not S["wvis"][k]:
                    c.itemconfigure(it["water"][k], state="normal")
                    S["wvis"][k] = True
            elif S["wvis"][k]:
                c.itemconfigure(it["water"][k], state="hidden")
                S["wvis"][k] = False
            dx, dy = x1 - x0, y1 - y0
            ln = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / ln, dx / ln
            if nx + ny > 0:
                nx, ny = -nx, -ny
            hx, hy = nx * r * 0.42, ny * r * 0.42
            c.coords(it["hi"][k], x0 + hx, y0 + hy, x1 + hx, y1 + hy)

        r0, r1 = R[0] * 1.15, R[-1] * 1.12
        c.coords(it["inlet"], X[0] - r0, Y[0] - r0, X[0] + r0, Y[0] + r0)
        c.coords(it["outlet"], X[-1] - r1, Y[-1] - r1, X[-1] + r1, Y[-1] + r1)

        g = S["grab"]
        if g is not None:
            rg = R[g] + 6
            c.coords(it["ring"], X[g] - rg, Y[g] - rg, X[g] + rg, Y[g] + rg)
            if not S["ringvis"]:
                c.itemconfigure(it["ring"], state="normal")
                S["ringvis"] = True
        elif S["ringvis"]:
            c.itemconfigure(it["ring"], state="hidden")
            S["ringvis"] = False

        self._ps_jet_render()

        D = S["d"]
        a, pa = D["alive"], D["prev_alive"]
        items = it["drops"]
        for i in np.flatnonzero(pa & ~a).tolist():
            c.itemconfigure(items[i], state="hidden")
        if a.any():
            sp = np.hypot(D["vx"], D["vy"])
            bucket = (sp > 250).astype(int) + (sp > 550) + (sp > 850)
            wb = np.clip(np.rint(D["size"] * 2.0 * np.array([1.0, 0.95, 0.8, 0.65])[bucket]), 2, 12).astype(int)
            key = bucket * 16 + wb
            chg = a & ((key != D["key"]) | ~pa)
            for i in np.flatnonzero(chg).tolist():
                c.itemconfigure(items[i], fill=DROP_COLORS[int(bucket[i])], width=int(wb[i]), state="normal")
            D["key"] = np.where(a, key, D["key"])
            tl = 0.022 if S["max_active"] > 300 else 0.012
            x1 = (D["px"] + ox).tolist()
            y1 = (D["py"] + oy).tolist()
            x0 = (D["px"] - D["vx"] * tl + ox).tolist()
            y0 = (D["py"] - D["vy"] * tl + oy).tolist()
            for i in np.flatnonzero(a).tolist():
                c.coords(items[i], x0[i], y0[i], x1[i], y1[i])
        D["prev_alive"] = a.copy()
