"""Platform-agnostic default download directories (resolution only)."""

import os
from pathlib import Path

# =====================================================================
# Platform-Agnostic Directory Helpers
# =====================================================================


def get_default_downloads_dir() -> Path:
    """Resolves the platform-agnostic default Downloads directory.

    Resolution only; nothing is created on disk. The download entry points
    mkdir the directory they are about to write into, so the menu can name the
    path without bringing it into existence.
    """
    xdg_download = os.environ.get("XDG_DOWNLOAD_DIR")
    if xdg_download and os.path.exists(xdg_download):
        return Path(xdg_download)
    return Path.home() / "Downloads"


def get_default_music_dir() -> Path:
    """Resolves the audio download directory (~/Downloads/audio), creating nothing."""
    return get_default_downloads_dir() / "audio"


def get_default_video_dir() -> Path:
    """Resolves the video download directory (~/Downloads/video), creating nothing."""
    return get_default_downloads_dir() / "video"
