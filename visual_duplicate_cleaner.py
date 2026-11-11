"""
Visual Duplicates Image Detector and Cleaner Mixin for Shoby Red Project.
(part 3 of 3 - main application integration module)
"""
import customtkinter as ctk
from visual_duplicate_ui import VisualDuplicateCleanerUI


class VisualDuplicateCleanerMixin:
    """Mixin class for integrating Duplicate Image Cleaner into CircuitBuilderApp."""

    def init_duplicate_cleaner(self):
        self._duplicate_cleaner_win = None

    def open_visual_duplicate_cleaner(self):
        """Launches the Visual Duplicates Image Detector window."""
        if hasattr(self, "_duplicate_cleaner_win") and self._duplicate_cleaner_win and self._duplicate_cleaner_win.winfo_exists():
            self._duplicate_cleaner_win.lift()
            self._duplicate_cleaner_win.focus_force()
            return

        win = ctk.CTkToplevel(self)
        win.title("Visual Duplicates Image Detector and Cleaner")
        win.geometry("1280x820")
        win.attributes("-topmost", True)
        self._duplicate_cleaner_win = win

        VisualDuplicateCleanerUI(win)