"""Notification sounds and window alert for completion events.

Vendored from dnd_logger@bc2bdf1 src/notifications.py — keep in sync manually.
Change: the Win32-only ``FlashWindowEx`` taskbar flash is replaced by
``QApplication.alert``, which flashes the taskbar on Windows and bounces the Dock on macOS.
"""

import logging
import os

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from .utils import resource_path

log = logging.getLogger("pvwriter.notifications")


class NotificationSounds:
    """Manages notification sound effects using lazy-loaded QSoundEffect.

    Sounds are only initialized on first play() to avoid interfering
    with PortAudio (sounddevice) during recording/transcription.
    """

    _NAMES = ("transcription_done", "summary_done", "error")

    def __init__(self):
        self._sounds = {}
        self._loaded = False

    def _ensure_loaded(self):
        """Lazily create QSoundEffect instances on first use."""
        if self._loaded:
            return
        self._loaded = True

        from PySide6.QtMultimedia import (  # noqa: E501  # late import to avoid early audio init
            QSoundEffect,
        )

        for name in self._NAMES:
            path = resource_path(f"assets/sounds/{name}.wav")
            if os.path.exists(path):
                effect = QSoundEffect()
                effect.setSource(QUrl.fromLocalFile(path))
                effect.setVolume(0.5)
                self._sounds[name] = effect
            else:
                log.warning("Notification sound not found: %s", path)

    def play(self, name: str):
        """Play a named notification sound, loading on first call."""
        self._ensure_loaded()
        if name in self._sounds:
            effect = self._sounds[name]
            if effect.isPlaying():
                effect.stop()
            effect.play()


def alert_window(window) -> None:
    """Draw the user's attention to the window when it is not focused.

    Args:
        window: Top-level QWidget to highlight in the taskbar/Dock.
    """
    try:
        QApplication.alert(window, 0)
    except Exception:
        log.debug("Window alert failed", exc_info=True)
