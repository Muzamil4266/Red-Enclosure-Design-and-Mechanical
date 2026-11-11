from dataclasses import dataclass
from typing import Iterable, List

from config import SETTINGS
from audio_intelligence_engine import SilenceRange


@dataclass
class EditSegment:
    start: float
    end: float
    reason: str = "keep"

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class TimelinePlanner:
    """Turns audio/scene analysis into real edit segments."""

    def build(self, duration: float, silences: List[SilenceRange], scene_times: Iterable[float] = ()) -> List[EditSegment]:
        if duration <= 0:
            return []
        if SETTINGS.keep_short_video_full and duration <= SETTINGS.short_video_seconds:
            return [EditSegment(0.0, duration, "short_video_full")]

        remove = []
        pad = SETTINGS.silence_padding
        for silence in silences:
            if silence.duration >= SETTINGS.min_silence_duration:
                start = max(0.0, silence.start + pad)
                end = min(duration, silence.end - pad)
                if end > start:
                    remove.append((start, end))

        keep = self._invert(duration, remove)
        kept_total = sum(end - start for start, end in keep)
        if kept_total < 0.3 * duration:
            # Detection would strip most of the video. Bail out and keep the
            # full source rather than collapsing to a few seconds.
            return [EditSegment(0.0, duration, "safety_fallback_removal_too_aggressive")]

        segments = []
        scene_times = sorted(t for t in scene_times if 0 < t < duration)
        for start, end in keep:
            segments.extend(self._split_for_pacing(start, end, scene_times))

        segments = [s for s in segments if s.duration >= SETTINGS.min_segment_duration]
        if not segments:
            return [EditSegment(0.0, duration, "fallback_full_video")]
        return self._merge_tiny_gaps(segments)

    def _invert(self, duration: float, remove: List[tuple]) -> List[tuple]:
        if not remove:
            return [(0.0, duration)]
        remove = sorted(remove)
        keep = []
        cursor = 0.0
        for start, end in remove:
            if start > cursor:
                keep.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < duration:
            keep.append((cursor, duration))
        return keep

    def _split_for_pacing(self, start: float, end: float, scene_times: List[float]) -> List[EditSegment]:
        max_len = SETTINGS.max_segment_duration
        if end - start <= max_len:
            return [EditSegment(start, end, "speech_or_action")]
        points = [start]
        cursor = start
        while end - cursor > max_len:
            candidates = [t for t in scene_times if cursor + 2.0 <= t <= cursor + max_len]
            cut = candidates[-1] if candidates else cursor + max_len
            points.append(cut)
            cursor = cut
        points.append(end)
        return [EditSegment(points[i], points[i + 1], "paced_cut") for i in range(len(points) - 1)]

    def _merge_tiny_gaps(self, segments: List[EditSegment]) -> List[EditSegment]:
        if not segments:
            return []
        out = [segments[0]]
        for segment in segments[1:]:
            last = out[-1]
            gap = segment.start - last.end
            if 0 < gap < 0.10:
                out[-1] = EditSegment(last.start, segment.end, last.reason)
            else:
                out.append(segment)
        return out
