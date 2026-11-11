"""
audio_workers.py  (part 2 of 3 of Audio-to-Text)

Model scanner, download handlers, engine management, Whisper runners, VAD,
language detection, and job execution pipeline (full job & 1-min trial).
"""
import os
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass

from audio_utils import (
    MODELS_DIR, APP_DIR, MODEL_INDEX_PATH, SAMPLE_RATE, BLOCK_SECONDS, GROUP_SECONDS,
    NON_ENGLISH, PRESETS, DETECTOR_MODEL, SCRIPT_PROMPT, MODEL_FILES, MODEL_HINTS, SKIP_DIRS,
    LANGS, Cancelled, Seg, Timings, Meter, decode_audio, to_float, drop_repeats, seg_line,
    count_words, TxtWriter, fmt_time, lower_priority, restore_priority, keep_awake
)


def _inside(path, folder):
    a = os.path.normcase(os.path.abspath(path))
    b = os.path.normcase(os.path.abspath(folder))
    return a.startswith(b + os.sep)


def _label(path):
    parts = path.replace("\\", "/").rstrip("/").split("/")
    if len(parts) >= 3 and parts[-2] == "snapshots":
        return parts[-3].lower()
    return parts[-1].lower()


def is_model_dir(path):
    try:
        names = set(os.listdir(path))
        if "model.bin" not in names or "config.json" not in names:
            return False
        if "tokenizer.json" not in names and not any(n.startswith("vocabulary.") for n in names):
            return False
        if os.path.getsize(os.path.join(path, "model.bin")) < 1_000_000:
            return False
    except OSError:
        return False
    if _inside(path, MODELS_DIR) and ".complete" not in names:
        return False
    return True


def search_roots():
    home = os.path.expanduser("~")
    hf = os.environ.get("HF_HOME") or os.path.join(home, ".cache", "huggingface")
    roots = [(MODELS_DIR, 2),
             (os.environ.get("HUGGINGFACE_HUB_CACHE") or os.path.join(hf, "hub"), 4),
             (APP_DIR, 3)]
    for sub in ("models", "Models", "Desktop", "Documents", "Downloads", ".cache",
                os.path.join("AppData", "Local"), os.path.join("AppData", "Roaming"), "OneDrive"):
        roots.append((os.path.join(home, sub), 5))
    if os.name == "nt":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            roots.append((f"{letter}:\\", 3))
    else:
        roots += [(home, 4), ("/mnt", 3), ("/media", 3), ("/opt", 3)]
    return [(p, d) for p, d in roots if os.path.isdir(p)]


def find_models(names, cancel=None, max_seconds=90, roots=None):
    found = {}
    wanted = [n for n in names if n in MODEL_HINTS]
    deadline = time.time() + max_seconds
    seen = set()
    for root, depth in (roots if roots is not None else search_roots()):
        stack = [(root, 0)]
        while stack:
            if len(found) == len(wanted):
                return found
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if time.time() > deadline:
                return found
            path, level = stack.pop()
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            try:
                entries = list(os.scandir(path))
            except OSError:
                continue
            files = {e.name for e in entries}
            if "model.bin" in files and "config.json" in files:
                label = _label(path)
                for n in wanted:
                    if n not in found and MODEL_HINTS[n].search(label) and is_model_dir(path):
                        found[n] = path
                continue
            if level >= depth:
                continue
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False) and e.name.lower() not in SKIP_DIRS:
                        stack.append((e.path, level + 1))
                except OSError:
                    pass
    return found


class Runner:
    def __init__(self, model, batch=0):
        self.model = model
        self.batch = batch if batch and batch > 1 else 0
        self._pipe = None
        if self.batch:
            from faster_whisper import BatchedInferencePipeline
            self._pipe = BatchedInferencePipeline(model)

    def transcribe(self, audio, **kw):
        if self._pipe is not None:
            kw["batch_size"] = self.batch
            return self._pipe.transcribe(audio, **kw)
        return self.model.transcribe(audio, **kw)


class Engine:
    def __init__(self, models_dir=MODELS_DIR):
        self.models_dir = models_dir
        self.paths = self._load_index()
        self._runner = None
        self._runner_name = None
        self._det = None

    def _load_index(self):
        try:
            with open(MODEL_INDEX_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {n: p for n, p in data.items() if is_model_dir(p)}
        except Exception:
            return {}

    def _save_index(self):
        try:
            tmp = MODEL_INDEX_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.paths, f, ensure_ascii=False)
            os.replace(tmp, MODEL_INDEX_PATH)
        except Exception:
            pass

    def is_downloaded(self, name):
        return name in self.paths and is_model_dir(self.paths[name])

    def locate(self, names, cancel=None):
        missing = [n for n in names if not self.is_downloaded(n)]
        if missing:
            self.paths.update(find_models(missing, cancel))
            self._save_index()

    def register(self, name, folder):
        abs_folder = os.path.abspath(folder)
        bin_path = os.path.join(abs_folder, "model.bin")
        cfg_path = os.path.join(abs_folder, "config.json")
        if os.path.isfile(bin_path) and os.path.isfile(cfg_path) and os.path.getsize(bin_path) >= 1_000_000:
            complete_file = os.path.join(abs_folder, ".complete")
            if not os.path.exists(complete_file):
                try:
                    with open(complete_file, "w") as f:
                        f.write("ok")
                except Exception:
                    pass

        found = find_models([name], roots=[(abs_folder, 3)], max_seconds=15).get(name)
        if not found:
            return False
        self.paths[name] = found
        self._save_index()
        return True
    def describe(self, names):
        lines = []
        for n in names:
            if self.is_downloaded(n):
                lines.append(f"{n}: {self.paths[n]}")
            else:
                lines.append(f"{n}: not found - will be downloaded to {self.models_dir}")
        return "\n".join(lines)

    def download(self, name, progress=None):
        if self.is_downloaded(name):
            return self.paths[name]
        from huggingface_hub import snapshot_download
        try:
            from faster_whisper.utils import _MODELS
            repo = _MODELS[name]
        except Exception:
            repo = "Systran/faster-whisper-" + name

        kwargs = {}
        if progress is not None:
            try:
                from huggingface_hub.utils import tqdm as hf_tqdm

                class _Bar(hf_tqdm):
                    def update(self, n=1):
                        r = super().update(n)
                        try:
                            if getattr(self, "unit", "") == "B" and self.total:
                                progress(self.n / self.total, f"Downloading model {name}: "
                                         f"{self.n / 1e6:.0f} / {self.total / 1e6:.0f} MB")
                        except Exception:
                            pass
                        return r
                kwargs["tqdm_class"] = _Bar
            except Exception:
                pass
        target = os.path.join(self.models_dir, name)
        os.makedirs(target, exist_ok=True)
        snapshot_download(repo, local_dir=target, allow_patterns=MODEL_FILES, **kwargs)
        with open(os.path.join(target, ".complete"), "w") as f:
            f.write("ok")
        self.paths[name] = target
        self._save_index()
        return target

    def get_runner(self, name, batch=0, progress=None):
        batch = batch if batch and batch > 1 else 0
        if self._runner is None or self._runner_name != name or self._runner.batch != batch:
            self._runner = None
            path = self.download(name, progress)
            from faster_whisper import WhisperModel
            model = WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)
            self._runner = Runner(model, batch)
            self._runner_name = name
        return self._runner

    def get_detector(self, progress=None):
        if self._det is None:
            path = self.download(DETECTOR_MODEL, progress)
            from faster_whisper import WhisperModel
            self._det = WhisperModel(path, device="cpu", compute_type="int8",
                                     cpu_threads=os.cpu_count() or 4)
        return self._det


def transcribe_block(runner, block_f32, lang_code, beam, offset, cancel=None, on_tick=None, timings=None):
    kw = dict(
        language=lang_code,
        beam_size=5 if lang_code != "en" else beam,
        patience=1.0,
        repetition_penalty=1.15,
        temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
        condition_on_previous_text=False,
        compression_ratio_threshold=2.2,
        initial_prompt=SCRIPT_PROMPT.get(lang_code),
        vad_filter=True,
        vad_parameters={
            "threshold": 0.3,
            "min_silence_duration_ms": 700,
            "speech_pad_ms": 400,
            "min_speech_duration_ms": 200
        }
    )

    t0 = time.time()
    out = []
    try:
        segs, _info = runner.transcribe(block_f32, **kw)
        for s in segs:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if on_tick:
                on_tick(s.end)
            text = s.text.strip()
            if text:
                out.append(Seg(offset + s.start, offset + s.end, text))
    finally:
        if timings is not None:
            timings.add("main", time.time() - t0)
    return out


def detect_probs(model, f32):
    try:
        res = model.detect_language(f32[:30 * SAMPLE_RATE])
        d = dict(res[2])
    except Exception:
        return None
    return {k: float(d.get(k, 0.0)) for k in ("en", "hi", "ur")}


def pick_language(p):
    if p is None:
        return NON_ENGLISH
    return "en" if p["en"] >= p["hi"] + p["ur"] else NON_ENGLISH


def speech_spans(f32):
    try:
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        return get_speech_timestamps(f32, VadOptions(min_silence_duration_ms=500, max_speech_duration_s=30))
    except Exception:
        return [{"start": 0, "end": len(f32)}]


def plan_groups(detector, block, spans, cancel=None, max_seconds=GROUP_SECONDS):
    pad = int(0.2 * SAMPLE_RATE)
    items = []
    for sp in spans:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        a, b = max(0, sp["start"] - pad), min(len(block), sp["end"] + pad)
        lang = None
        if b - a >= 1.5 * SAMPLE_RATE:
            lang = pick_language(detect_probs(detector, block[a:b]))
        items.append([a, b, lang])
    last = None
    for it in items:
        if it[2] is None:
            it[2] = last
        else:
            last = it[2]
    nxt = None
    for it in reversed(items):
        if it[2] is None:
            it[2] = nxt
        else:
            nxt = it[2]
    groups = []
    for a, b, lang in items:
        lang = lang or NON_ENGLISH
        if groups and groups[-1][2] == lang and b - groups[-1][0] <= max_seconds * SAMPLE_RATE:
            groups[-1][1] = b
        else:
            groups.append([a, b, lang])
    return groups


def mix_text(counts):
    tot = sum(counts.values())
    if not tot:
        return ""
    names = {"en": "English", "hi": "Hindi"}
    return "  |  Languages: " + ", ".join(
        f"{names[k]} {v / tot * 100:.0f}%" for k, v in sorted(counts.items(), key=lambda x: -x[1]))


@dataclass
class Job:
    audio_path: str
    lang_name: str = "English"
    out_path: str = ""
    timestamps: bool = False

    def __post_init__(self):
        if not self.out_path:
            self.out_path = os.path.splitext(self.audio_path)[0] + ".txt"

    @property
    def lang(self):
        return LANGS[self.lang_name]

    @property
    def spec(self):
        return PRESETS[self.lang]


@dataclass
class JobResult:
    path: str
    words: int
    timings: str
    mix: str
    duration: float


def _file_sig(path):
    st = os.stat(path)
    return {"size": st.st_size, "mtime": int(st.st_mtime)}


class Transcriber:
    def __init__(self, engine, job, cancel=None, status=None, timings=None):
        self.engine, self.job, self.cancel = engine, job, cancel
        self.status = status or (lambda m: None)
        self.timings = timings or Timings()
        self.spec = job.spec
        self.main = self.detector = None

    def prepare(self):
        def on_download(_fraction, message):
            self.status(message)

        def timed(name, fn):
            key = "load" if self.engine.is_downloaded(name) else "download"
            with self.timings.measure(key):
                return fn()

        self.main = timed(self.spec.model,
                          lambda: self.engine.get_runner(self.spec.model, self.spec.batch, on_download))
        if self.job.lang == "auto":
            self.detector = timed(DETECTOR_MODEL, lambda: self.engine.get_detector(on_download))

    def region(self, block, offset, tick=None, counts=None):
        dur = max(len(block) / SAMPLE_RATE, 1e-6)
        t = (lambda f: tick(min(1.0, max(0.0, f)))) if tick else None
        if self.job.lang == "auto":
            return self._auto(block, offset, t, counts, dur)
        return transcribe_block(self.main, block, self.job.lang, self.spec.beam, offset, self.cancel,
                                (lambda r: t(r / dur)) if t else None, self.timings)

    def _auto(self, block, offset, t, counts, dur):
        out = []
        with self.timings.measure("main"):
            groups = plan_groups(self.detector, block, speech_spans(block), self.cancel)
        for a, b, lang in groups:
            if self.cancel is not None and self.cancel.is_set():
                raise Cancelled()
            if counts is not None:
                counts[lang] = counts.get(lang, 0.0) + (b - a) / SAMPLE_RATE
            base = a / SAMPLE_RATE
            out += transcribe_block(self.main, block[a:b], lang, self.spec.beam, offset + base, self.cancel,
                                    (lambda r, base=base: t((base + r) / dur)) if t else None, self.timings)
        return out


def run_job(job, engine, cancel, on_status=None, on_progress=None):
    status = on_status or (lambda m: None)
    progress = on_progress or (lambda frac, eta: None)
    timings = Timings()
    lower_priority()
    keep_awake(True)
    try:
        tr = Transcriber(engine, job, cancel, status, timings)
        tr.prepare()
        status("Reading audio...")
        with timings.measure("decode"):
            audio = decode_audio(job.audio_path, cancel)
        total = len(audio) / SAMPLE_RATE
        if total < 1:
            raise ValueError("The audio is empty.")

        w = TxtWriter(job.out_path)
        pos, words, counts = 0.0, 0, {}
        state = w.load_state()
        sig = {"audio": os.path.abspath(job.audio_path), **_file_sig(job.audio_path),
               "model": job.spec.model, "lang": job.lang, "timestamps": job.timestamps}
        if state and os.path.exists(w.partial) and all(state.get(k) == v for k, v in sig.items()):
            pos = float(state["pos"])
            w.truncate_to(int(state["bytes"]))
            counts, words = dict(state.get("counts", {})), int(state.get("words", 0))
            status(f"Resuming from {fmt_time(pos)}")
        else:
            w.open_new()

        meter = Meter(total, pos, progress)
        while pos < total - 0.5:
            if cancel.is_set():
                raise Cancelled()
            end = min(total, pos + BLOCK_SECONDS)
            last_block = end >= total - 0.5
            block = to_float(audio[int(pos * SAMPLE_RATE):int(end * SAMPLE_RATE)])
            segs = tr.region(block, pos, counts=counts,
                             tick=lambda f, p=pos, e=end: meter.at(p + f * (e - p)))
            segs = drop_repeats(segs)

            if last_block:
                next_pos = total
            elif len(segs) >= 2:
                next_pos = segs[-1].start
                segs = segs[:-1]
            else:
                next_pos = end
            if next_pos <= pos:
                next_pos = end

            text = "".join(seg_line(s.start, s.text, job.timestamps) for s in segs)
            words += count_words(segs)
            size = w.append(text) if text else os.path.getsize(w.partial)
            w.save_state({**sig, "pos": next_pos, "bytes": size, "counts": counts, "words": words})
            pos = next_pos
            meter.at(pos, force=True)

        final = w.finish()
        meter.at(total, force=True)
        return JobResult(final, words, timings.summary(), mix_text(counts), total)
    finally:
        keep_awake(False)
        restore_priority()


@dataclass
class TrialResult:
    speed: float
    total_audio: float
    estimate_total: float
    text: str
    words: int
    mix: str
    timings: str


def _sample(audio, trial_seconds, at=0.3):
    total = len(audio) / SAMPLE_RATE
    n = min(trial_seconds, total)
    start = max(0.0, min(total * at, total - n))
    try:
        probe = to_float(audio[int(start * SAMPLE_RATE):int(min(total, start + 20) * SAMPLE_RATE)])
        for sp in speech_spans(probe):
            if 0.4 * SAMPLE_RATE <= sp["start"] <= 15 * SAMPLE_RATE:
                start += sp["start"] / SAMPLE_RATE - 0.1
                n = min(n, total - start)
                break
    except Exception:
        pass
    return start, n, to_float(audio[int(start * SAMPLE_RATE):int((start + n) * SAMPLE_RATE)]), total


def run_trial(job, engine, cancel, on_status=None, on_progress=None, trial_seconds=60):
    status = on_status or (lambda m: None)
    progress = on_progress or (lambda frac, eta: None)
    timings = Timings()
    lower_priority()
    try:
        tr = Transcriber(engine, job, cancel, status, timings)
        tr.prepare()
        status("Reading audio...")
        with timings.measure("decode"):
            audio = decode_audio(job.audio_path, cancel)
        start, n, block, total = _sample(audio, trial_seconds)
        status(f"Testing {fmt_time(start)} - {fmt_time(start + n)} ...")
        counts = {}
        t0 = time.time()
        segs = drop_repeats(tr.region(block, start, counts=counts, tick=lambda f: progress(f, None)))
        speed = n / max(time.time() - t0, 1e-6)
        text = "".join(seg_line(s.start, s.text, job.timestamps) for s in segs)
        return TrialResult(speed, total, total / speed, text, count_words(segs),
                           mix_text(counts), timings.summary())
    finally:
        restore_priority()
