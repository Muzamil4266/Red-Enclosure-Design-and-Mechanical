"""
settings.py  (part 3 of 3 of Settings Mixin)

CustomTkinter GUI windows, documentation builder, and main SettingsMixin.
Re-exports all constants and schema helpers so existing imports continue working uninterrupted.
"""
import os
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox

import customtkinter as ctk

from settings_schema import (  # noqa: F401
    SETTINGS_FILE, CANVAS_W, CENTER_X, SCREEN_MODES, THEME_OPTIONS,
    THEME_PALETTES, DEFAULT_TOOLBAR_BG, BTN_COLORS, BORDER_COLORS,
    AUTOSAVE_GAP_SECONDS, MANUAL_SAVE_GAP_SECONDS, SETTINGS_SCHEMA,
    UI_SCALE, LOW_MEMORY_UNDO_CAP, S, coerce_setting
)
from settings_logic import SettingsLogicMixin  # noqa: F401


class SettingsMixin(SettingsLogicMixin):

    # ------------------------------------------------------------------ Documentation, Help and Licences
    def open_docs_help_window(self):
        """Opens a dedicated window for Documentation, Help and Licences."""
        win = getattr(self, "_docs_help_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return

        w, h = 900, 700
        win = ctk.CTkToplevel(self)
        win.title("📖 Documentation, Help and Licences")
        win.attributes("-topmost", True)
        self._docs_help_win = win
        self.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - w) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - h) // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.minsize(700, 500)

        self._build_docs_help_tab(win)

        win.bind("<Escape>", lambda e: win.destroy())
        win.after(150, win.focus_force)

    def _build_docs_help_tab(self, parent):
        """Builds the Documentation, Help and Licences view content inside parent frame or window."""
        sf = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        sf.pack(fill="both", expand=True, padx=6, pady=6)

        ctk.CTkLabel(
            sf,
            text="📖 Documentation, Help and Licences",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#e0a070"
        ).pack(anchor="w", pady=(5, 15))

      # ------------------------------------------------------------- SECTION 1: WHAT IS THIS PROGRAM
        sec1_frame = ctk.CTkFrame(sf, fg_color="#1f1f1f", corner_radius=8, border_width=1, border_color="#333333")
        sec1_frame.pack(fill="x", pady=(0, 15), padx=2)

        ctk.CTkLabel(
            sec1_frame,
            text="⭐ What is this Program",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#f1c40f"
        ).pack(anchor="w", padx=12, pady=(10, 5))

        program_text = """Red Enclosure is a standalone, offline desktop application designed to integrate real-world hardware prototyping, mechanical system modeling, media processing, computer vision, and local artificial intelligence automation into a single workspace. Developed out of a practical need for physical accuracy in technical schematics, the software replaces non-functional visual approximations with user-built circuit designs, micro-PCB stitching, smartphone diagnostics, and low-overhead local software utilities.



 1. PROGRAM PHILOSOPHY AND ARCHITECTURAL PURPOSE



Critique of AI-Generated Schematics: The development of Red Enclosure originated from the realization that modern generative AI models frequently create circuit schematics that appear visually correct to non-experts but fail to work when physically constructed. Modern frontier AI models only get electrical circuit logic right approximately 50 to 60 percent of the time, resulting in online platforms filled with non-functional visual demonstrations.


Deterministic Verification: Red Enclosure resolves this issue by providing a manual drag-and-drop canvas where engineers, makers, and technicians can place real physical component models and draw precise wiring connections to verify whether a layout will actually function.


Offline Privacy and Security: The program operates completely offline without connecting to internet servers. All project files, captured photos, extracted text, and media recordings remain strictly within the local host computer.
Low Hardware Overhead: Every utility in the suite was optimized line-by-line so that the software runs smoothly on standard, lower-power PCs without requiring expensive hardware or dedicated GPUs.



AI-Assisted Systems Engineering: The software architecture was built using AI coding assistants, where Claude served as the primary implementation driver for feature coding and Gemini provided secondary technical support.



 2. INTERACTIVE CANVAS AND SCHEMATIC WORKSPACE
Electrical Component Library: The workspace contains an extensive library of real-world electrical components, including resistors, diodes, transformers, and switches.
Pin and Terminal Alignment: All library graphics are cropped and edited so that physical connecting pins, leads, and terminals are visually distinct for precise wiring alignment.
Realistic Wire Tool: A dedicated wire tool places adjustable, realistic wire objects onto the canvas that can be stretched, angled, and routed between component pins to assemble functioning circuit boards.



Mechanical and Fluid Pipe Modeling: The canvas extends beyond electronics to support mechanical plumbing layouts. It includes pipe sections, elbow joints, sockets, and adapters that connect together to design fluid conduits.
Drawing and Layout Tools: The top toolbar provides a freehand Pen tool, Line tool, Box tool, and geometric shapes such as Circles, Stars, and Springs.


Structured Data Tables: Users can insert formatted data tables directly onto the canvas to track bills of materials, component specs, or pinout mappings.



Workspace Controls: The interface incorporates an Eraser, stroke size adjustments, smooth Zoom controls, color palettes, Undo/Redo functions, and controls to insert and manipulate external images.
Exporting: Canvas projects can be cleared, saved into dedicated project folders, or exported directly into PDF documents across landscape, portrait, or full-screen screen layouts.



 3. COMPUTER VISION AND SPATIAL IMAGE STITCHING
2D Room Layout Stitcher: This module processes multiple overlapping photographs of an indoor room taken with a smartphone camera and automatically stitches them into a unified two-dimensional spatial layout inside the workspace.



High-Definition PCB Mapper: Standard smartphone cameras cannot capture an entire Printed Circuit Board in high resolution without lens distortion or blur. The PCB Mapper ingests close-up, overlapping photos of different board sections and stitches them into a single high-definition panoramic image of the entire PCB.
Image Enhancement: A dedicated post-processing filter sharpens traces, component labels, and solder joints on stitched PCB maps to facilitate visual circuit inspection.



 4. MOBILE DIAGNOSTICS AND COMMAND-LINE AUTOMATION
Low-Latency Screen Mirroring: The application replaces manual terminal commands with an automated Python GUI wrapper for Scrcpy. Users can mirror Android smartphone screens live over USB or Wi-Fi with high speed and zero display lag.
ADB Hardware Inspector and Age Verification: By communicating directly through Android Debug Bridge, the program extracts hardware registers, system metadata, and build configurations from connected Android devices. It calculates the estimated physical manufacturing age of the phone to assist in inspecting used hardware.
Accelerated Command-Line Downloader: To bypass browser download speed limits, the software embeds a Python GUI wrapper around the multi-threaded aria2 command-line downloader, enabling multi-connection file downloads at maximum network bandwidth.



 5. OFFLINE SPEECH RECOGNITION AND DOCUMENT OCR
Offline Multilingual Speech-to-Text: Red Enclosure includes a local speech recognition module for English and Hindi audio and video files. It converts spoken audio directly into text transcripts without an internet connection, scaling in speed based on system hardware.



Offline AI Paddle OCR Engine: The document engine extracts text from scanned pages and photographs. Images undergo automatic sharpening, contrast enhancement, and rotation correction prior to text extraction.
High Accuracy: The OCR pipeline achieves extraction accuracy rates between 90 and 99 percent across physical prints and multi-page PDF documents.



PDF OCR Processing: Multi-page PDF files are split into high-resolution images, enhanced through contrast filters, processed through the OCR engine, and aggregated into complete text transcripts.



 6. MEDIA AUTOMATION, SECURITY, AND SYSTEM TOOLS
Silence-Cutting Video Editor: An automated video editor analyzes audio waveforms to detect and remove silent pauses, stitching active vocal segments into a fast-paced finished video.
Integrated Screen Recorder: A desktop recording utility allows users to capture live video of their screen activities directly within the application.
Physics Simulation: A pipe simulation module models elastic water flow mechanics for interactive testing of fluid conduit designs.
Real Eye Animation and Emotion AI: The suite includes an interactive animated human eye module with realistic blinking controls, alongside a webcam-based AI facial emotion detector that identifies expressions in real time.
AES-256 PDF Password Locker: A security tool encrypts PDF documents with AES-256 bit password protection.



Android APK Virus Checker: A mobile security module scans APK installation files for malware signatures, suspicious code, and dangerous permission requests.


Cryptographic Hash Duplicate Finder: Storage utilities scan local directories for duplicate images and PDFs using cryptographic content hashing rather than filenames.


Automated Dependency Installer: An internal system monitor audits the runtime environment, automatically downloading and installing missing Python dependencies to ensure smooth software execution.


EXTRA EXTRA 




Engineered as an open, highly capable technical environment, the workspace liberates users from the boundaries of predefined components and static layouts. Its comprehensive array of integrated utilities enables limitless custom design, procedural assembly, and deep workflow integration tailored to any project requirement."""

        info_textbox = ctk.CTkTextbox(
            sec1_frame,
            font=ctk.CTkFont(size=12, family="Consolas"),
            fg_color="#181818",
            text_color="#dcdcdc",
            corner_radius=6,
            border_width=1,
            border_color="#2b2b2b",
            height=320,
            wrap="word"
        )
        info_textbox.pack(fill="x", padx=12, pady=(0, 12))
        info_textbox.insert("1.0", program_text.strip())
        info_textbox.configure(state="disabled")
        # ------------------------------------------------------------- SECTION 2: MORE TOOLS FEATURES EXPLANATION
        sec2_frame = ctk.CTkFrame(sf, fg_color="#1f1f1f", corner_radius=8, border_width=1, border_color="#333333")
        sec2_frame.pack(fill="x", pady=(0, 15), padx=2)

        ctk.CTkLabel(
            sec2_frame,
            text="⭐ More Tools Features Explanation",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#f1c40f"
        ).pack(anchor="w", padx=12, pady=(10, 10))

        features = [
            ("2D Room Stitcher",
             "Capture many overlapping photos of a room or a home with your smartphone and stitch them together to view fully your room in Full 2D single image on workspace."),
            ("PCB Mapper",
             "Capture multiple overlapping photos of a PCB board on your smartphone then stitch them together to form a highly detailed and sharp PCB image on which every component is visible that normally not with single photo."),
            ("Enhance PCB Mapped Image",
             "Enhance the PCB image that you just stitched to improve clarity and visualization of that PCB board."),
            ("Mirror Smartphone in PC",
             "Mirror your smartphone in PC using scrcpy fast and easy no need to type any commands manually all process is automatic and it is very fast with no lags. You can view your body parts like ears etc in real time which normally wouldn't visible to you."),
            ("Any Android Phone Age & Technical Details",
             "In this, you can check the age and find out technical details of any Android phone. A probability is also shown here, which lets you know how many years or how many months old the phone you are checking is. You just have to connect a USB cable from your laptop, and all the technical details will appear."),
            ("Screen Recorder",
             "In Screen Recorder, whatever work you are doing in the main workspace inside this program, you can record that entire screen and watch it later to see everything you did. This is strictly for this program only, not a screen recorder for the whole PC. It will only work within this program and save the recording so that you can play it back later."),
            ("Pipe Simulation Fun",
             "In Pipe Simulation Fun, you can see how water behaves inside a pipe. It uses real physics and was built for fun purposes."),
            ("Save Project as PDF",
             "In this function, whatever work you are doing in the workspace, you can export it to PDF along with the full screen. No matter whether your screen extends fully horizontally or vertically, it exports entirely into a PDF."),
            ("Export All Components / Photos Locally",
             "All the components provided in our program can be exported locally—meaning you can save all the provided components locally with full quality. Just as you imported them, you can export them in the exact same way with full quality."),
            ("AI Offline Paddle OCR Image to PDF Scanning",
             "In this, you can use Paddle OCR and use image-to-text. It has been extensively tested and works very well, and scanning is supported. First, it will perform scanning, and then you can either convert it into a PDF or extract text if you want to."),
            ("Python PDF Locker",
             "In this Python PDF Locker, you can lock any PDF file with AES-256 high security."),
            ("Scan & Auto-Install Missing Packages",
             "Whichever packages or dependencies this program is using, you can directly install them through this without needing to open CMD. It will scan the entire program and install all the dependencies being used in this program right from here."),
            ("Live Emotion Detector",
             "As soon as you place your face in front of your PC's camera, it will detect your emotion—meaning whether you are sad, neutral, happy, etc. It uses a real-time model completely offline."),
            ("Auto Video Editor",
             "In this video editor, you can remove silence from any video and make it fast-paced—meaning you can remove those dead cuts with the help of VAD (Voice Activity Detection). And it works very well."),
            ("APK Virus Checker",
             "In this, you can check any APK to see whether it contains a virus or not."),
            ("Duplicate Image & PDF Finder / Cleaner",
             "This is a top-level option where you can find duplicate images on your PC using actual hash values to verify what the actual content is. Rather than just checking file names—which would be very basic—this checks what their complete content is with full fingerprinting using hashing. Hashing is applied to both PDFs and duplicate images. It works very well, and is personally tested and verified.")
        ]

        for title, desc in features:
            f_box = ctk.CTkFrame(sec2_frame, fg_color="#181818", corner_radius=6)
            f_box.pack(fill="x", padx=10, pady=4)
            ctk.CTkLabel(
                f_box, text=f"• {title}", font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#3498db", anchor="w"
            ).pack(anchor="w", padx=10, pady=(6, 2))
            ctk.CTkLabel(
                f_box, text=desc, font=ctk.CTkFont(size=11),
                text_color="#cccccc", anchor="w", justify="left", wraplength=760
            ).pack(anchor="w", padx=14, pady=(0, 6))

        # ------------------------------------------------------------- SECTION 3: LICENCES AND DISCLAIMER
        sec3_frame = ctk.CTkFrame(sf, fg_color="#1f1f1f", corner_radius=8, border_width=1, border_color="#333333")
        sec3_frame.pack(fill="x", pady=(0, 15), padx=2)

        ctk.CTkLabel(
            sec3_frame,
            text="⭐ Licences and Disclaimer",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#f1c40f"
        ).pack(anchor="w", padx=12, pady=(10, 10))
        eula_text = (
            "END-USER LICENSE AGREEMENT (EULA) AND DISCLAIMER OF WARRANTY\n"
            "Copyright (c) 2026 Muzamil Khan. All Rights Reserved.\n"
            "Licensor: Muzamil Khan\n"
            "Contact Email: khanofficial108@gmail.com\n"
            "Jurisdiction: India\n\n"
            "IMPORTANT: READ CAREFULLY BEFORE INSTALLING, COPYING, OR USING THIS SOFTWARE.\n\n"
            "1. GRANT OF LICENSE (PERSONAL USE ONLY)\n"
            "Subject to the terms and conditions of this Agreement, Muzamil Khan grants you a non-exclusive, non-transferable, non-sublicensable, and revocable license to install and execute this software solely on hardware owned or directly controlled by you, strictly for your own private, non-commercial, personal purposes.\n\n"
            "2. STRICT PROHIBITIONS & RESTRICTIONS\n"
            "Any use outside the express grant of Section 1 is strictly forbidden and constitutes a material breach of contract as well as a direct violation of applicable copyright laws in India and international copyright treaties. You expressly agree NOT to:\n"
            "• No Commercial Use: Monetize, exploit, integrate, deploy, or utilize this software (or any part of its outputs, assets, algorithms, binaries, or code) for any commercial, business, enterprise, institutional, or revenue-generating purpose whatsoever.\n"
            "• No Sharing or Redistribution: Distribute, host, upload, seed, share, publish, lease, rent, loan, broadcast, transfer, or make this software accessible to any third party through any network, public repository, file-sharing service, cloud drive, or physical medium.\n"
            "• No Copying or Duplication: Copy, clone, duplicate, mirror, or backup any portion of this software, its underlying scripts, executable files, graphics, components, or source code, except as inherently required by your operating system's RAM during active personal execution.\n"
            "• No Reverse Engineering or Modification: Decompile, disassemble, decrypt, reverse engineer, unpack, modify, adapt, translate, tamper with, or create derivative works based upon this software or any component contained therein.\n\n"
            "3. INTELLECTUAL PROPERTY & OWNERSHIP\n"
            "This software is licensed to you by Muzamil Khan, not sold. All right, title, interest, copyright, trademarks, patents, trade secrets, design architecture, and intellectual property rights in and to this software remain exclusively with Muzamil Khan. No title or ownership of any kind is transferred to you under this Agreement.\n\n"
            "4. DISCLAIMER OF WARRANTY (\"AS IS\")\n"
            "TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW IN INDIA, THIS SOFTWARE IS PROVIDED \"AS IS\" AND \"AS AVAILABLE,\" WITH ALL FAULTS, BUGS, AND DEFECTS, AND WITHOUT WARRANTY OF ANY KIND, EITHER EXPRESS, IMPLIED, OR STATUTORY.\n"
            "MUZAMIL KHAN EXPRESSLY DISCLAIMS ALL WARRANTIES, INCLUDING BUT NOT LIMITED TO:\n"
            "• IMPLIED WARRANTIES OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, ACCURACY, TITLE, AND NON-INFRINGEMENT.\n"
            "• ANY WARRANTY THAT THE SOFTWARE WILL OPERATE UNINTERRUPTED, ERROR-FREE, SECURELY, OR COMPATIBLY WITH ANY SPECIFIC OPERATING SYSTEM, HARDWARE COMPONENT, SYSTEM ARCHITECTURE, OR PERIPHERAL DEVICE.\n"
            "• ANY WARRANTY REGARDING THE PRESERVATION, ACCURACY, INTEGRITY, OR SAFETY OF ANY DATA, FILES, PROCESSES, CALCULATIONS, OR HARDWARE SYSTEM OPERATED OR PROCESSED BY THIS SOFTWARE.\n\n"
            "5. LIMITATION OF LIABILITY\n"
            "TO THE FULLEST EXTENT PERMITTED BY THE LAWS OF INDIA, IN NO EVENT SHALL MUZAMIL KHAN BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, PUNITIVE, OR EXEMPLARY DAMAGES WHATSOEVER (INCLUDING, WITHOUT LIMITATION, DAMAGES FOR LOSS OF DATA, FILE CORRUPTION, SYSTEM INSTABILITY, HARDWARE FAILURE, LOSS OF PROFITS, BUSINESS INTERRUPTION, COMPUTER MALFUNCTION, OR ANY OTHER FINANCIAL OR PROPERTY DAMAGE) ARISING OUT OF OR IN ANY WAY CONNECTED WITH THE USE, INABILITY TO USE, PERFORMANCE, OR MISUSE OF THIS SOFTWARE.\n"
            "YOU ACKNOWLEDGE THAT YOU OPERATE THIS SOFTWARE ENTIRELY AT YOUR OWN RISK AND THAT YOU ARE SOLELY RESPONSIBLE FOR ANY RESULTING SYSTEM BEHAVIOR, FILE CONVERSIONS, OR DATA ALTERATIONS.\n\n"
            "6. GOVERNING LAW & JURISDICTION\n"
            "This Agreement shall be governed by, construed, and enforced in accordance with the laws of India, without regard to conflict of law principles. Any legal suit, action, or proceeding arising out of or related to this Agreement or the software shall be instituted exclusively in the competent courts located in India.\n\n"
            "7. TERMINATION & LEGAL REMEDIES\n"
            "This Agreement is effective automatically upon the first download, installation, or execution of the software. Any attempt to share, copy, commercially exploit, or tamper with this software instantly and automatically revokes your license without notice. Upon termination, you must immediately delete and destroy all cached, installed, or stored instances of the software in your possession. Muzamil Khan reserves all rights to seek civil damages, injunctive relief, and legal remedies under Indian law against any unauthorized distribution or commercial infringement.\n\n"
            "8. CONTACT INFORMATION\n"
            "If you have any questions, permission requests, or licensing inquiries regarding this End-User License Agreement, please contact the Licensor at:\n"
            "Licensor: Muzamil Khan\n"
            "Email: khanofficial108@gmail.com\n"
            "Country: India"
        )

        eula_box = ctk.CTkFrame(sec3_frame, fg_color="#181818", corner_radius=6)
        eula_box.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkLabel(
            eula_box, text=eula_text, font=ctk.CTkFont(size=11),
            text_color="#bbbbbb", anchor="w", justify="left", wraplength=760
        ).pack(anchor="w", padx=12, pady=10)

    # ------------------------------------------------------------------ Settings window
    def open_settings_window(self):
        win = self._st_win
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return

        w, h = 860, 640
        win = ctk.CTkToplevel(self)
        win.title("⚙️ Settings")
        win.attributes("-topmost", True)
        self._st_win = win
        self._st_widgets = {}
        self.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - w) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - h) // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.minsize(700, 440)

        tabs = ctk.CTkTabview(win)
        tabs.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        frames = {}
        for s in SETTINGS_SCHEMA:
            if s["tab"] not in frames:
                tabs.add(s["tab"])
                sf = ctk.CTkScrollableFrame(tabs.tab(s["tab"]), fg_color="transparent")
                sf.pack(fill="both", expand=True)
                frames[s["tab"]] = sf
        for s in SETTINGS_SCHEMA:
            self._st_build_row(frames[s["tab"]], win, s)

        # Add Documentation, Help and Licences Tab
        tabs.add("Documentation, Help & Licences")
        self._build_docs_help_tab(tabs.tab("Documentation, Help & Licences"))

        bottom = ctk.CTkFrame(win, fg_color="transparent")
        bottom.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(bottom, text="↺ Reset All to Defaults", width=170, height=30, fg_color="#c0392b",
                      hover_color="#962d22", font=ctk.CTkFont(size=12, weight="bold"),
                      command=lambda: self._st_confirm_reset_all(win)).pack(side="left")
        ctk.CTkButton(bottom, text="📖 Open Docs Window", width=170, height=30, fg_color="#1f6aa5",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=self.open_docs_help_window).pack(side="left", padx=10)
        ctk.CTkButton(bottom, text="Close", width=90, height=30, command=win.destroy).pack(side="right")
        ctk.CTkLabel(win, text=f"Saved automatically to: {SETTINGS_FILE}", font=ctk.CTkFont(size=10),
                     text_color="#888888", wraplength=780, justify="left").pack(anchor="w", padx=14, pady=(0, 8))

        win.bind("<Escape>", lambda e: win.destroy())
        win.after(150, win.focus_force)

    def _st_confirm_reset_all(self, win):
        if not messagebox.askyesno("Reset all settings", "Reset every setting to its default value?", parent=win):
            return
        self.reset_all_settings()
        for refresh in self._st_widgets.values():
            refresh()

    def _st_text_on(self, hexcol):
        r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
        return "#000000" if (r * 299 + g * 587 + b * 114) / 1000 > 140 else "#ffffff"

    def _st_short_path(self, p):
        if not p:
            return "Default (saved_projects folder)"
        return p if len(p) <= 34 else "…" + p[-33:]

    def _st_build_row(self, parent, win, s):
        key, kind = s["key"], s["kind"]
        if kind == "hidden":
            return
        row = ctk.CTkFrame(parent, fg_color="#222222", corner_radius=6)
        row.pack(fill="x", padx=4, pady=3)
        row.grid_columnconfigure(0, weight=1)

        label_box = ctk.CTkFrame(row, fg_color="transparent")
        label_box.grid(row=0, column=0, sticky="w", padx=10, pady=6)
        ctk.CTkLabel(label_box, text=s["label"], anchor="w", justify="left", wraplength=290,
                     font=ctk.CTkFont(size=12)).pack(anchor="w")
        if s.get("desc"):
            ctk.CTkLabel(label_box, text=s["desc"], anchor="w", justify="left", wraplength=290,
                         font=ctk.CTkFont(size=10), text_color="#9aa0a6").pack(anchor="w", pady=(2, 0))

        ctrl = ctk.CTkFrame(row, fg_color="transparent")
        ctrl.grid(row=0, column=1, padx=4)
        cur = self.app_settings[key]
        refresh = None

        if kind == "toggle":
            var = tk.BooleanVar(value=cur)
            ctk.CTkSwitch(ctrl, text="", variable=var, width=46,
                          command=lambda: self.set_setting(key, var.get())).pack()
            refresh = lambda: var.set(self.app_settings[key])

        elif kind == "slider":
            mn, mx, step, unit = s["min"], s["max"], s.get("step", 1), s.get("unit", "")
            val_lbl = ctk.CTkLabel(ctrl, width=64, text=f"{cur}{unit}", font=ctk.CTkFont(size=11, weight="bold"))
            val_lbl.pack(side="right", padx=(6, 0))

            def on_slide(x):
                self.set_setting(key, x)
                val_lbl.configure(text=f"{self.app_settings[key]}{unit}")

            sl = ctk.CTkSlider(ctrl, from_=mn, to=mx, number_of_steps=max(1, int((mx - mn) / step)),
                               width=170, command=on_slide)
            sl.set(cur)
            sl.pack(side="right")

            def refresh():
                sl.set(self.app_settings[key])
                val_lbl.configure(text=f"{self.app_settings[key]}{unit}")

        elif kind == "dropdown":
            om = ctk.CTkOptionMenu(ctrl, values=s["options"], width=s.get("width", 130),
                                   command=lambda v: self.set_setting(key, v))
            om.set(cur)
            om.pack()
            refresh = lambda: om.set(self.app_settings[key])

        elif kind == "color":
            btn = ctk.CTkButton(ctrl, text=cur, width=100, height=26, fg_color=cur, hover=False,
                                text_color=self._st_text_on(cur), border_width=1, border_color="gray50")
            btn.pack()

            def pick():
                result = colorchooser.askcolor(color=self.app_settings[key], parent=win, title=s["label"])
                if result and result[1]:
                    self.set_setting(key, result[1])
                    refresh()

            def refresh():
                c = self.app_settings[key]
                btn.configure(text=c, fg_color=c, text_color=self._st_text_on(c))

            btn.configure(command=pick)

        elif kind == "folder":
            btn = ctk.CTkButton(ctrl, text=self._st_short_path(cur), width=220, height=26, fg_color="#444444",
                                hover_color="#666666", font=ctk.CTkFont(size=11))
            btn.pack()

            def pick():
                kw = {}
                if self.app_settings[key] and os.path.isdir(self.app_settings[key]):
                    kw["initialdir"] = self.app_settings[key]
                d = filedialog.askdirectory(parent=win, title=s["label"], **kw)
                if d:
                    self.set_setting(key, d)
                    refresh()

            def refresh():
                btn.configure(text=self._st_short_path(self.app_settings[key]))

            btn.configure(command=pick)

        elif kind == "info":
            ctk.CTkLabel(ctrl, text="🔒 " + s.get("info", ""), text_color="#2ecc71",
                         font=ctk.CTkFont(size=11)).pack()

        if refresh is not None:
            self._st_widgets[key] = refresh
            ctk.CTkButton(row, text="↺ Reset", width=64, height=26, fg_color="#444444", hover_color="#666666",
                          font=ctk.CTkFont(size=11),
                          command=lambda: (self.reset_setting(key), refresh())).grid(row=0, column=2, padx=(4, 10))
