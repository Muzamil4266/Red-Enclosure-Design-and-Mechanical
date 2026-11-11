"""
pdf_split_worker.py  (part of the "Extract text from Pdf" feature of the PaddleOCR panel)

Background process that breaks PDFs into one image per page. No tkinter in here, so the worker process
starts without loading the GUI (same idea as ocr_workers.py).

Messages put on out_queue:
  ("split_total", (total_pages, skipped))   skipped = [(file name, reason), ...]  (password / unreadable PDFs)
  ("split_progress", pages_done)
  ("split_done", pages)                     pages = [{"pdf": index, "page": number, "path": png or None, "err": text or None}]
  ("split_error", message)
"""
import os

from ocr_imaging import FINAL_MAX_SIDE

PDF_DPI = 200   # A4 at 200 dpi = 1654 x 2339 px: sharp enough for OCR, fast to render


def pdf_split_process(pdf_paths, tmp_dir, out_queue, dpi=PDF_DPI):
    try:
        import fitz  # PyMuPDF
    except Exception:
        out_queue.put(("split_error", "PyMuPDF is not installed. Open More Tools -> "
                                      "'Scan & Auto-Install Missing Packages' to install it."))
        return
    try:
        docs, skipped, total = [], [], 0
        for index, path in enumerate(pdf_paths):
            name = os.path.basename(path)
            try:
                doc = fitz.open(path)
                if doc.needs_pass:
                    skipped.append((name, "password protected"))
                    doc.close()
                    continue
                docs.append((index, doc))
                total += doc.page_count
            except Exception as e:
                skipped.append((name, str(e)))
        out_queue.put(("split_total", (total, skipped)))

        pages, done = [], 0
        for index, doc in docs:
            for n in range(doc.page_count):
                try:
                    page = doc[n]
                    longest = max(1.0, page.rect.width, page.rect.height)
                    zoom = min(dpi / 72.0, FINAL_MAX_SIDE / longest)
                    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                    out = os.path.join(tmp_dir, f"pdf{index:03d}_p{n + 1:04d}.png")
                    pix.save(out)
                    pages.append({"pdf": index, "page": n + 1, "path": out, "err": None})
                except Exception as e:
                    pages.append({"pdf": index, "page": n + 1, "path": None, "err": str(e)})
                done += 1
                out_queue.put(("split_progress", done))
            doc.close()
        out_queue.put(("split_done", pages))
    except Exception as fatal:
        out_queue.put(("split_error", str(fatal)))
