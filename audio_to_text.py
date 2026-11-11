"""
audio_to_text.py  (v3.1 - part 3 of 3 of Audio-to-Text)

Main UI panel, standalone window runner, and integration mixin.
Re-exports symbols from audio_utils and audio_workers so external calls continue working uninterrupted.

v3.1: compact collapsible info banner, transcript box that is always visible with smooth scrolling,
and every dialog is owned by the Transcription window so the main app window never jumps forward.
"""
import os
import sys
import queue
import time
import threading

from audio_utils import (  # noqa: F401
    APP_DIR, MODELS_DIR, CONVERTED_DIR, MODEL_INDEX_PATH, SAMPLE_RATE, BLOCK_SECONDS,
    GROUP_SECONDS, NON_ENGLISH, LANGS, CHOOSE_LANG, VIDEO_EXTS, Preset, PRESETS,
    DETECTOR_MODEL, ALL_MODELS, SCRIPT_PROMPT, MODEL_FILES, MODEL_HINTS, SKIP_DIRS,
    Cancelled, Seg, fmt_time, fmt_secs, fmt_length, _trash, lower_priority, restore_priority,
    keep_awake, Timings, Meter, decode_audio, video_to_wav, to_float, drop_repeats,
    seg_line, count_words, TxtWriter
)
from audio_workers import (  # noqa: F401
    is_model_dir, search_roots, find_models, Runner, Engine, transcribe_block,
    detect_probs, pick_language, speech_spans, plan_groups, mix_text, Job, JobResult,
    Transcriber, run_job, TrialResult, run_trial
)

# ----------------------------------------------------------------- GUI
BROWN, BROWN_HOVER = "#6B3E26", "#8A5233"
MEDIA_TYPES = [("Audio / video", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma *.amr *.mka *.mp2 "
                                 "*.mpeg *.mpg *.mp4 *.mkv *.mov *.avi *.webm *.m4v *.3gp *.wmv *.flv *.ts"),
               ("All files", "*.*")]

SHORT_TEXT = ("ℹ Keep the default models.   Speed: English 1 min = 40 s, Hindi 1 min = 50 s   |   "
              "Accuracy: English 92%+, Hindi 85%+")

WARNING_TEXT = (
    "📊 MEASURED PERFORMANCE BENCHMARKS\n"
    "• Processing Speed:\n"
    "  🟢 English: 1 min audio = 40 seconds\n"
    "  🟢 Hindi:   1 min audio = 50 seconds\n\n"
    "• Model Accuracy:\n"
    "  🟢 English: 92%+ accuracy\n"
    "  🟢 Hindi:   85%+ accuracy\n\n"
    "--------------------------------------------------\n\n"
    "We have carefully chosen and optimized these models to balance transcription accuracy and processing speed.\n"
    "Avoid switching to heavier models unnecessarily. Heavier models do not automatically guarantee better "
    "practical results.\n"
    "On basic or low-performance laptops, heavier models can significantly slow down processing.\n"
    "You may end up waiting hours for a transcription that could otherwise be completed much faster.\n"
    "The selected models offer a carefully researched balance of accuracy, speed, and hardware compatibility.\n"
    "Our recommendation: Stick with the default model selections unless you have a specific reason to change "
    "them. The goal is to get accurate transcriptions without unnecessarily sacrificing processing speed."
)

def _build_panel_class():
    import tkinter as tk
    from tkinter import filedialog, messagebox
    import customtkinter as ctk

    class AudioToTextPanel(ctk.CTkFrame):
        def __init__(self, master, engine=None):
            super().__init__(master)
            self.engine = engine or Engine()
            self.q = queue.Queue()
            self.cancel = threading.Event()
            self.worker = None
            self.var_file = tk.StringVar()
            self.var_out = tk.StringVar()
            self.var_lang = tk.StringVar(value="English")
            self.var_ts = tk.BooleanVar(value=False)
            self.search_done = False
            self.pick_msg = {}
            self.mode = "pct"
            self.overlay_on = False
            self.finishing = False
            self.hiding = False
            self.got_progress = False
            self.shown = self.target = self.rate = 0.0
            self.t_upd = 0.0
            self._scroll_left = 0.0
            self._scroll_job = None
            self._build()
            self._init_smooth_scroll()
            self._refresh_note()
            self._refresh_models()
            self.after(100, self._poll)
            self.after(50, self._animate)
            self._start_model_search()

        def _btn(self, parent, text, cmd, **kw):
            return ctk.CTkButton(parent, text=text, command=cmd, fg_color=BROWN,
                                 hover_color=BROWN_HOVER, text_color="white", **kw)

        def _top(self):
            """The Transcription window itself: every dialog is opened on top of it, never on the main app."""
            return self.winfo_toplevel()

        def _build(self):
            self.grid_columnconfigure(1, weight=1)
            self.grid_rowconfigure(9, weight=1, minsize=220)   # the transcript box always keeps room

            # ---- very small info strip (the long text opens only when asked for)
            banner = ctk.CTkFrame(self, fg_color="#FFF4D6", border_color="#E0A800", border_width=1, corner_radius=6)
            banner.grid(row=0, column=0, columnspan=3, padx=12, pady=(8, 2), sticky="ew")
            strip = ctk.CTkFrame(banner, fg_color="transparent")
            strip.pack(fill="x", padx=8, pady=3)
            self.btn_more = ctk.CTkButton(strip, text="More ▾", width=60, height=18, corner_radius=6,
                                          font=ctk.CTkFont(size=10), fg_color=BROWN, hover_color=BROWN_HOVER,
                                          text_color="white", command=self._toggle_info)
            self.btn_more.pack(side="right", padx=(6, 0))
            ctk.CTkLabel(strip, text=SHORT_TEXT, font=ctk.CTkFont(size=10), text_color="#5C4400",
                         anchor="w", justify="left", wraplength=700).pack(side="left", fill="x", expand=True)
            self.info_box = ctk.CTkTextbox(banner, height=96, wrap="word", font=ctk.CTkFont(size=10),
                                           fg_color="#FFF9E8", text_color="#5C4400")
            self.info_box.insert("1.0", WARNING_TEXT)
            self.info_box.configure(state="disabled")
            self.info_open = False

            ctk.CTkLabel(self, text="Audio / Video to Text",
                         font=ctk.CTkFont(size=16, weight="bold")).grid(
                row=1, column=0, columnspan=3, padx=12, pady=(4, 2), sticky="w")

            ctk.CTkLabel(self, text="File").grid(row=2, column=0, padx=12, pady=3, sticky="w")
            ctk.CTkEntry(self, textvariable=self.var_file).grid(row=2, column=1, padx=4, sticky="ew")
            self._btn(self, "Browse", self._pick_file, width=90).grid(row=2, column=2, padx=12)

            ctk.CTkLabel(self, text="Save as").grid(row=3, column=0, padx=12, pady=3, sticky="w")
            ctk.CTkEntry(self, textvariable=self.var_out).grid(row=3, column=1, padx=4, sticky="ew")
            self._btn(self, "Change", self._pick_out, width=90).grid(row=3, column=2, padx=12)

            ctk.CTkLabel(self, text="Audio language").grid(row=4, column=0, padx=12, pady=3, sticky="w")
            ctk.CTkOptionMenu(self, variable=self.var_lang, values=list(LANGS),
                              command=lambda _v: self._refresh_note()).grid(row=4, column=1, padx=4, sticky="w")
            self.lbl_note = ctk.CTkLabel(self, text="", justify="left", wraplength=800)
            self.lbl_note.grid(row=5, column=0, columnspan=3, padx=12, sticky="w")

            ctk.CTkCheckBox(self, text="Add [hh:mm:ss] time before each line",
                            variable=self.var_ts).grid(row=6, column=0, columnspan=3, padx=12, pady=3, sticky="w")

            bar = ctk.CTkFrame(self, fg_color="transparent")
            bar.grid(row=7, column=0, columnspan=3, padx=8, pady=4, sticky="ew")
            bar.grid_columnconfigure((0, 1, 2), weight=1, uniform="buttons")
            small = ctk.CTkFont(size=12)
            self.btn_try = self._btn(
                bar, "Try 1 min first\nThis is just for your test, if you want.\nIt doesn't affect anything.",
                self._on_trial, height=64, font=small)
            self.btn_try.grid(row=0, column=0, padx=4, sticky="ew")
            self.btn_start = self._btn(
                bar, "Do full transcription of\nyour audio\nStart / Resume",
                self._on_start, height=64, font=small)
            self.btn_start.grid(row=0, column=1, padx=4, sticky="ew")
            self.btn_stop = self._btn(
                bar, "Stop here\nProgress is kept, so you can\nlater resume from here",
                self._on_stop, height=64, font=small)
            self.btn_stop.grid(row=0, column=2, padx=4, sticky="ew")
            self.btn_stop.configure(state="disabled")

            head = ctk.CTkFrame(self, fg_color="transparent")
            head.grid(row=8, column=0, columnspan=3, padx=12, sticky="ew")
            head.grid_columnconfigure(0, weight=1)
            self.lbl_transcript = ctk.CTkLabel(head, text="Transcript", anchor="w",
                                               font=ctk.CTkFont(size=14, weight="bold"))
            self.lbl_transcript.grid(row=0, column=0, sticky="w")
            self.btn_copy = self._btn(head, "Copy all text", self._copy, width=130, state="disabled")
            self.btn_copy.grid(row=0, column=1, sticky="e")

            # the dedicated transcript space: vertical scrollbar + smooth wheel scrolling
            self.box = ctk.CTkTextbox(self, wrap="word", height=240, font=ctk.CTkFont(size=15),
                                      border_width=1, border_color="#444444")
            self.box.grid(row=9, column=0, columnspan=3, padx=12, pady=4, sticky="nsew")

            self.lbl_status = ctk.CTkLabel(self, text="Choose a file and the audio language.",
                                           anchor="w", justify="left", wraplength=800)
            self.lbl_status.grid(row=10, column=0, columnspan=3, padx=12, pady=(2, 2), sticky="ew")

            self.lbl_models = ctk.CTkLabel(self, text="", anchor="e", justify="right", wraplength=640,
                                           font=ctk.CTkFont(size=10), text_color="gray50")
            self.lbl_models.grid(row=11, column=0, columnspan=3, padx=12, pady=(0, 4), sticky="e")

            self.pick_frame = ctk.CTkFrame(self, fg_color="transparent")
            self.pick_frame.grid(row=12, column=0, columnspan=3, padx=12, pady=(0, 8), sticky="w")

            self.overlay = ctk.CTkFrame(self, width=440, height=240, corner_radius=16,
                                        border_width=2, border_color=BROWN)
            self.overlay.pack_propagate(False)
            self.lbl_pct = ctk.CTkLabel(self.overlay, text="0%", text_color=BROWN,
                                        font=ctk.CTkFont(size=72, weight="bold"))
            self.lbl_pct.pack(pady=(22, 4))
            self.bar = ctk.CTkProgressBar(self.overlay, width=360, height=14, progress_color=BROWN)
            self.bar.set(0)
            self.bar.pack(pady=4)
            self.lbl_sub = ctk.CTkLabel(self.overlay, text="", wraplength=400, justify="center")
            self.lbl_sub.pack(pady=(8, 0))

        def _toggle_info(self):
            if self.info_open:
                self.info_box.pack_forget()
                self.btn_more.configure(text="More ▾")
            else:
                self.info_box.pack(fill="x", padx=8, pady=(0, 6))
                self.btn_more.configure(text="Less ▴")
            self.info_open = not self.info_open

        # ---- smooth vertical scrolling of the transcript (mouse wheel / touchpad)
        def _init_smooth_scroll(self):
            try:
                tb = self.box._textbox
            except AttributeError:
                return

            def step():
                self._scroll_job = None
                left = self._scroll_left
                if abs(left) < 1:
                    self._scroll_left = 0.0
                    return
                move = int(round(left * 0.3)) or (1 if left > 0 else -1)
                if abs(move) > abs(left):
                    move = int(round(left))
                tb.yview_scroll(move, "pixels")
                self._scroll_left -= move
                self._scroll_job = self.after(12, step)

            def on_wheel(e):
                delta = e.delta if getattr(e, "delta", 0) else (120 if getattr(e, "num", 0) == 4 else -120)
                self._scroll_left += -delta / 120.0 * 90.0   # about 90 pixels per wheel notch
                if self._scroll_job is None:
                    self._scroll_job = self.after(1, step)
                return "break"

            for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                tb.bind(ev, on_wheel)

        def _show_overlay(self, mode, text=""):
            self.mode = mode
            self.finishing = self.hiding = self.got_progress = False
            self.shown = self.target = self.rate = 0.0
            self.bar.stop()
            if mode == "busy":
                self.lbl_pct.configure(text="...")
                self.bar.configure(mode="indeterminate")
                self.bar.start()
            else:
                self.lbl_pct.configure(text="0%")
                self.bar.configure(mode="determinate")
                self.bar.set(0)
            self.lbl_sub.configure(text=text or "Preparing...")
            self.overlay.place(relx=0.5, rely=0.5, anchor="center")
            self.overlay.lift()
            self.overlay_on = True

        def _hide_overlay(self):
            self.bar.stop()
            self.overlay.place_forget()
            self.overlay_on = self.finishing = self.hiding = False

        def _animate(self):
            if self.overlay_on and self.mode == "pct":
                now = time.time()
                if self.finishing:
                    goal = 100.0
                else:
                    goal = self.target
                    if self.rate > 0:
                        goal = min(self.target + self.rate * (now - self.t_upd), self.target + 4.0)
                    goal = min(goal, 99.0)
                gap = goal - self.shown
                if gap > 0:
                    step = max(1.0, gap * 0.25) if self.finishing else max(0.05, gap * 0.06)
                    self.shown = min(goal, self.shown + step)
                self.lbl_pct.configure(text=f"{min(int(self.shown), 100)}%")
                self.bar.set(self.shown / 100)
                if self.finishing and self.shown >= 100 and not self.hiding:
                    self.hiding = True
                    self.after(800, self._hide_overlay)
            self.after(50, self._animate)

        def _refresh_note(self):
            lang = LANGS.get(self.var_lang.get())
            if lang is None:
                self.lbl_note.configure(text="Choose the audio language to continue.")
                return
            spec = PRESETS[lang]
            need = [spec.model] + ([DETECTOR_MODEL] if lang == "auto" else [])
            missing = [m for m in dict.fromkeys(need) if not self.engine.is_downloaded(m)]
            have = "found on this PC" if not missing else "will be downloaded once: " + ", ".join(missing)
            extra = ""
            if lang == "auto":
                extra = ("\nAuto: English and Hindi are detected for each speech part; "
                         "Hindi is written in Devanagari script.")
            self.lbl_note.configure(text=f"Model: {spec.note} ({have}){extra}")

        def _refresh_models(self):
            self.lbl_models.configure(text="Models location:\n" + self.engine.describe(ALL_MODELS))
            for w in self.pick_frame.winfo_children():
                w.destroy()
            if not self.search_done:
                return
            small = ctk.CTkFont(size=11)
            row = 0
            for name in ALL_MODELS:
                missing = not self.engine.is_downloaded(name)
                msg = self.pick_msg.get(name)
                if not missing and not msg:
                    continue
                ctk.CTkLabel(self.pick_frame, font=small,
                             text=f"{name}: " + ("not found on this PC" if missing else "found")).grid(
                    row=row, column=0, padx=(0, 8), sticky="w")
                if missing:
                    self._btn(self.pick_frame, "Pick model folder", lambda n=name: self._pick_model(n),
                              width=130, height=24, font=small).grid(row=row, column=1, padx=4, pady=1)
                if msg:
                    ctk.CTkLabel(self.pick_frame, text=msg[0], text_color=msg[1],
                                 font=ctk.CTkFont(size=12, weight="bold")).grid(row=row, column=2, padx=8)
                row += 1

        def _pick_model(self, name):
            folder = filedialog.askdirectory(parent=self._top(), title=f"Select the folder of the model: {name}")
            if not folder:
                return
            if self.engine.register(name, folder):
                self.pick_msg[name] = ("Passed", "#2E7D32")
            else:
                self.pick_msg[name] = ("Wrongly picked try again", "#C62828")
            self._refresh_note()
            self._refresh_models()

        def _start_model_search(self):
            def work():
                try:
                    self.engine.locate(ALL_MODELS, self.cancel)
                    self.q.put(("status", "Model search finished. Choose a file and the audio language."))
                except Cancelled:
                    self.q.put(("status", "Model search stopped."))
                except Exception as e:
                    self.q.put(("status", f"Model search failed ({e}). Missing models will be downloaded."))
                self.q.put(("idle", None))
            self._spawn(work, "busy", "Searching your PC for the Whisper models...")

        def _pick_file(self):
            if self.worker and self.worker.is_alive():
                return
            top = self._top()
            p = filedialog.askopenfilename(parent=top, filetypes=MEDIA_TYPES)
            top.lift()  # the Transcription window stays in front, the main app window is never raised
            if not p:
                return
            self.var_out.set(os.path.splitext(p)[0] + ".txt")
            if os.path.splitext(p)[1].lower() in VIDEO_EXTS:
                self._convert_video(p)
            else:
                self.var_file.set(p)

        def _convert_video(self, video):
            self.var_file.set("")
            self.lbl_status.configure(text="Converting the video to audio...")

            def work():
                try:
                    self.q.put(("video_ready", video_to_wav(video, self.cancel)))
                except Cancelled:
                    self.q.put(("status", "Video conversion stopped. Choose the file again."))
                except Exception as e:
                    self.q.put(("error", str(e)))
                self.q.put(("idle", None))
            self._spawn(work, "busy", "Converting the video to audio...")

        def _pick_out(self):
            top = self._top()
            p = filedialog.asksaveasfilename(parent=top, defaultextension=".txt", filetypes=[("Text", "*.txt")])
            top.lift()
            if p:
                self.var_out.set(p)

        def _job(self):
            f = self.var_file.get().strip()
            if not f or not os.path.isfile(f):
                messagebox.showwarning("Audio to Text", "Choose an existing audio or video file first.",
                                       parent=self._top())
                return None
            if self.var_lang.get() not in LANGS:
                messagebox.showwarning("Audio to Text", "Choose the audio language first.", parent=self._top())
                return None
            return Job(f, self.var_lang.get(), self.var_out.get().strip(), self.var_ts.get())

        def _busy(self, on):
            for b in (self.btn_try, self.btn_start):
                b.configure(state="disabled" if on else "normal")
            self.btn_stop.configure(state="normal" if on else "disabled")

        def _spawn(self, target, mode="pct", text=""):
            if self.worker and self.worker.is_alive():
                return
            self.cancel.clear()
            self._busy(True)
            self._show_overlay(mode, text)
            self.worker = threading.Thread(target=target, daemon=True)
            self.worker.start()

        def _set_box(self, text):
            self.box.delete("1.0", "end")
            self.box.insert("1.0", text)
            self.box.yview_moveto(0)
            self.btn_copy.configure(state="normal" if text.strip() else "disabled")
            self.update_idletasks()

        def _copy(self):
            text = self.box.get("1.0", "end-1c")
            if not text.strip():
                return
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()
            self.btn_copy.configure(text="Copied!")
            self.after(2000, lambda: self.btn_copy.configure(text="Copy all text"))

        def _on_trial(self):
            job = self._job()
            if not job:
                return
            self._set_box("")
            self.lbl_transcript.configure(text="Transcript")

            def work():
                try:
                    r = run_trial(job, self.engine, self.cancel,
                                  on_status=lambda m: self.q.put(("status", m)),
                                  on_progress=lambda f, eta: self.q.put(("progress", (f, eta))))
                    self.q.put(("trial", r))
                except Cancelled:
                    self.q.put(("status", "Stopped."))
                except Exception as e:
                    self.q.put(("error", str(e)))
                self.q.put(("idle", None))
            self._spawn(work)

        def _on_start(self):
            job = self._job()
            if not job:
                return
            self._set_box("")
            self.lbl_transcript.configure(text="Transcript")

            def work():
                try:
                    res = run_job(job, self.engine, self.cancel,
                                  on_status=lambda m: self.q.put(("status", m)),
                                  on_progress=lambda f, eta: self.q.put(("progress", (f, eta))))
                    self.q.put(("done", res))
                except Cancelled:
                    self.q.put(("status", "Stopped. Press Start / Resume to continue from the saved point."))
                except Exception as e:
                    self.q.put(("error", str(e)))
                self.q.put(("idle", None))
            self._spawn(work)

        def _on_stop(self):
            self.cancel.set()
            self.lbl_status.configure(text="Stopping...")

        def _poll(self):
            try:
                while True:
                    kind, val = self.q.get_nowait()
                    if kind == "status":
                        self.lbl_status.configure(text=val)
                        if self.overlay_on and not self.got_progress:
                            self.lbl_sub.configure(text=val)
                    elif kind == "progress":
                        f, eta = val
                        self.got_progress = True
                        self.target = f * 100
                        self.t_upd = time.time()
                        self.rate = (100 - self.target) / eta if eta and eta > 0 else 0.0
                        left = "Transcribing..." if eta is None else f"About {fmt_time(eta)} left"
                        self.lbl_sub.configure(text=left)
                        self.lbl_status.configure(text=f"{f * 100:.0f}% done  -  {left}")
                    elif kind == "video_ready":
                        self.var_file.set(val)
                        self.var_lang.set(CHOOSE_LANG)
                        self._refresh_note()
                        msg = "Video converted to audio. Now choose the audio language for transcription."
                        self.lbl_status.configure(text=msg)
                        messagebox.showinfo("Audio to Text", msg, parent=self._top())
                    elif kind == "done":
                        try:
                            with open(val.path, "r", encoding="utf-8-sig") as fh:
                                text = fh.read()
                        except OSError:
                            text = ""
                        self._set_box(text or "(no speech found)")
                        self.lbl_transcript.configure(text=f"Transcript  -  {val.words:,} words")
                        self.lbl_status.configure(
                            text=f"Done. Saved: {val.path}\n"
                                 f"User audio length = {fmt_length(val.duration)}\n"
                                 f"Total words: {val.words:,}{val.mix}\n{val.timings}")
                        self.finishing = True
                    elif kind == "trial":
                        self._set_box(val.text or "(no speech found in this minute)")
                        self.lbl_transcript.configure(text=f"Transcript (test)  -  {val.words:,} words")
                        self.lbl_status.configure(
                            text=f"User audio length = {fmt_length(val.total_audio)}\n"
                                 f"Speed: {val.speed:.2f}x real time on this laptop. "
                                 f"Whole file would take about "
                                 f"{fmt_time(val.estimate_total)}.{val.mix}\n{val.timings}")
                        self.finishing = True
                    elif kind == "error":
                        self.lbl_status.configure(text="Error: " + val)
                        messagebox.showerror("Audio to Text", val, parent=self._top())
                    elif kind == "idle":
                        self.search_done = True
                        self._busy(False)
                        if not self.finishing:
                            self._hide_overlay()
                        self._refresh_note()
                        self._refresh_models()
            except queue.Empty:
                pass
            self.after(100, self._poll)

    return AudioToTextPanel


def open_window(parent=None):
    """Open the tool in its own window (More Tools wiring)."""
    import customtkinter as ctk
    Panel = _build_panel_class()
    win = ctk.CTkToplevel(parent) if parent else ctk.CTk()
    win.title("English and Hindi offline Ai Transcription fast and accurate tested and Working")
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    w, h = min(900, sw - 40), min(960, sh - 110)      # always fits the screen, so nothing is cut off
    win.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
    win.minsize(620, 520)
    panel = Panel(win)
    panel.pack(fill="both", expand=True)
    win.lift()
    win.after(150, win.focus_force)
    return win


class AudioTranscriberMixin:
    """Mixin for integrating Audio/Video Transcription into main app."""

    def init_audio_transcriber(self):
        self._transcriber_win = None

    def open_audio_transcriber(self):
        if hasattr(self, "_transcriber_win") and self._transcriber_win is not None and self._transcriber_win.winfo_exists():
            self._transcriber_win.lift()
            self._transcriber_win.focus_force()
            return
        try:
            self._transcriber_win = open_window(self)
        except Exception as e:
            import tkinter.messagebox as msgbox
            msgbox.showerror("Transcription Tool", f"Could not launch transcription window:\n{e}")


if __name__ == "__main__":
    w = open_window(None)
    w.mainloop()
