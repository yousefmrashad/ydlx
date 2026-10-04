"""Tests for default directory resolution and persisted settings."""

import json
from pathlib import Path

import pytest

import ydlx
from ydlx import (
    get_default_downloads_dir,
    get_default_music_dir,
    get_default_video_dir,
    get_saved_cookie_source,
    load_settings,
    save_settings,
)

# --- Default download directories ---------------------------------------


def test_downloads_dir_prefers_xdg_env(isolated_downloads_dir: Path) -> None:
    assert get_default_downloads_dir() == isolated_downloads_dir


def test_downloads_dir_creates_missing_home_downloads(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ~/Downloads fallback is created on demand."""
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", "")
    target = isolated_home / "Downloads"
    assert not target.exists()
    assert get_default_downloads_dir() == target
    assert target.is_dir()


def test_downloads_dir_ignores_nonexistent_xdg_path(
    tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale XDG_DOWNLOAD_DIR must fall back to ~/Downloads, not mkdir a ghost."""
    ghost = tmp_path / "does-not-exist"
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", str(ghost))
    assert get_default_downloads_dir() == isolated_home / "Downloads"
    assert not ghost.exists()


def test_downloads_dir_ignores_empty_xdg_path(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", "")
    assert get_default_downloads_dir() == isolated_home / "Downloads"


def test_music_and_video_dirs_nest_under_downloads(
    isolated_downloads_dir: Path,
) -> None:
    assert get_default_music_dir() == isolated_downloads_dir / "audio"
    assert get_default_video_dir() == isolated_downloads_dir / "video"
    assert get_default_music_dir().is_dir()
    assert get_default_video_dir().is_dir()


# --- Persisted settings -------------------------------------------------


def test_load_settings_tolerates_missing_file(isolated_config_dir: Path) -> None:
    assert not (isolated_config_dir / "settings.json").exists()
    assert load_settings() == {}


def test_load_settings_tolerates_corrupt_json(isolated_config_dir: Path) -> None:
    isolated_config_dir.mkdir(parents=True, exist_ok=True)
    (isolated_config_dir / "settings.json").write_text("{not json", encoding="utf-8")
    assert load_settings() == {}


def test_load_settings_rejects_non_object_json(isolated_config_dir: Path) -> None:
    isolated_config_dir.mkdir(parents=True, exist_ok=True)
    (isolated_config_dir / "settings.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert load_settings() == {}


def test_save_then_load_round_trips(isolated_config_dir: Path) -> None:
    save_settings({"cookies_from_browser": "firefox"})
    assert load_settings() == {"cookies_from_browser": "firefox"}
    assert (isolated_config_dir / "settings.json").exists()


def test_save_settings_merges_instead_of_replacing(isolated_config_dir: Path) -> None:
    save_settings({"cookies_from_browser": "firefox"})
    save_settings({"other": "value"})
    assert load_settings() == {"cookies_from_browser": "firefox", "other": "value"}


def test_save_settings_none_removes_key(isolated_config_dir: Path) -> None:
    save_settings({"cookies_from_browser": "firefox", "keep": "yes"})
    save_settings({"cookies_from_browser": None})
    assert load_settings() == {"keep": "yes"}


def test_saved_cookie_source_returns_none_when_unset() -> None:
    assert get_saved_cookie_source() is None


def test_saved_cookie_source_is_string() -> None:
    save_settings({"cookies_from_browser": "brave"})
    assert get_saved_cookie_source() == "brave"


def test_settings_written_are_valid_utf8_json(isolated_config_dir: Path) -> None:
    save_settings({"cookies_from_browser": "chrome"})
    raw = (isolated_config_dir / "settings.json").read_text(encoding="utf-8")
    assert json.loads(raw) == {"cookies_from_browser": "chrome"}


def test_get_config_dir_is_redirected_by_fixture(isolated_config_dir: Path) -> None:
    """Guards the autouse fixture: tests must never touch the real user config."""
    assert ydlx.get_config_dir() == isolated_config_dir
