"""
Live Emotion Detector Mixin for Red Circuit / Visual Circuit Builder Pro.
Real-time facial expression analysis & emotion detection powered by OpenCV & ONNX AI models.
Embedded directly into the main workspace screen (Single Main Screen Overlay).
Non-blocking background loading to prevent UI freezing/crashing.
"""
import os
import sys
import time
import queue
import threading
import urllib.request
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
from PIL import Image, ImageTk

# Dynamic safe imports
try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

try:
    import onnxruntime as ort
except ImportError:
    ort = None

MODEL_FILENAME = "emotion_model.onnx"
FACE_CASCADE_FILENAME = "haarcascade_frontalface_default.xml"
EYE_CASCADE_FILENAME = "haarcascade_eye.xml"


def softmax(x):
    """Computes softmax probabilities for raw model output logits."""
    e_x = np.exp(x - np.max(x))
    return e_x / max(1e-5, e_x.sum(axis=-1, keepdims=True))


class LiveEmotionDetectorMixin:

    def init_emotion_detector(self):
        self._emotion_panel = None
        self._emotion_running = False
        self._emotion_cap = None
        self._emotion_thread = None
        self._emotion_frame_queue = queue.Queue(maxsize=2)
        self._emotion_result_queue = queue.Queue(maxsize=2)
        self._onnx_session = None
        self._face_cascade = None
        self._eye_cascade = None
        self.emotions = ['Neutral', 'Happy', 'Surprise', 'Sad', 'Angry', 'Disgust', 'Fear', 'Contempt']
        self.current_emotion = "Scanning face..."
        self.confidence = 0.0
        self._engine_ready = False

    def _ensure_cascade_file(self, filename, url):
        local_dir = os.path.dirname(os.path.abspath(__file__))
        local_path = os.path.join(local_dir, filename)

        if os.path.exists(local_path) and os.path.getsize(local_path) > 50000:
            return local_path

        if cv2 is not None and hasattr(cv2, "data") and hasattr(cv2.data, "haarcascades"):
            cv_path = os.path.join(cv2.data.haarcascades, filename)
            if os.path.exists(cv_path) and os.path.getsize(cv_path) > 50000:
                return cv_path

        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response, open(local_path, 'wb') as out_file:
                out_file.write(response.read())
            if os.path.exists(local_path) and os.path.getsize(local_path) > 50000:
                return local_path
        except Exception as e:
            print(f"[EMOTION] Download failed for {filename}: {e}")

        return None

    def _download_emotion_model(self):
        model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), MODEL_FILENAME)
        if os.path.exists(model_path) and os.path.getsize(model_path) > 100000:
            return model_path

        # Updated, working direct URLs (HuggingFace CDN + Updated GitHub Path)
        urls = [
            "https://huggingface.co/onnxmodelzoo/emotion-ferplus-8/resolve/main/emotion-ferplus-8.onnx",
            "https://github.com/onnx/models/raw/main/validated/vision/body_analysis/emotion_ferplus/model/emotion-ferplus-8.onnx"
        ]

        for url in urls:
            try:
                print(f"[EMOTION] Downloading AI ONNX model from: {url}...")
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=25) as response, open(model_path, 'wb') as out_file:
                    out_file.write(response.read())

                if os.path.exists(model_path) and os.path.getsize(model_path) > 100000:
                    print("[EMOTION] AI Emotion Model downloaded successfully!")
                    return model_path
            except Exception as ex:
                print(f"[EMOTION] ONNX fetch warning: {ex}")
                continue

        return None

    def _async_setup_engine(self):
        """Runs cascade and ONNX model loading in a background thread to prevent UI freezing."""
        face_url = "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml"
        eye_url = "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_eye.xml"

        face_path = self._ensure_cascade_file(FACE_CASCADE_FILENAME, face_url)
        eye_path = self._ensure_cascade_file(EYE_CASCADE_FILENAME, eye_url)

        if face_path:
            casc = cv2.CascadeClassifier(face_path)
            self._face_cascade = casc if not casc.empty() else None

        if eye_path:
            casc = cv2.CascadeClassifier(eye_path)
            self._eye_cascade = casc if not casc.empty() else None

        if self._onnx_session is None and ort is not None:
            model_path = self._download_emotion_model()
            if model_path and os.path.exists(model_path):
                try:
                    self._onnx_session = ort.InferenceSession(model_path)
                    print("[EMOTION] ONNX Inference Session loaded successfully!")
                except Exception as e:
                    print(f"[EMOTION] ONNX session error: {e}")

        self._engine_ready = True

        def finish_ui_setup():
            if hasattr(self, "emotion_start_btn") and self.emotion_start_btn.winfo_exists():
                self.emotion_start_btn.configure(state="normal")
            if hasattr(self, "emotion_status_lbl") and self.emotion_status_lbl.winfo_exists():
                mode_str = "Status: AI ONNX Engine Ready" if self._onnx_session else "Status: Feature Engine Ready"
                self.emotion_status_lbl.configure(text=mode_str, text_color="#2ecc71")
            if hasattr(self, "emotion_video_label") and self.emotion_video_label.winfo_exists():
                self.emotion_video_label.configure(
                    text="Engine ready! Click 'Start Camera' to launch real-time stream."
                )

        self.after(0, finish_ui_setup)

    def _preprocess_for_onnx(self, face_img):
        """Preprocesses face image to 64x64 float32 tensor required by emotion-ferplus ONNX model."""
        face_gray = cv2.cvtColor(face_img, cv2.COLOR_BGR2GRAY)
        face_resized = cv2.resize(face_gray, (64, 64))
        face_array = face_resized.astype(np.float32)
        face_array = np.expand_dims(face_array, axis=0)  # (1, 64, 64)
        face_array = np.expand_dims(face_array, axis=0)  # (1, 1, 64, 64)
        return face_array

    def _analyze_facial_features(self, face_roi, gray_face):
        """Dynamic heuristic fallback when ONNX model is unavailable."""
        h, w = gray_face.shape
        eyes = []
        if self._eye_cascade is not None and not self._eye_cascade.empty():
            try:
                eyes = self._eye_cascade.detectMultiScale(gray_face, 1.1, 3)
            except Exception:
                eyes = []

        mouth_region = gray_face[int(h*0.65):, :]
        eye_region = gray_face[:int(h*0.4), :]

        mouth_std = np.std(mouth_region) if mouth_region.size > 0 else 15
        eye_std = np.std(eye_region) if eye_region.size > 0 else 15

        left_half = gray_face[:, :w//2]
        right_half = gray_face[:, w//2:]
        if left_half.shape == right_half.shape and left_half.size > 0:
            symmetry = np.mean(np.abs(left_half - np.fliplr(right_half)))
        else:
            symmetry = 30.0

        contrast = np.std(gray_face)
        edges = cv2.Canny(gray_face, 40, 120)
        edge_activity = np.sum(edges) / max(1, (h * w))

        scores = {
            'Neutral': 0.15, 'Happy': 0.1, 'Surprise': 0.1,
            'Sad': 0.1, 'Angry': 0.1, 'Disgust': 0.1,
            'Fear': 0.1, 'Contempt': 0.05
        }

        # Dynamic feature detection bounds
        if mouth_std > 28 or edge_activity > 9:
            scores['Happy'] += 0.55
            scores['Neutral'] -= 0.10

        if eye_std > 32 and len(eyes) >= 1:
            scores['Surprise'] += 0.50
            scores['Neutral'] -= 0.10

        if contrast > 45 and symmetry > 38:
            scores['Angry'] += 0.40
            scores['Neutral'] -= 0.10

        if mouth_std < 18 and contrast < 30:
            scores['Sad'] += 0.35

        total = max(1e-5, sum(scores.values()))
        probs = np.array([scores[e] / total for e in self.emotions], dtype=np.float32)

        best_idx = int(np.argmax(probs))
        return self.emotions[best_idx], probs[best_idx], probs

    def _predict_emotion(self, face_img):
        if self._onnx_session is not None:
            try:
                processed = self._preprocess_for_onnx(face_img)
                input_name = self._onnx_session.get_inputs()[0].name
                outputs = self._onnx_session.run(None, {input_name: processed})
                logits = outputs[0][0]
                probs = softmax(logits)

                emotion_idx = int(np.argmax(probs))
                confidence = float(probs[emotion_idx])
                return self.emotions[emotion_idx], confidence, probs
            except Exception as ex:
                print(f"[EMOTION] ONNX inference warning: {ex}")

        gray = cv2.cvtColor(face_img, cv2.COLOR_BGR2GRAY)
        return self._analyze_facial_features(face_img, gray)

    def _emotion_inference_worker(self):
        while self._emotion_running:
            try:
                frame = self._emotion_frame_queue.get(timeout=0.5)
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

                faces = []
                if self._face_cascade is not None and hasattr(self._face_cascade, "empty") and not self._face_cascade.empty():
                    try:
                        faces = self._face_cascade.detectMultiScale(gray, 1.2, 4, minSize=(50, 50))
                    except Exception:
                        faces = []

                if len(faces) > 0:
                    x, y, w, h = max(faces, key=lambda f: f[2]*f[3])
                    face_img = frame[y:y+h, x:x+w]
                    emotion, conf, probs = self._predict_emotion(face_img)
                    self.current_emotion = emotion
                    self.confidence = conf
                    self._emotion_result_queue.put((x, y, w, h, emotion, conf, probs))
                else:
                    fh, fw = gray.shape
                    cx1, cy1 = int(fw * 0.25), int(fh * 0.15)
                    cx2, cy2 = int(fw * 0.75), int(fh * 0.85)
                    crop_img = frame[cy1:cy2, cx1:cx2]

                    if crop_img.size > 0:
                        emotion, conf, probs = self._predict_emotion(crop_img)
                        self.current_emotion = emotion
                        self.confidence = conf
                        self._emotion_result_queue.put((cx1, cy1, cx2 - cx1, cy2 - cy1, emotion, conf, probs))
                    else:
                        self.current_emotion = "Scanning face..."
                        self.confidence = 0.0
                        self._emotion_result_queue.put(None)
            except queue.Empty:
                continue
            except Exception:
                self.current_emotion = "Scanning face..."

    def _get_emotion_color(self, emotion):
        colors = {
            'Angry': (0, 0, 255), 'Disgust': (0, 140, 255),
            'Fear': (0, 255, 255), 'Happy': (0, 255, 0),
            'Sad': (255, 0, 0), 'Surprise': (255, 255, 0),
            'Neutral': (200, 200, 200), 'Contempt': (180, 0, 180)
        }
        return colors.get(emotion, (255, 255, 255))

    def _draw_emotion_overlay(self, frame, face_data, fps):
        if face_data is not None:
            x, y, w, h, emotion, conf, probs = face_data
            color = self._get_emotion_color(emotion)

            cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)

            label = f"{emotion} ({conf*100:.1f}%)"
            label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0]
            cv2.rectangle(frame, (x, max(0, y-label_size[1]-10)), (x+label_size[0]+10, y), color, -1)
            cv2.putText(frame, label, (x+5, max(15, y-5)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            bar_x = min(frame.shape[1] - 130, x + w + 10)
            if bar_x < 10:
                bar_x = 10
            bar_y = y
            bar_width = 110
            bar_height = 14

            for i, (emo, prob) in enumerate(zip(self.emotions, probs)):
                if bar_y + bar_height > frame.shape[0]:
                    break
                bar_color = self._get_emotion_color(emo)
                filled_width = int(bar_width * max(0.0, min(1.0, float(prob))))

                cv2.rectangle(frame, (bar_x, bar_y), (bar_x+bar_width, bar_y+bar_height), (40, 40, 40), -1)
                cv2.rectangle(frame, (bar_x, bar_y), (bar_x+filled_width, bar_y+bar_height), bar_color, -1)

                text = f"{emo}: {prob*100:.0f}%"
                cv2.putText(frame, text, (bar_x+2, bar_y+11),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1)
                bar_y += bar_height + 3

        # HUD Overlay
        hud_bg = np.zeros((110, 260, 3), dtype=np.uint8)
        hud_bg[:, :] = (20, 20, 20)

        cv2.putText(hud_bg, f"FPS: {fps}", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        cv2.putText(hud_bg, f"Status: {self.current_emotion}", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        mode_str = "Mode: AI ONNX Model" if self._onnx_session else "Mode: Feature Heuristic"
        mode_col = (0, 255, 0) if self._onnx_session else (0, 165, 255)
        cv2.putText(hud_bg, mode_str, (10, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.5, mode_col, 1)

        fh, fw = frame.shape[:2]
        y1, y2 = 10, 120
        x1, x2 = 10, 270
        if y2 < fh and x2 < fw:
            frame[y1:y2, x1:x2] = cv2.addWeighted(frame[y1:y2, x1:x2], 0.3, hud_bg, 0.7, 0)

        return frame

    def open_emotion_detector(self):
        """Displays the Live Emotion Detector panel directly inside the main workspace screen."""
        if cv2 is None or np is None:
            messagebox.showerror(
                "Missing Dependencies",
                "OpenCV and NumPy are required for Live Emotion Detection.\n"
                "Please run 'Scan & Auto-Install Missing Packages' in More Tools."
            )
            return

        if hasattr(self, "_emotion_panel") and self._emotion_panel is not None and self._emotion_panel.winfo_exists():
            self._emotion_panel.lift()
            return

        panel = ctk.CTkFrame(
            self.canvas_container,
            fg_color="#181818",
            corner_radius=12,
            border_width=2,
            border_color="#9b59b6"
        )
        panel.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.75, relheight=0.84)
        panel.lift()
        self._emotion_panel = panel

        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(10, 4))

        ctk.CTkLabel(
            header,
            text="😊 Real-Time Live Emotion Detector",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#9b59b6"
        ).pack(side="left")

        def close_panel():
            self.stop_emotion_camera()
            panel.destroy()
            self._emotion_panel = None

        ctk.CTkButton(
            header,
            text="✖",
            width=28,
            height=28,
            corner_radius=14,
            fg_color="#c0392b",
            hover_color="#e74c3c",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=close_panel
        ).pack(side="right")

        ctrl_bar = ctk.CTkFrame(panel, fg_color="transparent")
        ctrl_bar.pack(fill="x", padx=15, pady=4)

        self.emotion_start_btn = ctk.CTkButton(
            ctrl_bar,
            text="▶ Start Camera",
            width=120,
            height=30,
            fg_color="#27ae60",
            hover_color="#1e8449",
            font=ctk.CTkFont(size=11, weight="bold"),
            state="disabled",
            command=self.start_emotion_camera
        )
        self.emotion_start_btn.pack(side="left", padx=(0, 6))

        self.emotion_stop_btn = ctk.CTkButton(
            ctrl_bar,
            text="⏹ Stop Camera",
            width=120,
            height=30,
            fg_color="#c0392b",
            hover_color="#962d22",
            font=ctk.CTkFont(size=11, weight="bold"),
            state="disabled",
            command=self.stop_emotion_camera
        )
        self.emotion_stop_btn.pack(side="left", padx=6)

        ctk.CTkButton(
            ctrl_bar,
            text="📸 Save Screenshot",
            width=140,
            height=30,
            fg_color="#3498db",
            hover_color="#2980b9",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._save_emotion_screenshot
        ).pack(side="left", padx=6)

        self.emotion_status_lbl = ctk.CTkLabel(
            ctrl_bar,
            text="Status: Initializing Engine...",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#f1c40f"
        )
        self.emotion_status_lbl.pack(side="right", padx=10)

        video_box = ctk.CTkFrame(panel, fg_color="#0a0a0a", corner_radius=8)
        video_box.pack(fill="both", expand=True, padx=15, pady=(4, 12))

        self.emotion_video_label = ctk.CTkLabel(
            video_box,
            text="Initializing detection engine and downloading AI model in background...",
            font=ctk.CTkFont(size=13),
            text_color="#7f8c8d"
        )
        self.emotion_video_label.pack(fill="both", expand=True, padx=10, pady=10)

        self._last_processed_frame = None
        self._fps_time = time.time()
        self._fps_count = 0
        self._current_fps = 0

        if not self._engine_ready:
            threading.Thread(target=self._async_setup_engine, daemon=True).start()
        else:
            self.emotion_start_btn.configure(state="normal")
            mode_str = "Status: AI ONNX Engine Ready" if self._onnx_session else "Status: Feature Engine Ready"
            self.emotion_status_lbl.configure(text=mode_str, text_color="#2ecc71")
            self.emotion_video_label.configure(
                text="Engine ready! Click 'Start Camera' to launch real-time stream."
            )

    def start_emotion_camera(self):
        if self._emotion_running:
            return

        self._emotion_cap = cv2.VideoCapture(0)
        if not self._emotion_cap.isOpened():
            messagebox.showerror("Camera Error", "Could not access system webcam (index 0).")
            return

        self._emotion_running = True
        self.emotion_start_btn.configure(state="disabled")
        self.emotion_stop_btn.configure(state="normal")
        self.emotion_status_lbl.configure(text="Status: Live Stream Running", text_color="#2ecc71")

        self._emotion_thread = threading.Thread(target=self._emotion_inference_worker, daemon=True)
        self._emotion_thread.start()

        self._fps_time = time.time()
        self._fps_count = 0
        self._current_fps = 0
        self._update_emotion_frame_loop()

    def _update_emotion_frame_loop(self):
        if not self._emotion_running or self._emotion_cap is None:
            return

        ret, frame = self._emotion_cap.read()
        if ret:
            frame = cv2.flip(frame, 1)

            self._fps_count += 1
            if time.time() - self._fps_time >= 1.0:
                self._current_fps = self._fps_count
                self._fps_count = 0
                self._fps_time = time.time()

            try:
                if self._emotion_frame_queue.empty():
                    self._emotion_frame_queue.put_nowait(frame.copy())
            except queue.Full:
                pass

            face_data = None
            try:
                face_data = self._emotion_result_queue.get_nowait()
            except queue.Empty:
                pass

            processed_frame = self._draw_emotion_overlay(frame, face_data, self._current_fps)
            self._last_processed_frame = processed_frame.copy()

            rgb_frame = cv2.cvtColor(processed_frame, cv2.COLOR_BGR2RGB)
            h, w, _ = rgb_frame.shape

            max_w = max(320, self.emotion_video_label.winfo_width())
            max_h = max(240, self.emotion_video_label.winfo_height())
            scale = min(max_w / w, max_h / h)
            new_w, new_h = int(w * scale), int(h * scale)

            pil_img = Image.fromarray(rgb_frame)
            ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(new_w, new_h))

            self.emotion_video_label.configure(image=ctk_img, text="")
            self.emotion_video_label._image = ctk_img

        if self._emotion_running:
            self.after(25, self._update_emotion_frame_loop)

    def stop_emotion_camera(self):
        self._emotion_running = False
        if self._emotion_cap is not None:
            self._emotion_cap.release()
            self._emotion_cap = None

        if hasattr(self, "emotion_start_btn") and self.emotion_start_btn.winfo_exists():
            self.emotion_start_btn.configure(state="normal")
            self.emotion_stop_btn.configure(state="disabled")
            self.emotion_status_lbl.configure(text="Status: Camera Stopped", text_color="#f1c40f")
            self.emotion_video_label.configure(
                image="",
                text="Camera stopped. Click 'Start Camera' to resume."
            )

    def _save_emotion_screenshot(self):
        if self._last_processed_frame is None:
            messagebox.showwarning("No Stream", "No active camera frame to capture.")
            return

        filename = f"emotion_capture_{int(time.time())}.png"
        cv2.imwrite(filename, self._last_processed_frame)
        if hasattr(self, "toast"):
            self.toast(f"📸 Screenshot saved: {filename}")
        messagebox.showinfo("Screenshot Saved", f"Saved emotion detector frame as:\n{filename}")
