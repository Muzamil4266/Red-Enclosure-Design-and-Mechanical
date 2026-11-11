import collections
import contextlib
import logging
import os
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from typing import List

from config import SUBPROCESS_FLAGS


@dataclass
class SilenceRange:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class AudioIntelligenceEngine:
    """FFmpeg + WebRTC VAD backed audio analysis: finds stretches with no
    human speech (silence, music, noise) so they can be cut."""

    def __init__(self, video_path: str):
        self.video_path = video_path

    @staticmethod
    def extract_audio_to_wav(video_path: str, output_wav: str, sr: int = 44100) -> bool:
        cmd = [
            "ffmpeg", "-y", "-i", video_path, "-vn",
            "-acodec", "pcm_s16le", "-ar", str(sr), "-ac", "1", output_wav
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True, creationflags=SUBPROCESS_FLAGS)
            return True
        except Exception as exc:
            logging.warning("Audio extraction failed: %s", exc)
            return False

    def detect_non_speech(self, aggressiveness: int = 2, frame_ms: int = 30,
                           min_duration: float = 0.45, hangover_ms: int = 300,
                           trigger_ratio: float = 0.75) -> List[SilenceRange]:
        """Voice-activity based: flags stretches with no human speech,
        regardless of language, and unlike amplitude thresholds it ignores
        loud non-speech sound (music, noise, typing) instead of keeping it.

        Uses a rolling-window vote (hangover_ms) before flipping between
        speech/non-speech, so a brief mid-sentence pause or a single
        misclassified frame doesn't chop a sentence into pieces.
        Requires webrtcvad-wheels; gracefully returns empty list if missing."""
        try:
            import webrtcvad
        except ImportError:
            logging.warning("webrtcvad is not installed. VAD non-speech detection disabled.")
            return []

        sample_rate = 16000
        tmp_wav = tempfile.mktemp(suffix=".wav")
        if not self.extract_audio_to_wav(self.video_path, tmp_wav, sr=sample_rate):
            return []

        try:
            with contextlib.closing(wave.open(tmp_wav, "rb")) as wf:
                pcm = wf.readframes(wf.getnframes())

            vad = webrtcvad.Vad(aggressiveness)
            frame_bytes = int(sample_rate * (frame_ms / 1000.0) * 2)  # 16-bit = 2 bytes/sample
            frames = [pcm[i:i + frame_bytes] for i in range(0, len(pcm) - frame_bytes + 1, frame_bytes)]
            votes = [vad.is_speech(f, sample_rate) for f in frames]

            window = max(1, hangover_ms // frame_ms)
            ring = collections.deque(maxlen=window)
            triggered = False
            speech_runs = []
            run_start = 0
            for i, is_speech in enumerate(votes):
                ring.append(is_speech)
                voiced = sum(ring)
                if not triggered and voiced > trigger_ratio * len(ring):
                    triggered = True
                    run_start = max(0, i - len(ring) + 1)
                elif triggered and voiced < (1 - trigger_ratio) * len(ring):
                    triggered = False
                    speech_runs.append((run_start, i))
            if triggered:
                speech_runs.append((run_start, len(votes)))

            non_speech: List[SilenceRange] = []
            cursor = 0
            for start_idx, end_idx in speech_runs:
                if start_idx > cursor:
                    gap = (cursor * frame_ms / 1000.0, start_idx * frame_ms / 1000.0)
                    if gap[1] - gap[0] >= min_duration:
                        non_speech.append(SilenceRange(*gap))
                cursor = end_idx
            if cursor < len(votes):
                gap = (cursor * frame_ms / 1000.0, len(votes) * frame_ms / 1000.0)
                if gap[1] - gap[0] >= min_duration:
                    non_speech.append(SilenceRange(*gap))

            logging.info("Detected %d non-speech ranges (VAD-based)", len(non_speech))
            return non_speech
        finally:
            if os.path.exists(tmp_wav):
                os.remove(tmp_wav)
