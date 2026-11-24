"""PV Writer — entry point.

Adapted from dnd_logger@bc2bdf1 main.py: logging, crash capture and the exception hook are
kept; the splash screen, WebEngine setup and auto-update are removed.
"""

import os
import sys


def _set_windows_app_id():
    """Group the taskbar icon under PV Writer instead of python.exe (Windows only)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("pvwriter.meeting.minutes.1")
    except (AttributeError, OSError):
        pass


def _force_light_theme(app):
    """Use a light palette whatever the OS setting.

    The stylesheet only styles part of the widgets; without this, macOS / Windows dark mode
    leaves the rest dark and mixes light and dark text and backgrounds.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette

    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)  # Qt >= 6.8
    except AttributeError:
        pass
    app.setStyle("Fusion")  # the native macOS style ignores parts of the palette
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#f6f7f9",
        QPalette.ColorRole.WindowText: "#1f2328",
        QPalette.ColorRole.Base: "#ffffff",
        QPalette.ColorRole.AlternateBase: "#f1f3f5",
        QPalette.ColorRole.Text: "#1f2328",
        QPalette.ColorRole.PlaceholderText: "#8a8f98",
        QPalette.ColorRole.Button: "#ffffff",
        QPalette.ColorRole.ButtonText: "#1f2328",
        QPalette.ColorRole.BrightText: "#c62828",
        QPalette.ColorRole.ToolTipBase: "#1f2328",
        QPalette.ColorRole.ToolTipText: "#ffffff",
        QPalette.ColorRole.Highlight: "#cfe1f5",
        QPalette.ColorRole.HighlightedText: "#1f2328",
        QPalette.ColorRole.Link: "#2f6eb5",
        QPalette.ColorRole.Light: "#ffffff",
        QPalette.ColorRole.Midlight: "#eceff3",
        QPalette.ColorRole.Mid: "#c4cad3",
        QPalette.ColorRole.Dark: "#9aa1ab",
        QPalette.ColorRole.Shadow: "#6b7280",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#9aa1ab"))
    app.setPalette(palette)


def main():
    """Application entry point — logging, main window, first-run wizard."""
    import faulthandler
    import logging
    import traceback
    from logging.handlers import RotatingFileHandler

    from src.utils import APP_NAME, project_root, resource_path

    log_file = os.path.join(project_root(), "pv_writer.log")
    handler = RotatingFileHandler(log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.basicConfig(level=logging.DEBUG, handlers=[handler])
    log = logging.getLogger("pvwriter")

    # Without a console (pythonw, .app bundle) a native crash would vanish without a
    # trace: faulthandler writes the C+Python stack of every thread to this file.
    crash_file = open(  # pylint: disable=consider-using-with
        os.path.join(project_root(), "pv_writer_crash.log"), "a", encoding="utf-8"
    )
    faulthandler.enable(file=crash_file, all_threads=True)

    def exception_hook(exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log.critical("Unhandled exception:\n%s", msg)
        print(f"CRASH: {msg}", file=sys.stderr, flush=True)
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = exception_hook
    _set_windows_app_id()

    from PySide6.QtCore import QLibraryInfo, QLocale, QTimer, QTranslator
    from PySide6.QtWidgets import QApplication

    from src.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("PVWriter")
    app.setApplicationDisplayName(APP_NAME)
    _force_light_theme(app)

    # French labels for Qt's standard buttons and dialogs (Annuler, Enregistrer, Oui…)
    qt_translator = QTranslator(app)
    if qt_translator.load(
        QLocale(QLocale.Language.French), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    ):
        app.installTranslator(qt_translator)

    qss_path = resource_path("assets/styles/neutral.qss")
    if os.path.exists(qss_path):
        with open(qss_path, encoding="utf-8") as f:
            app.setStyleSheet(f.read())

    log.info("Starting %s", APP_NAME)
    window = MainWindow()
    window.show()
    QTimer.singleShot(0, window.check_first_run)
    code = app.exec()

    from src.audio_recorder import AudioRecorder

    if AudioRecorder.stream_stuck:
        # A deadlocked PortAudio stream would also hang sounddevice's atexit Pa_Terminate
        log.warning("Audio stream left deadlocked, exiting without PortAudio cleanup")
        logging.shutdown()
        os._exit(code)
    sys.exit(code)


if __name__ == "__main__":
    main()
