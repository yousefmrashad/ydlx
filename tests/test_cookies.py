"""Tests for cookie sources: browser, cookies.txt files, and failure guidance."""

import json
from http.cookiejar import LoadError
from pathlib import Path
from typing import Any, cast

import pytest
import typer
import yt_dlp
from yt_dlp.cookies import YoutubeDLCookieJar
from yt_dlp.utils import DownloadError

import ydlx
from ydlx import (
    SUPPORTED_BROWSERS,
    apply_cookie_opts,
    cookie_failure_hint,
    get_saved_cookie_file,
    parse_cookie_file,
    save_settings,
)

# A minimal but valid cookies.txt: one plain cookie, one HttpOnly cookie, and
# one session cookie (expires=0). All values are fake; this exercises parsing
# and wiring only, never real authentication.
MOCK_COOKIES_TXT = (
    "# Netscape HTTP Cookie File\n"
    "# mock file for tests\n"
    "\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1799999999\tSID\tmock-sid\n"
    "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1799999999\tHSID\tmock-hsid\n"
    ".youtube.com\tTRUE\t/\tFALSE\t0\tVISITOR_INFO1_LIVE\tmock-visitor\n"
)


def write_cookies_txt(tmp_path: Path, contents: str = MOCK_COOKIES_TXT) -> Path:
    """Writes a cookies.txt into tmp_path and returns its path."""
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(contents, encoding="utf-8")
    return cookie_file


# --- Cookie source resolution -------------------------------------------


def test_supported_browsers_matches_yt_dlp() -> None:
    """ydlx must not offer browsers yt-dlp cannot read."""
    from yt_dlp.cookies import SUPPORTED_BROWSERS as YTDLP_BROWSERS

    assert set(SUPPORTED_BROWSERS) <= set(YTDLP_BROWSERS)


def test_apply_cookie_opts_sets_both_sources() -> None:
    ydl_opts: dict[str, Any] = {}
    apply_cookie_opts(ydl_opts, "firefox", str(Path("cookies.txt")))
    assert ydl_opts["cookiesfrombrowser"] == ("firefox",)
    assert ydl_opts["cookiefile"] == str(Path("cookies.txt"))


def test_apply_cookie_opts_omits_keys_when_unset() -> None:
    ydl_opts: dict[str, Any] = {}
    apply_cookie_opts(ydl_opts, None, None)
    assert "cookiesfrombrowser" not in ydl_opts
    assert "cookiefile" not in ydl_opts


def test_apply_cookie_opts_falls_back_to_saved_settings() -> None:
    save_settings({"cookies_from_browser": "brave", "cookiefile": "/tmp/saved.txt"})
    ydl_opts: dict[str, Any] = {}
    apply_cookie_opts(ydl_opts)
    assert ydl_opts["cookiesfrombrowser"] == ("brave",)
    assert ydl_opts["cookiefile"] == "/tmp/saved.txt"


def test_explicit_browser_discards_saved_cookie_file() -> None:
    """`-b firefox` must not silently merge in a previously saved cookies.txt."""
    save_settings({"cookies_from_browser": "brave", "cookiefile": "/tmp/saved.txt"})
    ydl_opts: dict[str, Any] = {}
    apply_cookie_opts(ydl_opts, "firefox")
    assert ydl_opts["cookiesfrombrowser"] == ("firefox",)
    assert "cookiefile" not in ydl_opts


def test_explicit_cookie_file_discards_saved_browser() -> None:
    save_settings({"cookies_from_browser": "brave", "cookiefile": "/tmp/saved.txt"})
    ydl_opts: dict[str, Any] = {}
    apply_cookie_opts(ydl_opts, None, "/tmp/flag.txt")
    assert "cookiesfrombrowser" not in ydl_opts
    assert ydl_opts["cookiefile"] == "/tmp/flag.txt"


def test_explicit_both_sources_are_kept() -> None:
    """Layering a file over browser cookies remains possible when asked for."""
    save_settings({"cookies_from_browser": "brave"})
    ydl_opts: dict[str, Any] = {}
    apply_cookie_opts(ydl_opts, "firefox", "/tmp/flag.txt")
    assert ydl_opts["cookiesfrombrowser"] == ("firefox",)
    assert ydl_opts["cookiefile"] == "/tmp/flag.txt"


def test_apply_cookie_opts_empty_string_falls_through() -> None:
    """An empty string is not an explicit choice; it uses the saved source."""
    save_settings({"cookiefile": "/tmp/saved.txt"})
    ydl_opts: dict[str, Any] = {}
    apply_cookie_opts(ydl_opts, "", "")
    assert ydl_opts["cookiefile"] == "/tmp/saved.txt"


def test_saved_cookie_file_returns_none_when_unset() -> None:
    assert get_saved_cookie_file() is None


def test_cookiefile_survives_settings_round_trip() -> None:
    save_settings({"cookiefile": "/tmp/cookies.txt"})
    assert get_saved_cookie_file() == "/tmp/cookies.txt"
    save_settings({"cookiefile": None})
    assert get_saved_cookie_file() is None


# --- Flag precedence -----------------------------------------------------
# Explicit flags are authoritative: passing one must not silently merge in the
# other source persisted by the interactive menu.


# --- cookies.txt parsing -------------------------------------------------


def test_mock_cookies_txt_loads_into_yt_dlp(tmp_path: Path) -> None:
    """A hand-written cookies.txt is valid input for yt-dlp as-is."""
    cookie_file = write_cookies_txt(tmp_path)
    with yt_dlp.YoutubeDL(cast(Any, {"cookiefile": str(cookie_file)})) as ydl:
        cookies = {
            cookie.name: cookie
            for cookie in ydl.cookiejar.get_cookies_for_url("https://www.youtube.com/")
        }
    assert cookies["SID"].value == "mock-sid"
    assert cookies["HSID"].value == "mock-hsid"
    assert cookies["VISITOR_INFO1_LIVE"].value == "mock-visitor"


def test_mock_cookies_txt_builds_cookie_header(tmp_path: Path) -> None:
    cookie_file = write_cookies_txt(tmp_path)
    jar = YoutubeDLCookieJar(str(cookie_file))
    jar.load()
    header = jar.get_cookie_header("https://www.youtube.com/watch?v=abc")
    assert header is not None
    assert "SID=mock-sid" in header


def test_httponly_prefix_is_stripped_and_session_cookie_is_not_expired(
    tmp_path: Path,
) -> None:
    """#HttpOnly_ rows load normally and expires=0 means session, not epoch."""
    cookie_file = write_cookies_txt(tmp_path)
    jar = YoutubeDLCookieJar(str(cookie_file))
    jar.load()
    by_name = {cookie.name: cookie for cookie in jar}
    assert not by_name["HSID"].name.startswith("#HttpOnly_")
    assert by_name["VISITOR_INFO1_LIVE"].expires is None
    assert by_name["SID"].expires == 1799999999


def test_ydlx_forwards_cookiefile_into_ydl_opts(tmp_path: Path) -> None:
    """The cookiefile produced by apply_cookie_opts is what yt-dlp consumes."""
    cookie_file = write_cookies_txt(tmp_path)
    ydl_opts: dict[str, Any] = {"quiet": True}
    apply_cookie_opts(ydl_opts, None, str(cookie_file))
    with yt_dlp.YoutubeDL(cast(Any, ydl_opts)) as ydl:
        assert ydl.params.get("cookiefile") == str(cookie_file)


# --- Malformed cookie files ----------------------------------------------


def test_json_cookie_export_is_rejected(tmp_path: Path) -> None:
    """A JSON cookie dump cannot be used; yt-dlp says so explicitly."""
    json_file = tmp_path / "cookies.json"
    json_file.write_text(json.dumps({"cookies": []}), encoding="utf-8")
    with pytest.raises(DownloadError) as excinfo:
        with yt_dlp.YoutubeDL(cast(Any, {"cookiefile": str(json_file)})):
            pass
    assert "Netscape" in str(excinfo.value)


@pytest.mark.filterwarnings("ignore:http.cookiejar bug")
def test_subdomain_flag_must_match_leading_dot(tmp_path: Path) -> None:
    """A row claiming subdomains without a leading dot is a hard load error."""
    cookie_file = write_cookies_txt(
        tmp_path,
        "# Netscape HTTP Cookie File\n"
        "youtube.com\tTRUE\t/\tTRUE\t1799999999\tSID\tmock-sid\n",
    )
    with pytest.raises(LoadError):
        jar = YoutubeDLCookieJar(str(cookie_file))
        jar.load()


def test_missing_cookie_file_is_silently_skipped_by_yt_dlp(tmp_path: Path) -> None:
    """yt-dlp ignores an unreadable cookiefile instead of raising.

    This is why ydlx validates the path itself: a typo would otherwise
    download without cookies and only surface later as a confusing 403.
    """
    with yt_dlp.YoutubeDL(
        cast(Any, {"cookiefile": str(tmp_path / "absent.txt"), "quiet": True})
    ) as ydl:
        assert ydl.params.get("cookiefile") == str(tmp_path / "absent.txt")
        assert ydl.cookiejar.get_cookies_for_url("https://youtube.com/") == []


# --- CLI validation -----------------------------------------------------


def test_parse_cookie_file_accepts_existing_file(tmp_path: Path) -> None:
    cookie_file = write_cookies_txt(tmp_path)
    assert parse_cookie_file(str(cookie_file)) == str(cookie_file)


def test_parse_cookie_file_expands_user_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_file = write_cookies_txt(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert parse_cookie_file("~/cookies.txt") == str(cookie_file)


def test_parse_cookie_file_rejects_missing_path(tmp_path: Path) -> None:
    """A typo'd --cookies path must fail loudly rather than download unauthenticated."""
    with pytest.raises(typer.Exit) as excinfo:
        parse_cookie_file(str(tmp_path / "absent.txt"))
    assert excinfo.value.exit_code == 1


# --- Failure guidance ----------------------------------------------------


def test_hint_explains_app_bound_encryption() -> None:
    """The DPAPI failure users hit on Windows Edge/Chrome must be explained."""
    hint = cookie_failure_hint(
        "ERROR: Failed to decrypt with DPAPI. See "
        "https://github.com/yt-dlp/yt-dlp/issues/10927 for more info"
    )
    assert hint is not None
    assert "App-Bound Encryption" in hint
    assert "10927" in hint
    assert "--cookies" in hint


def test_hint_tells_user_to_close_the_browser() -> None:
    hint = cookie_failure_hint(
        "Could not copy Chrome cookie database. See "
        "https://github.com/yt-dlp/yt-dlp/issues/7271 for more info"
    )
    assert hint is not None
    assert "Close the browser" in hint


def test_hint_explains_json_export_is_unusable() -> None:
    hint = cookie_failure_hint(
        "Cookies file must be Netscape formatted, not JSON. See "
        "https://github.com/yt-dlp/yt-dlp/wiki/FAQ"
    )
    assert hint is not None
    assert "cookies.txt" in hint


def test_hint_for_generic_cookie_load_failure() -> None:
    hint = cookie_failure_hint("ERROR: failed to load cookies")
    assert hint is not None


@pytest.mark.parametrize(
    "message",
    [
        "HTTP Error 404: Not Found",
        "Sign in to confirm you're not a bot",
        "Unable to download video subtitles",
    ],
)
def test_hint_is_none_for_unrelated_errors(message: str) -> None:
    assert cookie_failure_hint(message) is None


def test_report_failure_shows_guidance_instead_of_raw_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """yt-dlp's duplicated DPAPI line is suppressed; the fix is shown instead."""
    console = ydlx.Console()
    ydlx.report_failure(
        "ERROR: ERROR: ERROR: Failed to decrypt with DPAPI. See "
        "https://github.com/yt-dlp/yt-dlp/issues/10927 for more info",
        console,
    )
    out = capsys.readouterr().out
    assert "App-Bound Encryption" in out
    assert "Failed to decrypt with DPAPI" not in out


def test_logger_does_not_reprint_cookie_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The logger defers cookie failures to report_failure instead of echoing them."""
    ydlx.MyLogger(verbose=True).error(
        "ERROR: Failed to decrypt with DPAPI. See https://example.invalid for more info"
    )
    assert capsys.readouterr().out == ""


def test_logger_still_reports_other_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ydlx.MyLogger(verbose=True).error("ERROR: something broke")
    assert "something broke" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ERROR: Failed to decrypt", "Failed to decrypt"),
        ("ERROR: ERROR: Failed to decrypt", "Failed to decrypt"),
        ("ERROR: ERROR: ERROR: Failed to decrypt", "Failed to decrypt"),
        ("Extraction failed: ERROR: boom", "Extraction failed: boom"),
        ("no prefix here", "no prefix here"),
    ],
)
def test_collapse_error_prefixes(raw: str, expected: str) -> None:
    assert ydlx.collapse_error_prefixes(raw) == expected


def test_report_failure_stays_quiet_for_other_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    console = ydlx.Console()
    ydlx.report_failure("HTTP Error 404: Not Found", console, "Download error")
    out = capsys.readouterr().out
    assert "Download error: HTTP Error 404: Not Found" in out
    assert "App-Bound" not in out
