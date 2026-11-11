"""
APK Virus Checker Mixin for Shoby Red Project.
Runs 4-stage local offline APK security analysis inside a CustomTkinter window.
"""
import os
import re
import shutil
import zipfile
import tempfile
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk
import customtkinter as ctk


def find_clamscan():
    possible_paths = [
        r"C:\Program Files\ClamAV\clamscan.exe",
        r"C:\Program Files (x86)\ClamAV\clamscan.exe",
        shutil.which("clamscan") or ""
    ]
    for path in possible_paths:
        if path and os.path.isfile(path):
            return path
    return None


CLAMSCAN_PATH = find_clamscan()

SUSPICIOUS_PERMISSIONS = {
    "BIND_ACCESSIBILITY_SERVICE": (5, "Keylogging / Auto-clicker Abuse"),
    "BIND_DEVICE_ADMIN": (4, "Full Device Administrator Takeover"),
    "REQUEST_INSTALL_PACKAGES": (4, "Silent Background App Dropper"),
    "SYSTEM_ALERT_WINDOW": (3, "Screen Overlay / Banking Credential Theft"),
    "READ_SMS": (3, "SMS Interception / 2FA Theft"),
    "SEND_SMS": (3, "Unauthorized Premium SMS Billing"),
    "RECEIVE_SMS": (3, "Incoming SMS Interception"),
    "RECORD_AUDIO": (2, "Background Microphone Eavesdropping"),
    "CAMERA": (2, "Unauthorized Camera Recording"),
    "ACCESS_FINE_LOCATION": (2, "GPS Location Tracking"),
    "RECEIVE_BOOT_COMPLETED": (1, "Auto-Start on Boot")
}

# Raw byte strings (rb"...") prevent Python escape sequence syntax warnings
DEX_PATTERNS = [
    (re.compile(rb"Ldalvik/system/InMemoryDexClassLoader;"), "In-Memory Payload Execution", 5),
    (re.compile(rb"Ldalvik/system/DexClassLoader;"), "Dynamic External Code Loading (Dropper)", 4),
    (re.compile(rb"Runtime;->exec\s*\(\s*\"/system/xbin/su\""), "Root Command Execution Attempt", 4),
    (re.compile(rb"http://(?!127\.0\.0\.1|10\.|192\.168\.|172\.(?:1[6-9]|2[0-9]|3[01])\.|0\.0\.0\.0)[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}"), "Public Raw IP C2 Address", 3),
    (re.compile(rb"getSimSerialNumber|getSubscriberId"), "Cellular Hardware Serial Tracking", 2),
]


class APKVirusCheckerMixin:

    def init_apk_virus_checker(self):
        self._apk_checker_win = None

    def open_apk_virus_checker(self):
        """Displays the APK Virus Checker window."""
        if hasattr(self, "_apk_checker_win") and self._apk_checker_win is not None and self._apk_checker_win.winfo_exists():
            self._apk_checker_win.lift()
            return

        win = ctk.CTkToplevel(self)
        win.title("🛡️ APK Virus Checker (Local Offline Security Engine)")
        win.geometry("740x580")
        win.attributes("-topmost", True)
        self._apk_checker_win = win

        # Header Bar
        header = ctk.CTkFrame(win, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(12, 6))

        ctk.CTkLabel(
            header,
            text="🛡️ Local Offline APK Virus & Malware Checker",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#3498db"
        ).pack(side="left")

        # Top Control Bar
        top_bar = ctk.CTkFrame(win, fg_color="transparent")
        top_bar.pack(fill="x", padx=15, pady=6)

        btn_scan = ctk.CTkButton(
            top_bar,
            text="📁 Choose APK File",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#1f6aa5",
            hover_color="#144870",
            height=32,
            command=lambda: select_apk()
        )
        btn_scan.pack(side="left", padx=(0, 10))

        progress = ttk.Progressbar(win, mode="indeterminate")
        progress.pack(fill="x", padx=15, pady=(4, 4))

        status_label = ctk.CTkLabel(
            win,
            text="Status: Ready to scan APK packages offline.",
            font=ctk.CTkFont(size=11),
            text_color="#a0a0a0",
            anchor="w"
        )
        status_label.pack(fill="x", padx=15, pady=(0, 4))

        # Output Log Box
        log_box = scrolledtext.ScrolledText(
            win,
            wrap=tk.WORD,
            bg="#0d0d0d",
            fg="#33ff33",
            insertbackground="#ffffff",
            font=("Consolas", 10),
            borderwidth=1,
            relief="solid"
        )
        log_box.pack(fill=tk.BOTH, expand=True, padx=15, pady=6)
        log_box.insert(tk.END, "=== APK Offline Security Engine Initialized ===\nSelect an APK file to begin local analysis.\n")

        def log(text):
            log_box.insert(tk.END, text + "\n")
            log_box.see(tk.END)

        def select_apk():
            path = filedialog.askopenfilename(filetypes=[("Android APK Files", "*.apk")])
            if not path:
                return

            btn_scan.configure(state="disabled")
            log_box.delete("1.0", tk.END)
            progress.start(10)
            status_label.configure(text=f"Analyzing {os.path.basename(path)}...", text_color="#f1c40f")

            threading.Thread(target=run_full_analysis, args=(path,), daemon=True).start()

        def parse_binary_xml_permissions(manifest_bytes):
            found_permissions = set()
            matches = re.findall(rb"android\.permission\.([A-Z0-9_]+)", manifest_bytes)
            for m in matches:
                found_permissions.add(m.decode("utf-8", errors="ignore"))
            return found_permissions

        def run_full_analysis(apk_path):
            log("=" * 70)
            log("🔍 STAGE 1: UNPACKING ARCHIVE")
            log(f"Target: {apk_path}")
            log("=" * 70)

            temp_dir = tempfile.mkdtemp(prefix="apk_scan_")
            total_risk_score = 0
            detections = []

            try:
                with zipfile.ZipFile(apk_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
                log("[OK] Extracted APK files to isolated temporary directory.")

                # Stage 2: DEX Static Heuristics
                log("\n" + "-" * 70)
                log("🔍 STAGE 2: DEX BINARY HEURISTICS & PATTERN MATCHING")
                log("-" * 70)

                dex_files = [f for f in os.listdir(temp_dir) if f.endswith(".dex")]
                if dex_files:
                    for dex in dex_files:
                        dex_path = os.path.join(temp_dir, dex)
                        with open(dex_path, "rb") as f:
                            content = f.read()
                            for pattern, desc, score in DEX_PATTERNS:
                                if pattern.search(content):
                                    total_risk_score += score
                                    msg = f"[SUSPICIOUS] {dex} -> {desc} (+{score} Risk)"
                                    detections.append(msg)
                                    log(msg)
                    if not detections:
                        log("[OK] Clean - No malicious DEX bytecode patterns detected.")
                else:
                    log("[WARN] No .dex bytecode files found inside package.")

                # Stage 3: Binary XML Manifest Permission Audit
                log("\n" + "-" * 70)
                log("🔍 STAGE 3: BINARY MANIFEST (AXML) PERMISSION AUDIT")
                log("-" * 70)

                manifest_path = os.path.join(temp_dir, "AndroidManifest.xml")
                if os.path.exists(manifest_path):
                    with open(manifest_path, "rb") as f:
                        raw_manifest_bytes = f.read()
                    
                    requested_perms = parse_binary_xml_permissions(raw_manifest_bytes)
                    if requested_perms:
                        log(f"Found {len(requested_perms)} requested permission(s) in Binary XML:")
                        for perm in sorted(requested_perms):
                            if perm in SUSPICIOUS_PERMISSIONS:
                                score, desc = SUSPICIOUS_PERMISSIONS[perm]
                                total_risk_score += score
                                msg = f"  ⚠️ {perm} -> {desc} (+{score} Risk)"
                                detections.append(msg)
                                log(msg)
                            else:
                                log(f"  • {perm}")
                    else:
                        log("[INFO] No standard android.permission declarations parsed.")
                else:
                    log("[WARN] AndroidManifest.xml missing from APK root.")

                # Stage 4: ClamAV Offline Signature Scan
                log("\n" + "-" * 70)
                log("🔍 STAGE 4: LOCAL CLAMAV RECURSIVE BINARY SCAN")
                log("-" * 70)

                clam_bin = CLAMSCAN_PATH
                if clam_bin:
                    log(f"Using ClamAV binary at: {clam_bin}")
                    cmd = [clam_bin, "-r", "-v", temp_dir]
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

                    for line in proc.stdout:
                        if "FOUND" in line:
                            total_risk_score += 10
                            msg = f"[CLAMAV MALWARE MATCH] {line.strip()}"
                            detections.append(msg)
                            log(f"🚨 {line.strip()}")

                    proc.wait()
                    if proc.returncode == 0:
                        log("[OK] ClamAV Engine: 0 signature matches across extracted files.")
                    elif proc.returncode == 1:
                        log("[ALERT] ClamAV matched known malware signatures!")
                else:
                    log("[SKIP] ClamAV not installed locally. (Install ClamAV to enable Stage 4).")

            except Exception as e:
                log(f"\n[ERROR] Analysis failed: {e}")
                total_risk_score += 5
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

            self.after(0, lambda: finish_scan(total_risk_score, detections))

        def finish_scan(score, detections):
            progress.stop()
            btn_scan.configure(state="normal")

            log("\n" + "=" * 70)
            log(f"📊 VERDICT SUMMARY (Total Risk Score: {score})")
            log("=" * 70)

            if score == 0:
                status_text = "✅ Clean - No suspicious code or sensitive permissions."
                status_label.configure(text=status_text, text_color="#2ecc71")
            elif score < 6:
                status_text = "⚠️ Low Risk - Standard app permissions / generic APIs."
                status_label.configure(text=status_text, text_color="#f1c40f")
            else:
                status_text = f"🚨 HIGH RISK ({len(detections)} detection flags triggered)!"
                status_label.configure(text=status_text, text_color="#e74c3c")

            log(f"Result: {status_text}")
            if hasattr(self, "toast"):
                self.toast("🛡️ APK Security Scan Complete")
