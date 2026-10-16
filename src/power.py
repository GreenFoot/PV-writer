"""Keep the machine awake while recording, on Windows and macOS.

Replaces dnd_logger's Windows-only ``AudioRecorder._keep_screen_awake``.
"""

import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger("pvwriter.power")

# Windows SetThreadExecutionState flags
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


class KeepAwake:
    """Prevents system and display sleep between start() and stop()."""

    def __init__(self):
        self._caffeinate = None

    def start(self):
        """Inhibit sleep. Safe to call twice."""
        try:
            if sys.platform == "win32":
                import ctypes

                flags = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
                ctypes.windll.kernel32.SetThreadExecutionState(flags)
            elif sys.platform == "darwin" and self._caffeinate is None:
                exe = shutil.which("caffeinate") or "/usr/bin/caffeinate"
                # -w ties the assertion to our PID, so it ends even if we crash
                self._caffeinate = subprocess.Popen(  # pylint: disable=consider-using-with
                    [exe, "-dims", "-w", str(os.getpid())],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception:
            log.debug("Could not inhibit sleep", exc_info=True)

    def stop(self):
        """Allow sleep again."""
        try:
            if sys.platform == "win32":
                import ctypes

                ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
            elif self._caffeinate is not None:
                self._caffeinate.terminate()
                self._caffeinate.wait(timeout=2)
        except Exception:
            log.debug("Could not release sleep inhibition", exc_info=True)
        finally:
            self._caffeinate = None
