"""
Scan & Auto-Install Missing Packages Mixin for Red Enclosure.

Sweeps EVERY .py file in the app folder (and all sub-folders) with Python's AST, finds every
third-party import (top-level, inside functions, inside try/except, __import__ and import_module
calls), maps import names to the real pip package names, checks what is missing and installs it
with pip, one package at a time, with retries and verification.

Only pip packages are handled. Nothing else is downloaded here.
Renders directly inside the main workspace screen (Single Main Screen Overlay).
"""
import os
import re
import sys
import ast
import site
import queue
import sysconfig
import threading
import subprocess
import importlib
import importlib.util
import importlib.metadata
import tkinter as tk
from tkinter import scrolledtext, ttk
import customtkinter as ctk

# Folders that never contain the app's own source
SKIP_DIRS = {
    "__pycache__", ".git", ".idea", ".vscode", "venv", ".venv", "env", ".env", "site-packages",
    "node_modules", "build", "dist", ".mypy_cache", ".pytest_cache", ".tox", "lib", "scripts",
    "include", "share", "models", "model", "weights", "temp", "tmp"
}

# Import names that are never pip packages
IGNORE_IMPORTS = {"__main__", "__future__", "main", "this", "antigravity"}

# import name  ->  pip package name (only where they differ, or to be explicit)
IMPORT_TO_PYPI = {
    # imaging / vision
    "PIL": "Pillow", "cv2": "opencv-python", "skimage": "scikit-image", "sklearn": "scikit-learn",
    "pillow_heif": "pillow-heif", "imageio_ffmpeg": "imageio-ffmpeg", "ffmpeg": "ffmpeg-python",
    "fitz": "PyMuPDF", "pdf2image": "pdf2image", "img2pdf": "img2pdf", "qrcode": "qrcode",
    "pyzbar": "pyzbar", "rawpy": "rawpy", "mediapipe": "mediapipe", "ultralytics": "ultralytics",
    # OCR / AI / audio
    "paddle": "paddlepaddle", "paddleocr": "paddleocr", "easyocr": "easyocr",
    "pytesseract": "pytesseract", "onnxruntime": "onnxruntime", "onnx": "onnx",
    "faster_whisper": "faster-whisper", "ctranslate2": "ctranslate2", "whisper": "openai-whisper",
    "huggingface_hub": "huggingface-hub", "tokenizers": "tokenizers", "safetensors": "safetensors",
    "torch": "torch", "torchaudio": "torchaudio", "torchvision": "torchvision",
    "transformers": "transformers", "tensorflow": "tensorflow", "keras": "keras",
    "speech_recognition": "SpeechRecognition", "pyttsx3": "pyttsx3", "pydub": "pydub",
    "librosa": "librosa", "soundfile": "soundfile", "sounddevice": "sounddevice", "av": "av",
    "pyaudio": "PyAudio", "webrtcvad": "webrtcvad-wheels", "noisereduce": "noisereduce",
    "numba": "numba",
    # GUI / graphics / system
    "customtkinter": "customtkinter", "darkdetect": "darkdetect", "tkinterdnd2": "tkinterdnd2",
    "ttkbootstrap": "ttkbootstrap", "CTkMessagebox": "CTkMessagebox", "CTkToolTip": "CTkToolTip",
    "tkcolorpicker": "tkcolorpicker", "pygame": "pygame", "moderngl": "moderngl",
    "OpenGL": "PyOpenGL", "PyQt5": "PyQt5", "PyQt6": "PyQt6", "PySide2": "PySide2",
    "PySide6": "PySide6", "wx": "wxPython", "pystray": "pystray", "plyer": "plyer",
    "send2trash": "Send2Trash", "psutil": "psutil", "GPUtil": "GPUtil", "pynvml": "nvidia-ml-py",
    "cpuinfo": "py-cpuinfo", "wmi": "WMI", "comtypes": "comtypes", "winshell": "winshell",
    "pyautogui": "pyautogui", "pynput": "pynput", "pyperclip": "pyperclip", "keyboard": "keyboard",
    "mouse": "mouse", "mss": "mss", "screeninfo": "screeninfo", "pywinstyles": "pywinstyles",
    "win32api": "pywin32", "win32con": "pywin32", "win32gui": "pywin32", "win32com": "pywin32",
    "win32clipboard": "pywin32", "win32process": "pywin32", "win32file": "pywin32",
    "win32event": "pywin32", "win32print": "pywin32", "win32ui": "pywin32",
    "pythoncom": "pywin32", "pywintypes": "pywin32", "winerror": "pywin32",
    "serial": "pyserial", "usb": "pyusb", "magic": "python-magic-bin",
    # documents / data
    "docx": "python-docx", "pptx": "python-pptx", "openpyxl": "openpyxl", "xlsxwriter": "XlsxWriter",
    "xlrd": "xlrd", "pypdf": "pypdf", "PyPDF2": "PyPDF2", "pdfplumber": "pdfplumber",
    "pikepdf": "pikepdf", "weasyprint": "weasyprint", "reportlab": "reportlab", "fpdf": "fpdf2",
    "cairosvg": "cairosvg", "svglib": "svglib", "markdown": "Markdown", "pygments": "Pygments",
    "lxml": "lxml", "bs4": "beautifulsoup4", "html5lib": "html5lib", "yaml": "PyYAML",
    "dotenv": "python-dotenv", "dateutil": "python-dateutil", "chardet": "chardet",
    "charset_normalizer": "charset-normalizer", "unidecode": "Unidecode", "emoji": "emoji",
    "regex": "regex", "natsort": "natsort", "jinja2": "Jinja2", "tqdm": "tqdm", "rich": "rich",
    "colorama": "colorama", "packaging": "packaging", "six": "six", "certifi": "certifi",
    "typing_extensions": "typing-extensions", "pkg_resources": "setuptools", "distutils": "setuptools",
    "setuptools": "setuptools", "attr": "attrs", "jwt": "PyJWT", "zmq": "pyzmq", "git": "GitPython",
    "Crypto": "pycryptodome", "cryptography": "cryptography", "PyInstaller": "pyinstaller",
    # science / math / CAD
    "numpy": "numpy", "scipy": "scipy", "pandas": "pandas", "matplotlib": "matplotlib",
    "seaborn": "seaborn", "plotly": "plotly", "sympy": "sympy", "mpmath": "mpmath",
    "networkx": "networkx", "shapely": "shapely", "ezdxf": "ezdxf", "trimesh": "trimesh",
    "imageio": "imageio",
    # network
    "requests": "requests", "urllib3": "urllib3", "httpx": "httpx", "aiohttp": "aiohttp",
    "websockets": "websockets", "flask": "Flask", "fastapi": "fastapi", "uvicorn": "uvicorn",
    "pydantic": "pydantic",
}

# Packages that cannot work alone: importing the key at runtime also needs these import names.
COMPANIONS = {
    "paddleocr": ["paddle"],
}

# Installed first (many other packages build on them)
INSTALL_FIRST = ["setuptools", "wheel", "numpy", "pillow"]


# ======================================================================================
# Helpers (no GUI)
# ======================================================================================
def app_root():
    return os.path.dirname(os.path.abspath(__file__))


def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def collect_python_files(root):
    """Every .py / .pyw file under the app folder, skipping virtual environments and caches."""
    files = []
    for folder, dirs, names in os.walk(root):
        keep = []
        for d in dirs:
            if d.lower() in SKIP_DIRS or d.startswith("."):
                continue
            if os.path.exists(os.path.join(folder, d, "pyvenv.cfg")):
                continue
            keep.append(d)
        dirs[:] = keep
        for n in names:
            if n.lower().endswith((".py", ".pyw")):
                files.append(os.path.join(folder, n))
    return files


def collect_local_names(root, files):
    """Names of the app's own modules and packages, so they are never mistaken for pip packages."""
    local = set()
    for path in files:
        rel = os.path.relpath(path, root)
        parts = rel.split(os.sep)
        local.add(os.path.splitext(parts[-1])[0])
        for part in parts[:-1]:
            local.add(part)
    return local


def _spec_in_stdlib(name):
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError, AttributeError):
        return False
    if spec is None or not spec.origin:
        return False
    stdlib_dir = sysconfig.get_paths().get("stdlib", "")
    origin = str(spec.origin)
    return bool(stdlib_dir) and origin.startswith(stdlib_dir) and "site-packages" not in origin


_STDLIB_NAMES = getattr(sys, "stdlib_module_names", None)


def is_stdlib(name):
    if name in sys.builtin_module_names:
        return True
    if _STDLIB_NAMES is not None:
        return name in _STDLIB_NAMES
    return _spec_in_stdlib(name)


def is_importable(name):
    mod = sys.modules.get(name)
    if mod is not None and getattr(mod, "__spec__", None) is not None:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def imports_from_tree(tree):
    """Top-level import names from every import anywhere in the file."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            fname = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "")
            if fname in ("__import__", "import_module"):
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value:
                    found.add(first.value.split(".")[0])
    return found


def read_requirements(root):
    """Lines of requirements.txt (if the app has one) as (package name, full pip spec)."""
    path = os.path.join(root, "requirements.txt")
    out = []
    if not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            for raw in fh:
                line = raw.split(" #")[0].strip()
                if not line or line.startswith(("#", "-")) or "://" in line:
                    continue
                m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", line)
                if m:
                    out.append((m.group(1), line))
    except OSError:
        pass
    return out


def _dist_installed(name):
    try:
        importlib.metadata.version(name)
        return True
    except importlib.metadata.PackageNotFoundError:
        return False
    except Exception:
        return False


def scan_project(root=None):
    """Full sweep of the app. Returns {'files', 'warnings', 'groups'} with one group per pip package."""
    root = root or app_root()
    files = collect_python_files(root)
    local = collect_local_names(root, files)
    warnings = []
    found = {}

    for path in files:
        rel = os.path.relpath(path, root)
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                tree = ast.parse(fh.read(), filename=rel)
        except SyntaxError as e:
            warnings.append(f"{rel}: syntax error on line {e.lineno}, file skipped")
            continue
        except Exception as e:
            warnings.append(f"{rel}: {e}")
            continue
        for top in imports_from_tree(tree):
            if not top or top in IGNORE_IMPORTS or top in local or is_stdlib(top):
                continue
            found.setdefault(top, set()).add(rel)

    for top in list(found):
        for comp in COMPANIONS.get(top, []):
            found.setdefault(comp, set()).add(f"(needed by {top})")

    groups = {}
    for top, sources in found.items():
        mapped = top in IMPORT_TO_PYPI or top.lower() in IMPORT_TO_PYPI
        pypi = IMPORT_TO_PYPI.get(top) or IMPORT_TO_PYPI.get(top.lower()) or top
        key = _norm(pypi)
        g = groups.setdefault(key, {"pypi": pypi, "spec": pypi, "imports": [], "files": set(),
                                    "missing": False, "mapped": mapped})
        g["imports"].append(top)
        g["files"].update(sources)
        g["mapped"] = g["mapped"] and mapped
        if not is_importable(top):
            g["missing"] = True

    for name, line in read_requirements(root):
        key = _norm(name)
        if key in groups:
            groups[key]["spec"] = line
            groups[key]["files"].add("requirements.txt")
        else:
            groups[key] = {"pypi": name, "spec": line, "imports": [], "files": {"requirements.txt"},
                           "missing": not _dist_installed(name), "mapped": True}

    ordered = sorted(groups.values(), key=lambda g: g["pypi"].lower())
    for g in ordered:
        g["imports"].sort()
    return {"files": len(files), "warnings": warnings, "groups": ordered}


# ---------------------------------------------------------------- pip runner
def _pip_env():
    env = dict(os.environ)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def pip_run(args, emit, stop_event, holder):
    """Runs `python -m pip <args>` and streams every line to emit(). Returns (returncode, tail text)."""
    cmd = [sys.executable, "-m", "pip"] + args
    kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                  errors="replace", bufsize=1, env=_pip_env())
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    proc = subprocess.Popen(cmd, **kwargs)
    holder["proc"] = proc
    tail = []
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                emit(line)
                tail.append(line)
                tail = tail[-60:]
            if stop_event.is_set():
                proc.terminate()
                break
        proc.wait()
    finally:
        holder["proc"] = None
    return proc.returncode, "\n".join(tail)


def ensure_pip(emit, stop_event, holder):
    """Makes sure pip exists and is reasonably recent. Never fatal."""
    try:
        rc, _ = pip_run(["--version"], lambda s: None, stop_event, holder)
        if rc != 0:
            emit("pip is missing, bootstrapping it with ensurepip...")
            cmd = [sys.executable, "-m", "ensurepip", "--upgrade"]
            kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                          errors="replace")
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            res = subprocess.run(cmd, **kwargs)
            if res.stdout:
                emit(res.stdout.strip())
        emit("Updating pip itself (helps find newer wheels)...")
        pip_run(["install", "--upgrade", "pip", "--progress-bar", "off", "--no-input"],
                lambda s: None, stop_event, holder)
    except Exception as e:
        emit(f"(pip check skipped: {e})")


def diagnose(tail):
    low = tail.lower()
    ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    if "externally-managed-environment" in low:
        return "This Python is managed by the system. Use a virtual environment."
    if "no matching distribution" in low or "could not find a version" in low:
        return (f"No build of this package exists for Python {ver} (or the package name is wrong). "
                f"Try Python 3.10 to 3.12 for heavy AI packages.")
    if any(k in low for k in ("getaddrinfo", "max retries", "connection", "timed out", "name resolution")):
        return "No internet connection or the download timed out. Check your network and try again."
    if "permission" in low or "access is denied" in low:
        return "Permission denied. Close other copies of the app, or run Red Enclosure as administrator."
    if "microsoft visual c++" in low or "building wheel" in low and "error" in low:
        return "This package must be compiled. Install Microsoft C++ Build Tools, or use another Python version."
    if "no space left" in low:
        return "The disk is full."
    return "See the pip output above for the exact error."


def install_group(group, emit, stop_event, holder):
    """Installs one pip package. Returns ('ok' | 'unverified' | 'failed' | 'stopped', hint)."""
    spec = group["spec"]
    base = ["install", "--prefer-binary", "--no-input", "--progress-bar", "off",
            "--default-timeout", "60", "--retries", "5"]
    attempts = [("normal", []), ("without cache", ["--no-cache-dir"])]
    if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
        attempts.append(("user install", ["--no-cache-dir", "--user"]))
    last_tail = ""
    for label, extra in attempts:
        if stop_event.is_set():
            return "stopped", ""
        if label != "normal":
            emit(f"↻ Retrying {spec} ({label})...")
        rc, tail = pip_run(base + extra + [spec], emit, stop_event, holder)
        last_tail = tail
        if rc != 0 and "externally-managed-environment" in tail and not stop_event.is_set():
            rc, tail = pip_run(base + extra + ["--break-system-packages", spec], emit, stop_event, holder)
            last_tail = tail
        if stop_event.is_set():
            return "stopped", ""
        if rc == 0:
            importlib.invalidate_caches()
            if "--user" in extra:
                try:
                    site.addsitedir(site.getusersitepackages())
                except Exception:
                    pass
                importlib.invalidate_caches()
            bad = [m for m in group["imports"] if not is_importable(m)]
            if bad:
                return "unverified", ("pip finished but Python still cannot import: " + ", ".join(bad) +
                                      ". Restart Red Enclosure; if it persists the pip name may differ.")
            return "ok", ""
    return "failed", diagnose(last_tail)


# ======================================================================================
# Mixin
# ======================================================================================
class DependencyInstallerMixin:

    def init_dependency_installer(self):
        self._dep_installer_panel = None
        self._missing_packages = []

    def open_dependency_installer(self):
        """Displays the Scan & Auto-Install Missing Packages panel directly inside the main workspace screen."""
        if hasattr(self, "_dep_installer_panel") and self._dep_installer_panel is not None \
                and self._dep_installer_panel.winfo_exists():
            self._dep_installer_panel.lift()
            return

        panel = ctk.CTkFrame(
            self.canvas_container,
            fg_color="#181818",
            corner_radius=12,
            border_width=2,
            border_color="#3498db"
        )
        panel.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.7, relheight=0.86)
        panel.lift()
        self._dep_installer_panel = panel

        ui_q = queue.Queue()
        stop_event = threading.Event()
        holder = {"proc": None}
        state = {"closed": False, "mode": "idle", "groups": []}

        # ---------------- header ----------------
        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(12, 6))

        ctk.CTkLabel(
            header,
            text="📦 Scan & Auto-Install Missing Packages",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#3498db"
        ).pack(side="left")

        def close_panel():
            state["closed"] = True
            stop_event.set()
            p = holder.get("proc")
            if p is not None:
                try:
                    p.terminate()
                except Exception:
                    pass
            panel.destroy()
            self._dep_installer_panel = None

        ctk.CTkButton(
            header, text="✖", width=28, height=28, corner_radius=14,
            fg_color="#c0392b", hover_color="#e74c3c",
            font=ctk.CTkFont(size=12, weight="bold"), command=close_panel
        ).pack(side="right")

        # ---------------- controls ----------------
        top_bar = ctk.CTkFrame(panel, fg_color="transparent")
        top_bar.pack(fill="x", padx=15, pady=6)

        scan_btn = ctk.CTkButton(
            top_bar, text="🔍 Scan Whole App", font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#1f6aa5", hover_color="#144870", height=32, command=lambda: start_scan()
        )
        scan_btn.pack(side="left", padx=(0, 10))

        install_btn = ctk.CTkButton(
            top_bar, text="⚡ Auto-Install Missing", font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#27ae60", hover_color="#1e8449", height=32, state="disabled",
            command=lambda: start_install()
        )
        install_btn.pack(side="left", padx=(0, 10))

        stop_btn = ctk.CTkButton(
            top_bar, text="⏹ Stop", font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#c0392b", hover_color="#962d22", height=32, width=80, state="disabled",
            command=lambda: stop_install()
        )
        stop_btn.pack(side="left")

        progress = ttk.Progressbar(panel, mode="determinate", maximum=100)
        progress.pack(fill="x", padx=15, pady=(4, 4))

        status_label = ctk.CTkLabel(
            panel, text="Preparing to scan...", font=ctk.CTkFont(size=11),
            text_color="#a0a0a0", anchor="w"
        )
        status_label.pack(fill="x", padx=15, pady=(0, 4))

        # Bottom row packed before the log so it is never squeezed out
        button_row = ctk.CTkFrame(panel, fg_color="transparent")
        button_row.pack(side="bottom", pady=(4, 12))
        ctk.CTkButton(
            button_row, text="Close", width=100, fg_color="#555555", hover_color="#777777",
            command=close_panel
        ).pack(side="left", padx=5)

        log_box = scrolledtext.ScrolledText(
            panel, wrap=tk.WORD, bg="#0d0d0d", fg="#33ff33", insertbackground="#ffffff",
            font=("Consolas", 10), borderwidth=1, relief="solid"
        )
        log_box.pack(fill=tk.BOTH, expand=True, padx=15, pady=6)

        def log(text):
            log_box.insert(tk.END, text if text.endswith("\n") else text + "\n")
            if int(log_box.index("end-1c").split(".")[0]) > 5000:
                log_box.delete("1.0", "1000.0")
            log_box.see(tk.END)

        log("=== Dependency Audit Engine ===\n"
            f"Python {sys.version.split()[0]}  |  {sys.executable}\n"
            f"App folder: {app_root()}\n")

        def set_mode(mode):
            state["mode"] = mode
            progress.stop()
            if mode == "scan":
                progress.configure(mode="indeterminate")
                progress.start(10)
                scan_btn.configure(state="disabled")
                install_btn.configure(state="disabled")
                stop_btn.configure(state="disabled")
            elif mode == "install":
                progress.configure(mode="determinate")
                progress["value"] = 0
                scan_btn.configure(state="disabled")
                install_btn.configure(state="disabled")
                stop_btn.configure(state="normal")
            else:
                progress.configure(mode="determinate")
                scan_btn.configure(state="normal")
                stop_btn.configure(state="disabled")
                install_btn.configure(state="normal" if state["groups"] else "disabled")

        # ---------------- scan ----------------
        def start_scan():
            if state["mode"] != "idle":
                return
            set_mode("scan")
            status_label.configure(text="Sweeping every .py file in the app (all sub-folders)...",
                                   text_color="#f1c40f")
            log("\n🔍 Scanning all project files with AST...\n")

            def work():
                try:
                    ui_q.put(("scan_done", scan_project()))
                except Exception as e:
                    ui_q.put(("scan_error", str(e)))

            threading.Thread(target=work, daemon=True).start()

        def show_scan(result):
            groups = result["groups"]
            missing = [g for g in groups if g["missing"]]
            present = [g for g in groups if not g["missing"]]
            state["groups"] = missing
            self._missing_packages = [(m, g["pypi"]) for g in missing for m in (g["imports"] or [g["pypi"]])]
            log("--------------------------------------------------")
            log(f"📁 Scanned {result['files']} project file(s).")
            for w in result["warnings"]:
                log(f"⚠️ {w}")
            log(f"📦 Already installed ({len(present)}):")
            for g in present:
                log(f"   ✅ {g['pypi']}")
            if missing:
                log(f"\n⚠️ Missing ({len(missing)}):")
                for g in missing:
                    used = ", ".join(sorted(g["files"])[:3]) + (" ..." if len(g["files"]) > 3 else "")
                    guess = "" if g["mapped"] else "  (name guessed from import)"
                    log(f"   ❌ {g['pypi']}   <- {used}{guess}")
                status_label.configure(
                    text=f"Found {len(missing)} missing package(s). Click 'Auto-Install Missing'.",
                    text_color="#e74c3c")
            else:
                log("\n🎉 Every pip dependency of the app is installed.")
                status_label.configure(text="✅ All pip dependencies are installed and ready.",
                                       text_color="#2ecc71")
            set_mode("idle")

        # ---------------- install ----------------
        def start_install():
            if state["mode"] != "idle" or not state["groups"]:
                return
            if getattr(sys, "frozen", False):
                log("❌ Red Enclosure is running as a packaged program, so pip cannot install into it.\n"
                    "   Run it from Python (python main.py) to use this tool.")
                return
            stop_event.clear()
            groups = sorted(state["groups"], key=lambda g: (
                INSTALL_FIRST.index(_norm(g["pypi"])) if _norm(g["pypi"]) in INSTALL_FIRST else 99,
                g["pypi"].lower()))
            set_mode("install")
            status_label.configure(text="Downloading and installing via pip...", text_color="#f1c40f")
            log(f"\n⚡ Installing {len(groups)} package(s): {', '.join(g['pypi'] for g in groups)}\n")

            def work():
                result = {"ok": [], "unverified": [], "failed": [], "stopped": False}
                emit = lambda line: ui_q.put(("log", line))
                try:
                    ensure_pip(emit, stop_event, holder)
                    total = len(groups)
                    for i, g in enumerate(groups):
                        if stop_event.is_set():
                            result["stopped"] = True
                            break
                        ui_q.put(("status", f"Installing {g['pypi']}  ({i + 1}/{total})"))
                        emit(f"\n── {g['spec']} ──")
                        outcome, hint = install_group(g, emit, stop_event, holder)
                        if outcome == "ok":
                            result["ok"].append(g["pypi"])
                            emit(f"✅ Installed {g['pypi']}")
                        elif outcome == "unverified":
                            result["unverified"].append((g["pypi"], hint))
                            emit(f"⚠️ {g['pypi']}: {hint}")
                        elif outcome == "stopped":
                            result["stopped"] = True
                            break
                        else:
                            result["failed"].append((g["pypi"], hint))
                            emit(f"❌ Failed: {g['pypi']}  -> {hint}")
                        ui_q.put(("progress", (i + 1) * 100.0 / total))
                except Exception as e:
                    result["failed"].append(("installer", str(e)))
                ui_q.put(("install_done", result))

            threading.Thread(target=work, daemon=True).start()

        def stop_install():
            stop_event.set()
            p = holder.get("proc")
            if p is not None:
                try:
                    p.terminate()
                except Exception:
                    pass
            status_label.configure(text="Stopping...", text_color="#f1c40f")

        def show_install_done(result):
            log("\n==================== INSTALL REPORT ====================")
            log(f"✅ Installed: {len(result['ok'])}" + (f"  ({', '.join(result['ok'])})" if result["ok"] else ""))
            for name, hint in result["unverified"]:
                log(f"⚠️ {name}: {hint}")
            if result["failed"]:
                log(f"❌ Failed: {len(result['failed'])}")
                for name, hint in result["failed"]:
                    log(f"   • {name}: {hint}")
                    log(f'     manual command:  "{sys.executable}" -m pip install {name}')
            if result["stopped"]:
                log("⏹ Stopped by user. Press 'Auto-Install Missing' to continue.")
            log("========================================================\n")
            progress["value"] = 100
            set_mode("idle")
            if hasattr(self, "toast"):
                self.toast("📦 Package installation finished" if not result["failed"]
                           else "📦 Installation finished with some failures")
            status_label.configure(text="Re-scanning to verify...", text_color="#2ecc71")
            start_scan()

        # ---------------- main-thread event pump ----------------
        def poll():
            if state["closed"]:
                return
            try:
                if not panel.winfo_exists():
                    return
            except Exception:
                return
            try:
                while True:
                    kind, payload = ui_q.get_nowait()
                    if kind == "log":
                        log(payload)
                    elif kind == "status":
                        status_label.configure(text=payload, text_color="#f1c40f")
                    elif kind == "progress":
                        progress["value"] = payload
                    elif kind == "scan_done":
                        show_scan(payload)
                    elif kind == "scan_error":
                        log(f"❌ Scan failed: {payload}")
                        status_label.configure(text="Scan failed.", text_color="#e74c3c")
                        set_mode("idle")
                    elif kind == "install_done":
                        show_install_done(payload)
            except queue.Empty:
                pass
            self.after(80, poll)

        set_mode("idle")
        poll()
        start_scan()
