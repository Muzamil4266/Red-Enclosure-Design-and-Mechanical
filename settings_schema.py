"""
settings_schema.py  (part 1 of 3 of Settings Mixin)

Schema definitions, default configurations, color palettes, button options,
and data validation coercion functions for Red Enclosure settings.
"""
import os
import re
from canvas_ui_setup import BTN_BLUE

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

CANVAS_W = 11520   # full canvas width (and height in Mode 3)
CENTER_X = 5760    # horizontal centre of the canvas

SCREEN_MODES = [
    "Mode 1 - Fit laptop screen (no scrolling)",
    "Mode 2 - Horizontal scroll only",
    "Mode 3 - Full scroll (horizontal + vertical)",
]
THEME_OPTIONS = ["Dark", "Light", "System", "Blue", "Violet", "Purple", "Brown", "Red", "Grey"]

# Bright & Vibrant color themes: (window background, toolbar background)
THEME_PALETTES = {
    "Blue": ("#1e3a8a", "#2563eb"),      # Bright Royal Blue
    "Violet": ("#4c1d95", "#7c3aed"),    # Vivid Electric Violet
    "Purple": ("#6b21a8", "#a855f7"),    # Bright Neon Purple
    "Brown": ("#78350f", "#d97706"),     # Vibrant Amber / Brown
    "Red": ("#991b1b", "#ef4444"),       # Bright Vivid Red
    "Grey": ("#374151", "#6b7280"),      # Bright Slate Grey
}
DEFAULT_TOOLBAR_BG = "#181818"

# toolbar button colours: (normal, hover)
BTN_COLORS = {
    "Purple": ("#8e44ad", "#6c3483"),
    "Red": ("#c0392b", "#962d22"),
    "Blue": (BTN_BLUE, "#144870"),
    "Green": ("#27ae60", "#1e8449"),
    "Gray": ("#566573", "#3d4852"),
    "Brown": ("#8B4513", "#6B3410"),
}

# screen border colours (the border drawn around the workspace when "Screen border" is on)
BORDER_COLORS = {
    "Green": "#2ecc71",
    "Violet": "#8a2be2",
    "Brown": "#8B4513",
    "Red": "#e74c3c",
    "Blue": "#3498db",
}

AUTOSAVE_GAP_SECONDS = 600   # backups made by auto-save are at least this far apart
MANUAL_SAVE_GAP_SECONDS = 60  # backups made by the Save button are at least this far apart

UI_SCALE = {"Small": 0.9, "Normal": 1.0, "Large": 1.15}
LOW_MEMORY_UNDO_CAP = 30


def S(key, tab, label, kind, default, **kw):
    """One settings row. kind: toggle | slider | dropdown | color | folder | info | hidden.
    Optional: desc (small grey text under the label), width (dropdown width), unit, step, min, max, options."""
    return dict(key=key, tab=tab, label=label, kind=kind, default=default, **kw)


SETTINGS_SCHEMA = [
    # ---- Appearance
    S("screen_mode", "Appearance", "Screen mode", "dropdown", SCREEN_MODES[1], options=SCREEN_MODES, width=290,
      desc="Mode 1 fits the laptop screen with no scrolling. Mode 2 scrolls sideways only (the mouse wheel moves "
           "left/right). Mode 3 scrolls sideways and up/down and shows a vertical scrollbar that starts at the top. "
           "The horizontal scrollbar starts centered."),
    S("theme", "Appearance", "Theme", "dropdown", "Dark", options=THEME_OPTIONS,
      desc="Dark, Light and System change the whole app. Blue, Violet, Purple, Brown and Red keep dark mode "
           "and tint the window and toolbar."),
    S("toolbar_btn_color", "Appearance", "Toolbar button color", "dropdown", "Blue", options=list(BTN_COLORS),
      desc="Color of the toolbar buttons (the ones that are blue by default), Undo and Redo included."),
    S("canvas_bg", "Appearance", "Canvas background color", "color", "#000000"),
    S("screen_border", "Appearance", "Screen border", "toggle", False,
      desc="Draws a visible border around the workspace screen in all 3 screen modes."),
    S("border_color", "Appearance", "Screen border color", "dropdown", "Green", options=list(BORDER_COLORS),
      desc="Color of the screen border (used when Screen border is on)."),
    S("border_width", "Appearance", "Screen border thickness", "slider", 3, min=1, max=12, unit=" px",
      desc="Make the screen border thinner or thicker."),
    S("ui_size", "Appearance", "Interface size", "dropdown", "Normal", options=["Small", "Normal", "Large"]),
    S("toast_enabled", "Appearance", "Show toast messages", "toggle", True,
      desc="A toast message is the small pop-up note that appears briefly on screen to confirm something "
           "(for example \"Absolute Dark Mode Applied\") and then disappears by itself. Turn this off if you "
           "do not want these notes."),
    # ---- Drawing
    S("pen_color", "Drawing", "Default pen color", "color", "#e74c3c",
      desc="The toolbar changes the pen while you work. This is the value used when the app starts."),
    S("pen_size", "Drawing", "Default pen size", "slider", 3, min=1, max=20),
    S("eraser_size", "Drawing", "Default eraser size", "slider", 15, min=1, max=60),
    S("default_zoom", "Drawing", "Zoom (applied now and at startup)", "slider", 50, min=0, max=100, step=10, unit="%"),
    S("scroll_speed", "Drawing", "Mouse wheel / touchpad scroll speed", "slider", 100, min=25, max=300, step=25, unit="%"),
    S("invert_scroll", "Drawing", "Invert scroll direction", "toggle", False),
    # ---- Projects
    S("projects_folder", "Projects", "Projects folder", "folder", "",
      desc="Where projects are saved. Empty = the saved_projects folder next to the program. Projects already "
           "saved elsewhere stay where they are (copy them into the new folder to see them in the list)."),
    S("autosave_enabled", "Projects", "Auto-save the current project", "toggle", True,
      desc="The app saves your work into the open project (or a new \"Enclosure 001\" draft) after changes. "
           "Turn off to save only with the Save button; unsaved changes are then lost when you close."),
    S("autosave_minutes", "Projects", "Auto-save delay", "slider", 0, min=0, max=30, unit=" min",
      desc="0 = save right after each change (original behavior). Otherwise save once every N minutes of work."),
    S("backup_copies", "Projects", "Backup copies kept per project", "slider", 3, min=0, max=10,
      desc="Older versions of the project file are kept in a \"backups\" folder inside the project "
           "(at most one every 10 minutes while auto-saving). 0 = no backups."),
    S("remember_last_project", "Projects", "Open the last project on startup", "toggle", False),
    S("last_project", "Projects", "", "hidden", ""),
    # ---- Performance
    S("undo_limit", "Performance", "Undo steps kept", "slider", 100, min=10, max=500, step=10),
    S("low_memory", "Performance", "Low memory mode (undo capped at 30, frees RAM regularly)", "toggle", False),
    # ---- Safety
    S("confirm_new_project", "Safety", "Ask before starting a new project", "toggle", False),
    S("confirm_exit", "Safety", "Ask before closing the app", "toggle", False),
]


def coerce_setting(schema_dict, key, v):
    if key not in schema_dict:
        return v
    s = schema_dict[key]
    d, k = s["default"], s["kind"]
    try:
        if k == "toggle":
            return v if isinstance(v, bool) else d
        if k == "slider":
            step, mn, mx = s.get("step", 1), s["min"], s["max"]
            v = int(round((float(v) - mn) / step)) * step + mn
            return max(mn, min(mx, v))
        if k == "dropdown":
            return v if v in s["options"] else d
        if k == "color":
            return v.lower() if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v) else d
        if k == "folder":
            return os.path.normpath(v) if isinstance(v, str) and v.strip() else ""
        if k == "hidden":
            return v if isinstance(v, str) else ""
    except (TypeError, ValueError):
        pass
    return d
