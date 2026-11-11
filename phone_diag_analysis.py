#!/usr/bin/env python3
"""
Android Diagnostic Assessment & Report Builder (part 4 of 5)
============================================================
Evaluates facts gathered from ADB, estimates device age, and builds reports.
"""
from datetime import datetime

from phone_diag_parsers import (
    ANDROID_BY_API, ANDROID_RELEASE, CHECK_BY_HAND, CYCLES_PER_DAY, FEATURES,
    STATUS_ICONS, R, fmt_age, months_between, parse_features, parse_getprop,
    short_age
)
from phone_diag_sections import (
    section_battery, section_display, section_hardware, section_identity,
    section_security, section_sim, section_touch, section_usage
)


def build_assessment(facts):
    items = []

    def add(st, text):
        items.append({"status": st, "text": text})

    p, c = facts.get("batt_pct"), facts.get("cycles")
    if p is not None:
        add("ok" if p >= 80 else ("warn" if p >= 70 else "bad"), f"Battery health about {p:.0f} %"
            + ("" if p >= 80 else " - expect to replace the battery soon" if p >= 70 else " - weak battery, budget for a replacement"))
    else:
        add("warn", "Battery health is not reported by this phone - judge it by how fast it drains in a short test")
    if c is not None:
        add("ok" if c <= 500 else ("warn" if c <= 1000 else "bad"), f"{c} charge cycles"
            + (" (low wear)" if c <= 300 else " (normal wear)" if c <= 500 else " (heavily used)"))
    if facts.get("batt_state") and facts["batt_state"] not in ("Good", "Unknown"):
        add("bad", f"Android reports the battery as '{facts['batt_state']}'")
    t = facts.get("temp_c")
    if t is not None and t >= 40:
        add("warn" if t < 46 else "bad", f"Battery is warm ({t:.0f} °C) - repeat the scan when cool")
    age = facts.get("patch_age")
    if age is not None and age > 24:
        add("bad", f"Security patches stopped about {age} months ago")
    elif age is not None and age > 12:
        add("warn", f"Last security patch is {age} months old")
    if facts.get("api") and facts["api"] < 29:
        add("warn", f"Old Android (API {facts['api']}) - many apps will stop supporting it")
    if facts.get("google_accounts"):
        add("bad", "A Google account is still signed in - factory-reset lock risk, seller must sign out")
    if facts.get("bl_locked") is False:
        add("warn", "Bootloader is unlocked - phone may have been modified")
    if facts.get("rooted"):
        add("warn", "Root access detected")
    if facts.get("warranty") == "1":
        add("bad", "Samsung warranty bit is tripped - phone was modified")
    if facts.get("test_keys"):
        add("warn", "Not a stock release build (test-keys / debug build)")
    if facts.get("custom_rom"):
        add("warn", "Custom ROM markers found - not the maker's software")
    if facts.get("model_mismatch"):
        add("warn", "Model name differs between system and vendor partitions (rebrand, custom ROM or fake)")
    if facts.get("imei_invalid"):
        add("bad", "IMEI checksum is invalid - possibly fake or cloned")
    if facts.get("touch_present") is False:
        add("bad", "No touchscreen controller found")
    fp = facts.get("free_pct")
    if fp is not None and fp < 10:
        add("warn", f"Storage almost full ({fp:.0f} % free)")
    feats = facts.get("feats") or set()
    if feats:
        for feat, label, sev in FEATURES:
            if sev and feat not in feats:
                add(sev, f"{label} not reported by the phone")
    if not items:
        add("ok", "Nothing alarming found")
    bad = sum(1 for i in items if i["status"] == "bad")
    warn = sum(1 for i in items if i["status"] == "warn")
    if bad:
        verdict = ("bad", f"Serious concerns found ({bad}). Check the red items before paying, or walk away.")
    elif warn >= 3:
        verdict = ("warn", f"Some concerns ({warn}). Check the yellow items and negotiate the price.")
    elif warn:
        verdict = ("warn", "Mostly fine, with small points to check.")
    else:
        verdict = ("ok", "Looks healthy on everything this scan can measure.")
    return items, verdict


def estimate_age(facts):
    now = datetime.now()
    ev = []

    def note(label, text):
        ev.append(R(label, text))

    upper = lower = None
    api = facts.get("first_api")
    if api in ANDROID_RELEASE:
        y, mo = ANDROID_RELEASE[api]
        upper = months_between(datetime(y, mo, 1))
        note("Shipped with Android", f"API {api} (released about {y}-{mo:02d}) - this unit cannot be older than "
                                     f"{fmt_age(upper)}")
    else:
        note("Shipped with Android", None)
    oa = facts.get("oldest_user_app")
    if oa and datetime(2012, 1, 1) <= oa <= now:
        lower = months_between(oa)
        note("Oldest user app install", f"{oa:%Y-%m-%d} - the phone has been in use for at least {fmt_age(lower)} "
                                        f"(the date resets on a factory reset)")
    else:
        note("Oldest user app install", None)
    if lower is not None and upper is not None and lower > upper + 6:
        note("Install date ignored", "older than the phone model itself (wrong clock or copied data)")
        lower = None
    if lower is not None and upper is not None and lower > upper:
        upper = lower

    est = []
    c = facts.get("cycles")
    if c is not None and c >= 30:
        lo, typ, hi = (c / (r * 30.44) for r in CYCLES_PER_DAY)
        est.append((3, typ, lo, hi))
        note("Battery wear (cycles)", f"{c} charge cycles = about {fmt_age(typ)} at a typical 0.8 cycles per day "
                                      f"(heavy use {fmt_age(lo)}, light use {fmt_age(hi)})")
    else:
        note("Battery wear (cycles)", "Not reported by this phone" if c is None else "Too few cycles to judge")
    h = facts.get("batt_pct")
    if h is not None and h < 99:
        typ = ((100 - h) / 0.04) / (0.8 * 30.44)
        est.append((1, typ, typ * 0.5, typ * 2))
        note("Battery wear (health %)", f"{h:.0f} % health = about {fmt_age(typ)} (very rough: batteries age at "
                                        f"different speeds)")
    else:
        note("Battery wear (health %)", "No usable signal (100 % or not reported)")

    centre = low = high = None
    if est:
        wsum = sum(w for w, *_ in est)
        centre, low, high = (sum(e[0] * e[i] for e in est) / wsum for i in (1, 2, 3))
    elif lower is not None and upper is not None and upper - lower <= 12:
        centre, low, high = (lower + upper) / 2, lower, upper
    conflict = None
    if centre is not None and est:
        if lower is not None and high < lower:
            conflict = "younger"
            centre, low = lower, lower
            high = min(upper, lower + 12) if upper is not None else lower + 12
            note("Clues disagree", "The battery looks younger than the time the phone has been in use - the battery may "
                                   "have been replaced, or the phone is used lightly. The install date was trusted.")
        elif upper is not None and low > upper:
            conflict = "older"
            centre, high = upper, upper
            low = max(lower if lower is not None else 0.0, upper - 12)
            note("Clues disagree", "The battery looks more worn than a phone of this model could be - the estimate "
                                   "was capped at the age of the model.")
    if centre is not None and conflict is None:
        if lower is not None:
            centre, low = max(centre, lower), max(low, lower)
        if upper is not None:
            centre, high = min(centre, upper), min(high, upper)
        low, high = min(low, centre), max(high, centre)

    if centre is None:
        conf = None
        line = "Probable age of phone = not enough data for a reliable estimate"
        bits = []
        if upper is not None:
            bits.append(f"at most about {fmt_age(upper)} old")
        if lower is not None:
            bits.append(f"in use for at least {fmt_age(lower)}")
        sub = ("Only this is certain: " + ", ".join(bits)) if bits else "The phone reported too little."
        short = "n/a"
    else:
        width = high - low
        if conflict:
            conf = "low"
        elif c is not None and c >= 150 and len(est) >= 2 and width <= 10:
            conf = "high"
        elif width <= 20:
            conf = "medium"
        else:
            conf = "low"
        line = f"Probable age of phone = {fmt_age(centre)} old"
        sub = (f"Rough estimate, most likely between {fmt_age(low)} and {fmt_age(high)}  (confidence: {conf}). "
               f"The phone does not store its purchase date.")
        short = short_age(centre)
    note("Result", line.replace("Probable age of phone = ", ""))
    note("Limits", "A replaced battery or a factory reset makes the phone look younger; heavy or light charging "
                   "habits move the estimate by months.")
    return {"line": line, "sub": sub, "short": short, "confidence": conf,
            "months": None if centre is None else round(centre, 1), "rows": ev}


def collect_report(sh, serial, progress=lambda m: None):
    progress("Reading system properties...")
    props = parse_getprop(sh("getprop", 30))
    if not props:
        raise RuntimeError("The phone returned no data. Unlock it, accept the USB debugging prompt "
                           "and try again.")
    facts, sections = {}, []

    def add(title, icon, fn, *args):
        progress(f"{title}...")
        try:
            sections.append({"title": title, "icon": icon, "rows": fn(*args)})
        except Exception as e:
            sections.append({"title": title, "icon": icon, "rows": [R("Could not read this section", str(e), "warn")]})

    progress("Reading hardware features...")
    feats = parse_features(sh("pm list features", 25))
    add("Identity & software", "📱", section_identity, sh, props, serial, facts)
    add("Battery", "🔋", section_battery, sh, props, facts)
    add("Display", "🖥️", section_display, sh, props, facts)
    add("Touch & buttons", "👆", section_touch, sh, props, facts)
    add("Hardware & sensors", "⚙️", section_hardware, sh, props, feats, facts)
    add("SIM & IMEI", "📶", section_sim, sh, props, facts)
    add("Security & ownership", "🔐", section_security, sh, props, facts)
    add("Apps & usage (may take a few seconds)", "🗂️", section_usage, sh, props, facts)
    sections.append({"title": "Check by hand (cannot be read over USB)", "icon": "📝",
                     "rows": [R("•", t) for t in CHECK_BY_HAND]})
    items, verdict = build_assessment(facts)
    age = estimate_age(facts)
    sections.insert(1, {"title": "Probable age of phone (estimate)", "icon": "⏳", "rows": age["rows"]})

    def tile(label, value, status):
        return {"label": label, "value": value, "status": status}

    p, c = facts.get("batt_pct"), facts.get("cycles")
    headline = [
        tile("Battery health", f"{p:.0f} %" if p is not None else "n/a",
             None if p is None else ("ok" if p >= 80 else ("warn" if p >= 70 else "bad"))),
        tile("Probable age", age["short"], None),
        tile("Charge cycles", str(c) if c is not None else "n/a",
             None if c is None else ("ok" if c <= 500 else ("warn" if c <= 1000 else "bad"))),
        tile("Android", f"{facts.get('api') and ANDROID_BY_API.get(facts['api'], ('API ' + str(facts['api']),))[0] or 'n/a'}", None),
        tile("Security patch age", f"{facts['patch_age']} mo" if facts.get("patch_age") is not None else "n/a",
             None if facts.get("patch_age") is None else ("ok" if facts["patch_age"] <= 12 else ("warn" if facts["patch_age"] <= 24 else "bad"))),
        tile("Storage free", f"{facts['free_pct']:.0f} %" if facts.get("free_pct") is not None else "n/a",
             None if facts.get("free_pct") is None else ("warn" if facts["free_pct"] < 10 else "ok")),
    ]
    return {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "serial": serial, "name": facts.get("name", serial),
            "headline": headline, "age": {k: age[k] for k in ("line", "sub", "short", "confidence", "months")},
            "verdict": {"status": verdict[0], "text": verdict[1]},
            "assessment": items, "sections": sections}


def report_to_text(rep, extra_sections=None):
    lines = ["=" * 78, f"ANDROID PHONE DIAGNOSTIC REPORT - {rep['name']}", f"Generated: {rep['generated']}   Serial: {rep['serial']}",
             "=" * 78, ""]
    if rep.get("age"):
        lines += [rep["age"]["line"].upper(), "  " + rep["age"]["sub"], ""]
    lines += [f"VERDICT: {STATUS_ICONS[rep['verdict']['status']]} {rep['verdict']['text']}", ""]
    lines.append("KEY POINTS")
    for it in rep["assessment"]:
        lines.append(f"  {STATUS_ICONS[it['status']]} {it['text']}")
    for sec in rep["sections"] + (extra_sections or []):
        lines += ["", f"--- {sec['title']} ---"]
        for r in sec["rows"]:
            icon = STATUS_ICONS.get(r["status"], "")
            lines.append(f"  {r['label']:<42} {icon} {r['value']}".rstrip())
    lines += ["", "Note: values come from the phone itself; a replaced battery or a modified phone can show clean numbers.",
              "This report contains the serial number / IMEI - do not post it publicly."]
    return "\n".join(lines)