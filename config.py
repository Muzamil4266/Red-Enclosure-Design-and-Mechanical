import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, Tuple

# Prevents a console window from flashing on screen each time ffmpeg/ffprobe
# is spawned from the windowed GUI. No-op on non-Windows platforms.
SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


@dataclass
class MasterConfig:
    # CPU/RAM safe defaults for an 8GB laptop.
    low_memory_mode: bool = True
    analysis_width: int = 426
    scene_sample_step: int = 12
    max_render_chunk_seconds: float = 8.0

    # Editing behavior.
    min_silence_duration: float = 0.45
    silence_padding: float = 0.12
    min_segment_duration: float = 0.45
    max_segment_duration: float = 8.0
    keep_short_video_full: bool = True
    short_video_seconds: float = 18.0
    punch_in_every: int = 3

    # Export.
    target_preset: str = "original"
    presets: Dict[str, Tuple[int, int]] = field(default_factory=lambda: {
        "youtube": (1920, 1080),
        "tiktok": (1080, 1920),
        "cinema": (1920, 800),
        "square": (1080, 1080),
    })
    target_color_preset: str = "cinematic"
    video_encoder: str = "libx264"
    video_preset: str = "veryfast"
    crf: int = 18
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    fps: float = None  # None = auto-match source's own frame rate
    threads: int = 2
    parallel_render_workers: int = 3
    keep_temp: bool = False


SETTINGS = MasterConfig()
