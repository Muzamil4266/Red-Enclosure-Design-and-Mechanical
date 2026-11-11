"""
UI Layout & Canvas Setup Mixin for Red Enclosure.
Configures the workspace canvas and a 2-row horizontally scrollable top toolbar
with full two-finger touchpad and mouse wheel gesture scrolling.
Includes Insert Table tool on toolbar row 1 (next to Spring) and Insert Picture tool on toolbar row 2.
Includes a dedicated 'More Tools' popup menu for extended hardware, 2D room stitcher, export tools,
AI OCR, PDF locker, Dependency Auto-Installer, Live Emotions Detector, Auto Video Editor, APK Virus Checker,
Duplicates Images/PDFs finder and Cleaner, Any Android phone age check and technical details,
English and Hindi offline AI Transcription, aria2 Fast Downloader, and Real Eye.
Includes a '⚙️ Settings' button on toolbar row 2 positioned upfront for instant access.
Default canvas background set to Absolute Dark Mode (#000000).
"""
import tkinter as tk
import customtkinter as ctk
from tkinter import colorchooser

SWATCHES = ["#e74c3c", "#f1c40f", "#2ecc71", "#3498db", "#000000", "#ffffff"]
DEFAULT_BG = "#000000"
PURE_BLACK_BG = "#000000"
SCROLL_TOP, SCROLL_BOTTOM = -2000, 2800
SCROLL_Y_DIR = -1
BTN_BLUE = "#1f6aa5"


class CanvasUISetupMixin:

    def _btn(self, parent, text, width, command, color=BTN_BLUE, side="left", padx=2):
        b = ctk.CTkButton(parent, text=text, width=width, height=26, command=command, fg_color=color, font=ctk.CTkFont(size=11, weight="bold"))
        b.pack(side=side, padx=padx, pady=2)
        if hasattr(self, "_themed_buttons"):
            self._themed_buttons.append(b)
        return b

    def set_absolute_dark_mode(self):
        """Sets the workspace canvas and container background to absolute pure black (#000000)."""
        self.current_bg_type = "solid"
        if hasattr(self, "canvas") and self.canvas:
            self.canvas.configure(bg=PURE_BLACK_BG)
            self.canvas.delete("bg_grid")
        if hasattr(self, "canvas_container") and self.canvas_container:
            self.canvas_container.configure(fg_color=PURE_BLACK_BG)
        if hasattr(self, "toast"):
            self.toast("🖤 Absolute Dark Mode Applied")

    # ------------------------------------------------------------------ MORE TOOLS (in-window panel)
    MORE_PANEL_W = 650
    MORE_BROWN = "#8B4513"
    MORE_BROWN_HOVER = "#6B3410"

    def _more_tools_items(self):
        return [
            ("🖼️ 2D Room Stitcher", getattr(self, "open_room_stitcher_dialog", lambda: None)),
            ("🧩 PCB Mapper", getattr(self, "open_pcb_mapper", lambda: None)),
            ("✨ Enhance Pcb mapped image", getattr(self, "open_pcb_enhance", lambda: None)),
            ("📱 Mirror smartphone in pc", getattr(self, "open_mirroring", lambda: None)),
            ("📱 Any Android phone age check and technical details", getattr(self, "open_phone_diagnostics", lambda: None)),
            ("🎙️ English and Hindi offline Ai Transcription fast and accurate tested and Working", getattr(self, "open_audio_transcriber", lambda: None)),
            ("⚡ Download anything very fast using cmd method without manually typing anything in cmd", getattr(self, "open_aria2_downloader", lambda: None)),
            ("👁️ REAL EYE", getattr(self, "open_real_eye", lambda: None)),
            ("🎥 Screen Recorder", getattr(self, "toggle_screen_recording", lambda: None)),
            ("🚿 Pipe Simulation Fun", getattr(self, "toggle_pipe_simulation", lambda: None)),
            ("📄 Save Project as PDF", getattr(self, "export_to_pdf", lambda: None)),
            ("⬇ Export all components photos locally", getattr(self, "export_component_photos", lambda: None)),
            ("🤖 Ai offline paddle OCR and Images to PDF with scanning", getattr(self, "open_paddle_ocr", lambda: None)),
            ("🔒 Python pdf locker", getattr(self, "open_pdf_locker", lambda: None)),
            ("📦 Scan & Auto-Install Missing Packages", getattr(self, "open_dependency_installer", lambda: None)),
            ("😊 Live Emotions Detector", getattr(self, "open_emotion_detector", lambda: None)),
            ("🎬 Auto Video Editor", getattr(self, "launch_auto_video_editor", lambda: None)),
            ("🛡️ APK Virus Checker", getattr(self, "open_apk_virus_checker", lambda: None)),
            ("🔍 Duplicates Images, PDFs finder and Cleaner", self.open_duplicates_chooser),
        ]

    def open_duplicates_chooser(self):
        """Small window to choose between Images duplicates or PDFs duplicates tools."""
        win = getattr(self, "_dup_chooser_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return

        w, h = 440, 250
        win = ctk.CTkToplevel(self)
        win.title("Duplicates Images, PDFs finder and Cleaner")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        self._dup_chooser_win = win
        self.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - w) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - h) // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")

        ctk.CTkLabel(win, text="What do you want to scan for duplicates?",
                     font=ctk.CTkFont(size=14, weight="bold"), text_color="#e0a070").pack(pady=(22, 12))

        def launch(method_name):
            win.destroy()
            fn = getattr(self, method_name, None)
            if callable(fn):
                fn()

        for text, method_name in (
            ("🖼️ Images Duplicates finder and Cleaner", "open_visual_duplicate_cleaner"),
            ("📄 PDFs Duplicates finder and Cleaner", "open_pdf_duplicate_cleaner"),
        ):
            ctk.CTkButton(
                win, text=text, width=360, height=46,
                fg_color=self.MORE_BROWN, hover_color=self.MORE_BROWN_HOVER, text_color="white",
                font=ctk.CTkFont(size=13, weight="bold"),
                command=lambda m=method_name: launch(m)
            ).pack(pady=7)

        win.bind("<Escape>", lambda e: win.destroy())
        win.after(150, win.focus_force)

    def _build_more_tools_panel(self):
        items = self._more_tools_items()
        panel = ctk.CTkFrame(self, fg_color="#1b1b1b", border_width=2, border_color=self.MORE_BROWN, corner_radius=10)
        panel.pack_propagate(False)

        head = ctk.CTkFrame(panel, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(head, text="🛠️ More Tools Menu", font=ctk.CTkFont(size=14, weight="bold"), text_color="#e0a070").pack(side="left")
        ctk.CTkButton(head, text="✖", width=32, height=26, fg_color=self.MORE_BROWN, hover_color=self.MORE_BROWN_HOVER,
                      text_color="white", command=self.close_more_tools_menu).pack(side="right")

        body = ctk.CTkScrollableFrame(panel, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=4, pady=(0, 6))

        def run_action(cmd_fn):
            self.close_more_tools_menu()
            if callable(cmd_fn):
                cmd_fn()

        rec_settings = getattr(self, "open_recorder_settings", None)
        for label, cmd in items:
            btn = ctk.CTkButton(
                body, text=label, height=30,
                fg_color=self.MORE_BROWN, hover_color=self.MORE_BROWN_HOVER, text_color="white",
                font=ctk.CTkFont(size=12, weight="bold"), anchor="w",
                command=lambda c=cmd: run_action(c)
            )
            btn.pack(fill="x", expand=True, pady=2, padx=4)
            if label == "🎥 Screen Recorder":
                self.recorder_btn = btn
                if rec_settings:
                    for _ev in ("<Button-3>", "<Button-2>"):
                        btn.bind(_ev, lambda e, f=rec_settings: (self.close_more_tools_menu(), f(e)))

        self._more_needed_h = 50 + len(items) * 34 + 15
        self._more_panel = panel
        self.bind_all("<Button-1>", self._more_tools_click_away, add="+")
        self.bind_all("<Escape>", lambda e: self.close_more_tools_menu(), add="+")

    def _more_tools_click_away(self, event):
        panel = getattr(self, "_more_panel", None)
        if panel is None or not panel.winfo_ismapped():
            return
        w = event.widget
        while w is not None and not isinstance(w, str):
            if w is panel or w is getattr(self, "more_tools_btn", None):
                return
            w = getattr(w, "master", None)
        self.close_more_tools_menu()

    def close_more_tools_menu(self):
        panel = getattr(self, "_more_panel", None)
        if panel is not None:
            panel.place_forget()

    def open_more_tools_menu(self):
        panel = getattr(self, "_more_panel", None)
        if panel is not None and panel.winfo_ismapped():
            self.close_more_tools_menu()
            return
        if panel is None:
            self._build_more_tools_panel()
            panel = self._more_panel

        self.update_idletasks()
        win_w, win_h = self.winfo_width(), self.winfo_height()

        w = min(self.MORE_PANEL_W, max(320, win_w - 60))
        max_allowed_h = max(250, win_h - 140)
        h = min(self._more_needed_h, max_allowed_h)

        x = max(20, (win_w - w) // 2)
        y = max(60, (win_h - h) // 2)

        panel.configure(width=w, height=h)
        panel.place(x=x, y=y)
        panel.lift()

    def init_canvas_and_toolbar(self):
        self.crop_corner = None
        self.editing_text = None
        self.text_pre = None
        self.press_x = 0
        self.press_y = 0
        self.temp_box_id = None
        self.temp_circle_id = None
        self.temp_star_id = None
        self.temp_spring_id = None
        self.stroke_w = {}
        self.aa_ids = {}
        self.aa_tk = {}
        self.live = set()
        self.eraser_size = 15
        self.cable_flags = {}
        self._aa_job = None
        self._zoom_job = None
        self._hist_job = None
        self._hist_down = False
        self._hist_kind = "undo"
        self._skip_aa = False
        self.spring_init = None
        self.rot_center = (0, 0)
        self.box_op = None
        self.box_ang = 0.0
        self.current_bg_type = "solid"
        self._stroke_start = None
        self._dot_item = None
        self._dot_job = None
        self._hover_item = None
        self.current_project = None
        self._proj_win = None

        self.canvas_container = ctk.CTkFrame(self, fg_color="transparent")
        self.canvas_container.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.canvas_container.grid_rowconfigure(1, weight=1)
        self.canvas_container.grid_columnconfigure(0, weight=1)

        # ------------------------------------------------------------------ TOP TOOLBAR
        self.top_scroll_toolbar = ctk.CTkScrollableFrame(
            self.canvas_container,
            height=72,
            orientation="horizontal",
            fg_color="#181818",
            corner_radius=6,
            scrollbar_button_color="#333333",
            scrollbar_button_hover_color="#555555"
        )
        self.top_scroll_toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        toolbar = ctk.CTkFrame(self.top_scroll_toolbar, fg_color="transparent")
        toolbar.pack(side="left", fill="both", expand=True)

        row1 = ctk.CTkFrame(toolbar, fg_color="transparent")
        row1.pack(fill="x", side="top", anchor="w")

        row2 = ctk.CTkFrame(toolbar, fg_color="transparent")
        row2.pack(fill="x", side="top", anchor="w", pady=(2, 0))

        # --- ROW 1: DRAWING TOOLS & PALETTE ---
        for mode, label, w in [
            ("select", "👆 Select", 70), ("draw", "✏️ Pen", 60), ("straight_line", "📏 Line", 60),
            ("cable", "🔌 Wire", 60), ("box", "🔲 Box", 50), ("circle", "🔵 Circle", 60),
            ("star", "⭐ Star", 50), ("spring", "🌀 Spring", 65), ("eraser", "🧹 Eraser", 70)
        ]:
            self.toolbar_buttons[mode] = self._btn(row1, label, w, lambda m=mode: self.set_mode(m))
            if mode == "spring":
                # Insert Table sits right next to Spring (an action button: it opens the Word-style grid picker)
                self.insert_table_btn = self._btn(
                    row1, "📊 Insert Table", 115,
                    lambda: getattr(self, "open_table_picker", lambda: None)(),
                    color=BTN_BLUE
                )

        for _ev in ("<Button-3>", "<Button-2>"):
            self.toolbar_buttons["eraser"].bind(_ev, getattr(self, "show_eraser_options", None))

        ctk.CTkLabel(row1, text="Drawings size:", font=ctk.CTkFont(size=11)).pack(side="left", padx=(6, 2))
        self.size_slider = ctk.CTkSlider(row1, from_=1, to=20, number_of_steps=19, width=60, command=self.update_tool_size)
        self.size_slider.set(self.tool_size)
        self.size_slider.pack(side="left", padx=2)

        self.zoom_label = ctk.CTkLabel(row1, text="Zoom: 50%", width=75, font=ctk.CTkFont(size=11, weight="bold"))
        self.zoom_label.pack(side="left", padx=(6, 0))
        ctk.CTkLabel(row1, text="0%", font=ctk.CTkFont(size=10)).pack(side="left", padx=(2, 0))
        zoom_box = ctk.CTkFrame(row1, fg_color="transparent")
        zoom_box.pack(side="left", padx=2)
        self.univ_zoom_slider = ctk.CTkSlider(zoom_box, from_=0, to=100, number_of_steps=10, width=90, command=self._on_zoom_slider)
        self.univ_zoom_slider.set(50.0)
        self.univ_zoom_slider.pack(side="top")
        ctk.CTkLabel(zoom_box, text="▲ 50% default", font=ctk.CTkFont(size=8), text_color="#f1c40f", height=10).pack(side="top")
        ctk.CTkLabel(row1, text="100%", font=ctk.CTkFont(size=10)).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(row1, text="Drawings color:", font=ctk.CTkFont(size=11)).pack(side="left", padx=(6, 2))
        for c in SWATCHES:
            ctk.CTkButton(row1, text="", width=20, height=20, fg_color=c, hover=False, border_width=1,
                          border_color="gray50", command=lambda c=c: self.set_pen_color(c)).pack(side="left", padx=2)
        self.color_btn = self._btn(row1, "🎨", 40, self.change_pen_color, self.pen_color, padx=4)

        # --- ROW 2: MAIN ACTIONS TOOLBAR & MORE TOOLS DROPDOWN ---
        undo_btn = ctk.CTkButton(row2, text="↩️ Undo", width=60, height=26, fg_color=BTN_BLUE, font=ctk.CTkFont(size=11))
        undo_btn.pack(side="left", padx=2, pady=2)
        undo_btn.bind("<ButtonPress-1>", lambda e: self.start_history("undo"))

        redo_btn = ctk.CTkButton(row2, text="🔁 Redo", width=60, height=26, fg_color=BTN_BLUE, font=ctk.CTkFont(size=11))
        redo_btn.pack(side="left", padx=2, pady=2)
        redo_btn.bind("<ButtonPress-1>", lambda e: self.start_history("redo"))
        self.bind_all("<ButtonRelease-1>", self.stop_history, add="+")

        self.undo_btn, self.redo_btn = undo_btn, redo_btn
        if hasattr(self, "_themed_buttons"):
            self._themed_buttons.extend([undo_btn, redo_btn])

        self.settings_btn = self._btn(row2, "⚙️ Settings", 105, getattr(self, "open_settings_window", lambda: None), color=BTN_BLUE)
        self.insert_picture_btn = self._btn(
            row2, "🖼️ Insert Picture", 130,
            lambda: getattr(self, "open_insert_picture", lambda: None)(),
            color=BTN_BLUE
        )

        self._btn(row2, "🧹 Clear Workspace", 150, getattr(self, "clear_workspace", lambda: None), color=BTN_BLUE)
        self._btn(row2, "📁 Saved Projects in Folder", 175, getattr(self, "open_projects_window", lambda: None), color=BTN_BLUE)
        self._btn(row2, "🆕 Start new project", 140, getattr(self, "new_project", lambda: None), color=BTN_BLUE)
        self._btn(row2, "🖼️ Change background color", 180, getattr(self, "change_background_color", lambda: None), color=BTN_BLUE)

        self.more_tools_btn = self._btn(row2, "🛠️ More Tools", 115, self.open_more_tools_menu, color="#8e44ad")

        self._btn(row2, "🎯 Move project to centre", 165, getattr(self, "center_all_components", lambda: None), color=BTN_BLUE)
        self.sidebar_toggle_btn = self._btn(row2, "🔽 Hide Components", 140, getattr(self, "toggle_sidebar", lambda: None), color=BTN_BLUE)

        self.component_counter_label = ctk.CTkLabel(
            row2, text="Components: 0", font=ctk.CTkFont(size=12, weight="bold"), text_color="#f1c40f"
        )
        self.component_counter_label.pack(side="left", padx=8, pady=2)

        self.highlight_toolbar_button("select")

        # WORKSPACE CANVAS
        canvas_frame = ctk.CTkFrame(self.canvas_container, fg_color="transparent")
        canvas_frame.grid(row=1, column=0, sticky="nsew")
        canvas_frame.grid_rowconfigure(0, weight=1)
        canvas_frame.grid_columnconfigure(0, weight=1)

        self.canvas = ctk.CTkCanvas(canvas_frame, bg=DEFAULT_BG, highlightthickness=0, insertbackground="red")
        self.canvas.configure(scrollregion=(0, SCROLL_TOP, 11520, SCROLL_BOTTOM))

        self.h_scrollbar = ctk.CTkScrollbar(canvas_frame, orientation="horizontal", command=self.canvas.xview)
        self.v_scrollbar = ctk.CTkScrollbar(canvas_frame, orientation="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=self.h_scrollbar.set, yscrollcommand=self.v_scrollbar.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.h_scrollbar.grid(row=1, column=0, sticky="ew")
        self.v_scrollbar.grid(row=0, column=1, sticky="ns")
        self.v_scrollbar.grid_remove()

        self._bind_toolbar_scroll_recursive(self.top_scroll_toolbar)

        self.canvas.bind("<MouseWheel>", self.scroll_view_y)
        self.canvas.bind("<Shift-MouseWheel>", lambda e: self.scroll_view(e, self.canvas.xview))
        self.canvas.bind("<Control-MouseWheel>", getattr(self, "on_two_finger_component_zoom", None))

        self.canvas.bind("<Button-1>", getattr(self, "on_canvas_press", None))
        self.canvas.bind("<Double-Button-1>", getattr(self, "on_canvas_double_click", None))
        self.canvas.bind("<B1-Motion>", getattr(self, "on_canvas_drag", None))
        self.canvas.bind("<ButtonRelease-1>", getattr(self, "on_canvas_release", None))
        self.canvas.bind("<Key>", getattr(self, "on_text_key", None))
        self.canvas.bind("<Motion>", getattr(self, "on_canvas_hover", None))
        self.canvas.bind("<Leave>", getattr(self, "clear_hover", None))

        self.current_zoom_scale = 1.0
        if hasattr(self, "init_insert_picture"):
            self.init_insert_picture()
        if hasattr(self, "init_table_tool"):
            self.init_table_tool()
        if hasattr(self, "update_workspace_counter"):
            self.update_workspace_counter()
        self.after(300, self.center_view)

    def _bind_toolbar_scroll_recursive(self, widget):
        for ev in ("<MouseWheel>", "<Shift-MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(ev, self._on_toolbar_touchpad_scroll, add="+")
        for child in widget.winfo_children():
            self._bind_toolbar_scroll_recursive(child)

    def _on_toolbar_touchpad_scroll(self, event):
        delta = 0
        if hasattr(event, "delta") and event.delta != 0:
            delta = event.delta
        elif getattr(event, "num", None) == 4:
            delta = 120
        elif getattr(event, "num", None) == 5:
            delta = -120

        if delta != 0:
            step = int(-1 * (delta / 3))
            self.top_scroll_toolbar._parent_canvas.xview_scroll(step, "units")
        return "break"

    def center_view(self):
        if hasattr(self, "_st_center_view") and getattr(self, "app_settings", None):
            self._st_center_view()
            return
        self.update_idletasks()
        c = self.canvas
        w = c.winfo_width() if c.winfo_width() > 1 else 1480
        c.xview_moveto(max(0.0, (5760 - w / 2) / 11520))
        c.yview_moveto(-SCROLL_TOP / (SCROLL_BOTTOM - SCROLL_TOP))

    def _on_zoom_slider(self, value):
        self.zoom_label.configure(text=f"Zoom: {int(float(value))}%")
        if hasattr(self, "on_universal_slider_zoom"):
            self.on_universal_slider_zoom(value)

    def scroll_view_y(self, e):
        self.canvas.yview_moveto(self.canvas.yview()[0] + SCROLL_Y_DIR * e.delta / 120 * 40 / (SCROLL_BOTTOM - SCROLL_TOP))

    def scroll_view(self, e, view):
        view("moveto", max(0, view()[0] - e.delta / 120 * 40 / 11520))
