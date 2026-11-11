import argparse
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from tqdm import tqdm

from config import SETTINGS
from scene_boundary_engine import SceneBoundaryDetector, probe_video
from audio_intelligence_engine import AudioIntelligenceEngine
from rhythm_continuity_engine import TimelinePlanner
from video_render_engine import VideoRenderEngine


logging.basicConfig(filename="editor.log", level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def require_ffmpeg():
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise RuntimeError("Missing required tool(s): " + ", ".join(missing) + ". Install FFmpeg and add it to PATH.")


def choose_video_interactively() -> str:
    video_types = [
        ("Video files", "*.mp4 *.mov *.avi *.mkv *.m4v *.webm"),
        ("All files", "*.*"),
    ]
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.update()
        selected = filedialog.askopenfilename(
            title="Select the video you want Auto Editor to edit",
            filetypes=video_types,
        )
        root.destroy()
    except Exception as exc:
        raise RuntimeError(f"Windows file picker is unavailable: {exc}") from exc
    if not selected:
        raise RuntimeError("No video selected. Run the program again and choose a video file.")
    return selected


class CompleteAutoEditor:
    """Real automatic editor: analyze, cut dead air, pace, polish, render."""

    def __init__(self, input_path: str, output_path: str):
        self.input_path = os.path.abspath(input_path)
        self.output_path = output_path
        self.plan_path = str(Path(output_path).with_suffix(".edit_plan.json"))
        self.info = None
        self.segments = []

    def execute(self, progress_callback=None):
        start = time.time()
        require_ffmpeg()
        if not os.path.isfile(self.input_path):
            raise ValueError(f"Selected input is not a video file: {self.input_path}")

        total_steps = 7
        progress = tqdm(
            total=total_steps,
            desc="Editing",
            unit="step",
            ncols=100,
            dynamic_ncols=True,
            bar_format="{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} {postfix}",
            leave=True,
        )

        def step(label: str):
            progress.set_postfix_str(label)
            progress.update(1)
            if progress_callback:
                progress_callback(progress.n / total_steps, label)

        def render_step_progress(fraction: float):
            # Render is step 5 of 7; report fine-grained movement through its slice.
            if progress_callback:
                progress_callback((4 + fraction) / total_steps, f"rendering clips ({fraction * 100:.0f}%)")

        try:
            self.info = probe_video(self.input_path)
            step("video analyzed")

            audio = AudioIntelligenceEngine(self.input_path)
            silences = audio.detect_non_speech(min_duration=SETTINGS.min_silence_duration) if self.info.has_audio else []
            step("audio analyzed")

            scenes = SceneBoundaryDetector(self.input_path).detect_boundaries(
                frame_step=SETTINGS.scene_sample_step,
                analysis_width=SETTINGS.analysis_width,
            )
            step("scenes analyzed")

            planner = TimelinePlanner()
            self.segments = planner.build(self.info.duration, silences, [s.time for s in scenes])
            if not self.segments:
                raise RuntimeError("Editor could not build a valid timeline")
            step(f"timeline: {len(self.segments)} cuts")

            self._save_plan()
            step("plan saved")

            renderer = VideoRenderEngine()
            try:
                renderer.render(self.input_path, self.info, self.segments, self.output_path, progress=progress, on_progress=render_step_progress)
            finally:
                renderer.cleanup()
            step("render complete")

            elapsed = time.time() - start
            result = {
                "output_path": self.output_path,
                "source_duration": self.info.duration,
                "edited_duration": sum(s.duration for s in self.segments),
                "cuts": len(self.segments),
                "plan_path": self.plan_path,
                "elapsed": elapsed,
            }
            step("complete")
            progress.close()
            return result
        except Exception:
            progress.close()
            raise

    def _save_plan(self):
        data = {
            "source": self.input_path,
            "output": self.output_path,
            "source_duration": self.info.duration,
            "edited_duration": sum(s.duration for s in self.segments),
            "segments": [s.__dict__ for s in self.segments],
        }
        with open(self.plan_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Automatic human-style video editor")
    parser.add_argument("-o", "--output", default="output.mp4", help="Output video file")
    parser.add_argument("-p", "--preset", default="original", choices=["original"] + list(SETTINGS.presets.keys()))
    parser.add_argument("-c", "--color", default="cinematic", choices=["cinematic", "vibrant", "noir", "none"])
    parser.add_argument("--keep-temp", action="store_true", help="Keep temporary render files")
    args = parser.parse_args()

    SETTINGS.target_preset = args.preset
    SETTINGS.target_color_preset = args.color
    SETTINGS.keep_temp = args.keep_temp

    input_path = choose_video_interactively()
    print("=" * 60)
    print(" PYTHON AUTO VIDEO EDITOR")
    print(f" Input: {input_path}")
    print(f" Output: {args.output}")
    print("=" * 60)

    result = CompleteAutoEditor(input_path, args.output).execute()
    print("\nEdit complete")
    print(f"  Output: {result['output_path']}")
    print(f"  Original duration: {result['source_duration']:.1f}s")
    print(f"  Edited duration:   {result['edited_duration']:.1f}s")
    print(f"  Cuts/segments:     {result['cuts']}")
    print(f"  Edit plan:         {result['plan_path']}")


if __name__ == "__main__":
    main()
