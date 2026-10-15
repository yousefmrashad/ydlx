"""Error text handling, logging, cookie diagnostics, and failure reporting."""

import re
from typing import Any

from rich.console import Console
from rich.markup import escape

# =====================================================================
# Custom Logger and Progress Tracker
# =====================================================================

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from yt-dlp messages."""
    return ANSI_ESCAPE_RE.sub("", text)


def collapse_error_prefixes(text: str) -> str:
    """
    Flattens the repeated "ERROR: " prefixes yt-dlp stacks when it re-raises
    text that a logger already reported, e.g. "ERROR: ERROR: Failed to ...".
    """
    return re.sub(r"(?:ERROR:\s*)+", "", strip_ansi(text)).strip()


class MyLogger:
    """Custom logger to pipe yt-dlp output to Rich formatting."""

    console: Console
    verbose: bool
    reported_errors: set[str]

    def __init__(self, verbose: bool = False):
        self.console = Console()
        self.verbose = verbose
        self.reported_errors = set()

    def debug(self, msg: str) -> None:
        # yt-dlp outputs debug and info messages through debug()
        if msg.startswith("[debug] "):
            if self.verbose:
                self.console.print(f"[grey50]{msg}[/grey50]")
        else:
            self.info(msg)

    def info(self, msg: str) -> None:
        if self.verbose:
            self.console.print(f"[cyan]{msg}[/cyan]")

    def warning(self, msg: str) -> None:
        self.console.print(f"[yellow]⚠️  Warning: {escape(strip_ansi(msg))}[/yellow]")

    def error(self, msg: str) -> None:
        # Always report what yt-dlp actually said, so an unrelated error is never
        # lost to a cookie hint. But yt-dlp reports some failures twice: once via
        # the generic trouble() path and again from the cookiejar property with
        # the chained cause. Print only the first occurrence of a given message,
        # which keeps the error visible without echoing it.
        clean = collapse_error_prefixes(msg)
        if clean in self.reported_errors:
            return
        self.reported_errors.add(clean)
        # Escaped: yt-dlp prefixes failures with "[youtube]" / "[info]", which
        # Rich would otherwise read as markup tags and silently drop.
        self.console.print(f"[bold red]❌ Error: {escape(clean)}[/bold red]")


# =====================================================================
# Cookie Failure Diagnostics
# =====================================================================

# yt-dlp issue numbers quoted in cookie guidance. Quoted by number instead of
# URL because these messages print on a narrow terminal, where a bare link
# pushes the actual fix off-screen; README.md links both.
DPAPI_ISSUE = 10927

COOKIE_LOCK_ISSUE = 7271


def cookie_failure_hint(message: str) -> str | None:
    """
    Turns a yt-dlp cookie failure into actionable guidance, or None when the
    error is unrelated to cookies. Pure text matching so it stays testable.

    Issue numbers are referenced without URLs: this text is printed on a narrow
    terminal, and a bare link buries the actual fix. README.md carries the
    full table of links.
    """
    lowered = message.lower()
    # Require a decryption context alongside the keyword. A video title or a
    # directory named "dpapi"/"app-bound" used to be enough to trigger this,
    # which replaced an unrelated error with cookie advice.
    if ("dpapi" in lowered or "app-bound" in lowered) and "decrypt" in lowered:
        # Only cookies already migrated to the v20 App-Bound scheme fail: yt-dlp
        # routes any non-v10 value through CryptUnprotectData, which the
        # App-Bound service refuses. Whether a given Chromium browser is
        # affected depends on its version, so a browser that still stores v10
        # cookies (older or un-migrated installs) can still work.
        return (
            "These cookies use App-Bound Encryption, which yt-dlp cannot "
            "decrypt. Chromium browsers migrate their cookies to this scheme "
            "over time, so switching browser may help if the other one still "
            "uses the older format. The reliable options are "
            "--cookies-from-browser firefox, or exporting cookies.txt with a "
            '"Get cookies.txt LOCALLY" extension and passing --cookies FILE '
            f"(yt-dlp issue {DPAPI_ISSUE})."
        )
    if "could not find" in lowered and "cookies database" in lowered:
        return (
            "That browser's cookie database was not found. The browser may not "
            "be installed, it may be a portable or non-default install, or the "
            "cookie profile name may be wrong. A Firefox-based browser that "
            "keeps its profiles elsewhere (Zen, LibreWolf, Waterfox) needs an "
            "explicit path: --cookies-from-browser "
            "firefox:/path/to/profiles/Default"
        )
    if "cookie database" in lowered and (
        "copy" in lowered or "permission" in lowered or "lock" in lowered
    ):
        return (
            "The browser's cookie database could not be read. Close the browser "
            "completely so its cookies are flushed to disk, then retry "
            f"(yt-dlp issue {COOKIE_LOCK_ISSUE})."
        )
    if "netscape" in lowered:
        return (
            "That file is not in Netscape cookies.txt format. Export cookies with "
            'a "Get cookies.txt LOCALLY" extension; a JSON export cannot be used.'
        )
    if "failed to load cookies" in lowered:
        return (
            "No cookies could be loaded. Check the browser profile name, or the "
            "cookies.txt path."
        )
    return None


def report_failure(
    message: str,
    console: Console,
    prefix: str = "Error",
    already_reported: set[str] | None = None,
) -> None:
    """
    Reports a failure, skipping text yt-dlp already logged through MyLogger.

    yt-dlp logs through logger.error before raising, so by the time an except
    handler runs the raw message is usually on screen already (under an
    "Error" prefix) and printing it again under "<prefix>" only duplicates it.
    already_reported is that logger's reported_errors set: a message found
    there is not re-printed, though a cookie hint still is, since the logger
    only ever prints the raw text. Anything new is printed as before and
    recorded, so repeats across handlers collapse too.

    A cookie problem still prints only actionable guidance: yt-dlp's own
    message is typically a duplicated "ERROR: ERROR: ..." line whose link the
    guidance already carries, so repeating it only buries the fix.
    """
    clean = collapse_error_prefixes(message)
    hint = cookie_failure_hint(clean)
    if already_reported is not None and clean in already_reported:
        if hint:
            console.print(f"[yellow]💡 {hint}[/yellow]")
        return
    if hint:
        console.print(f"[yellow]💡 {hint}[/yellow]")
    else:
        # Escaped for the same "[youtube]"-as-markup reason as MyLogger.error.
        console.print(f"[bold red]❌ {prefix}: {escape(clean)}[/bold red]")
    if already_reported is not None:
        already_reported.add(clean)


def _logger_reported(ydl_opts: dict[str, Any]) -> set[str] | None:
    """The reported-errors set of the MyLogger carried in ydl_opts, if any."""
    logger = ydl_opts.get("logger")
    return logger.reported_errors if isinstance(logger, MyLogger) else None
