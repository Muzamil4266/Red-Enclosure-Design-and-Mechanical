# Red Enclosure — Offline Hardware, CAD, Vision & Local AI Desktop Suite

An offline-first Python desktop suite built for **2D/3D hardware enclosure & layout design**, **PCB & 2D room image stitching**, **pipe simulation**, **Android ADB & `scrcpy` diagnostics**, **local speech-to-text (`faster-whisper`)**, **offline OCR & PDF tools (`PaddleOCR`, `PyMuPDF`)**, and **automated video editing**.

Designed to run locally without cloud lock-in, heavy bloated IDEs, or subscription paywalls—giving makers, engineers, and developers a single modular workbench on Windows.

---

## Why Red Enclosure?

Most hardware and developer utilities are split across dozens of disconnected scripts, browser tabs, or cloud services. **Red Enclosure** brings practical engineering, inspection, and media tools into one modular Python desktop application (`main.py` / `gui.py`):

* **Enclosure, Canvas & Pipe Workbench:** Interactive 2D/3D canvas rendering (`canvas_graphics_rendering.py`, `canvas_graphics_geometry.py`, `canvas_mouse_events.py`, `canvas_tools_controller.py`), project serialization (`serialization_core.py`, `serialization_projects.py`), and pipe simulation (`pipe_simulation.py`, `pipe_sim_engine.py`).
* **PCB & 2D Room Stitching:** Stitch multi-frame PCB captures and 2D room layouts (`pcb_stitch.py`, `pcb_mapper.py`, `room_stitcher_core.py`, `room_stitcher_2d.py`).
* **Android ADB & `scrcpy` Diagnostics:** Mirror devices and run structured phone/touch/ADB diagnostics (`scrcpy_mirror_ui.py`, `scrcpy_mirror_stream.py`, `scrcpy_mirror_win32.py`, `phone_diagnostics.py`, `phone_diag_adb.py`, `phone_diag_sections.py`, `phone_diag_touch.py`, `phone_diag_parsers.py`, `phone_diag_analysis.py`).
* **Offline Speech, OCR & Document Tools:** Local audio/video transcription (`audio_to_text.py`, `audio_workers.py`, `audio_intelligence_engine.py`, `audio_utils.py`), offline OCR scan editor & PDF generator (`paddle_ocr.py`, `ocr_scan_editor.py`, `ocr_pdf_maker.py`, `ocr_workers.py`, `ocr_imaging.py`, `ocr_gallery.py`, `ocr_image_extractor.py`, `ocr_support.py`), PDF text extraction/splitting/locking (`pdf_text_extractor.py`, `pdf_split_worker.py`, `pdf_locker.py`, `pdf_duplicate_cleaner.py`), and visual duplicate cleaning (`visual_duplicate_cleaner.py`, `visual_duplicate_ui.py`, `visual_dup_engine.py`).
* **Video Editing, Vision & Utilities:** Automated video editing & rendering (`complete_auto_editor.py`, `auto_video_editor.py`, `video_render_engine.py`, `scene_boundary_engine.py`, `rhythm_continuity_engine.py`), screen recording (`screen_recorder.py`), live camera/vision modules (`live_emotion_detector.py`, `realistic_eye.py`, `haarcascade_frontalface_default.xml`, `haarcascade_eye.xml`), APK inspection (`apk_virus_checker.py`), and `aria2` downloads (`aria2_downloader.py`).

---

## Quick Start

### 1. Clone the Repository

```cmd
git clone https://github.com/Muzamil4266/Red-Enclosure-Design-and-Mechanical.git
cd Red-Enclosure-Design-and-Mechanical

```

### 2. Install Python Dependencies

Ensure Python 3 is installed, then install required packages (or run `dependency_installer.py` / `check_library.py` to inspect your environment):

```cmd
pip install customtkinter pillow numpy pymupdf opencv-python faster-whisper paddleocr aria2p

```

*(Install only the optional packages needed for the modules you use, and verify available libraries with `python check_library.py`.)*

### 3. Run the Application

```cmd
python main.py

```

---

## Optional Local Models, Databases & External Binaries

To keep the Git repository fast and lightweight, heavy runtime assets and local data directories are excluded via `.gitignore`:

* **Local speech/vision models:** Place Whisper/ONNX models under `models/` or `emotion_model.onnx` if using offline transcription or emotion detection.
* **Component database & store:** `components.db`, `components.db.part*`, `components_store/`, and `saved_projects/` stay local on your machine.
* **Android / `scrcpy` tools:** Place ADB / `scrcpy` binaries in `platform-tools/` or `scrcpy_tools/` (and configure paths in `scrcpy_tools/paths.json` or `settings.json`).
* **Output & cache folders:** `converted/`, `downloaded_audios/`, `recordings/`, `transcribed_texts/`, `editor.log`, and `__pycache__/` are generated automatically at runtime.

---

## Project Structure (Core Modules)

| Area | Key Files |
| --- | --- |
| **Entry & Main UI** | `main.py`, `gui.py`, `config.py`, `settings.py`, `settings_logic.py`, `settings_schema.py`, `sidebar_ui.py`, `sidebar_actions.py`, `more_tools_menu.py` |
| **Canvas, CAD & Pipes** | `canvas_ui_setup.py`, `canvas_ui_setup_controls.py`, `canvas_graphics_rendering.py`, `canvas_graphics_geometry.py`, `canvas_mouse_events.py`, `canvas_mouse_utils.py`, `canvas_tools_controller.py`, `canvas_tools_zoom_text.py`, `pipe_simulation.py`, `pipe_sim_engine.py`, `table_tool.py`, `insert_picture.py`, `history.py`, `analyze_project.py` |
| **Stitching** | `pcb_stitch.py`, `pcb_mapper.py`, `room_stitcher_core.py`, `room_stitcher_2d.py` |
| **ADB & `scrcpy**` | `scrcpy_mirror_ui.py`, `scrcpy_mirror_stream.py`, `scrcpy_mirror_win32.py`, `scrcpy_tools.py`, `phone_diagnostics.py`, `phone_diag_adb.py`, `phone_diag_sections.py`, `phone_diag_touch.py`, `phone_diag_parsers.py`, `phone_diag_analysis.py` |
| **Audio, OCR & PDF** | `audio_to_text.py`, `audio_workers.py`, `audio_intelligence_engine.py`, `audio_utils.py`, `paddle_ocr.py`, `ocr_scan_editor.py`, `ocr_pdf_maker.py`, `ocr_workers.py`, `ocr_imaging.py`, `ocr_gallery.py`, `ocr_image_extractor.py`, `ocr_support.py`, `pdf_text_extractor.py`, `pdf_split_worker.py`, `pdf_locker.py`, `pdf_duplicate_cleaner.py` |
| **Video, Vision & Cleaners** | `complete_auto_editor.py`, `auto_video_editor.py`, `video_render_engine.py`, `scene_boundary_engine.py`, `rhythm_continuity_engine.py`, `screen_recorder.py`, `live_emotion_detector.py`, `realistic_eye.py`, `visual_duplicate_cleaner.py`, `visual_duplicate_ui.py`, `visual_dup_engine.py`, `apk_virus_checker.py`, `aria2_downloader.py` |

---

## Contributing & Issues

Contributions, bug reports, module improvements, and test cases are welcome:

1. Fork the repository and create a branch for your feature or fix.
2. Test your changes directly against the running application before opening a pull request.
3. Open an **Issue** on GitHub if you spot a bug, edge case, or improvement idea.

---

## License
This project is licensed under the MIT License — see the LICENSE file for details.
This project is licensed under the **MIT License** — see the [LICENSE](https://www.google.com/search?q=LICENSE) file for details.

