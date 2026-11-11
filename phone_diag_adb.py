#!/usr/bin/env python3
"""
Android Platform-Tools & ADB Interface (part 1 of 5)
===================================================
Manages ADB process execution, configuration, and automatic fetching of binaries.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "phone_diagnostics_config.json")

PLATFORM_TOOLS_URLS = {
    "win32": "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
    "darwin": "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
    "linux": "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
}


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


def find_adb():
    cfg = load_config().get("adb_path")
    if cfg and os.path.isfile(cfg):
        return cfg
    p = shutil.which("adb")
    if p:
        return p
    local = os.environ.get("LOCALAPPDATA", "")
    cands = [
        os.path.join(SCRIPT_DIR, "platform-tools", "adb.exe"),
        os.path.join(SCRIPT_DIR, "adb.exe"),
        os.path.join(SCRIPT_DIR, "platform-tools", "adb"),
        os.path.join(local, "Android", "Sdk", "platform-tools", "adb.exe"),
        r"C:\platform-tools\adb.exe",
        r"C:\adb\adb.exe",
        r"C:\Android\platform-tools\adb.exe",
        os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
        "/usr/local/bin/adb",
        "/opt/homebrew/bin/adb",
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def platform_tools_url():
    if sys.platform.startswith("win"):
        return PLATFORM_TOOLS_URLS["win32"]
    return PLATFORM_TOOLS_URLS["darwin" if sys.platform == "darwin" else "linux"]


def adb_name():
    return "adb.exe" if sys.platform.startswith("win") else "adb"


def extract_platform_tools(zip_path, dest_dir):
    root = os.path.realpath(dest_dir)
    with zipfile.ZipFile(zip_path) as z:
        members = z.infolist()
        for m in members:
            name = m.filename.replace("\\", "/")
            target = os.path.realpath(os.path.join(dest_dir, name))
            if not name.startswith("platform-tools/") or not (target == root or target.startswith(root + os.sep)):
                raise RuntimeError(f"The downloaded file has an unexpected entry ({m.filename}); it was not unpacked.")
        for m in members:
            z.extract(m, dest_dir)
    if not sys.platform.startswith("win"):
        for exe in ("adb", "fastboot"):
            p = os.path.join(dest_dir, "platform-tools", exe)
            if os.path.isfile(p):
                os.chmod(p, 0o755)


def download_platform_tools(progress=lambda pct, msg: None, url=None, dest_dir=SCRIPT_DIR):
    url = url or platform_tools_url()
    os.makedirs(dest_dir, exist_ok=True)
    tmp_zip = None
    try:
        try:
            progress(0, "Connecting to Google (dl.google.com)...")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Shoby Phone Diagnostics)"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                fd, tmp_zip = tempfile.mkstemp(suffix=".zip", prefix="platform-tools-", dir=dest_dir)
                done = 0
                with os.fdopen(fd, "wb") as f:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        progress(int(done * 100 / total) if total else 0, f"Downloading adb... {done / 1048576:.1f} MB")
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise RuntimeError("Could not download adb (no internet, or the connection is blocked).\n"
                               "Download 'SDK Platform-Tools' manually from developer.android.com/tools/releases/platform-tools "
                               "and unzip it next to this script.\n\nDetails: " + str(e))
        progress(100, "Unpacking...")
        try:
            extract_platform_tools(tmp_zip, dest_dir)
        except zipfile.BadZipFile:
            raise RuntimeError("The downloaded file was damaged. Please try again.")
        except PermissionError as e:
            raise RuntimeError("Could not replace the old platform-tools folder - adb is probably still running. "
                               "Close it (run 'adb kill-server') and try again.\n\nDetails: " + str(e))
        except OSError as e:
            raise RuntimeError("Could not unpack adb into this folder (disk full or no write permission).\n\nDetails: " + str(e))
    finally:
        if tmp_zip and os.path.exists(tmp_zip):
            try:
                os.remove(tmp_zip)
            except OSError:
                pass
    adb = os.path.join(dest_dir, "platform-tools", adb_name())
    if not os.path.isfile(adb):
        raise RuntimeError("adb was not found inside the downloaded file.")
    cfg = load_config()
    cfg["adb_path"] = adb
    cfg.pop("adb_download_declined", None)
    save_config(cfg)
    return adb


def cli_download_adb():
    state = {"last": -1}

    def prog(pct, msg):
        step = pct // 10
        if pct == 0 and state["last"] == -1 or step != state["last"] and pct > 0:
            state["last"] = step
            print(f"..   {msg}" + (f"  ({pct} %)" if pct else ""), file=sys.stderr)

    try:
        return download_platform_tools(prog)
    except RuntimeError as e:
        print(str(e))
        return None


class Adb:
    def __init__(self, path):
        self.path = path

    def run(self, args, timeout=20):
        try:
            r = subprocess.run([self.path] + list(args), capture_output=True, timeout=timeout,
                               creationflags=CREATE_NO_WINDOW)
            out = r.stdout.decode("utf-8", "replace").replace("\r", "")
            err = r.stderr.decode("utf-8", "replace").replace("\r", "")
            return r.returncode, out, err
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"
        except OSError as e:
            return 127, "", str(e)

    def start_server(self):
        self.run(["start-server"], 20)

    def devices(self):
        from phone_diag_parsers import parse_devices
        _, out, _ = self.run(["devices", "-l"], 10)
        return parse_devices(out)

    def shell(self, serial, cmd, timeout=25):
        _, out, _ = self.run(["-s", serial, "shell", cmd], timeout)
        return out.strip("\n")

    def popen(self, args):
        return subprocess.Popen([self.path] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=CREATE_NO_WINDOW)