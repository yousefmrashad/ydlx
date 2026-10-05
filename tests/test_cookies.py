"""Tests for cookie sources: browser, cookies.txt files, and failure guidance."""

import json
import sys
from http.cookiejar import LoadError
from pathlib import Path
from typing import Any, cast

import pytest
import typer
import yt_dlp
from yt_dlp.cookies import CHROMIUM_BASED_BROWSERS, YoutubeDLCookieJar
from yt_dlp.utils import DownloadError

import ydlx
from ydlx import (
    CHROMIUM_BROWSER,
    CUSTOM_BASE_CHOICES,
    GECKO_BROWSER,
    SUPPORTED_BROWSERS,
    apply_cookie_opts,
    cookie_browser_error,
    cookie_failure_hint,
    cookie_source_choice,
    format_cookie_file,
    format_cookie_source,
    get_saved_cookie_file,
    is_profile_path,
    normalize_profile_arg,
    parse_browser_spec,
    parse_cookie_file,
    parse_cookies_from_browser,
    profile_arg_help,
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
    apply_cookie_opts(ydl_opts, ("firefox",), str(Path("cookies.txt")))
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
    apply_cookie_opts(ydl_opts, ("firefox",))
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
    apply_cookie_opts(ydl_opts, ("firefox",), "/tmp/flag.txt")
    assert ydl_opts["cookiesfrombrowser"] == ("firefox",)
    assert ydl_opts["cookiefile"] == "/tmp/flag.txt"


def test_apply_cookie_opts_empty_string_falls_through() -> None:
    """An empty string is not an explicit choice; it uses the saved source."""
    save_settings({"cookiefile": "/tmp/saved.txt"})
    ydl_opts: dict[str, Any] = {}
    apply_cookie_opts(ydl_opts, None, "")
    assert ydl_opts["cookiefile"] == "/tmp/saved.txt"


def test_saved_cookie_file_returns_none_when_unset() -> None:
    assert get_saved_cookie_file() is None


def test_cookiefile_survives_settings_round_trip() -> None:
    save_settings({"cookiefile": "/tmp/cookies.txt"})
    assert get_saved_cookie_file() == "/tmp/cookies.txt"
    save_settings({"cookiefile": None})
    assert get_saved_cookie_file() is None


# --- browser:profile spec ------------------------------------------------
# yt-dlp only searches Mozilla's own profile directory on Windows, so a
# Firefox-based browser that keeps profiles elsewhere (Zen, LibreWolf, Waterfox)
# is unreachable without an explicit path. yt-dlp's CLI splits the colon form in
# its option parser; ydlx calls the API directly, so it must split it itself.


def test_parse_cookies_from_browser_plain_name() -> None:
    assert parse_cookies_from_browser("firefox") == ("firefox",)


def test_parse_cookies_from_browser_splits_profile() -> None:
    assert parse_cookies_from_browser("firefox:default") == ("firefox", "default")


def test_parse_cookies_from_browser_keeps_windows_path_intact() -> None:
    """Only the first colon separates, so C:\\... survives."""
    spec = r"firefox:C:\Users\me\AppData\Roaming\zen\Profiles\Default (release)"
    assert parse_cookies_from_browser(spec) == (
        "firefox",
        r"C:\Users\me\AppData\Roaming\zen\Profiles\Default (release)",
    )


def test_parse_cookies_from_browser_trailing_colon_is_ignored() -> None:
    assert parse_cookies_from_browser("firefox:") == ("firefox",)


def test_apply_cookie_opts_passes_profile_to_ydl() -> None:
    ydl_opts: dict[str, Any] = {}
    apply_cookie_opts(
        ydl_opts, ("firefox", r"C:\Users\me\zen\Profiles\Default (release)")
    )
    assert ydl_opts["cookiesfrombrowser"] == (
        "firefox",
        r"C:\Users\me\zen\Profiles\Default (release)",
    )


def test_apply_cookie_opts_reads_profile_from_saved_settings() -> None:
    """The saved browser and profile are combined into the tuple yt-dlp wants."""
    save_settings(
        {"cookies_from_browser": "firefox", "cookies_profile": "/home/me/.zen/Default"}
    )
    ydl_opts: dict[str, Any] = {}
    apply_cookie_opts(ydl_opts)
    assert ydl_opts["cookiesfrombrowser"] == ("firefox", "/home/me/.zen/Default")


def test_apply_cookie_opts_survives_a_saved_colon_form() -> None:
    """A hand-edited or pre-tuple settings file must not be handed over whole."""
    save_settings({"cookies_from_browser": "firefox:/home/me/.zen/Default"})
    ydl_opts: dict[str, Any] = {}
    apply_cookie_opts(ydl_opts)
    assert ydl_opts["cookiesfrombrowser"] == ("firefox", "/home/me/.zen/Default")


# --- Dashboard indicator formatting -------------------------------------


def test_format_cookie_source_plain_browser() -> None:
    assert format_cookie_source("firefox") == "firefox"


def test_format_cookie_source_shortens_profile_path(tmp_path: Path) -> None:
    """The indicator must not dump a whole path into the menu panel."""
    profile = tmp_path / "zen" / "Profiles" / "bi1m31bh.Default (release)"
    label = format_cookie_source("firefox", str(profile))
    assert label == "firefox: bi1m31bh.Default (release)"
    assert len(label) < 45


def test_format_cookie_source_uses_parent_of_sqlite_path(tmp_path: Path) -> None:
    sqlite = tmp_path / "zen" / "Profiles" / "abc.default" / "cookies.sqlite"
    assert format_cookie_source("firefox", str(sqlite)) == "firefox: abc.default"


def test_format_cookie_source_is_empty_without_a_browser() -> None:
    assert format_cookie_source(None) == ""


def test_format_cookie_file_keeps_drive_letter_intact(tmp_path: Path) -> None:
    """A cookies.txt path must never be colon-split; "C:" is not a browser."""
    assert format_cookie_file(str(tmp_path / "mycookies.txt")) == "mycookies.txt"
    assert format_cookie_file("/home/me/cookies.txt") == "cookies.txt"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive letters only")
def test_format_cookie_file_handles_a_windows_path() -> None:
    assert format_cookie_file(r"C:\tmp\mycookies.txt") == "mycookies.txt"


# --- Browser name validation ---------------------------------------------
# ydlx checks only the name, against the list yt-dlp supports. It never infers
# which reader matches an unknown browser: there is no reliable name-to-format
# mapping, so guessing would produce confidently incorrect advice.


@pytest.mark.parametrize("browser", SUPPORTED_BROWSERS)
def test_supported_browser_names_are_accepted(browser: str) -> None:
    assert cookie_browser_error(browser) is None


def test_supported_name_with_profile_is_accepted() -> None:
    assert cookie_browser_error(r"firefox:C:\me\profile") is None


def test_unknown_browser_name_is_rejected() -> None:
    error = cookie_browser_error("zen")
    assert error is not None
    assert "'zen' is not a browser yt-dlp can read" in error


def test_unknown_name_error_lists_supported_browsers() -> None:
    error = cookie_browser_error("librewolf") or ""
    for browser in SUPPORTED_BROWSERS:
        assert browser in error


def test_unknown_name_error_explains_profile_mechanism() -> None:
    error = cookie_browser_error("zen") or ""
    assert "firefox:<path to profile>" in error
    assert "--cookies" in error


def test_unknown_name_error_makes_no_engine_claim() -> None:
    """The message must not assert what kind of browser the name refers to.

    Inventing that mapping is how this produced wrong advice before.
    """
    error = (cookie_browser_error("zen") or "").lower()
    for claim in ("firefox-based", "is based on", "chromium-based", "is a fork of"):
        assert claim not in error


def test_unknown_name_before_colon_is_rejected() -> None:
    assert cookie_browser_error(r"zen:C:\me\profile") is not None


def test_empty_spec_is_left_to_yt_dlp() -> None:
    assert cookie_browser_error("") is None


def test_parse_browser_spec_exits_on_unknown_name() -> None:
    with pytest.raises(typer.Exit) as excinfo:
        parse_browser_spec("zen")
    assert excinfo.value.exit_code == 1


def test_parse_browser_spec_returns_the_tuple() -> None:
    """The flag is text; this is the one boundary that converts it."""
    assert parse_browser_spec(r"firefox:C:\me\p") == ("firefox", r"C:\me\p")
    assert parse_browser_spec("firefox") == ("firefox",)


# --- Menu default mapping -------------------------------------------------


def test_choice_is_none_when_unset() -> None:
    assert cookie_source_choice(None) == "none"


def test_choice_is_the_browser_name_when_bare() -> None:
    assert cookie_source_choice("firefox") == "firefox"


def test_choice_is_custom_when_a_profile_is_present() -> None:
    """A profile can never be one of the bare names in the choice list."""
    assert cookie_source_choice("firefox", r"C:\me\p") == "custom"


# --- Custom profile reader -----------------------------------------------
# Once a profile path is supplied, yt-dlp derives the install directory from
# it, so the browser name only selects the cookie reader. Verified on Windows:
# chrome:<path> and vivaldi:<path> gave identical results for the same profile.


def test_custom_base_choices_collapse_the_families() -> None:
    assert CUSTOM_BASE_CHOICES == ("firefox", "chrome")


@pytest.mark.parametrize("browser", CHROMIUM_BASED_BROWSERS)
def test_every_chromium_browser_reads_identically(browser: str) -> None:
    """The families are interchangeable, which is why only one is offered."""
    assert browser not in CUSTOM_BASE_CHOICES or browser == CHROMIUM_BROWSER


def test_gecko_browser_is_offered() -> None:
    assert GECKO_BROWSER in CUSTOM_BASE_CHOICES


def test_flag_still_accepts_every_supported_browser() -> None:
    """The -b flag keeps all names: without a path the name finds the install."""
    for browser in SUPPORTED_BROWSERS:
        assert cookie_browser_error(browser) is None


def test_profile_help_distinguishes_the_two_layouts() -> None:
    gecko = profile_arg_help(GECKO_BROWSER)
    chromium = profile_arg_help(CHROMIUM_BROWSER)
    assert "cookies.sqlite" in gecko
    assert "cookies.sqlite" not in chromium
    assert "Default" in chromium


def test_normalize_profile_arg_expands_home_in_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # expanduser reads HOME/USERPROFILE, not Path.home(), so both must be set.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert normalize_profile_arg("~/zen/Default") == str(tmp_path / "zen" / "Default")


def test_normalize_profile_arg_keeps_bare_profile_names() -> None:
    """A Chromium profile name is not a folder and must survive untouched."""
    assert normalize_profile_arg("Default") == "Default"
    assert normalize_profile_arg("Profile 1") == "Profile 1"


@pytest.mark.parametrize(
    "raw",
    ["Default", "Profile 1", "default-release"],
)
def test_bare_profile_names_are_not_treated_as_paths(raw: str) -> None:
    assert is_profile_path(raw) is False


@pytest.mark.parametrize("prefix", ["me", "home/me"])
def test_a_relative_path_is_recognised(prefix: str) -> None:
    assert is_profile_path(f"{prefix}/p") is True


def test_a_tilde_path_is_recognised() -> None:
    """The "~" form expands, so it must be recognised as a path."""
    assert is_profile_path("~/me/p") is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive letters only")
@pytest.mark.parametrize("raw", [r"C:\me\p", "C:/me/p"])
def test_a_windows_path_is_recognised(raw: str) -> None:
    assert is_profile_path(raw) is True


def test_absolute_path_is_a_path_even_without_a_separator() -> None:
    """A bare name is only ever relative; a drive or root means a real path."""
    assert is_profile_path(Path.cwd().anchor) is True


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
