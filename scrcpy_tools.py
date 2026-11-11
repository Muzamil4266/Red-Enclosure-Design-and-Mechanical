"""Everything the Live Mirroring feature needs that is NOT user interface:
finding scrcpy / adb on this PC, downloading missing binaries, talking to the
phone through adb, and turning adb responses into plain-language status.
"""
import os
import re
import sys
import json
import glob
import time
import struct
import shutil
import hashlib
import zipfile
import subprocess
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(BASE_DIR, "scrcpy_tools")
SETTINGS_FILE = os.path.join(TOOLS_DIR, "paths.json")
IS_WIN = os.name == "nt"
EXE = ".exe" if IS_WIN else ""
NOWIN = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
UA = {"User-Agent": "VisualCircuitBuilder"}

SCRCPY_RELEASES = "https://github.com/Genymobile/scrcpy/releases"
FALLBACK_TAG = "v5.0.1"
PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"


def run(cmd, timeout=10, env=None):
  """(returncode, stdout, stderr) - never raises, never opens a console window."""
  try:
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout,
                       creationflags=NOWIN, env=env)
    return p.returncode, p.stdout.strip(), p.stderr.strip()
  except subprocess.TimeoutExpired:
    return -1, "", "timed out"
  except OSError as e:
    return -2, "", str(e)


def _ver(text, pattern):
  m = re.search(pattern, text)
  return tuple(int(x) for x in m.group(1).split(".")) if m else None


def scrcpy_version(path):
  code, out, err = run([path, "--version"], timeout=8)
  return _ver(out + "\n" + err, r"scrcpy\s+(\d+(?:\.\d+)*)")


def adb_version(path):
  code, out, err = run([path, "version"], timeout=8)
  return _ver(out + "\n" + err, r"Android Debug Bridge version\s+(\d+(?:\.\d+)*)")


_help_cache = {}


def scrcpy_supports(path, flag):
  if path not in _help_cache:
    code, out, err = run([path, "--help"], timeout=10)
    _help_cache[path] = out + "\n" + err
  return flag in _help_cache[path]


def _load_saved():
  try:
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
      return json.load(f)
  except Exception:
    return {}


def save_manual_path(kind, path):
  os.makedirs(TOOLS_DIR, exist_ok=True)
  d = _load_saved()
  d[kind] = path
  with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
    json.dump(d, f)


def _candidate_dirs():
  out = []
  home = os.path.expanduser("~")
  env = os.environ

  def add(*paths):
    for p in paths:
      if p and os.path.isdir(p) and p not in out:
        out.append(p)

  add(*glob.glob(os.path.join(TOOLS_DIR, "*")), *glob.glob(os.path.join(TOOLS_DIR, "*", "*")))
  add(*[p for p in env.get("PATH", "").split(os.pathsep) if p])
  for root in (env.get("LOCALAPPDATA"), env.get("ProgramFiles"), env.get("ProgramFiles(x86)"), "C:\\"):
    if root:
      add(os.path.join(root, "scrcpy"), os.path.join(root, "Programs", "scrcpy"),
          *glob.glob(os.path.join(root, "scrcpy*")), os.path.join(root, "platform-tools"))
  local = env.get("LOCALAPPDATA")
  if local:
    add(*glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "*scrcpy*")),
        *glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "*scrcpy*", "*")),
        *glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "*Android*Platform*Tools*")),
        os.path.join(local, "Android", "Sdk", "platform-tools"))
  for sdk in (env.get("ANDROID_HOME"), env.get("ANDROID_SDK_ROOT")):
    if sdk:
      add(os.path.join(sdk, "platform-tools"))
  add(os.path.join(home, "scoop", "apps", "scrcpy", "current"), os.path.join(home, "scoop", "shims"),
      *glob.glob(os.path.join(env.get("ProgramData", "C:\\ProgramData"), "chocolatey", "lib", "*scrcpy*", "tools", "*")),
      os.path.join(env.get("ProgramData", "C:\\ProgramData"), "chocolatey", "bin"))
  for sub in ("Downloads", "Desktop", "Documents"):
    base = os.path.join(home, sub)
    add(*glob.glob(os.path.join(base, "scrcpy*")), *glob.glob(os.path.join(base, "scrcpy*", "scrcpy*")),
        *glob.glob(os.path.join(base, "platform-tools*")), *glob.glob(os.path.join(base, "platform-tools*", "platform-tools")))
  return out


def _origin(path):
  return "downloaded by this program" if os.path.abspath(path).lower().startswith(TOOLS_DIR.lower()) else "found on this PC"


def find_tools():
  saved = _load_saved()
  dirs = _candidate_dirs()
  result = {"scrcpy": None, "adb": None}

  def usable(kind, path):
    v = scrcpy_version(path) if kind == "scrcpy" else adb_version(path)
    return (path, v, _origin(path)) if v else None

  sp = saved.get("scrcpy")
  if sp and os.path.isfile(sp):
    result["scrcpy"] = usable("scrcpy", sp)
  if not result["scrcpy"]:
    for d in dirs:
      p = os.path.join(d, "scrcpy" + EXE)
      if os.path.isfile(p):
        result["scrcpy"] = usable("scrcpy", p)
        if result["scrcpy"]:
          break

  order = []
  if result["scrcpy"]:
    order.append(os.path.join(os.path.dirname(result["scrcpy"][0]), "adb" + EXE))
  if saved.get("adb"):
    order.append(saved["adb"])
  order += [os.path.join(d, "adb" + EXE) for d in dirs]
  for p in order:
    if os.path.isfile(p):
      result["adb"] = usable("adb", p)
      if result["adb"]:
        break
  return result


def load_prefs():
  p = _load_saved().get("prefs", {})
  return {
      "source": p.get("source", "screen"),
      "flip": bool(p.get("flip", False)),
      "rotate": str(p.get("rotate", "0")),
      "torch": bool(p.get("torch", False)),
      "last_ip": p.get("last_ip", "")
  }


def save_prefs(prefs):
  os.makedirs(TOOLS_DIR, exist_ok=True)
  d = _load_saved()
  d["prefs"] = prefs
  with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
    json.dump(d, f)


def ensure_tools(progress=None):
  say = progress or (lambda t, f=None: None)
  say("🔎 Looking for scrcpy and adb...", None)
  tools = find_tools()
  if tools["scrcpy"] and tools["adb"]:
    say("✅ Tools are ready.", 1.0)
    return tools
  raise RuntimeError("scrcpy or adb binary missing.")


def parse_devices(text):
  devs = []
  for line in text.splitlines():
    line = line.strip()
    if not line or line.startswith(("List of devices", "*")):
      continue
    parts = line.split()
    if len(parts) < 2:
      continue
    info = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
    serial = parts[0]
    devs.append({
        "serial": serial, "state": parts[1],
        "model": info.get("model", "").replace("_", " ") or "Android phone",
        "wireless": ":" in serial or serial.startswith("adb-")
    })
  return devs


class Adb:
  def __init__(self, path):
    self.path = path

  def run(self, *args, serial=None, timeout=10):
    cmd = [self.path] + (["-s", serial] if serial else []) + list(args)
    return run(cmd, timeout=timeout)

  def devices(self):
    code, out, err = self.run("devices", "-l", timeout=8)
    if code != 0:
      raise RuntimeError(err or out or "adb did not answer")
    return parse_devices(out)

  def phone_ip(self, serial):
    code, out, err = self.run("shell", "ip", "-f", "inet", "addr", "show", "wlan0", serial=serial, timeout=6)
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", out)
    if m:
      return m.group(1)
    code, out, err = self.run("shell", "ip", "route", serial=serial, timeout=6)
    for line in out.splitlines():
      if "wlan" in line:
        m = re.search(r"src (\d+\.\d+\.\d+\.\d+)", line)
        if m:
          return m.group(1)
    return None

  def connect(self, hostport):
    if ":" not in hostport:
      hostport = f"{hostport}:5555"
    c, out, err = self.run("connect", hostport, timeout=20)
    text = (out + " " + err).strip()
    low = text.lower()
    ok = ("connected to" in low or "already connected" in low) and "cannot" not in low and "failed" not in low
    return ok, text

  def tcpip(self, serial=None, port=5555):
    c, out, err = self.run("tcpip", str(port), serial=serial, timeout=10)
    return c == 0, (out + " " + err).strip()


def mirror_command(scrcpy_path, serial, title, source="screen", flip=False, rotate="0", embedded=True):
  cmd = [scrcpy_path, "-s", serial, "--window-title", title, "--max-fps", "30"]
  if embedded:
    if scrcpy_supports(scrcpy_path, "--window-borderless"):
      cmd.append("--window-borderless")
  if scrcpy_supports(scrcpy_path, "--no-audio"):
    cmd.append("--no-audio")
  return cmd
