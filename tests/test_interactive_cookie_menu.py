"""Tests for the dashboard's interactive cookie-source flow.

The suite previously exercised the cookie helpers but never drove the menu that
uses them, so the prompt sequence was untested. Answers are scripted by patching
builtins.input, which is what rich's Console.input falls back to.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

import ydlx
from ydlx import get_saved_cookie_profile, get_saved_cookie_source, save_settings


@pytest.fixture(autouse=True)
def reset_session_cookies() -> Iterator[None]:
    """The dashboard keeps its source in module globals; keep tests isolated."""
    ydlx.session_cookies_browser = None
    ydlx.session_cookies_profile = None
    ydlx.session_cookies_file = None
    yield
    ydlx.session_cookies_browser = None
    ydlx.session_cookies_profile = None
    ydlx.session_cookies_file = None


def feed(monkeypatch: pytest.MonkeyPatch, *answers: str) -> None:
    """Answers prompts in order, failing loudly if the menu asks for more."""
    queue = list(answers)

    def fake_input(prompt: str = "") -> str:
        if not queue:
            raise AssertionError(f"menu asked for unexpected input: {prompt!r}")
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)


def test_custom_gecko_profile_is_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Firefox-family profile outside yt-dlp's search path must be storable."""
    profile = tmp_path / "zen" / "abc123.default"
    profile.mkdir(parents=True)
    feed(monkeypatch, "6", "browser", "custom", "firefox", str(profile), "7")

    ydlx.run_interactive_menu()

    assert get_saved_cookie_source() == "firefox"
    assert get_saved_cookie_profile() == str(profile)


def test_custom_accepts_a_bare_chromium_profile_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Default" is a profile name, not a folder, and must not be rejected."""
    feed(monkeypatch, "6", "browser", "custom", "chrome", "Profile 1", "7")

    ydlx.run_interactive_menu()

    assert get_saved_cookie_source() == "chrome"
    assert get_saved_cookie_profile() == "Profile 1"


def test_custom_rejects_a_folder_that_does_not_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A bad path must re-prompt rather than persist something unusable."""
    missing = tmp_path / "not-here"
    profile = tmp_path / "good.default"
    profile.mkdir()
    feed(
        monkeypatch,
        "6",
        "browser",
        "custom",
        "firefox",
        str(missing),
        "6",
        "browser",
        "custom",
        "firefox",
        str(profile),
        "7",
    )

    ydlx.run_interactive_menu()

    assert "Not a folder" in capsys.readouterr().out
    assert get_saved_cookie_source() == "firefox"
    assert get_saved_cookie_profile() == str(profile)


def test_custom_requires_a_profile(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pressing Enter with nothing saved must not store a bare browser name."""
    feed(monkeypatch, "6", "browser", "custom", "firefox", "", "7")

    ydlx.run_interactive_menu()

    assert "No profile given" in capsys.readouterr().out
    assert get_saved_cookie_source() is None
    assert get_saved_cookie_profile() is None


def test_saved_profile_keeps_its_path_out_of_the_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A saved path must not be pre-filled into the prompt.

    The prompt offers bare browser names plus "custom", so a path cannot be a
    valid default; showing it wrapped the prompt across several lines.
    """
    profile = tmp_path / "abc123.default"
    profile.mkdir(parents=True)
    save_settings({"cookies_from_browser": "firefox", "cookies_profile": str(profile)})
    feed(monkeypatch, "6", "browser", "firefox", "7")

    ydlx.run_interactive_menu()

    assert str(profile) not in capsys.readouterr().out


def test_none_clears_a_previously_saved_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "abc.default"
    profile.mkdir(parents=True)
    save_settings({"cookies_from_browser": "firefox", "cookies_profile": str(profile)})
    feed(monkeypatch, "6", "none", "7")

    ydlx.run_interactive_menu()

    assert get_saved_cookie_source() is None
    assert get_saved_cookie_profile() is None


def test_choosing_a_browser_clears_a_saved_cookie_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Switching source kinds must not leave both active, or the status is ambiguous."""
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    save_settings({"cookiefile": str(cookie_file)})
    feed(monkeypatch, "6", "browser", "firefox", "7")

    ydlx.run_interactive_menu()

    assert get_saved_cookie_source() == "firefox"
    assert ydlx.get_saved_cookie_file() is None
