"""
More Tools Menu Mixin for Red Enclosure.

Replaces the old plain brown list with a modern card-based panel:
  * Cards grouped into colour-coded categories (2 columns, 1 on narrow screens)
  * Full button names are ALWAYS shown (text wraps onto extra lines, never cut)
  * Live search box
  * Panel size is clamped to the VISIBLE screen area (never goes under the taskbar)
  * Hover glow, big icon badges, scrolling with the mouse wheel

Add MoreToolsMenuMixin BEFORE CanvasUISetupMixin in main.py so these methods
override the old ones.
"""
import customtkinter as ctk

PANEL_BG = "#121212"
CARD_BG = "#1e1e1e"
CARD_HOVER = "#2b2b2b"
CARD_BORDER = "#2e2e2e"
ACCENT = "#e67e22"

# (category title, accent colour, keywords found in the button label - lowercase)
CATEGORIES = [
    ("🧩  Design & Mapping", "#e67e22", ("room stitcher", "pcb", "enhance")),
    ("📱  Phone & Downloads", "#3498db", ("smartphone", "android", "download anything")),
    ("🤖  AI & Vision", "#9b59b6", ("transcription", "paddle", "real eye", "emotions")),
    ("🎬  Media & Fun", "#e74c3c", ("screen recorder", "video editor", "pipe simulation")),
    ("🗂️  Files & Security", "#2ecc71", ("pdf", "export all", "duplicates", "virus", "auto-install")),
]
OTHER = ("🛠️  Other Tools", "#95a5a6", ())


def _categorize(label):
    low = label.lower()
    for title, color, keys in CATEGORIES:
        if any(k in low for k in keys):
            return title, color
    return OTHER[0], OTHER[1]


class MoreToolCard(ctk.CTkFrame):
    """A clickable card (icon badge + full wrapped title).

    Behaves enough like a CTkButton (configure text/fg_color, bind) so other
    modules that keep a reference to it (e.g. recorder_btn) keep working.
    """

    def __init__(self, parent, label, accent, command, category):
        super().__init__(parent, fg_color=CARD_BG, corner_radius=12,
                         border_width=1, border_color=CARD_BORDER)
        self.category = category
        self.accent = accent
        self._command = command
        self._base_fg = CARD_BG
        self.title_text = ""
        self._full_label = label

        self.grid_columnconfigure(1, weight=1)

        self.badge = ctk.CTkFrame(self, width=46, height=46, corner_radius=12, fg_color=accent)
        self.badge.grid(row=0, column=0, padx=(10, 8), pady=9)
        self.badge.pack_propagate(False)
        self.icon_lbl = ctk.CTkLabel(self.badge, text="", font=ctk.CTkFont(size=20),
                                     fg_color="transparent", text_color="white")
        self.icon_lbl.place(relx=0.5, rely=0.5, anchor="center")

        self.title_lbl = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=12, weight="bold"),
                                      anchor="w", justify="left", text_color="#f2f2f2", wraplength=300)
        self.title_lbl.grid(row=0, column=1, sticky="w", padx=(0, 12), pady=9)

        self._hit = [self, self.badge, self.icon_lbl, self.title_lbl]
        self.set_label(label)

        for w in self._hit:
            self._raw_bind(w, "<Enter>", lambda e: self._set_hover(True))
            self._raw_bind(w, "<Leave>", lambda e: self._on_leave())
            self._raw_bind(w, "<Button-1>", lambda e: self._click())
            try:
                w.configure(cursor="hand2")
            except Exception:
                pass

    # ---- helpers
    @staticmethod
    def _raw_bind(widget, seq, fn):
        if isinstance(widget, MoreToolCard):
            ctk.CTkFrame.bind(widget, seq, fn, True)
        else:
            widget.bind(seq, fn, True)

    def _click(self):
        if callable(self._command):
            self._command()

    def _set_hover(self, on):
        ctk.CTkFrame.configure(self, fg_color=CARD_HOVER if on else self._base_fg,
                               border_color=self.accent if on else CARD_BORDER)

    def _on_leave(self):
        try:
            x, y = self.winfo_pointerxy()
            w = self.winfo_containing(x, y)
        except Exception:
            w = None
        while w is not None:
            if w is self:
                return  # pointer is still inside the card (moved onto a child)
            w = getattr(w, "master", None)
        self._set_hover(False)

    # ---- public API
    def set_label(self, label):
        self._full_label = label
        parts = label.split(" ", 1)
        if len(parts) == 2 and not parts[0].isascii():
            icon, title = parts[0], parts[1].strip()
        else:
            icon, title = "•", label
        self.title_text = title
        self.icon_lbl.configure(text=icon)
        self.title_lbl.configure(text=title)

    def set_wrap(self, px):
        self.title_lbl.configure(wraplength=px)

    # ---- CTkButton compatibility shims
    def configure(self, **kw):
        if "text" in kw:
            self.set_label(kw.pop("text"))
        if "command" in kw:
            self._command = kw.pop("command")
        for k in ("hover_color", "text_color", "font", "state", "anchor", "hover"):
            kw.pop(k, None)
        if "fg_color" in kw:
            self._base_fg = kw["fg_color"]
        if kw:
            super().configure(**kw)

    def cget(self, name):
        if name == "text":
            return self._full_label
        return super().cget(name)

    def bind(self, sequence=None, command=None, add=True):
        for w in self._hit:
            self._raw_bind(w, sequence, command)


class MoreToolsMenuMixin:
    MORE_PANEL_W = 880
    MORE_PANEL_MAX_H = 720
    MORE_TOP = 56
    TASKBAR_MARGIN = 64   # keeps the panel above the Windows taskbar

    def _build_more_tools_panel(self):
        items = self._more_tools_items()

        panel = ctk.CTkFrame(self, fg_color=PANEL_BG, border_width=2,
                             border_color=ACCENT, corner_radius=16)
        panel.pack_propagate(False)

        # ---- header
        head = ctk.CTkFrame(panel, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 2))
        ctk.CTkLabel(head, text="🛠️  More Tools", font=ctk.CTkFont(size=20, weight="bold"),
                     text_color="#f5b041").pack(side="left")
        ctk.CTkLabel(head, text=f"   {len(items)} tools", font=ctk.CTkFont(size=12),
                     text_color="#7f8c8d").pack(side="left", pady=(6, 0))
        ctk.CTkButton(head, text="✖", width=34, height=28, corner_radius=8,
                      fg_color="#c0392b", hover_color="#e74c3c", text_color="white",
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=self.close_more_tools_menu).pack(side="right")

        # ---- search
        self._more_search_var = ctk.StringVar()
        self._more_search = ctk.CTkEntry(panel, textvariable=self._more_search_var, height=34,
                                         corner_radius=10, fg_color="#1c1c1c", border_color="#3a3a3a",
                                         placeholder_text="🔍  Search tools...")
        self._more_search.pack(fill="x", padx=16, pady=(8, 6))

        ctk.CTkFrame(panel, height=2, fg_color=ACCENT, corner_radius=1).pack(fill="x", padx=16, pady=(0, 4))

        # ---- footer (packed before the body so it always stays visible)
        ctk.CTkLabel(panel, text="Right-click 🎥 Screen Recorder for its settings   •   Esc to close",
                     font=ctk.CTkFont(size=10), text_color="#7f8c8d").pack(side="bottom", pady=(0, 8))

        body = ctk.CTkScrollableFrame(panel, fg_color="transparent",
                                      scrollbar_button_color="#3a3a3a",
                                      scrollbar_button_hover_color=ACCENT)
        body.pack(fill="both", expand=True, padx=(8, 4), pady=(0, 4))
        self._more_body = body

        def run_action(cmd_fn):
            self.close_more_tools_menu()
            if callable(cmd_fn):
                self.after(30, cmd_fn)

        rec_settings = getattr(self, "open_recorder_settings", None)
        self._more_cards = []
        self._more_headers = {}
        used = []
        for label, cmd in items:
            cat, color = _categorize(label)
            card = MoreToolCard(body, label, color, (lambda c=cmd: run_action(c)), cat)
            self._more_cards.append(card)
            if cat not in used:
                used.append(cat)
                self._more_headers[cat] = ctk.CTkLabel(
                    body, text=cat, anchor="w", font=ctk.CTkFont(size=14, weight="bold"), text_color=color)
            if label == "🎥 Screen Recorder":
                self.recorder_btn = card
                if rec_settings:
                    for _ev in ("<Button-3>", "<Button-2>"):
                        card.bind(_ev, lambda e, f=rec_settings: (self.close_more_tools_menu(), f(e)))

        self._more_empty = ctk.CTkLabel(body, text="😕  No tool matches your search",
                                        font=ctk.CTkFont(size=13), text_color="#7f8c8d")
        self._more_cols = 2
        self._more_wrap = 260
        self._more_search_var.trace_add("write", lambda *a: self._layout_more_tools())

        self._more_panel = panel
        self.bind_all("<Button-1>", self._more_tools_click_away, add="+")
        self.bind_all("<Escape>", lambda e: self.close_more_tools_menu(), add="+")

    def _layout_more_tools(self):
        body = getattr(self, "_more_body", None)
        if body is None:
            return
        cols = self._more_cols
        q = self._more_search_var.get().strip().lower()

        for w in list(self._more_cards) + list(self._more_headers.values()) + [self._more_empty]:
            w.grid_forget()
        for c in range(2):
            body.grid_columnconfigure(c, weight=1 if c < cols else 0, uniform="mtcol" if c < cols else "")

        order = [c[0] for c in CATEGORIES] + [OTHER[0]]
        row = 0
        shown = 0
        for cat in order:
            cards = [c for c in self._more_cards if c.category == cat and q in c.title_text.lower()]
            if not cards:
                continue
            self._more_headers[cat].grid(row=row, column=0, columnspan=cols, sticky="ew", padx=8, pady=(10, 2))
            row += 1
            for i, card in enumerate(cards):
                card.set_wrap(self._more_wrap)
                card.grid(row=row + i // cols, column=i % cols, sticky="nsew", padx=5, pady=4)
            row += (len(cards) + cols - 1) // cols
            shown += len(cards)
        if not shown:
            self._more_empty.grid(row=0, column=0, columnspan=cols, pady=40)
        try:
            body._parent_canvas.yview_moveto(0)
        except Exception:
            pass

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
        scr_w, scr_h = self.winfo_screenwidth(), self.winfo_screenheight()

        # Visible area of the window (the window may be taller than the screen)
        vis_bottom = min(win_h, scr_h - self.winfo_rooty() - self.TASKBAR_MARGIN) - 10
        vis_right = min(win_w, scr_w - self.winfo_rootx()) - 10
        top = self.MORE_TOP

        w = min(self.MORE_PANEL_W, max(340, vis_right - 20))
        h = max(260, min(self.MORE_PANEL_MAX_H, vis_bottom - top))

        self._more_cols = 2 if w >= 700 else 1
        col_w = (w - 70) / self._more_cols
        self._more_wrap = max(120, int(col_w - 46 - 8 - 10 - 12 - 24))

        self._more_search_var.set("")
        panel.configure(width=w, height=h)
        self._layout_more_tools()

        x = max(10, (win_w - w) // 2)
        y = top + max(0, (vis_bottom - top - h) // 2)
        panel.place(x=x, y=y)
        panel.lift()
        panel.after(60, self._more_search.focus_set)
