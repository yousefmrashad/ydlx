"""Cookie sources: browser specs, cookies.txt validation, and opt wiring."""

import os
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from yt_dlp.cookies import CHROMIUM_BASED_BROWSERS

from ydlx.settings import (
    get_saved_cookie_file,
    get_saved_cookie_profile,
    get_saved_cookie_source,
)

# Every browser whose cookie store yt-dlp knows how to read. On Windows,
# Chromium browsers that have migrated their cookies to the App-Bound (v20)
# scheme cannot be read; whether a given install has migrated depends on its
# version, so firefox stays the only choice that is reliable everywhere. The
# rest are listed because they work on macOS, Linux, and older installs.
SUPPORTED_BROWSERS: tuple[str, ...] = (
    "brave",
    "chrome",
    "chromium",
    "edge",
    "firefox",
    "opera",
    "safari",
    "vivaldi",
    "whale",
)

BROWSER_OPTION_HELP = (
    "Extract cookies from browser ("
    + ", ".join(SUPPORTED_BROWSERS)
    + "); on Windows, firefox is the most reliable, since Chromium browsers "
    "may use App-Bound Encryption. Append ':<profile path>' to read a profile "
    "yt-dlp does not search by default, e.g. firefox:C:/path/to/profile"
)

# Reader choices for the dashboard's custom profile flow. Once a profile path is
# given, yt-dlp derives the install directory from that path (dirname of the
# profile), so the browser name stops selecting where to look and only selects
# the cookie reader. Verified on Windows: chrome:<path> and vivaldi:<path>
# returned identical results against the same profile. So the family collapses
# to one representative each rather than listing nine interchangeable names.
GECKO_BROWSER = "firefox"

CHROMIUM_BROWSER = "chrome"

CUSTOM_BASE_CHOICES: tuple[str, ...] = (GECKO_BROWSER, CHROMIUM_BROWSER)

COOKIE_FILE_OPTION_HELP = (
    'Path to a Netscape cookies.txt file (export it with a "Get cookies.txt '
    'LOCALLY" extension when your browser cannot be read directly)'
)


def parse_cookies_from_browser(spec: str) -> tuple[str, ...]:
    """
    Splits "browser:profile" into the (browser, profile) pair yt-dlp expects.

    yt-dlp's own CLI performs this split inside parseOpts, but ydlx calls the
    API directly, so the colon form has to be unpacked at the command boundary.
    Without it a Firefox-based browser that stores profiles outside the
    standard folder (Zen,
    LibreWolf, Waterfox, Floorp) is unreachable, because yt-dlp only searches
    Mozilla's own profile directory.

    Only the first colon separates, so Windows paths survive intact
    ("firefox:C:\\Users\\me\\zen\\Profiles\\Default").
    """
    browser, separator, profile = spec.partition(":")
    if not separator or not profile:
        return (browser,)
    return (browser, profile)


def build_browser_spec(
    browser: str | None, profile: str | None = None
) -> tuple[str, ...] | None:
    """
    Builds the tuple that yt-dlp documents as its browser-spec form.

    A separate profile is used as given. Without one the browser value is run
    through parse_cookies_from_browser, so a settings file that carries the
    colon form is still understood instead of being handed to yt-dlp whole and
    rejected as an unknown browser name.
    """
    if not browser:
        return None
    if profile:
        return (browser, profile)
    return parse_cookies_from_browser(browser)


def format_cookie_source(browser: str | None, profile: str | None = None) -> str:
    """
    Renders the active browser source compactly for the dashboard.

    The profile is a full path, far too wide for the menu panel, so only the
    profile directory's name is kept ("firefox: Default (release)"). A profile
    pointing straight at a cookies.sqlite keeps its parent directory instead.
    """
    if not browser:
        return ""
    if not profile:
        return browser
    path = Path(profile)
    name = path.parent.name if path.suffix == ".sqlite" else path.name
    return f"{browser}: {name or profile}"


def format_cookie_file(path: str) -> str:
    """Shows only the file name; a full path is too wide for the menu panel."""
    return Path(path).name or path


def apply_cookie_opts(
    ydl_opts: dict[str, Any],
    browser_spec: tuple[str, ...] | None = None,
    cookie_file: str | None = None,
) -> None:
    """
    Applies the resolved cookie sources to ydl_opts.

    Explicit arguments are authoritative: passing either one discards the
    persisted settings, so `-b firefox` never silently merges in a previously
    saved cookies.txt. Passing neither uses the saved source. Supplying both
    explicitly is honored, since yt-dlp merges cookiesfrombrowser with
    cookiefile and gives file entries precedence.
    """
    if browser_spec or cookie_file:
        spec = browser_spec
        resolved_file = cookie_file or None
    else:
        spec = build_browser_spec(get_saved_cookie_source(), get_saved_cookie_profile())
        resolved_file = get_saved_cookie_file()

    if spec:
        ydl_opts["cookiesfrombrowser"] = spec
    if resolved_file:
        ydl_opts["cookiefile"] = resolved_file


def cookie_source_choice(browser: str | None, profile: str | None = None) -> str:
    """
    Maps the active source onto a value that exists in the menu's choice list.

    A saved profile can never be one of the bare browser names the prompt
    offers, so it maps to "custom"; otherwise the prompt would pre-fill with
    something absent from its own options.
    """
    if not browser:
        return "none"
    return "custom" if profile else browser


def cookie_browser_error(spec: str) -> str | None:
    """
    Returns why a --cookies-from-browser value cannot be used, or None if it can.

    Only the browser name is checked, against the fixed list yt-dlp supports.
    ydlx deliberately does not try to infer what kind of browser a name refers
    to: there is no reliable mapping from browser name to cookie format, forks
    are numerous and their bases differ, so any guess would produce
    confidently incorrect advice. Naming the closest supported base is the
    user's call, so the message lists what is supported and explains the
    "name:profile" mechanism rather than guessing on their behalf.
    """
    browser, separator, _profile = spec.partition(":")
    if not separator:
        browser = spec
    name = browser.strip()
    if not name or name in SUPPORTED_BROWSERS:
        return None
    return (
        f"'{name}' is not a browser yt-dlp can read. It supports: "
        f"{', '.join(SUPPORTED_BROWSERS)}. If your browser stores cookies in the "
        "same format as one of those, name that one and point at the profile "
        "directly, e.g. --cookies-from-browser firefox:<path to profile>. "
        "Otherwise export a cookies.txt and pass --cookies instead."
    )


def parse_browser_spec(spec: str) -> tuple[str, ...]:
    """
    Validates a --cookies-from-browser value and converts it to yt-dlp's tuple.

    Validation happens here so an unsupported name is rejected with the way out
    spelled out, rather than reaching yt-dlp as a bare
    `unsupported browser: "..."`. Splitting the optional ":profile" suffix is
    the same step, since this is the one place the flag is still text.
    """
    error = cookie_browser_error(spec)
    if error:
        Console().print(f"[bold red]❌ {error}[/bold red]")
        raise typer.Exit(code=1)
    return parse_cookies_from_browser(spec)


def profile_arg_help(browser: str) -> str:
    """
    Describes what yt-dlp expects after the colon for a given base reader.

    The two cookie layouts differ, so one instruction cannot fit both: Gecko
    browsers are read by walking a profile folder for cookies.sqlite, while
    Chromium browsers look for a file named "Cookies" (no extension) under a
    profile folder, and accept a bare profile name such as "Default".
    """
    if browser in CHROMIUM_BASED_BROWSERS:
        return "profile name (e.g. Default) or folder path"
    return "profile folder path (the one containing cookies.sqlite)"


def is_profile_path(raw: str) -> bool:
    """
    Whether a profile argument is a filesystem path rather than a bare name.

    Chromium readers accept a profile name such as "Default" that resolves
    under the browser's own directory, so a name must not be required to exist.
    """
    return (
        os.sep in raw
        or (os.altsep is not None and os.altsep in raw)
        or Path(raw).is_absolute()
    )


def normalize_profile_arg(raw: str) -> str:
    """
    Cleans up a profile path, leaving a bare Chromium profile name untouched.

    Only a path is expanded, so "~" works; anything without a separator is a
    name that yt-dlp resolves itself and is returned as typed.
    """
    return str(Path(os.path.expanduser(raw))) if is_profile_path(raw) else raw


def parse_cookie_file(cookie_file: str) -> str:
    """
    Validates a --cookies argument, exiting with an error when it is unusable.

    yt-dlp silently ignores a cookie file it cannot read, so a mistyped path
    would otherwise download without cookies and only fail as a confusing 403.
    """
    expanded = str(Path(os.path.expanduser(cookie_file)))
    if not Path(expanded).is_file():
        Console().print(f"[bold red]❌ Cookies file not found: {expanded}[/bold red]")
        Console().print(
            '[yellow]💡 Export one with a "Get cookies.txt LOCALLY" browser '
            "extension, or pass --cookies-from-browser instead.[/yellow]"
        )
        raise typer.Exit(code=1)
    return expanded
