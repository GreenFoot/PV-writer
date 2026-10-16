"""Utility functions: resource paths, data directory, config I/O, formatting helpers.

Adapted from dnd_logger@bc2bdf1 src/utils.py: campaigns and the shared/personal config
split are gone, and the data directory is resolved per platform (Windows and macOS).
"""

import json
import logging
import os
import re
import sys
import unicodedata

log = logging.getLogger("pvwriter.utils")

APP_NAME = "PV Writer"

_DEFAULT_CONFIG = {
    "api_key": "",
    "audio_device": None,
    "sample_rate": 16000,
    "channels": 1,
    "chunk_duration_minutes": 60,
    "transcription_model": "voxtral-mini-latest",
    "summary_model": "mistral-large-latest",
    # PV generation backend: "mistral" or "claude_cli". Transcription always uses Mistral.
    "summary_provider": "mistral",
    "claude_model": "",
    "language": "fr",
    "diarize": False,
    "auto_timestamp_notes": True,
    "notification_sounds_enabled": True,
    "default_organization": "",
    "prompt_extract": "",
    "prompt_redact": "",
    # Global glossary merged with each meeting's own glossary for the STT context bias.
    "context_bias": [],
    # Mistral API limits of the key (admin.mistral.ai > Limits); defaults = free tier
    "rate_limit_chat_tpm": 20000,
    "rate_limit_chat_rps": 0.8,
    "rate_limit_transcription_tpm": 50000,
    "rate_limit_transcription_rps": 1.0,
    "window_geometry": "",
}


def resource_path(relative_path: str) -> str:
    """Return the absolute path of a bundled resource, in dev and in PyInstaller builds.

    Args:
        relative_path: Path relative to the repository root.

    Returns:
        Absolute path to the resource.
    """
    if hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS  # pylint: disable=protected-access,no-member
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def _data_dir() -> str:
    """Return the user-writable data directory for the app.

    - Frozen build on Windows: ``%APPDATA%/PV Writer``
    - Frozen build on macOS: ``~/Library/Application Support/PV Writer``
    - Dev mode: the repository root (where main.py lives)
    """
    if not hasattr(sys, "_MEIPASS"):
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", APP_NAME)
    return os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), APP_NAME)


def project_root() -> str:
    """Return the data root directory for config, meetings and logs."""
    return ensure_dir(_data_dir())


def config_path() -> str:
    """Return the path to config.json in the data root."""
    return os.path.join(project_root(), "config.json")


def meetings_dir() -> str:
    """Return the directory holding one sub-folder per meeting."""
    return ensure_dir(os.path.join(project_root(), "meetings"))


def slugify(text: str, max_len: int = 40) -> str:
    """Turn free text into a short filesystem-safe slug.

    Args:
        text: Any text, accents allowed.
        max_len: Maximum slug length.

    Returns:
        An ASCII slug made of lowercase letters, digits and dashes (``"reunion"`` if empty).
    """
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    return slug[:max_len].strip("-") or "reunion"


def load_config() -> dict:
    """Load config from disk, layered over the defaults.

    Returns:
        The merged configuration dict.
    """
    cfg = dict(_DEFAULT_CONFIG)
    path = config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            log.exception("Could not read %s, using defaults", path)
    # French only, whatever an old config file says
    cfg["language"] = "fr"
    return cfg


def save_config(cfg: dict) -> None:
    """Write the config dict to disk.

    Args:
        cfg: Configuration dict to persist.
    """
    with open(config_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def ensure_dir(path: str) -> str:
    """Create a directory if it doesn't exist and return its path."""
    os.makedirs(path, exist_ok=True)
    return path


def format_duration(seconds: int) -> str:
    """Format seconds as HH:MM:SS."""
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_file_size(size_bytes: int) -> str:
    """Format bytes as a human-readable size."""
    from .i18n import tr

    units = (
        tr("units.bytes"),
        tr("units.kilobytes"),
        tr("units.megabytes"),
        tr("units.gigabytes"),
    )
    for unit in units:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} {tr('units.terabytes')}"
