"""Tests of the recorder's stop and pause paths, without a real audio device."""

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402  pylint: disable=wrong-import-position
import pytest  # noqa: E402  pylint: disable=wrong-import-position
from PySide6.QtWidgets import QApplication  # noqa: E402  pylint: disable=wrong-import-position

from src.audio_recorder import AudioRecorder  # noqa: E402  pylint: disable=wrong-import-position


class _DeadlockedStream:
    """Stands in for a PortAudio stream whose stop() never returns (CoreAudio deadlock)."""

    def __init__(self):
        self.release = threading.Event()

    def stop(self):
        """Block until the test releases the stream."""
        self.release.wait()

    def close(self):
        """Nothing to close."""


@pytest.fixture(name="recorder")
def fixture_recorder(monkeypatch):
    """An AudioRecorder with a short stream-close timeout."""
    _app = QApplication.instance() or QApplication([])  # noqa: F841  keep the app alive
    monkeypatch.setattr(AudioRecorder, "_STREAM_CLOSE_TIMEOUT", 0.2)
    monkeypatch.setattr(AudioRecorder, "stream_stuck", False)
    return AudioRecorder({"sample_rate": 16000, "channels": 1})


def test_stop_returns_when_the_stream_stop_deadlocks(recorder, tmp_path):
    """A stuck stream stop no longer freezes the caller; the WAV is still closed and reported."""
    recorder._stream = _DeadlockedStream()  # pylint: disable=protected-access
    recorder._wav_path = str(tmp_path / "recording.wav")  # pylint: disable=protected-access
    recorder._is_recording = True  # pylint: disable=protected-access
    stopped = []
    recorder.recording_stopped.connect(stopped.append)

    start = time.monotonic()
    path = recorder.stop_recording()

    assert time.monotonic() - start < 2.0
    assert path == str(tmp_path / "recording.wav")
    assert stopped == [path]
    assert AudioRecorder.stream_stuck
    stream_stop_threads = [t for t in threading.enumerate() if t.name == "audio-stream-stop"]
    for t in stream_stop_threads:
        assert t.daemon


def test_paused_callback_drops_audio(recorder):
    """While paused the stream keeps running but nothing reaches the writer queue."""
    recorder._is_recording = True  # pylint: disable=protected-access
    recorder.pause_recording()
    block = np.zeros((800, 1), dtype=np.int16)
    recorder._audio_callback(block, 800, None, None)  # pylint: disable=protected-access
    assert recorder._queue.empty()  # pylint: disable=protected-access

    recorder.resume_recording()
    recorder._audio_callback(block, 800, None, None)  # pylint: disable=protected-access
    assert recorder._queue.qsize() == 1  # pylint: disable=protected-access
