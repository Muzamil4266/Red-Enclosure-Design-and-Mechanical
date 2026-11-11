"""
ocr_workers.py  (part 3 of 5 of the PaddleOCR scanner)

The two background processes: the page scanner (crop + sharpen + filter -> PNGs or a PDF) and the
persistent PaddleOCR engine. No tkinter in here, so a worker process starts without loading the GUI.
"""
import os
import time

from ocr_imaging import (FINAL_MAX_SIDE, DEFAULT_SHARPEN, GRAY_FILTERS, DET_MAX_SIDE,
                         USE_MKLDNN, USE_MOBILE_MODELS, build_scanned)
from ocr_support import PdfWriter, _bench_image


# ----------------------------------------------------------------------------------------------
# Background processes
# ----------------------------------------------------------------------------------------------
def scan_worker_process(jobs, tmp_dir, out_queue, pdf_path=None):
    """STEP A (own process): crop + sharpen + filter every page.
    Text mode: saves each scanned page as PNG.  PDF mode (pdf_path given): writes the pages into a PDF."""
    pdf = None
    try:
        scanned, failed = [], 0
        if pdf_path:
            pdf = PdfWriter(pdf_path)
        for i, job in enumerate(jobs):
            try:
                img = build_scanned(job["path"], job["quad"], job["filter"], job["rot"],
                                    job.get("sharpen", DEFAULT_SHARPEN), FINAL_MAX_SIDE)
                if pdf is not None:
                    pdf.add_image(img, gray=job["filter"] in GRAY_FILTERS)
                else:
                    out = os.path.join(tmp_dir, f"scan_{i:03d}.png")
                    img.save(out)
                    scanned.append((job["path"], out, None))
            except Exception as e:
                failed += 1
                if pdf is None:
                    scanned.append((job["path"], None, str(e)))
            out_queue.put(("scan_progress", i + 1))
        if pdf is not None:
            pdf.close()
            pdf = None
            out_queue.put(("pdf_done", (pdf_path, len(jobs) - failed, failed)))
        else:
            out_queue.put(("scan_done", scanned))
    except Exception as fatal_e:
        if pdf is not None:
            try:
                pdf.f.close()
                os.remove(pdf_path)
            except Exception:
                pass
        out_queue.put(("scan_error", str(fatal_e)))


def _build_ocr():
    """Creates PaddleOCR tuned for a slow CPU (2 models: Detection + Recognition only, angle classifier disabled)
    and warms it up. Tries the fastest PP-OCRv5 setup first and quietly falls back to safer ones.
    Returns (ocr, estimated seconds for one real photo page)."""
    import numpy as np
    from paddleocr import PaddleOCR
    threads = max(2, min(os.cpu_count() or 4, 8))
    fast = dict(use_doc_orientation_classify=False,      # pages are already cropped / straightened by the scanner
                use_doc_unwarping=False,                 # extra model disabled
                use_textline_orientation=False,          # disable line angle classifier
                use_angle_cls=False,                     # 2 models only (Det + Rec), angle classifier disabled
                text_det_limit_side_len=DET_MAX_SIDE, text_det_limit_type="max",
                text_recognition_batch_size=6, cpu_threads=threads)
    # best English accuracy among the fast models first; older PaddleOCR versions fall through to the next one
    model_sets = [dict(text_detection_model_name="PP-OCRv5_mobile_det",
                       text_recognition_model_name="en_PP-OCRv5_mobile_rec"),
                  dict(text_detection_model_name="PP-OCRv5_mobile_det",
                       text_recognition_model_name="PP-OCRv5_mobile_rec")]
    attempts = []
    if USE_MOBILE_MODELS:
        for models in model_sets:
            if USE_MKLDNN:
                attempts.append(dict(fast, **models, enable_mkldnn=True))
            attempts.append(dict(fast, **models, enable_mkldnn=False))
    if USE_MKLDNN:
        attempts.append(dict(fast, lang="en", enable_mkldnn=True))
    attempts += [dict(fast, lang="en", enable_mkldnn=False),
                 dict(lang="en", enable_mkldnn=False)]
    page = np.array(_bench_image().resize((900, 1200)))
    last = None
    for kw in attempts:
        try:
            ocr = PaddleOCR(**kw)
            t = time.perf_counter()
            ocr.predict(page)                            # warm-up (first call is always slower)
            return ocr, (time.perf_counter() - t) * 2.5  # a real photo page is ~2.5x heavier than the warm-up page
        except Exception as e:
            last = e
    raise last


def ocr_engine_process(task_q, out_q, cancel_val):
    """STEP B (own process): PaddleOCR is loaded ONCE here, in the background, as soon as the OCR
    panel opens - so by the time the pages are scanned the model is already ready."""
    try:
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")  # skip slow online source check
        ocr, bench = _build_ocr()
    except Exception as e:
        out_q.put(("engine_error", str(e)))
        return
    out_q.put(("engine_ready", bench))
    while True:
        task = task_q.get()
        if task is None:
            break
        _, rid, scanned = task
        for orig, scan_path, err in scanned:
            if cancel_val.value >= rid:         # user went back / stopped this run
                break
            if scan_path is None:
                text = f"[Error scanning {os.path.basename(orig)}: {err}]"
            else:
                try:
                    result = ocr.predict(scan_path)
                    lines = result[0]["rec_texts"] if result else []
                    text = "\n".join(lines)
                except Exception as e:
                    text = f"[Error reading {os.path.basename(orig)}: {e}]"
            out_q.put(("result", (rid, orig, text)))
        out_q.put(("done", rid))
