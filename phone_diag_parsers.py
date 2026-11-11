#!/usr/bin/env python3
"""
Android Phone Diagnostics Parsers & Utilities (part 2 of 5)
==========================================================
Data maps, raw text parsing utilities, and hardware value sanitizers.
"""
import math
import re
from datetime import datetime

STATUS_COLORS = {"ok": "#2ecc71", "warn": "#f1c40f", "bad": "#e74c3c", "na": "#7f8c8d", None: "#e6e6e6"}
STATUS_ICONS = {"ok": "✅", "warn": "⚠️", "bad": "❌", "na": "➖", None: ""}

ANDROID_BY_API = {
    19: ("4.4", 2013), 21: ("5.0", 2014), 22: ("5.1", 2015), 23: ("6.0", 2015), 24: ("7.0", 2016),
    25: ("7.1", 2016), 26: ("8.0", 2017), 27: ("8.1", 2017), 28: ("9", 2018), 29: ("10", 2019),
    30: ("11", 2020), 31: ("12", 2021), 32: ("12L", 2022), 33: ("13", 2022), 34: ("14", 2023),
    35: ("15", 2024), 36: ("16", 2025),
}

ANDROID_RELEASE = {
    19: (2013, 10), 21: (2014, 11), 22: (2015, 3), 23: (2015, 10), 24: (2016, 8), 25: (2016, 10), 26: (2017, 8),
    27: (2017, 12), 28: (2018, 8), 29: (2019, 9), 30: (2020, 9), 31: (2021, 10), 32: (2022, 3), 33: (2022, 8),
    34: (2023, 10), 35: (2024, 10), 36: (2025, 6),
}

BATT_STATUS = {1: "Unknown", 2: "Charging", 3: "Discharging", 4: "Not charging", 5: "Full"}
BATT_HEALTH = {1: "Unknown", 2: "Good", 3: "Overheat", 4: "Dead", 5: "Over voltage",
               6: "Unspecified failure", 7: "Cold"}

FEATURES = [
    ("android.hardware.touchscreen", "Touchscreen", "bad"),
    ("android.hardware.touchscreen.multitouch", "Multi-touch", "warn"),
    ("android.hardware.touchscreen.multitouch.jazzhand", "5-finger multi-touch", None),
    ("android.hardware.camera", "Rear camera", "warn"),
    ("android.hardware.camera.front", "Front camera", "warn"),
    ("android.hardware.camera.autofocus", "Camera autofocus", None),
    ("android.hardware.camera.flash", "Camera flash", None),
    ("android.hardware.fingerprint", "Fingerprint sensor", None),
    ("android.hardware.biometrics.face", "Face unlock hardware", None),
    ("android.hardware.nfc", "NFC", None),
    ("android.hardware.bluetooth", "Bluetooth", "warn"),
    ("android.hardware.bluetooth_le", "Bluetooth LE", None),
    ("android.hardware.wifi", "Wi-Fi", "warn"),
    ("android.hardware.telephony", "Mobile network (SIM calls)", "warn"),
    ("android.hardware.location.gps", "GPS", "warn"),
    ("android.hardware.sensor.accelerometer", "Accelerometer", "warn"),
    ("android.hardware.sensor.gyroscope", "Gyroscope", None),
    ("android.hardware.sensor.compass", "Compass", None),
    ("android.hardware.sensor.proximity", "Proximity sensor", "warn"),
    ("android.hardware.sensor.light", "Light sensor", None),
    ("android.hardware.sensor.barometer", "Barometer", None),
    ("android.hardware.sensor.stepcounter", "Step counter", None),
    ("android.hardware.usb.host", "USB host (OTG)", None),
    ("android.hardware.microphone", "Microphone", "warn"),
    ("android.hardware.audio.output", "Audio output", "warn"),
]

CHECK_BY_HAND = [
    "Purchase / first activation date - the phone does not store it in a readable way. Ask for the receipt or "
    "check the warranty on the maker's website with the IMEI or serial number.",
    "Blacklist / stolen / carrier-lock status - needs a carrier or IMEI database, not the cable.",
    "Camera quality (photo + video, both cameras, flash) - take test shots.",
    "Speaker, earpiece and microphone - make a call or record a voice note.",
    "Screen quality - look for dead pixels, burn-in, yellow tint, cracks; use a plain white / black / red screen.",
    "Vibration motor, charging port wobble, headphone port, SIM tray, water-damage indicator.",
    "Face unlock / fingerprint enrolment, Wi-Fi and Bluetooth really connecting, mobile data with your SIM.",
    "Screen lock and Google account: the seller must sign out and factory reset in front of you.",
]

CYCLES_PER_DAY = (1.3, 0.8, 0.45)


def num(s, default=None):
    m = re.search(r"-?\d+(?:\.\d+)?", "" if s is None else str(s))
    return float(m.group()) if m else default


def first(props, *keys):
    for k in keys:
        v = (props.get(k) or "").strip()
        if v and v.lower() not in ("unknown", "null", "none"):
            return v
    return None


def R(label, value, status=None):
    missing = value is None or value == ""
    return {"label": label, "value": "Not available" if missing else str(value),
            "status": "na" if missing and status is None else status}


def luhn_ok(s):
    if not s.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(s)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def months_since(date_str):
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    now = datetime.now()
    return (now.year - d.year) * 12 + (now.month - d.month)


def months_between(d):
    return max(0.0, (datetime.now() - d).days / 30.44)


def fmt_gb(kb):
    return f"{kb / 1024 / 1024:.1f} GB"


def round_up_class(gb, classes):
    for c in classes:
        if gb <= c * 1.02:
            return c
    return None


def fmt_age(m):
    m = int(round(m))
    y, mo = divmod(m, 12)
    parts = []
    if y:
        parts.append(f"{y} year{'s' if y != 1 else ''}")
    if mo:
        parts.append(f"{mo} month{'s' if mo != 1 else ''}")
    return " ".join(parts) if parts else "less than 1 month"


def short_age(m):
    m = int(round(m))
    y, mo = divmod(m, 12)
    return f"{y}y {mo}m" if y else f"{mo} mo"


def parse_devices(text):
    devs = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*") or line.startswith("adb server"):
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        rest = parts[2] if len(parts) > 2 else ""
        if state == "no":
            state = "no permissions"
        m = re.search(r"model:(\S+)", rest)
        devs.append({"serial": serial, "state": state, "model": m.group(1).replace("_", " ") if m else ""})
    return devs


def parse_getprop(text):
    return {k: v for k, v in re.findall(r"^\[(.*?)\]: \[(.*?)\]$", text, re.M)}


def parse_kv(text):
    d = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z][\w \-/]*?):\s*(.*?)\s*$", line)
        if m:
            d[m.group(1).strip().lower()] = m.group(2)
    return d


def parse_sysfs_blocks(text):
    blocks, cur = {}, None
    for line in text.splitlines():
        if line.startswith("@@"):
            cur = line[2:].strip()
            blocks[cur] = {}
        elif cur and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                blocks[cur][k.strip()] = v.strip()
    return blocks


def parse_input_devices(raw):
    devs = []
    for chunk in re.split(r"(?m)^add device \d+:\s*", raw)[1:]:
        path = chunk.split("\n", 1)[0].strip()
        name = re.search(r'name:\s*"(.*?)"', chunk)
        mx = re.search(r"ABS_MT_POSITION_X\s*:\s*value\s+-?\d+,\s*min\s+-?\d+,\s*max\s+(\d+)", chunk)
        my = re.search(r"ABS_MT_POSITION_Y\s*:\s*value\s+-?\d+,\s*min\s+-?\d+,\s*max\s+(\d+)", chunk)
        ms = re.search(r"ABS_MT_SLOT\s*:\s*value\s+-?\d+,\s*min\s+-?\d+,\s*max\s+(\d+)", chunk)
        devs.append({
            "path": path, "name": name.group(1) if name else "",
            "max_x": int(mx.group(1)) if mx else 0, "max_y": int(my.group(1)) if my else 0,
            "slots": int(ms.group(1)) + 1 if ms else 0,
            "keys": set(re.findall(r"\b(KEY_[A-Z0-9_]+|BTN_[A-Z0-9_]+)\b", chunk)),
        })
    return devs


def parse_features(text):
    return {line.split(":", 1)[1].split("=")[0].strip() for line in text.splitlines()
            if line.startswith("feature:")}


def decode_parcel_string(text):
    words = []
    for line in text.splitlines():
        line = line.split("'")[0]
        if ":" in line:
            line = line.split(":", 1)[1]
        words += re.findall(r"\b[0-9a-fA-F]{8}\b", line)
    if len(words) < 3:
        return ""
    length = int(words[1], 16)
    chars = []
    for w in words[2:]:
        v = int(w, 16)
        for c in (v & 0xFFFF, (v >> 16) & 0xFFFF):
            if c:
                chars.append(chr(c))
    return "".join(chars)[:length if 0 < length < 64 else 64]