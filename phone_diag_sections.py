#!/usr/bin/env python3
"""
Android Phone Diagnostic Section Extractor (part 3 of 5)
========================================================
Collects technical parameters from raw property dumps and system files.
"""
import math
import re
from datetime import datetime

from phone_diag_parsers import (
    ANDROID_BY_API, BATT_HEALTH, BATT_STATUS, FEATURES, R, decode_parcel_string,
    first, fmt_gb, luhn_ok, months_since, num, parse_input_devices, parse_kv,
    parse_sysfs_blocks, round_up_class
)

SYSFS_FILES = ("type status health present technology capacity capacity_level voltage_now voltage_max_design "
               "current_now temp charge_counter charge_full charge_full_design charge_now energy_full "
               "energy_full_design cycle_count cycle_counts battery_cycle batt_cycle bat_cycle soh batt_soh "
               "batt_full_capacity manufacturer model_name bsoh batt_asoc asoc").split()
BATT_SCRIPT = ("for d in /sys/class/power_supply/*; do if [ -r $d/type ]; then echo @@$d; for f in "
               + " ".join(SYSFS_FILES)
               + "; do [ -r $d/$f ] && echo $f=$(cat $d/$f 2>/dev/null); done; fi; done")


def section_identity(sh, props, serial, facts):
    rows = []
    brand, manu, model = first(props, "ro.product.brand"), first(props, "ro.product.manufacturer"), first(props, "ro.product.model")
    facts["name"] = " ".join(x for x in (manu or brand, model) if x) or serial
    rows += [
        R("Manufacturer", manu), R("Brand", brand),
        R("Marketing name", first(props, "ro.product.marketname", "ro.product.vendor.marketname",
                                  "ro.vendor.product.display", "ro.config.marketing_name", "ro.product.odm.marketname")),
        R("Model", model), R("Device codename", first(props, "ro.product.device")),
        R("Serial number", first(props, "ro.serialno", "ro.boot.serialno") or serial),
    ]
    release, api = first(props, "ro.build.version.release"), num(first(props, "ro.build.version.sdk"))
    facts["api"] = int(api) if api else None
    rows.append(R("Android version", f"Android {release} (API {int(api)})" if release and api else release,
                  "warn" if api and api < 29 else None))
    patch = first(props, "ro.build.version.security_patch")
    age = months_since(patch) if patch else None
    facts["patch_age"] = age
    if patch and age is not None:
        st = "ok" if age <= 12 else ("warn" if age <= 24 else "bad")
        note = "" if age <= 12 else f" - {age} months old, probably no longer receives security updates"
        rows.append(R("Security patch level", patch + note, st))
    else:
        rows.append(R("Security patch level", patch))
    rows.append(R("Build", first(props, "ro.build.display.id")))
    rows.append(R("Build fingerprint", first(props, "ro.build.fingerprint")))
    utc = num(first(props, "ro.build.date.utc"))
    build_date = None
    if utc:
        try:
            build_date = datetime.fromtimestamp(utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            build_date = None
    rows.append(R("Firmware build date", build_date or first(props, "ro.build.date")))
    first_api = num(first(props, "ro.product.first_api_level", "ro.vendor.build.version.sdk"))
    facts["first_api"] = int(first_api) if first_api and 19 <= first_api <= 40 else None
    if first_api and int(first_api) in ANDROID_BY_API:
        ver, year = ANDROID_BY_API[int(first_api)]
        rows.append(R("Launch era (estimate)", f"Shipped with Android {ver}, so launched around {year}"))
    else:
        rows.append(R("Launch era (estimate)", f"Shipped with API {int(first_api)}" if first_api else None))
    rows.append(R("Region / sales code", first(props, "ro.csc.sales_code", "ro.boot.hwc", "ro.product.locale", "ro.boot.hardware.sku")))
    rows.append(R("Bootloader version", first(props, "ro.bootloader", "ro.boot.bootloader")))
    rows.append(R("Baseband (modem) version", first(props, "gsm.version.baseband", "ro.baseband")))
    ver = sh("cat /proc/version", 10).strip()
    rows.append(R("Kernel", ver[:140] if ver else None))
    sys_model = first(props, "ro.product.system.model")
    vend_model = first(props, "ro.product.vendor.model")
    facts["model_mismatch"] = bool(model and ((sys_model and sys_model.lower() != model.lower())
                                              or (vend_model and vend_model.lower() != model.lower())))
    return rows


def section_battery(sh, props, facts):
    rows = []
    bat = parse_kv(sh("dumpsys battery", 20))
    sys_b = {}
    for path, b in parse_sysfs_blocks(sh(BATT_SCRIPT, 30)).items():
        if b.get("type", "").lower() == "battery" and (not sys_b or path.endswith("/battery")):
            sys_b = b
    stats = sh("dumpsys batterystats | grep -iE 'capacity|cycle'", 45)

    level = num(sys_b.get("capacity")) if sys_b.get("capacity") else num(bat.get("level"))
    facts["level"] = level
    rows.append(R("Charge level", f"{int(level)} %" if level is not None else None))
    st_raw = bat.get("status")
    st_num = num(st_raw)
    rows.append(R("Charging state", BATT_STATUS.get(int(st_num), st_raw) if st_num is not None else (sys_b.get("status"))))
    sources = [n for n, k in (("AC charger", "ac powered"), ("USB", "usb powered"), ("Wireless", "wireless powered"))
               if str(bat.get(k, "")).lower() == "true"]
    rows.append(R("Power source now", ", ".join(sources) if sources else "On battery"))
    h_raw = bat.get("health")
    h_num = num(h_raw)
    h_txt = BATT_HEALTH.get(int(h_num)) if h_num is not None else sys_b.get("health")
    facts["batt_state"] = h_txt
    rows.append(R("Battery condition (Android's verdict)", h_txt,
                  None if not h_txt else ("ok" if h_txt == "Good" else ("na" if h_txt == "Unknown" else "bad"))))

    health, source, raw, derived = None, None, None, False
    design_mah, full_mah = None, None
    for key in ("soh", "batt_soh", "bsoh", "batt_asoc", "asoc"):
        v = num(sys_b.get(key))
        if v is not None and 0 < v <= 100:
            health, source = v, f"reported by the phone ({key})"
            break
    cf, cfd = num(sys_b.get("charge_full")), num(sys_b.get("charge_full_design"))
    ef, efd = num(sys_b.get("energy_full")), num(sys_b.get("energy_full_design"))
    if cfd and cfd > 0:
        design_mah = cfd / 1000 if cfd >= 100000 else cfd
    if cf and cf > 0:
        full_mah = cf / 1000 if cf >= 100000 else cf
    if health is None and cf and cfd and 0.3 <= cf / cfd <= 1.3:
        raw, derived, source = cf / cfd * 100, True, "current full capacity / design capacity"
    if health is None and raw is None and ef and efd and 0.3 <= ef / efd <= 1.3:
        raw, derived, source = ef / efd * 100, True, "energy full / design"
    est = re.search(r"Estimated battery capacity:\s*([\d.]+)\s*mAh", stats)
    learned = re.search(r"Last learned battery capacity:\s*([\d.]+)\s*mAh", stats)
    if est and design_mah is None:
        design_mah = float(est.group(1))
    if learned and full_mah is None:
        full_mah = float(learned.group(1))
    if health is None and raw is None and est and learned and float(est.group(1)) > 0:
        ratio = float(learned.group(1)) / float(est.group(1))
        if 0.3 <= ratio <= 1.3:
            raw, derived, source = ratio * 100, True, "Android's learned capacity (rough estimate)"
    if raw is not None:
        health = min(100.0, raw)
    facts["batt_pct"] = health
    facts["batt_capped"] = bool(raw is not None and raw > 100.4)
    if health is not None:
        st = "ok" if health >= 80 else ("warn" if health >= 70 else "bad")
        text = f"{health:.0f} %"
        if facts["batt_capped"]:
            text += f"  (measured {raw:.0f} %, shown as 100 % because a battery cannot be above 100 %)"
        rows.append(R("Battery health", f"{text}  - {source}", st))
        if derived:
            rows.append(R("Why this can differ from phone Settings",
                          "Settings shows the maker's own calculation. This tool divides the battery's current full "
                          "capacity by its design capacity. That counter is only 'learned' after full charges and the "
                          "design value is the typical (not the guaranteed minimum) capacity, so a gap of about 5 % is "
                          "normal. When your phone has its own battery-health number, trust that one."))
    else:
        rows.append(R("Battery health", "Not reported by this phone (many models hide it)"))
    shown = [f"{k}={sys_b[k]}" for k in ("soh", "batt_soh", "bsoh", "batt_asoc", "asoc", "charge_full", "charge_full_design",
                                         "charge_now", "energy_full", "energy_full_design", "cycle_count", "cycle_counts",
                                         "batt_full_capacity") if k in sys_b]
    if est:
        shown.append(f"estimated capacity={est.group(1)} mAh")
    if learned:
        shown.append(f"learned capacity={learned.group(1)} mAh")
    rows.append(R("Raw battery values read (for checking)", ", ".join(shown) if shown else None))
    rows.append(R("Design capacity (new battery)", f"{design_mah:.0f} mAh" if design_mah else None))
    rows.append(R("Current full capacity", f"{full_mah:.0f} mAh" if full_mah else None))

    cycles = None
    for key in ("cycle_count", "cycle_counts", "battery_cycle", "batt_cycle", "bat_cycle"):
        v = num(sys_b.get(key))
        if v is not None and 0 <= v < 10000:
            cycles = int(v)
            break
    if cycles is None:
        for k, v in bat.items():
            if "cycle" in k and num(v) is not None and 0 <= num(v) < 10000:
                cycles = int(num(v))
                break
    if cycles is None:
        m = re.search(r"(?i)cycle[ _]?count\D{0,5}(\d{1,5})", stats)
        if m:
            cycles = int(m.group(1))
    facts["cycles"] = cycles
    if cycles is not None:
        st = "ok" if cycles <= 500 else ("warn" if cycles <= 1000 else "bad")
        rows.append(R("Charge cycles", f"{cycles} full charge cycles", st))
    else:
        rows.append(R("Charge cycles", "Not reported by this phone"))

    temp = num(bat.get("temperature"))
    if temp is None:
        temp = num(sys_b.get("temp"))
    temp_c = temp / 10 if temp is not None else None
    facts["temp_c"] = temp_c
    rows.append(R("Temperature", f"{temp_c:.1f} °C" if temp_c is not None else None,
                  None if temp_c is None else ("ok" if temp_c < 40 else ("warn" if temp_c < 46 else "bad"))))
    volt = num(bat.get("voltage"))
    if volt is None and num(sys_b.get("voltage_now")):
        v = num(sys_b.get("voltage_now"))
        volt = v / 1000 if v > 100000 else v
    rows.append(R("Voltage", f"{volt:.0f} mV" if volt else None))
    cur = num(sys_b.get("current_now"))
    if cur is not None:
        cur_ma = abs(cur) / 1000 if abs(cur) >= 10000 else abs(cur)
        rows.append(R("Current right now", f"{cur_ma:.0f} mA"))
    mc = num(bat.get("max charging current"))
    rows.append(R("Max charging current", f"{mc / 1000:.0f} mA" if mc else None))
    rows.append(R("Battery technology", bat.get("technology") or sys_b.get("technology")))
    rows.append(R("Battery present", bat.get("present") or sys_b.get("present")))
    maker = " ".join(x for x in (sys_b.get("manufacturer"), sys_b.get("model_name")) if x)
    if maker:
        rows.append(R("Battery maker / model", maker))
    return rows


def section_display(sh, props, facts):
    rows = []
    size = re.search(r"Physical size:\s*(\d+)x(\d+)", sh("wm size", 10))
    dens = re.search(r"Physical density:\s*(\d+)", sh("wm density", 10))
    if size:
        w, h = int(size.group(1)), int(size.group(2))
        rows.append(R("Resolution", f"{w} x {h} pixels"))
        if dens and int(dens.group(1)) > 0:
            inches = math.hypot(w, h) / int(dens.group(1))
            rows.append(R("Screen size (estimate)", f"about {inches:.1f} inches"))
    else:
        rows.append(R("Resolution", None))
    rows.append(R("Pixel density", f"{dens.group(1)} dpi" if dens else None))
    fps = sh("dumpsys display | grep -i fps", 20)
    rates = sorted({int(round(float(x))) for x in re.findall(r"fps=([0-9.]+)", fps) if 20 <= float(x) <= 240})
    rows.append(R("Supported refresh rates", ", ".join(f"{r} Hz" for r in rates) if rates else None))
    return rows


def section_touch(sh, props, facts):
    rows = []
    devs = parse_input_devices(sh("getevent -lp", 20))
    touch = [d for d in devs if d["max_x"] and d["max_y"]]
    facts["touch_present"] = bool(touch) if devs else None
    if touch:
        t = max(touch, key=lambda d: (d["max_x"] + 1) * (d["max_y"] + 1))
        rows.append(R("Touchscreen controller", t["name"] or t["path"], "ok"))
        rows.append(R("Touch grid resolution", f"{t['max_x'] + 1} x {t['max_y'] + 1}"))
        rows.append(R("Simultaneous touch points", t["slots"] if t["slots"] else None))
    elif devs:
        rows.append(R("Touchscreen controller", "No touchscreen input device found", "bad"))
    else:
        rows.append(R("Touchscreen controller", "This phone does not allow reading input devices"))
    keys = set().union(*(d["keys"] for d in devs)) if devs else set()
    names = {"KEY_VOLUMEUP": "Volume Up", "KEY_VOLUMEDOWN": "Volume Down", "KEY_POWER": "Power",
             "KEY_HOMEPAGE": "Home", "KEY_BACK": "Back", "KEY_MENU": "Menu", "KEY_APPSELECT": "Recents"}
    found = [v for k, v in names.items() if k in keys]
    rows.append(R("Hardware buttons detected", ", ".join(found) if found else None))
    rows.append(R("Live touch & button test", "Use the 'Touch & Button Test' button in this app"))
    return rows


def section_hardware(sh, props, feats, facts):
    rows = []
    soc = " ".join(x for x in (first(props, "ro.soc.manufacturer"), first(props, "ro.soc.model")) if x)
    rows.append(R("Chipset (SoC)", soc or first(props, "ro.board.platform", "ro.hardware")))
    cpuinfo = sh("cat /proc/cpuinfo", 15)
    cores = len(re.findall(r"^processor\s*:", cpuinfo, re.M))
    hw = re.search(r"^Hardware\s*:\s*(.+)$", cpuinfo, re.M)
    rows.append(R("CPU hardware string", hw.group(1).strip() if hw else None))
    freqs = sh("for i in 0 1 2 3 4 5 6 7 8 9 10 11; do f=/sys/devices/system/cpu/cpu$i/cpufreq/cpuinfo_max_freq; "
               "[ -r $f ] && echo $(cat $f); done", 15)
    mhz = sorted({int(int(x) / 1000) for x in freqs.split() if x.isdigit()}, reverse=True)
    if cores:
        txt = f"{cores} cores"
        if mhz:
            txt += f" (max clocks: {', '.join(str(m) for m in mhz)} MHz)"
        rows.append(R("CPU cores", txt))
    else:
        rows.append(R("CPU cores", None))
    rows.append(R("CPU architecture", first(props, "ro.product.cpu.abilist", "ro.product.cpu.abi")))
    gles = sh("dumpsys SurfaceFlinger | grep -m1 GLES", 20)
    rows.append(R("GPU", gles.split(":", 1)[1].strip()[:110] if "GLES" in gles and ":" in gles else None))

    mem = re.search(r"MemTotal:\s*(\d+)\s*kB", sh("cat /proc/meminfo", 10))
    if mem:
        kb = int(mem.group(1))
        gb = kb / 1024 / 1024
        facts["ram_gb"] = gb
        cls = round_up_class(gb, (1, 2, 3, 4, 6, 8, 10, 12, 16, 18, 24))
        rows.append(R("RAM (usable)", f"{fmt_gb(kb)}" + (f"  (sold as about {cls} GB)" if cls else "")))
    else:
        rows.append(R("RAM (usable)", None))

    df = sh("df -k /data", 15)
    total_kb = used_kb = avail_kb = None
    for line in df.splitlines():
        parts = line.split()
        for i, tok in enumerate(parts):
            if tok.endswith("%") and i >= 3 and parts[i - 1].isdigit() and parts[i - 2].isdigit() and parts[i - 3].isdigit():
                total_kb, used_kb, avail_kb = int(parts[i - 3]), int(parts[i - 2]), int(parts[i - 1])
                break
        if total_kb:
            break
    if total_kb:
        free_pct = avail_kb * 100 / total_kb if total_kb else None
        facts["free_pct"] = free_pct
        cls = round_up_class(total_kb / 1024 / 1024, (8, 16, 32, 64, 128, 256, 512, 1024, 2048))
        rows.append(R("Storage (user partition)", f"{fmt_gb(total_kb)}" + (f"  (sold as about {cls} GB)" if cls else "")))
        rows.append(R("Storage used / free", f"{fmt_gb(used_kb)} used, {fmt_gb(avail_kb)} free ({free_pct:.0f} % free)",
                      "warn" if free_pct < 10 else None))
    else:
        rows.append(R("Storage (user partition)", None))

    cams = re.search(r"devices:\s*(\d+)", sh("dumpsys media.camera | grep -m1 'Number of camera devices'", 20))
    rows.append(R("Cameras reported", cams.group(1) if cams else None))

    for feat, label, severity in FEATURES:
        has = feat in feats
        if has:
            rows.append(R(label, "Present", "ok"))
        else:
            rows.append(R(label, "Missing" if feats else None, severity if feats else None))
    facts["feats"] = feats
    return rows


def find_imeis(sh, props):
    cands = set()
    for k, v in props.items():
        if "imei" in k.lower():
            cands.update(re.findall(r"\d{15}", v))
    for cmd in ("service call iphonesubinfo 1", "service call iphonesubinfo 1 s16 com.android.shell"):
        s = decode_parcel_string(sh(cmd, 15))
        cands.update(re.findall(r"\d{15}", s))
    m = re.findall(r"Device ID\s*=\s*(\d{14,16})", sh("dumpsys iphonesubinfo", 15))
    cands.update(x for x in m if len(x) == 15)
    return sorted(cands)


def section_sim(sh, props, facts):
    rows = []
    imeis = find_imeis(sh, props)
    valid = [i for i in imeis if luhn_ok(i)]
    invalid = [i for i in imeis if not luhn_ok(i)]
    facts["imei_invalid"] = bool(invalid and not valid)
    if valid:
        for n, i in enumerate(valid, 1):
            rows.append(R(f"IMEI {n}", f"{i}  (checksum valid)", "ok"))
    elif invalid:
        rows.append(R("IMEI", f"{invalid[0]}  (checksum INVALID - possibly fake or cloned)", "bad"))
    else:
        rows.append(R("IMEI", "Not readable over USB on this phone (Android 11+ blocks it). Press 'Show IMEI on phone' "
                              "or dial *#06# and compare with the box / sticker."))
    rows.append(R("SIM state", first(props, "gsm.sim.state")))
    rows.append(R("Network operator", first(props, "gsm.operator.alpha")))
    rows.append(R("Network type now", first(props, "gsm.network.type")))
    rows.append(R("SIM slots (reported)", first(props, "ro.telephony.sim.count", "persist.radio.multisim.config")))
    return rows


def mask_account(name):
    at = name.find("@")
    return (name[:2] + "***" + (name[at:] if at > 0 else "")) if name else name


def section_security(sh, props, facts):
    rows = []
    vb = first(props, "ro.boot.verifiedbootstate")
    rows.append(R("Verified boot state", vb, None if not vb else ("ok" if vb == "green" else ("warn" if vb == "yellow" else "bad"))))
    locked = first(props, "ro.boot.flash.locked")
    dev_state = first(props, "ro.boot.vbmeta.device_state")
    if locked is not None:
        is_locked = locked == "1"
    elif dev_state:
        is_locked = dev_state == "locked"
    else:
        is_locked = None
    facts["bl_locked"] = is_locked
    rows.append(R("Bootloader", None if is_locked is None else ("Locked" if is_locked else "UNLOCKED"),
                  None if is_locked is None else ("ok" if is_locked else "warn")))
    warr = first(props, "ro.boot.warranty_bit", "ro.warranty_bit")
    facts["warranty"] = warr
    if warr is not None:
        rows.append(R("Samsung warranty bit (Knox)", "Intact" if warr == "0" else "TRIPPED (device was modified)",
                      "ok" if warr == "0" else "bad"))
    su = sh("ls /system/bin/su /system/xbin/su /sbin/su /system/sbin/su /vendor/bin/su /su/bin/su 2>/dev/null", 15).strip()
    pk = sh("pm list packages | grep -iE 'topjohnwu.magisk|supersu|kingroot|kingoot'", 20).strip()
    rooted = bool(su or pk)
    facts["rooted"] = rooted
    rows.append(R("Root access", "Root files / apps found" if rooted else "No sign of root", "warn" if rooted else "ok"))
    se = sh("getenforce", 10).strip()
    rows.append(R("SELinux", se or None, None if not se else ("ok" if se == "Enforcing" else "bad")))
    crypto = first(props, "ro.crypto.state")
    rows.append(R("Storage encryption", crypto, None if not crypto else ("ok" if crypto == "encrypted" else "warn")))
    btype, btags = first(props, "ro.build.type"), first(props, "ro.build.tags")
    clean = (btype == "user" and btags == "release-keys") if (btype and btags) else None
    facts["test_keys"] = clean is False
    rows.append(R("Build type / signing", f"{btype} / {btags}" if btype and btags else None,
                  None if clean is None else ("ok" if clean else "warn")))
    rom = sorted({k.split(".")[1] for k in props
                  if re.match(r"ro\.(lineage|crdroid|pixelexperience|evolution|paranoid|havoc|aospa)", k)})
    facts["custom_rom"] = bool(rom)
    rows.append(R("Custom ROM markers", ", ".join(rom) if rom else "None found", "warn" if rom else "ok"))
    acc_txt = sh("dumpsys account", 20)
    accounts = re.findall(r"Account \{name=([^,}]+), type=([^}]+)\}", acc_txt)
    google = [a for a in accounts if "google" in a[1].lower()]
    facts["google_accounts"] = len(google)
    if accounts:
        shown = ", ".join(f"{mask_account(n)} ({t})" for n, t in accounts[:5])
        rows.append(R("Accounts still signed in", shown,
                      "bad" if google else "warn"))
        if google:
            rows.append(R("Factory-reset lock (FRP) risk",
                          "A Google account is still on the phone. If the phone is reset while it is signed in, "
                          "only that account can unlock it. Make the seller sign out first.", "bad"))
    else:
        rows.append(R("Accounts still signed in", "None found (or the phone hides them)", "ok"))
    return rows


def section_usage(sh, props, facts):
    rows = []
    pk3 = [l for l in sh("pm list packages -3", 25).splitlines() if l.startswith("package:")]
    pks = [l for l in sh("pm list packages -s", 25).splitlines() if l.startswith("package:")]
    rows.append(R("User-installed apps", len(pk3) if pk3 or pks else None))
    rows.append(R("System apps", len(pks) if pks else None))
    out = sh("for p in $(pm list packages -3 | cut -d: -f2 | head -n 80); do echo $p $(dumpsys package $p | "
             "grep -m1 firstInstallTime); done", 120)
    found = re.findall(r"(\S+)\s+firstInstallTime=(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", out)
    if found:
        pkg, when = min(found, key=lambda x: x[1])
        try:
            facts["oldest_user_app"] = datetime.strptime(when, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
        rows.append(R("Oldest user app install (hint of first use)", f"{when[:10]}  ({pkg})  - resets on factory reset"))
    else:
        rows.append(R("Oldest user app install (hint of first use)", None))
    up_txt = sh("cat /proc/uptime", 10)
    up = num(up_txt.split(" ")[0]) if up_txt else None
    if up is not None:
        d, rem = divmod(int(up), 86400)
        rows.append(R("Time since last restart", f"{d} days {rem // 3600} h"))
    rows.append(R("Time zone set on phone", first(props, "persist.sys.timezone")))
    return rows