"""Shared fixtures for the ydlx test suite.

Every fixture here keeps the tests hermetic: no test may read or write the real
user's settings.json, ~/Downloads, or home directory. The config/Downloads
routing is redirected through environment variables rather than by stubbing
get_config_dir, so the real directory-resolution logic is what gets exercised.
"""

import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Points Path.home() at tmp_path so ~/Downloads fallbacks stay in tmp."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirects the config directory to tmp_path via the platform env var.

    The real get_config_dir() still runs (and still creates the directory), so
    this exercises the production code path instead of a stand-in.
    """
    base = tmp_path / "appdata"
    base.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        monkeypatch.setenv("APPDATA", str(base))
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(base))
    return base / "ydlx"


@pytest.fixture(autouse=True)
def isolated_downloads_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Points XDG_DOWNLOAD_DIR at an existing tmp directory."""
    downloads = tmp_path / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", str(downloads))
    return downloads
