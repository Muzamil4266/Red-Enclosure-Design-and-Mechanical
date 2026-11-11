"""GUI front end for the auto video editor. Wraps the existing pipeline
(complete_auto_editor.py) with no logic duplication - it only reads/writes
SETTINGS and calls CompleteAutoEditor.execute() in a background thread."""

import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from config import SETTINGS, MasterConfig
from complete_auto_editor import CompleteAutoEditor, require_ffmpeg

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

PRESETS = ["original", "youtube", "tiktok", "cinema", "square"]
COLOR_STYLES = ["cinematic", "vibrant", "noir", "none"]
ENCODER_SPEEDS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
AUDIO_BITRATES = ["128k", "192k", "256k", "320k"]

# name -> (label, min, max, is_int, decimals)
SLIDER_SPECS = {
    "crf": ("Quality / CRF (lower = better, bigger file)", 0, 51, True, 0),
    "threads": ("Encoder threads per clip", 1, 8, True, 0),
    "parallel_render_workers": ("Clips rendered in parallel", 1, 8, True, 0),
    "min_silence_duration": ("Min non-speech gap to cut (sec)", 0.1, 2.0, False, 2),
    "silence_padding": ("Cut padding around speech (sec)", 0.0, 0.5, False, 2),
    "min_segment_duration": ("Min kept segment length (sec)", 0.1, 2.0, False, 2),
    "max_segment_duration": ("Max segment before pacing cut (sec)", 2.0, 20.0, False, 1),
    "short_video_seconds": ("Skip editing if source shorter than (sec)", 5.0, 60.0, False, 0),
    "punch_in_every": ("Punch-in every N clips", 1, 10, True, 0),
}


class AutoEditorGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Auto Video Editor")
        self.geometry("580x780")

        self.input_path = ctk.StringVar()
        self.output_path = ctk.StringVar(value="output.mp4")
        self.fps_text = ctk.StringVar()
        self.keep_temp_var = ctk.BooleanVar()
        self.option_vars = {}   # name -> StringVar, for dropdowns
        self.sliders = {}       # name -> (CTkSlider, value_label)

        self._build_file_section()
        self._build_settings_section()
        self._build_action_section()
        self._load_from_settings()

    # ---------- layout ----------
    def _build_file_section(self):
        frame = ctk.CTkFrame(self)
        frame.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(frame, text="Input video").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ctk.CTkEntry(frame, textvariable=self.input_path, width=340).grid(row=0, column=1, padx=8, pady=6)
        ctk.CTkButton(frame, text="Browse", width=80, command=self._pick_input).grid(row=0, column=2, padx=8, pady=6)

        ctk.CTkLabel(frame, text="Output file").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        ctk.CTkEntry(frame, textvariable=self.output_path, width=340).grid(row=1, column=1, padx=8, pady=6)
        ctk.CTkButton(frame, text="Save as", width=80, command=self._pick_output).grid(row=1, column=2, padx=8, pady=6)

    def _build_settings_section(self):
        frame = ctk.CTkScrollableFrame(self, label_text="Settings")
        frame.pack(fill="both", expand=True, padx=16, pady=8)
        row = 0

        row = self._add_option_row(frame, row, "target_preset", "Export preset", PRESETS)
        row = self._add_option_row(frame, row, "target_color_preset", "Color style", COLOR_STYLES)
        row = self._add_option_row(frame, row, "video_preset", "Encoder speed", ENCODER_SPEEDS)
        row = self._add_option_row(frame, row, "audio_bitrate", "Audio bitrate", AUDIO_BITRATES)

        ctk.CTkLabel(frame, text="FPS override (blank = match source)").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        ctk.CTkEntry(frame, textvariable=self.fps_text, width=100).grid(row=row, column=1, sticky="w", padx=8, pady=6)
        row += 1

        for name, (label, lo, hi, is_int, decimals) in SLIDER_SPECS.items():
            row = self._add_slider_row(frame, row, name, label, lo, hi, is_int, decimals)

        ctk.CTkCheckBox(frame, text="Keep temporary render files", variable=self.keep_temp_var).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=8, pady=10)

    def _add_option_row(self, frame, row, name, label, values):
        var = ctk.StringVar()
        self.option_vars[name] = var
        ctk.CTkLabel(frame, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=6)
        ctk.CTkOptionMenu(frame, variable=var, values=values, width=160).grid(
            row=row, column=1, columnspan=2, sticky="w", padx=8, pady=6)
        return row + 1

    def _add_slider_row(self, frame, row, name, label, lo, hi, is_int, decimals):
        ctk.CTkLabel(frame, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=6)
        value_label = ctk.CTkLabel(frame, text="")
        value_label.grid(row=row, column=2, sticky="e", padx=8)

        def on_change(v, _label=value_label, _decimals=decimals, _is_int=is_int):
            value = int(float(v)) if _is_int else round(float(v), _decimals)
            _label.configure(text=str(value))

        steps = int(hi - lo) if is_int else int((hi - lo) * (10 ** decimals))
        slider = ctk.CTkSlider(frame, from_=lo, to=hi, number_of_steps=max(1, steps), command=on_change, width=200)
        slider.grid(row=row, column=1, sticky="w", padx=8, pady=6)
        self.sliders[name] = (slider, value_label)
        return row + 1

    def _build_action_section(self):
        frame = ctk.CTkFrame(self)
        frame.pack(fill="x", padx=16, pady=(8, 16))

        ctk.CTkButton(frame, text="Reset to Defaults", fg_color="gray30", command=self._reset_defaults).pack(
            side="left", padx=8, pady=8)
        self.start_button = ctk.CTkButton(frame, text="Start Editing", command=self._start)
        self.start_button.pack(side="left", padx=8, pady=8)

        self.progress = ctk.CTkProgressBar(self, mode="determinate")
        self.progress.set(0)
        self.progress.pack(fill="x", padx=16, pady=(0, 4))
        self.status_label = ctk.CTkLabel(self, text="Ready")
        self.status_label.pack(padx=16, pady=(0, 12))

    # ---------- settings <-> widgets ----------
    def _set_slider(self, name, value):
        slider, label = self.sliders[name]
        slider.set(value)
        _, _, _, is_int, decimals = SLIDER_SPECS[name]
        label.configure(text=str(int(value) if is_int else round(value, decimals)))

    def _load_from_settings(self):
        self.option_vars["target_preset"].set(SETTINGS.target_preset)
        self.option_vars["target_color_preset"].set(SETTINGS.target_color_preset)
        self.option_vars["video_preset"].set(SETTINGS.video_preset)
        self.option_vars["audio_bitrate"].set(SETTINGS.audio_bitrate)
        self.fps_text.set("" if not SETTINGS.fps else str(SETTINGS.fps))
        self.keep_temp_var.set(SETTINGS.keep_temp)

        for name in SLIDER_SPECS:
            self._set_slider(name, getattr(SETTINGS, name))

    def _apply_to_settings(self):
        SETTINGS.target_preset = self.option_vars["target_preset"].get()
        SETTINGS.target_color_preset = self.option_vars["target_color_preset"].get()
        SETTINGS.video_preset = self.option_vars["video_preset"].get()
        SETTINGS.audio_bitrate = self.option_vars["audio_bitrate"].get()
        fps_text = self.fps_text.get().strip()
        SETTINGS.fps = float(fps_text) if fps_text else None
        SETTINGS.keep_temp = bool(self.keep_temp_var.get())

        for name, (slider, _label) in self.sliders.items():
            _, _, _, is_int, decimals = SLIDER_SPECS[name]
            value = slider.get()
            setattr(SETTINGS, name, int(value) if is_int else round(value, decimals))

    def _reset_defaults(self):
        defaults = MasterConfig()
        for field_name in defaults.__dataclass_fields__:
            setattr(SETTINGS, field_name, getattr(defaults, field_name))
        self._load_from_settings()
        self.status_label.configure(text="Settings reset to defaults")

    # ---------- file pickers ----------
    def _pick_input(self):
        path = filedialog.askopenfilename(
            title="Select the video to edit",
            filetypes=[("Video files", "*.mp4 *.mov *.avi *.mkv *.m4v *.webm"), ("All files", "*.*")],
        )
        if path:
            self.input_path.set(path)

    def _pick_output(self):
        path = filedialog.asksaveasfilename(
            title="Save edited video as", defaultextension=".mp4", filetypes=[("MP4 video", "*.mp4")],
        )
        if path:
            self.output_path.set(path)

    # ---------- run ----------
    def _start(self):
        input_path = self.input_path.get().strip()
        output_path = self.output_path.get().strip()
        if not input_path or not Path(input_path).is_file():
            messagebox.showerror("Missing input", "Choose a valid input video first.")
            return
        if not output_path:
            messagebox.showerror("Missing output", "Choose an output file name.")
            return

        try:
            self._apply_to_settings()
        except ValueError:
            messagebox.showerror("Invalid setting", "FPS override must be a number, or left blank.")
            return

        self.start_button.configure(state="disabled", text="Working...")
        self.status_label.configure(text="Starting...")
        self.progress.set(0)

        threading.Thread(target=self._run_editor, args=(input_path, output_path), daemon=True).start()

    def _update_progress(self, fraction, label):
        self.after(0, lambda: (self.progress.set(fraction), self.status_label.configure(text=label)))

    def _run_editor(self, input_path, output_path):
        try:
            require_ffmpeg()
            result = CompleteAutoEditor(input_path, output_path).execute(progress_callback=self._update_progress)
            self.after(0, self._on_success, result)
        except Exception as exc:
            self.after(0, self._on_error, exc)

    def _on_success(self, result):
        self.progress.set(1)
        self.start_button.configure(state="normal", text="Start Editing")
        self.status_label.configure(text=f"Done - {result['edited_duration']:.1f}s output, {result['cuts']} cuts")
        messagebox.showinfo(
            "Edit complete",
            f"Output: {result['output_path']}\n"
            f"Original duration: {result['source_duration']:.1f}s\n"
            f"Edited duration: {result['edited_duration']:.1f}s\n"
            f"Cuts/segments: {result['cuts']}\n"
            f"Elapsed: {result['elapsed']:.1f}s",
        )

    def _on_error(self, exc):
        self.progress.set(0)
        self.start_button.configure(state="normal", text="Start Editing")
        self.status_label.configure(text="Failed - see error")
        messagebox.showerror("Edit failed", str(exc))


def main():
    AutoEditorGUI().mainloop()


if __name__ == "__main__":
    main()
