"""
audio_utils.py  (part 1 of 3 of Audio-to-Text)

Constants, data classes, time formatting, process priority/power management,
audio decoding (av), video-to-WAV conversion, and streaming TxtWriter.
No GUI or model loading dependencies here.
"""
import os
import sys
import re
import time
import json
import ctypes
import wave
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))
MODELS_DIR = os.path.join(APP_DIR, "models")                 # where missing models are downloaded to
CONVERTED_DIR = os.path.join(APP_DIR, "converted")           # WAV files made from videos
MODEL_INDEX_PATH = os.path.join(APP_DIR, "audio_to_text_models.json")   # remembers where models were found
SAMPLE_RATE = 16000
BLOCK_SECONDS = 480          # audio is transcribed and saved in blocks of this length
GROUP_SECONDS = 28           # auto mode: neighbouring speech in the same language is merged up to this length
NON_ENGLISH = "hi"           # every non-English part is Hindi (Devanagari script)

LANGS = {"English": "en", "Hindi": "hi", "Auto (English + Hindi mixed)": "auto"}
CHOOSE_LANG = "Choose language..."

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".3gp", ".wmv", ".flv", ".ts", ".mpeg", ".mpg"}


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class Preset:
    model: str
    beam: int = 1
    batch: int = 0           # >1 = batched decoding with this batch size
    note: str = ""


PRESETS = {
    "en": Preset("distil-large-v3", 1, batch=8, note="distil-large-v3 (English)"),
    "hi": Preset("large-v3-turbo", 1, batch=8, note="large-v3-turbo (Hindi)"),
}
PRESETS["auto"] = PRESETS["hi"]              # English + Hindi mixed: multilingual large-v3-turbo
DETECTOR_MODEL = "base"                      # small model that tells English speech from Hindi speech (auto mode)
ALL_MODELS = list(dict.fromkeys([PRESETS["en"].model, PRESETS["hi"].model, DETECTOR_MODEL]))

SCRIPT_PROMPT = {"hi": "यह एक हिंदी बातचीत है।"}

MODEL_FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]

MODEL_HINTS = {
    "distil-large-v3": re.compile(r"(^|[-_])distil[-_](whisper[-_])?large[-_]v3(-ct2)?$"),
    "large-v3-turbo": re.compile(r"(^|[-_])large[-_]v3[-_]turbo(-ct2)?$"),
    "base": re.compile(r"(^|[-_])whisper[-_]base$|^base$"),
}
SKIP_DIRS = {"windows", "program files", "program files (x86)", "programdata", "$recycle.bin",
             "system volume information", "recovery", "node_modules", ".git", "__pycache__",
             "site-packages", "temp", "tmp"}


@dataclass
class Seg:
    start: float
    end: float
    text: str


def fmt_time(sec):
    sec = int(max(0, sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def fmt_secs(sec):
    return f"{sec:.1f}s" if sec < 60 else f"{int(sec) // 60}m {int(sec) % 60:02d}s"


def fmt_length(sec):
    sec = int(round(max(0, sec)))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    parts = []
    if h:
        parts.append(f"{h} hr")
    if m:
        parts.append(f"{m} min" + ("s" if m != 1 else ""))
    if s and not h:
        parts.append(f"{s} sec" + ("s" if s != 1 else ""))
    return " ".join(parts) or "0 secs"


def _trash(path):
    try:
        from send2trash import send2trash
        send2trash(path)
        return True
    except Exception:
        return False


def lower_priority():
    if os.name == "nt":
        try:
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)
        except Exception:
            pass


def restore_priority():
    if os.name == "nt":
        try:
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), 0x20)
        except Exception:
            pass


def keep_awake(on):
    if os.name == "nt":
        try:
            flags = 0x80000001 if on else 0x80000000
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
        except Exception:
            pass


class Timings:
    LABELS = [("download", "Download models"), ("load", "Load models"),
              ("decode", "Read audio"), ("main", "Transcribe")]

    def __init__(self):
        self.t = {}

    def add(self, key, sec):
        self.t[key] = self.t.get(key, 0.0) + sec

    @contextmanager
    def measure(self, key):
        t0 = time.time()
        try:
            yield
        finally:
            self.add(key, time.time() - t0)

    def summary(self):
        parts = [f"{label} {fmt_secs(self.t[k])}" for k, label in self.LABELS if self.t.get(k, 0) >= 0.05]
        return ("Time taken:  " + "   |   ".join(parts)) if parts else ""


class Meter:
    def __init__(self, total, start_pos, cb, min_gap=0.25):
        self.total, self.start_pos, self.cb, self.min_gap = total, start_pos, cb, min_gap
        self.t0 = time.time()
        self.best = start_pos
        self.last_emit = 0.0

    def at(self, pos, force=False):
        self.best = max(self.best, min(pos, self.total))
        now = time.time()
        if not force and now - self.last_emit < self.min_gap:
            return
        self.last_emit = now
        done = self.best / self.total if self.total else 1.0
        elapsed = now - self.t0
        speed = (self.best - self.start_pos) / elapsed if elapsed > 0 else 0
        eta = (self.total - self.best) / speed if speed > 0 else None
        self.cb(min(1.0, done), eta)


def decode_audio(path, cancel=None):
    import av
    container = av.open(path)
    try:
        if not container.streams.audio:
            raise ValueError("This file has no audio track.")
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        parts = []
        for frame in container.decode(stream):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            for f in resampler.resample(frame):
                parts.append(f.to_ndarray().reshape(-1))
        for f in resampler.resample(None) or []:
            parts.append(f.to_ndarray().reshape(-1))
    finally:
        container.close()
    if not parts:
        raise ValueError("No audio could be decoded from this file.")
    return np.concatenate(parts).astype(np.int16)


def video_to_wav(video_path, cancel=None):
    audio = decode_audio(video_path, cancel)
    os.makedirs(CONVERTED_DIR, exist_ok=True)
    stem = os.path.splitext(os.path.basename(video_path))[0]
    out = os.path.join(CONVERTED_DIR, stem + "_audio.wav")
    tmp = out + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(audio.tobytes())
    os.replace(tmp, out)
    return out


def to_float(block_i16):
    return block_i16.astype(np.float32) / 32768.0


def drop_repeats(segments, max_repeat=2):
    out, last, run = [], None, 0
    for s in segments:
        key = " ".join(s.text.split())
        if key and key == last:
            run += 1
            if run >= max_repeat:
                continue
        else:
            last, run = key, 0
        out.append(s)
    return out


def seg_line(start, text, timestamps):
    text = " ".join(text.split())
    return f"[{fmt_time(start)}] {text}\n" if timestamps else text + "\n"


def count_words(segs):
    return sum(len(s.text.split()) for s in segs)


class TxtWriter:
    def __init__(self, out_path):
        self.final = out_path
        self.partial = os.path.splitext(out_path)[0] + ".partial.txt"
        self.state_path = os.path.splitext(out_path)[0] + ".resume.json"

    def open_new(self):
        with open(self.partial, "wb") as f:
            f.write(b"\xef\xbb\xbf")

    def truncate_to(self, nbytes):
        with open(self.partial, "r+b") as f:
            f.truncate(nbytes)

    def append(self, text):
        with open(self.partial, "a", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        return os.path.getsize(self.partial)

    def save_state(self, state):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, self.state_path)

    def load_state(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def finish(self):
        if os.path.exists(self.final):
            _trash(self.final)
        os.replace(self.partial, self.final)
        if os.path.exists(self.state_path):
            _trash(self.state_path)
        return self.final
