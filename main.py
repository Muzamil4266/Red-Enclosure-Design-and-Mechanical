"""
Red Enclosure
Main Entry Point
"""
import multiprocessing as mp
import customtkinter as ctk

# Canvas Modules
from more_tools_menu import MoreToolsMenuMixin  # redesigned More Tools panel (must be listed BEFORE CanvasUISetupMixin)
from canvas_ui_setup import CanvasUISetupMixin
from canvas_ui_setup_controls import CanvasUIControlsMixin
from canvas_graphics_rendering import CanvasGraphicsRenderingMixin
from canvas_graphics_geometry import CanvasGraphicsGeometryMixin
from canvas_mouse_events import CanvasMouseEventsMixin
from canvas_mouse_utils import CanvasMouseUtilsMixin
from canvas_tools_controller import CanvasToolsControllerMixin
from canvas_tools_zoom_text import CanvasToolsZoomTextMixin
from pcb_mapper import PCBMapperMixin
from scrcpy_mirror_ui import ScrcpyMirrorUIMixin
from scrcpy_mirror_stream import ScrcpyMirrorStreamMixin
from screen_recorder import ScreenRecorderMixin
from room_stitcher_2d import RoomStitcher2DMixin
from pipe_simulation import PipeSimulationMixin
from paddle_ocr import PaddleOCRMixin
from pdf_locker import PDFLockerMixin
from dependency_installer import DependencyInstallerMixin
from live_emotion_detector import LiveEmotionDetectorMixin
from auto_video_editor import AutoVideoEditorMixin
from apk_virus_checker import APKVirusCheckerMixin
from visual_duplicate_cleaner import VisualDuplicateCleanerMixin
from pdf_duplicate_cleaner import PdfDuplicateCleanerMixin
from phone_diagnostics import PhoneDiagnosticsMixin
from settings import SettingsMixin
from audio_to_text import AudioTranscriberMixin
from aria2_downloader import Aria2DownloaderMixin
from realistic_eye import RealisticEyeMixin
from insert_picture import InsertPictureMixin
from table_tool import TableToolMixin

# Sidebar Modules
from sidebar_ui import SidebarUIMixin
from sidebar_actions import SidebarActionsMixin

# Serialization & History Modules
from serialization_core import SerializationCoreMixin
from serialization_projects import SerializationProjectsMixin
from history import HistoryManagerMixin


ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class DesignAndMechanicalDevelopment(
    ctk.CTk,
    MoreToolsMenuMixin,  # overrides the old More Tools panel methods in CanvasUISetupMixin
    CanvasUISetupMixin,
    CanvasUIControlsMixin,
    CanvasGraphicsRenderingMixin,
    CanvasGraphicsGeometryMixin,
    CanvasMouseEventsMixin,
    CanvasMouseUtilsMixin,
    CanvasToolsControllerMixin,
    CanvasToolsZoomTextMixin,
    PCBMapperMixin,
    ScrcpyMirrorUIMixin,
    ScrcpyMirrorStreamMixin,
    ScreenRecorderMixin,
    RoomStitcher2DMixin,
    PipeSimulationMixin,
    PaddleOCRMixin,
    PDFLockerMixin,
    DependencyInstallerMixin,
    LiveEmotionDetectorMixin,
    AutoVideoEditorMixin,
    APKVirusCheckerMixin,
    VisualDuplicateCleanerMixin,
    PdfDuplicateCleanerMixin,
    PhoneDiagnosticsMixin,
    AudioTranscriberMixin,
    Aria2DownloaderMixin,
    RealisticEyeMixin,
    InsertPictureMixin,
    TableToolMixin,
    SettingsMixin,
    SidebarUIMixin,
    SidebarActionsMixin,
    HistoryManagerMixin,
    SerializationCoreMixin,
    SerializationProjectsMixin
):
    """Red Enclosure - Design and Mechanical Development main window."""

    def __init__(self):
        super().__init__()
        self.title("Red Enclosure")
        self.geometry("1500x950")

        self.mode = "select"
        self.pen_color = "#e74c3c"
        self.tool_size = 3
        self.last_x = 0
        self.last_y = 0
        self.line_start_x = None
        self.line_start_y = None
        self.temp_line_id = None
        self.dragging = False
        self.pre_state = None

        self.selected_item = None
        self.image_refs = {}
        self.item_scales = {}
        self.item_rotations = {}
        self.sidebar_img_refs = []

        self.undo_stack = []
        self.redo_stack = []
        self.toolbar_buttons = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        self.init_settings()  # load settings.json BEFORE the toolbar exists
        self.init_canvas_and_toolbar()
        self.init_paddle_ocr()  # starts background engine
        self.set_absolute_dark_mode()  # Enforce dark mode default
        self.init_pcb()
        self.init_mirror()
        self.init_recorder()
        self.init_room_stitcher()
        self.init_pdf_locker()
        self.init_dependency_installer()
        self.init_emotion_detector()
        self.init_video_editor()
        self.init_apk_virus_checker()
        self.init_duplicate_cleaner()
        self.init_pdf_duplicate_cleaner()
        self.init_phone_diagnostics()
        self.init_audio_transcriber()
        self.init_aria2_downloader()
        self.init_realistic_eye()
        self.init_insert_picture()
        self.init_sidebar()
        self.load_components_to_sidebar()
        self.toggle_sidebar()
        self.apply_all_settings()
        self.init_pipe_sim()
        self.purge_empty_projects()  # removes empty projects left by older versions
        self.bind("<Unmap>", self._on_window_unmap, add="+")  # minimize = user stopped working -> save

        self._previous_close_cmd = self.protocol("WM_DELETE_WINDOW")
        self.protocol("WM_DELETE_WINDOW", self.on_app_close)

    def _on_window_unmap(self, event):
        if event.widget is self:
            try:
                self.flush_autosave()
            except Exception:
                pass

    def on_app_close(self):
        """Window close handler."""
        try:
            self.flush_autosave()  # everything the user did is saved on quit (empty workspace is never saved)
        except Exception:
            pass
        try:
            self.shutdown_recorder()  # finishes a running screen recording so the video file is complete
        except Exception:
            pass
        try:
            self.shutdown_paddle_ocr()
        except Exception:
            pass
        prev = getattr(self, "_previous_close_cmd", "")
        if prev:
            try:
                self.tk.call(prev)
                return
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    mp.freeze_support()
    app = DesignAndMechanicalDevelopment()
    app.mainloop()
