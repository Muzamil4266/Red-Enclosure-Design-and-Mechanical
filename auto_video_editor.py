"""
Auto Video Editor Launcher Mixin for Red Circuit / Visual Circuit Builder Pro.
Launches the standalone Auto Video Editor GUI in its own separate process window.
"""
import os
import sys
import subprocess
from tkinter import messagebox


class AutoVideoEditorMixin:

    def init_video_editor(self):
        self._video_editor_process = None

    def launch_auto_video_editor(self):
        """Launches gui.py from the Auto Video Editor in its own separate window."""
        # Check if editor process is already running
        if self._video_editor_process is not None and self._video_editor_process.poll() is None:
            if hasattr(self, "toast"):
                self.toast("🎬 Auto Video Editor is already open!")
            return

        # Locate gui.py (checks same folder or 'auto_video_editor' subfolder)
        base_dir = os.path.dirname(os.path.abspath(__file__))
        target_script = os.path.join(base_dir, "gui.py")

        if not os.path.exists(target_script):
            target_script = os.path.join(base_dir, "auto_video_editor", "gui.py")

        if not os.path.exists(target_script):
            messagebox.showerror(
                "File Not Found",
                f"Could not locate Auto Video Editor GUI at:\n{target_script}\n\n"
                "Please ensure gui.py is placed in your project directory."
            )
            return

        try:
            working_dir = os.path.dirname(target_script)
            cmd = [sys.executable, target_script]

            # Spawn process in background without blocking Red Circuit mainloop
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self._video_editor_process = subprocess.Popen(cmd, cwd=working_dir)

            if hasattr(self, "toast"):
                self.toast("🎬 Auto Video Editor Launched!")
        except Exception as e:
            messagebox.showerror("Launch Error", f"Failed to launch Auto Video Editor:\n{e}")
