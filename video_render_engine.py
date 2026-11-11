import concurrent.futures
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import List

from config import SETTINGS, SUBPROCESS_FLAGS
from scene_boundary_engine import VideoInfo
from rhythm_continuity_engine import EditSegment


class VideoRenderEngine:
    """Renders planned edit segments through FFmpeg."""

    def __init__(self):
        self.temp_dir = tempfile.mkdtemp(prefix="autoedit_render_")

    def render(self, source: str, info: VideoInfo, segments: List[EditSegment], output_path: str, progress=None, on_progress=None) -> str:
        if not segments:
            raise RuntimeError("No edit segments to render")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True) if Path(output_path).parent != Path(".") else None

        clips = [None] * len(segments)
        progress_lock = threading.Lock()
        completed = 0

        def render_one(idx: int) -> None:
            nonlocal completed
            segment = segments[idx]
            clip = os.path.join(self.temp_dir, f"clip_{idx:04d}.mp4")
            self._render_clip(source, info, segment, clip, punch_in=(idx % SETTINGS.punch_in_every == 1))
            clips[idx] = clip
            with progress_lock:
                completed += 1
                if progress:
                    progress.set_postfix_str(f"rendering clips {completed}/{len(segments)}")
                if on_progress:
                    on_progress(completed / len(segments))

        workers = max(1, SETTINGS.parallel_render_workers)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(render_one, range(len(segments))))

        concat_file = os.path.join(self.temp_dir, "concat.txt")
        with open(concat_file, "w", encoding="utf-8") as handle:
            for clip in clips:
                handle.write(f"file '{os.path.abspath(clip).replace(chr(92), '/')}'\n")

        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
            "-c", "copy", "-movflags", "+faststart", output_path
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True, creationflags=SUBPROCESS_FLAGS)
        except subprocess.CalledProcessError:
            # Copy concat can fail across some FFmpeg builds; re-encode as fallback.
            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
                "-c:v", SETTINGS.video_encoder, "-preset", SETTINGS.video_preset,
                "-crf", str(SETTINGS.crf), "-pix_fmt", "yuv420p",
                "-c:a", SETTINGS.audio_codec, "-b:a", SETTINGS.audio_bitrate,
                "-movflags", "+faststart", output_path
            ]
            subprocess.run(cmd, capture_output=True, text=True, check=True, creationflags=SUBPROCESS_FLAGS)
        return output_path

    def _render_clip(self, source: str, info: VideoInfo, segment: EditSegment, output_path: str, punch_in: bool):
        filters = []
        if punch_in:
            filters.append("crop=trunc(iw*0.92/2)*2:trunc(ih*0.92/2)*2")
        if SETTINGS.target_preset == "original":
            # Keep the source's own resolution/aspect ratio untouched.
            filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
        else:
            width, height = SETTINGS.presets[SETTINGS.target_preset]
            filters.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease")
            filters.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")
        filters.append("setsar=1")
        if SETTINGS.target_color_preset == "cinematic":
            filters.append("eq=contrast=1.06:saturation=1.08:brightness=0.01")
        elif SETTINGS.target_color_preset == "vibrant":
            filters.append("eq=contrast=1.08:saturation=1.22")
        elif SETTINGS.target_color_preset == "noir":
            filters.append("format=gray")

        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{segment.start:.3f}", "-to", f"{segment.end:.3f}",
            "-i", source,
            "-vf", ",".join(filters),
        ]
        if SETTINGS.fps:  # explicit override only; otherwise ffmpeg keeps the source's native fps
            cmd.extend(["-r", str(SETTINGS.fps)])
        cmd.extend([
            "-c:v", SETTINGS.video_encoder,
            "-preset", SETTINGS.video_preset,
            "-crf", str(SETTINGS.crf),
            "-pix_fmt", "yuv420p",
            "-threads", str(SETTINGS.threads),
        ])
        if info.has_audio:
            cmd.extend([
                "-af", "highpass=f=80,lowpass=f=12000,loudnorm=I=-16:TP=-1.5:LRA=11",
                "-c:a", SETTINGS.audio_codec, "-b:a", SETTINGS.audio_bitrate,
            ])
        else:
            cmd.append("-an")
        cmd.append(output_path)
        subprocess.run(cmd, capture_output=True, text=True, check=True, creationflags=SUBPROCESS_FLAGS)

    def cleanup(self):
        if not SETTINGS.keep_temp and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)
