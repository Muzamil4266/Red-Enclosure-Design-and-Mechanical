import json
import logging
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List

from config import SUBPROCESS_FLAGS


class TransitionType(Enum):
    HARD_CUT = "hard_cut"
    UNKNOWN = "unknown"


@dataclass
class SceneBoundary:
    frame: int
    time: float
    transition_type: TransitionType
    confidence: float
    features: Dict


@dataclass
class VideoInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


def _run(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=True, creationflags=SUBPROCESS_FLAGS)


def probe_video(path: str) -> VideoInfo:
    result = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", path
    ])
    data = json.loads(result.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise ValueError(f"No video stream found: {path}")
    audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    duration = float(video.get("duration") or data.get("format", {}).get("duration") or 0)
    rate = video.get("avg_frame_rate") or "30/1"
    fps = 30.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        if float(den):
            fps = float(num) / float(den)
    return VideoInfo(
        path=str(Path(path)),
        duration=max(0.0, duration),
        width=int(video.get("width", 1920)),
        height=int(video.get("height", 1080)),
        fps=fps,
        has_audio=audio,
    )


class SceneBoundaryDetector:
    """Fast optional scene boundary detection for pacing cut suggestions."""

    def __init__(self, video_path: str):
        self.video_path = video_path
        self.info = probe_video(video_path)

    def detect_boundaries(self, frame_step: int = 12, analysis_width: int = 426, **_) -> List[SceneBoundary]:
        try:
            import cv2
            import numpy as np
        except Exception:
            return []

        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            return []

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = cap.get(cv2.CAP_PROP_FPS) or self.info.fps or 30.0
        prev_hist = None
        boundaries: List[SceneBoundary] = []
        step = max(1, int(frame_step))

        for frame_idx in range(0, total_frames, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            if analysis_width and frame.shape[1] > analysis_width:
                h = max(2, int(frame.shape[0] * analysis_width / frame.shape[1]))
                frame = cv2.resize(frame, (analysis_width, h), interpolation=cv2.INTER_AREA)
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [24, 24], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            if prev_hist is not None:
                corr = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
                if corr < 0.42:
                    boundaries.append(SceneBoundary(
                        frame=frame_idx,
                        time=frame_idx / fps,
                        transition_type=TransitionType.HARD_CUT,
                        confidence=float(1.0 - corr),
                        features={"hist_corr": float(corr)},
                    ))
            prev_hist = hist

        cap.release()
        logging.info("Detected %d scene boundaries", len(boundaries))
        return boundaries
