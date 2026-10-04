import json
import os
import re
import sys
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import urlparse

# Reconfigure stdout/stderr to support UTF-8 characters (like Arabic and emojis) on Windows
if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "reconfigure"):
        cast(Any, sys.stdout).reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        cast(Any, sys.stderr).reconfigure(encoding="utf-8")

import typer
import yt_dlp
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from typer import Argument, Option, Typer
from yt_dlp.utils import DownloadError

app = Typer(
    name="ydlx",
    help="An interactive and feature-rich CLI wrapper and dashboard for yt-dlp.",
    no_args_is_help=False,
)

# Global session variables for cookies in interactive mode
session_cookies_browser: str | None = None
session_cookies_file: str | None = None

# Every browser whose cookie store yt-dlp knows how to read. Chrome and Edge
# encrypt cookies with App-Bound Encryption on Windows, so extraction fails
# there; the others still use keyring/DPAPI encryption yt-dlp can handle.
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
    + "); chrome and edge cannot be read on Windows, prefer firefox or brave"
)
COOKIE_FILE_OPTION_HELP = (
    'Path to a Netscape cookies.txt file (export it with a "Get cookies.txt '
    'LOCALLY" extension when your browser cannot be read directly)'
)

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
    already_downloaded: list[str]

    def __init__(self, verbose: bool = False):
        self.console = Console()
        self.verbose = verbose
        self.already_downloaded = []

    def debug(self, msg: str) -> None:
        # yt-dlp outputs debug and info messages through debug()
        clean = strip_ansi(msg)
        if "has already been downloaded" in clean:
            self.already_downloaded.append(clean.replace("[download] ", ""))
        if msg.startswith("[debug] "):
            if self.verbose:
                self.console.print(f"[grey50]{msg}[/grey50]")
        else:
            self.info(msg)

    def info(self, msg: str) -> None:
        if self.verbose:
            self.console.print(f"[cyan]{msg}[/cyan]")

    def warning(self, msg: str) -> None:
        self.console.print(f"[yellow]⚠️  Warning: {strip_ansi(msg)}[/yellow]")

    def error(self, msg: str) -> None:
        clean = collapse_error_prefixes(msg)
        # Cookie failures are re-raised and reported once with actionable
        # guidance by report_failure; echoing the raw text here as well is what
        # produced the same yt-dlp error three times over.
        if cookie_failure_hint(clean):
            return
        self.console.print(f"[bold red]❌ Error: {clean}[/bold red]")


class DownloadTracker:
    """Manages a beautiful, real-time Rich progress bar for downloads."""

    progress: Progress | None
    task_id: TaskID | None
    current_filename: str | None

    def __init__(self):
        self.progress = None
        self.task_id = None
        self.current_filename = None

    def hook(self, d: dict[str, Any]) -> None:
        if d.get("status") == "downloading":
            total: int = int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
            downloaded: int = int(d.get("downloaded_bytes") or 0)
            filename: str = str(d.get("filename", "Unknown File"))
            display_name = os.path.basename(filename)

            if self.progress is None:
                self.progress = Progress(
                    TextColumn("[bold blue]{task.description}"),
                    BarColumn(),
                    DownloadColumn(),
                    TransferSpeedColumn(),
                    TimeRemainingColumn(),
                    transient=True,
                )
                self.progress.start()
                self.task_id = self.progress.add_task(
                    f"Downloading {display_name[:30]}...", total=total
                )
                self.current_filename = filename

            if self.progress is not None and self.task_id is not None:
                if filename != self.current_filename:
                    self.current_filename = filename
                    self.progress.update(
                        self.task_id,
                        description=f"Downloading {display_name[:30]}...",
                        total=total,
                        completed=downloaded,
                    )
                else:
                    self.progress.update(
                        self.task_id, completed=downloaded, total=total
                    )

        elif d.get("status") == "finished":
            if self.progress is not None:
                self.progress.stop()
                self.progress = None
                self.task_id = None
            filename = str(d.get("filename", "Unknown File"))
            display_name = os.path.basename(filename)
            rich_console = Console()
            rich_console.print(
                f"[bold green]✓[/bold green] Finished downloading: [cyan]{display_name}[/cyan]"
            )


# =====================================================================

# Format Selectors and Filters
# =====================================================================


def _video_codec_label(vcodec: str) -> str:
    """Maps a raw codec string to a human-readable video codec name."""
    lowered = vcodec.lower()
    if lowered.startswith("av01"):
        return "AV1"
    if lowered.startswith("vp9") or lowered.startswith("vp09"):
        return "VP9"
    if lowered.startswith("avc1") or lowered.startswith("h264"):
        return "H.264"
    if lowered.startswith(("h265", "hev1", "hvc1")):
        return "H.265"
    if lowered.startswith("vp8"):
        return "VP8"
    return vcodec or "unknown"


def get_preset_format_choices(
    info: dict[str, Any], universal: bool = False
) -> list[tuple[str, str, str | None]]:
    """
    Builds download choices for the selected codec preset. The quality preset
    keeps yt-dlp's best-ranked format per resolution (usually AV1/VP9) and
    names the codec in the label; the universal preset lists only H.264 video
    paired with AAC audio so results play on any device.
    """
    formats: list[dict[str, Any]] = info.get("formats", [])

    choices: list[tuple[str, str, str | None]] = []
    resolutions: dict[str, dict[str, Any]] = {}

    for f in formats:
        vcodec = str(f.get("vcodec", "none"))
        if universal and vcodec != "none" and not vcodec.startswith("avc1"):
            continue
        if vcodec != "none":
            height = f.get("height")
            if height:
                resolutions[f"{height}p"] = f

    sorted_res = sorted(
        resolutions.keys(),
        key=lambda x: int(x[:-1]) if x[:-1].isdigit() else 0,
        reverse=True,
    )

    if universal:
        choices.append(
            (
                "🚀 Best Universal Quality (Auto H.264 + AAC)",
                "bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "mp4",
            )
        )
    else:
        choices.append(("🚀 Best Quality (Auto)", "bestvideo+bestaudio/best", None))

    for res in sorted_res:
        f = resolutions[res]
        fid = str(f.get("format_id", ""))
        ext = str(f.get("ext", ""))
        size_bytes = f.get("filesize") or f.get("filesize_approx")
        size_str = f" (~{size_bytes / (1024 * 1024):.1f} MB)" if size_bytes else ""
        codec = _video_codec_label(str(f.get("vcodec", "")))

        if universal:
            choices.append(
                (
                    f"📺 Video: {res} (H.264 + AAC){size_str}",
                    f"{fid}+bestaudio[ext=m4a]/best[ext=mp4]/best",
                    "mp4",
                )
            )
        elif str(f.get("acodec", "none")) != "none":
            choices.append(
                (
                    f"📺 Video: {res} ({codec}, {ext}){size_str}",
                    fid,
                    ext,
                )
            )
        else:
            choices.append(
                (
                    f"📺 Video: {res} ({codec}, {ext}) + Best Audio{size_str}",
                    f"{fid}+bestaudio/best",
                    ext,
                )
            )

    return choices


def make_duration_filter(
    min_len: int | None = None, max_len: int | None = None
) -> Callable[..., str | None]:
    """Generates a duration matching filter callback."""

    def duration_filter(
        info: dict[str, Any], *, incomplete: bool = False
    ) -> str | None:
        duration = info.get("duration")
        if duration:
            if min_len is not None and duration < min_len:
                return f"The video is too short ({duration}s < {min_len}s)"
            if max_len is not None and duration > max_len:
                return f"The video is too long ({duration}s > {max_len}s)"
        return None

    return duration_filter


def make_audio_format_spec(format_codec: str) -> str:
    """Returns the download format spec: native best m4a for m4a targets, bestaudio otherwise."""
    if format_codec == "m4a":
        return "bestaudio[ext=m4a]/bestaudio/best"
    return "bestaudio/best"


class VideoPreset(StrEnum):
    """Codec presets for video downloads, mirroring the interactive wizard."""

    UNIVERSAL = "universal"
    BEST = "best"


def make_video_format_spec(preset: VideoPreset, max_height: int | None = None) -> str:
    """
    Builds a video format selector for the given preset, optionally capped to a
    maximum height. The universal preset pairs H.264 video with AAC audio so the
    result merges into MP4; the best preset takes yt-dlp's highest ranked formats
    (usually AV1/VP9) and keeps their native container.

    Each branch ends in an uncapped fallback so an over-tight --res degrades to
    the nearest available quality instead of failing the download.
    """
    cap = f"[height<={max_height}]" if max_height else ""
    if preset is VideoPreset.UNIVERSAL:
        return (
            f"bestvideo[vcodec^=avc1]{cap}+bestaudio[ext=m4a]/best[ext=mp4]{cap}/best"
        )
    if not cap:
        return "bestvideo+bestaudio/best"
    return f"bestvideo{cap}+bestaudio/best{cap}/best"


# =====================================================================
# Platform-Agnostic Directory Helpers
# =====================================================================


def get_default_downloads_dir() -> Path:
    """Returns the platform-agnostic default Downloads directory."""
    xdg_download = os.environ.get("XDG_DOWNLOAD_DIR")
    if xdg_download and os.path.exists(xdg_download):
        download_path = Path(xdg_download)
    else:
        download_path = Path.home() / "Downloads"
    download_path.mkdir(parents=True, exist_ok=True)
    return download_path


def get_default_music_dir() -> Path:
    """Returns the platform-agnostic audio download directory (~/Downloads/audio)."""
    audio_dir = get_default_downloads_dir() / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    return audio_dir


def get_default_video_dir() -> Path:
    """Returns the platform-agnostic video download directory (~/Downloads/video)."""
    video_dir = get_default_downloads_dir() / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    return video_dir


# =====================================================================
# Persistent Settings
# =====================================================================


def get_config_dir() -> Path:
    """Returns the platform-agnostic config directory (%APPDATA%/ydlx or ~/.config/ydlx)."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    config_dir = Path(base) / "ydlx"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def load_settings() -> dict[str, Any]:
    """Loads persisted settings, tolerating a missing or corrupt config file."""
    try:
        with open(get_config_dir() / "settings.json", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(settings: dict[str, Any]) -> None:
    """Merges settings into the config file; None values remove their key."""
    current = load_settings()
    for key, value in settings.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    with open(get_config_dir() / "settings.json", "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)


def get_saved_cookie_source() -> str | None:
    """Returns the persisted cookie-source browser, if any."""
    value = load_settings().get("cookies_from_browser")
    return str(value) if value else None


def get_saved_cookie_file() -> str | None:
    """Returns the persisted cookies.txt path, if any."""
    value = load_settings().get("cookiefile")
    return str(value) if value else None


def apply_cookie_opts(
    ydl_opts: dict[str, Any],
    cookies_from_browser: str | None = None,
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
    if cookies_from_browser or cookie_file:
        browser = cookies_from_browser or None
        resolved_file = cookie_file or None
    else:
        browser = get_saved_cookie_source()
        resolved_file = get_saved_cookie_file()

    if browser:
        ydl_opts["cookiesfrombrowser"] = (browser,)
    if resolved_file:
        ydl_opts["cookiefile"] = resolved_file


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
    if "dpapi" in lowered or "app-bound" in lowered:
        return (
            "This browser encrypts cookies with App-Bound Encryption on Windows, "
            "which yt-dlp cannot decrypt. Use --cookies-from-browser firefox, "
            "brave, vivaldi, opera, or chromium, or export cookies.txt with a "
            '"Get cookies.txt LOCALLY" extension and pass --cookies FILE '
            f"(yt-dlp issue {DPAPI_ISSUE})."
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


def report_failure(message: str, console: Console, prefix: str = "Error") -> None:
    """
    Reports a failure exactly once.

    A cookie problem prints only actionable guidance: yt-dlp's own message is
    typically a duplicated "ERROR: ERROR: ..." line whose link the guidance
    already carries, so repeating it only buries the fix.
    """
    clean = collapse_error_prefixes(message)
    hint = cookie_failure_hint(clean)
    if hint:
        console.print(f"[yellow]💡 {hint}[/yellow]")
        return
    console.print(f"[bold red]❌ {prefix}: {clean}[/bold red]")


# =====================================================================
# URL Helpers
# =====================================================================


def normalize_url(url: str) -> str | None:
    """Returns a normalized http(s) URL, or None when the input is empty or invalid."""
    url = url.strip()
    if not url:
        return None
    if "://" not in url:
        first_segment = url.split("/")[0]
        if not first_segment or "." not in first_segment:
            return None
        url = f"https://{url}"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return url


def parse_cli_url(url: str) -> str:
    """Validates a CLI URL argument, exiting with an error when it is invalid."""
    normalized = normalize_url(url)
    if normalized is None:
        Console().print(
            "[bold red]❌ Invalid URL. Provide a full link, e.g. https://youtu.be/VIDEO_ID[/bold red]"
        )
        raise typer.Exit(code=1)
    return normalized


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


def prompt_for_url(console: Console) -> str:
    """Prompts until the user enters a usable video URL."""
    while True:
        url = Prompt.ask("Enter video URL").strip()
        normalized = normalize_url(url)
        if normalized:
            return normalized
        console.print(
            "[bold red]❌ Empty or invalid URL. Use a full link, e.g. https://youtu.be/VIDEO_ID[/bold red]"
        )


# =====================================================================
# API / Core Functions
# =====================================================================


def get_video_info(
    url: str,
    cookies_from_browser: str | None = None,
    cookie_file: str | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Extract video metadata without downloading it."""
    ydl_opts: dict[str, Any] = {
        "logger": MyLogger(verbose=verbose),
        "quiet": not verbose,
        "color": "never",
        "remote_components": ["ejs:github"],
    }
    apply_cookie_opts(ydl_opts, cookies_from_browser, cookie_file)

    with yt_dlp.YoutubeDL(cast(Any, ydl_opts)) as ydl:
        info = ydl.extract_info(url, download=False)
        return cast(Any, info)


def download_video(
    url: str,
    opts_override: dict[str, Any] | None = None,
    cookies_from_browser: str | None = None,
    cookie_file: str | None = None,
    output_dir: str | Path | None = None,
    verbose: bool = False,
) -> int:
    """Download a video with optional custom configurations."""
    target_dir = Path(output_dir) if output_dir else get_default_video_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    tracker = DownloadTracker()
    ydl_opts: dict[str, Any] = {
        "logger": MyLogger(verbose=verbose),
        "progress_hooks": [tracker.hook],
        "quiet": True,
        "paths": {"home": str(target_dir)},
        # yt-dlp colors ERROR:/WARNING: prefixes whenever stderr is a TTY,
        # even when a custom logger is set; our logger does its own styling.
        "color": "never",
        # Fetch yt-dlp's official challenge solver scripts so YouTube's JS
        # challenges get solved when a JS runtime (e.g. Deno) is available,
        # instead of silently accepting throttled/missing formats.
        "remote_components": ["ejs:github"],
    }

    apply_cookie_opts(ydl_opts, cookies_from_browser, cookie_file)

    if opts_override:
        ydl_opts.update(opts_override)

    try:
        with yt_dlp.YoutubeDL(cast(Any, ydl_opts)) as ydl:
            code = int(ydl.download([url]))
    except DownloadError as e:
        msg = strip_ansi(str(e))
        if "Unable to download video subtitles" in msg and (
            ydl_opts.get("writesubtitles") or ydl_opts.get("writeautomaticsub")
        ):
            console = Console()
            console.print(f"[yellow]⚠️  Warning: {msg}[/yellow]")
            console.print("[yellow]⚠️  Retrying without subtitles...[/yellow]")
            retry_opts = {
                key: value
                for key, value in ydl_opts.items()
                if key not in ("writesubtitles", "writeautomaticsub")
            }
            retry_opts["progress_hooks"] = [DownloadTracker().hook]
            with yt_dlp.YoutubeDL(cast(Any, retry_opts)) as ydl:
                return int(ydl.download([url]))
        report_failure(msg, Console(), "Download error")
        return 1
    except Exception as e:
        report_failure(str(e), Console(), "Download error")
        return 1

    if ydl_opts["logger"].already_downloaded:
        console = Console()
        for line in ydl_opts["logger"].already_downloaded:
            console.print(f"[yellow]⚠️  {line}[/yellow]")
        console.print(
            "[yellow]⚠️  yt-dlp skips files matched by name, even if the requested "
            "format differs. Delete the file(s) or pick another output directory "
            "to force a redownload.[/yellow]"
        )
    return code


def download_audio(
    url: str,
    format_codec: str = "m4a",
    cookies_from_browser: str | None = None,
    cookie_file: str | None = None,
    output_dir: str | Path | None = None,
    opts_override: dict[str, Any] | None = None,
    sponsorblock: bool = False,
    verbose: bool = False,
) -> int:
    """Download and extract audio format only."""
    target_dir = Path(output_dir) if output_dir else get_default_music_dir()
    opts: dict[str, Any] = {
        "format": make_audio_format_spec(format_codec),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": format_codec,
            }
        ],
    }
    if sponsorblock:
        opts["sponsorblock_skip"] = ["sponsor", "selfpromo"]

    if opts_override:
        opts.update(opts_override)

    return download_video(
        url,
        opts_override=opts,
        cookies_from_browser=cookies_from_browser,
        cookie_file=cookie_file,
        output_dir=target_dir,
        verbose=verbose,
    )


def download_from_info_json(
    info_file: str,
    opts_override: dict[str, Any] | None = None,
    cookies_from_browser: str | None = None,
    cookie_file: str | None = None,
    output_dir: str | Path | None = None,
    verbose: bool = False,
) -> int:
    """Download video using an existing info.json file."""
    target_dir = Path(output_dir) if output_dir else get_default_video_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    tracker = DownloadTracker()
    ydl_opts: dict[str, Any] = {
        "logger": MyLogger(verbose=verbose),
        "progress_hooks": [tracker.hook],
        "quiet": True,
        "paths": {"home": str(target_dir)},
        "color": "never",
    }

    apply_cookie_opts(ydl_opts, cookies_from_browser, cookie_file)

    if opts_override:
        ydl_opts.update(opts_override)

    try:
        with yt_dlp.YoutubeDL(cast(Any, ydl_opts)) as ydl:
            code = int(ydl.download_with_info_file(info_file))
    except DownloadError as e:
        report_failure(str(e), Console(), "Download error")
        return 1
    except Exception as e:
        report_failure(str(e), Console(), "Download error")
        return 1

    if ydl_opts["logger"].already_downloaded:
        console = Console()
        for line in ydl_opts["logger"].already_downloaded:
            console.print(f"[yellow]⚠️  {line}[/yellow]")
        console.print(
            "[yellow]⚠️  yt-dlp skips files matched by name, even if the requested "
            "format differs. Delete the file(s) or pick another output directory "
            "to force a redownload.[/yellow]"
        )
    return code


def configure_subtitles(
    opts: dict[str, Any],
    *,
    write_subs: bool = False,
    embed_subs: bool = False,
    auto_subs: bool = False,
    sub_langs: str = "en",
    sub_format: str = "srt",
) -> None:
    """Configures subtitle download, conversion, and embedding options in ydl_opts."""
    if not (write_subs or embed_subs):
        return

    opts["writesubtitles"] = True
    if auto_subs:
        opts["writeautomaticsub"] = True

    langs = [lang.strip() for lang in sub_langs.split(",") if lang.strip()]
    opts["subtitleslangs"] = langs if langs else ["en"]

    # "srt" is (almost) never a native source format; pick the best native
    # track and let the convertor below produce the .srt file.
    opts["subtitlesformat"] = "best" if sub_format.lower() == "srt" else sub_format

    if "postprocessors" not in opts:
        opts["postprocessors"] = []

    # Convert subtitles to srt if requested. The CLI registers this converter
    # as "before_dl": that phase runs right after subtitle files are written,
    # including under skip_download, where later PP phases never run.
    if sub_format.lower() in ("srt", "vtt"):
        opts["postprocessors"].append(
            {
                "key": "FFmpegSubtitlesConvertor",
                "format": sub_format.lower(),
                "when": "before_dl",
            }
        )

    if embed_subs:
        opts["postprocessors"].append(
            {
                "key": "FFmpegEmbedSubtitle",
                "already_have_subtitle": write_subs,
            }
        )


def download_subtitles_only(
    url: str,
    sub_langs: str = "en",
    auto_subs: bool = True,
    sub_format: str = "srt",
    cookies_from_browser: str | None = None,
    cookie_file: str | None = None,
    output_dir: str | Path | None = None,
    verbose: bool = False,
) -> int:
    """Download subtitles only without downloading the media."""
    target_dir = Path(output_dir) if output_dir else get_default_video_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    opts: dict[str, Any] = {
        "skip_download": True,
        "writesubtitles": True,
    }
    if auto_subs:
        opts["writeautomaticsub"] = True

    langs = [lang.strip() for lang in sub_langs.split(",") if lang.strip()]
    opts["subtitleslangs"] = langs if langs else ["en"]
    # "srt" is (almost) never a native source format; pick the best native
    # track and let the convertor below produce the .srt file.
    opts["subtitlesformat"] = "best" if sub_format.lower() == "srt" else sub_format
    # A single failed language track (e.g. HTTP 429 rate limit) must not abort
    # the whole run; yt-dlp then reports it as a warning and keeps going.
    opts["ignoreerrors"] = True

    # Only convert when the target format is a convertible subtitle container;
    # anything else (e.g. "best") saves files in their native extracted format.
    # "before_dl" mirrors the yt-dlp CLI: that phase runs right after subtitle
    # files are written, including under skip_download, where later PP phases
    # never run.
    opts["postprocessors"] = []
    if sub_format.lower() in ("srt", "vtt"):
        opts["postprocessors"].append(
            {
                "key": "FFmpegSubtitlesConvertor",
                "format": sub_format.lower(),
                "when": "before_dl",
            }
        )

    return download_video(
        url,
        opts_override=opts,
        cookies_from_browser=cookies_from_browser,
        cookie_file=cookie_file,
        output_dir=target_dir,
        verbose=verbose,
    )


def report_subtitle_download(
    url: str,
    target_dir: Path,
    console: Console,
    *,
    langs: str,
    auto_subs: bool,
    sub_format: str,
    cookies_from_browser: str | None,
    cookie_file: str | None = None,
    verbose: bool = False,
) -> int:
    """Download subtitles and report how many files landed; returns an exit code."""
    existing = {p for p in target_dir.iterdir() if p.is_file()}
    error_code = download_subtitles_only(
        url,
        sub_langs=langs,
        auto_subs=auto_subs,
        sub_format=sub_format,
        cookies_from_browser=cookies_from_browser,
        cookie_file=cookie_file,
        output_dir=target_dir,
        verbose=verbose,
    )
    if error_code:
        console.print("[bold red]❌ Subtitles download failed![/bold red]")
        return error_code
    new_files = {p for p in target_dir.iterdir() if p.is_file()} - existing
    if not new_files:
        console.print(
            f"[bold red]❌ No subtitles found for '{langs}' on this video.[/bold red]"
        )
        console.print(
            "[dim]Tip: try common languages like 'en', or allow auto-generated captions.[/dim]"
        )
        return 1
    console.print(
        f"[bold green]✓ Downloaded {len(new_files)} subtitle file(s) to [cyan]{target_dir}[/cyan][/bold green]"
    )
    return 0


# =====================================================================
# UI Printing & Menus
# =====================================================================


def print_video_info(info: dict[str, Any], console: Console) -> None:
    """Print video/playlist metadata beautifully using Rich panels and tables."""
    _type = info.get("_type", "video")

    if _type == "playlist":
        title = str(info.get("title", "Unknown Playlist"))
        uploader = str(info.get("uploader") or info.get("uploader_id") or "Unknown")
        entries: list[dict[str, Any]] = info.get("entries", [])
        video_count = len(entries)

        console.print(
            Panel(
                f"[bold cyan]Playlist: {title}[/bold cyan]\n"
                f"[bold]Channel/Author:[/bold] {uploader}\n"
                f"[bold]Videos:[/bold] {video_count} videos in playlist",
                title="Playlist Metadata",
                expand=False,
            )
        )

        table = Table(title="Playlist Videos Preview", box=box.ROUNDED)
        table.add_column("#", style="cyan", justify="right")
        table.add_column("Video Title", style="green")
        table.add_column("Duration", style="yellow")

        for idx, entry in enumerate(entries[:10], 1):
            if entry:
                dur_secs = entry.get("duration")
                duration = (
                    f"{dur_secs // 60}:{dur_secs % 60:02d}"
                    if isinstance(dur_secs, int)
                    else "Unknown"
                )
                table.add_row(str(idx), str(entry.get("title", "Unknown")), duration)

        console.print(table)
        if video_count > 10:
            console.print(f"[dim]... and {video_count - 10} more videos[/dim]")
    else:
        title = str(info.get("title", "Unknown Title"))
        uploader = str(info.get("uploader", "Unknown Uploader"))
        duration_secs = info.get("duration")
        duration = (
            f"{duration_secs // 60}:{duration_secs % 60:02d}"
            if isinstance(duration_secs, int)
            else "Unknown"
        )
        view_count = info.get("view_count")
        views = f"{view_count:,}" if isinstance(view_count, int) else "Unknown"
        upload_date = str(info.get("upload_date", "Unknown"))
        if len(upload_date) == 8:
            upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

        description = str(info.get("description", ""))
        desc_lines = description.split("\n")
        desc_summary = "\n".join(desc_lines[:3])
        if len(desc_lines) > 3 or len(desc_summary) > 200:
            desc_summary = desc_summary[:200] + "..."

        subtitles_dict: dict[str, Any] = info.get("subtitles") or {}
        auto_captions_dict: dict[str, Any] = info.get("automatic_captions") or {}
        sub_langs = list(subtitles_dict.keys())
        auto_langs = list(auto_captions_dict.keys())

        subs_parts: list[str] = []
        if sub_langs:
            subs_parts.append(
                f"Official: {', '.join(sub_langs[:8])}{'...' if len(sub_langs) > 8 else ''}"
            )
        if auto_langs:
            subs_parts.append(
                f"Auto: {', '.join(auto_langs[:8])}{'...' if len(auto_langs) > 8 else ''}"
            )
        subs_text = " | ".join(subs_parts) if subs_parts else "None available"

        console.print(
            Panel(
                f"[bold cyan]{title}[/bold cyan]\n"
                f"[bold]Channel:[/bold] {uploader}\n"
                f"[bold]Duration:[/bold] {duration} | [bold]Views:[/bold] {views} | [bold]Uploaded:[/bold] {upload_date}\n"
                f"[bold]Subtitles:[/bold] {subs_text}\n\n"
                f"[dim]{desc_summary}[/dim]",
                title="Video Metadata",
                expand=False,
            )
        )

        formats: list[dict[str, Any]] = info.get("formats", [])
        table = Table(title="Available Formats", box=box.ROUNDED)
        table.add_column("Format ID", style="cyan")
        table.add_column("Ext", style="green")
        table.add_column("Resolution", style="yellow")
        table.add_column("Codec", style="magenta")
        table.add_column("Size", style="blue")

        # Show the 15 formats (worst to best)
        for f in formats[-15:]:
            fid = str(f.get("format_id", "N/A"))
            ext = str(f.get("ext", "N/A"))
            res = str(
                f.get("resolution") or f"{f.get('width', '?')}x{f.get('height', '?')}"
            )
            if res == "?x?":
                res = "audio only" if f.get("vcodec") == "none" else "N/A"

            vcodec = str(f.get("vcodec", "none"))
            acodec = str(f.get("acodec", "none"))
            if vcodec != "none" and acodec != "none":
                codec = f"V:{vcodec.split('.')[0]} + A:{acodec.split('.')[0]}"
            elif vcodec != "none":
                codec = f"V:{vcodec.split('.')[0]}"
            else:
                codec = f"A:{acodec.split('.')[0]}"

            size_bytes = f.get("filesize") or f.get("filesize_approx")
            if isinstance(size_bytes, (int, float)):
                size_mb = size_bytes / (1024 * 1024)
                size = f"{size_mb:.1f} MB"
            else:
                size = "Unknown"

            table.add_row(fid, ext, res, codec, size)

        console.print(table)

        audio_only = [
            f
            for f in formats
            if str(f.get("vcodec", "none")) == "none"
            and str(f.get("acodec", "none")) != "none"
        ]
        if audio_only:
            audio_table = Table(title="Audio Formats", box=box.ROUNDED)
            audio_table.add_column("Format ID", style="cyan")
            audio_table.add_column("Ext", style="green")
            audio_table.add_column("Codec", style="magenta")
            audio_table.add_column("Bitrate", style="yellow")
            audio_table.add_column("Size", style="blue")

            for f in audio_only[-5:]:
                fid = str(f.get("format_id", "N/A"))
                ext = str(f.get("ext", "N/A"))
                acodec = str(f.get("acodec", "none")).split(".")[0]
                abr = f.get("abr") or f.get("tbr")
                bitrate = (
                    f"{abr:.0f} kbps" if isinstance(abr, (int, float)) else "Unknown"
                )
                size_bytes = f.get("filesize") or f.get("filesize_approx")
                size = (
                    f"{size_bytes / (1024 * 1024):.1f} MB"
                    if isinstance(size_bytes, (int, float))
                    else "Unknown"
                )
                audio_table.add_row(fid, ext, acodec, bitrate, size)

            console.print(audio_table)


def prompt_for_max_height(console: Console) -> int | None:
    """
    Prompts for a resolution cap, accepting a bare number or the "p" suffix the
    prompt suggests ("720p"). Blank or 0 means no limit; returns None so
    make_video_format_spec leaves the selector uncapped.
    """
    while True:
        console.print(
            "[dim]Common heights: 360p, 480p, 720p, 1080p, 1440p, 2160p "
            "(0 = highest available)[/dim]"
        )
        raw = Prompt.ask("Maximum resolution height (e.g. 720p)", default="0").strip()
        # Accept exactly what the prompt advertises, plus a stray space before
        # the "p"; anything else re-prompts rather than being guessed at.
        cleaned = raw.lower()
        if cleaned.endswith("p"):
            cleaned = cleaned[:-1].strip()
        if not cleaned:
            return None
        try:
            value = int(cleaned)
        except ValueError:
            console.print(
                "[bold red]❌ Enter a height like 720p, or 0 for no limit.[/bold red]"
            )
            continue
        if value < 0:
            console.print(
                "[bold red]❌ Height must be 0 or a positive number.[/bold red]"
            )
            continue
        return value or None


def do_download_interactive(
    url: str, info: dict[str, Any] | None = None, console: Console | None = None
) -> None:
    """Sub-menu to choose download settings interactively."""
    if console is None:
        console = Console()
    if info is None:
        with console.status("[bold blue]Fetching metadata...[/bold blue]"):
            try:
                info = get_video_info(
                    url,
                    cookies_from_browser=session_cookies_browser,
                    cookie_file=session_cookies_file,
                )
            except Exception as e:
                report_failure(f"Failed to fetch metadata: {e}", console, "Error")
                return

    is_playlist = info.get("_type") == "playlist"
    menu_table = Table(show_header=False, box=box.SIMPLE, border_style="dim magenta")
    menu_table.add_row(
        "[bold cyan]1.[/bold cyan] 🚀 Best Quality",
        "[cyan]Default Video + Audio combined (Auto)[/cyan]",
    )
    quality_hint = (
        "[green]Cap the height per video, with an AV1/VP9 or H.264/AAC preset[/green]"
        if is_playlist
        else "[green]Pick quality (AV1/VP9) or compatibility (H.264/AAC) per resolution[/green]"
    )
    menu_table.add_row(
        "[bold green]2.[/bold green] 🎨 Choose Resolution & Preset",
        quality_hint,
    )
    menu_table.add_row(
        "[bold bright_yellow]3.[/bold bright_yellow] 🎯 Choose Specific Format ID",
        "[bright_yellow]Manually input a format code[/bright_yellow]",
    )
    valid_choices = ["1", "2", "3"]
    if is_playlist:
        menu_table.add_row(
            "[bold orange3]4.[/bold orange3] ⏱️  Filter by Duration",
            "[orange3]Skip playlist videos outside a duration range[/orange3]",
        )
        valid_choices.append("4")
    back_choice = str(len(valid_choices) + 1)
    menu_table.add_row(
        f"[bold red]{back_choice}.[/bold red] ↩ Back", "[red]Return to main menu[/red]"
    )
    valid_choices.append(back_choice)

    console.print("\n")
    console.print(
        Panel(
            menu_table,
            title=f"[bold magenta]📥 Download Settings for: {str(info.get('title', 'Video'))[:50]}...[/bold magenta]",
            border_style="magenta",
            expand=False,
        )
    )

    choice = Prompt.ask("Select download type", choices=valid_choices, default="1")

    opts: dict[str, Any] = {}
    target_dir = get_default_video_dir()

    if choice == "1":
        pass
    elif choice == "2":
        console.print("\n[bold]Codec presets:[/bold]")
        console.print(
            "  [cyan]1[/cyan]. 🚀 Best Quality — AV1/VP9, smaller files (modern players only)"
        )
        console.print(
            "  [cyan]2[/cyan]. 📱 Universal — H.264 + AAC (plays on any device)"
        )
        universal = Prompt.ask("Select preset", choices=["1", "2"], default="1") == "2"
        preset = VideoPreset.UNIVERSAL if universal else VideoPreset.BEST

        if is_playlist:
            # Every entry has its own format ids, so a per-resolution list built
            # from playlist metadata would be meaningless. Cap by height instead:
            # yt-dlp evaluates the selector per entry, which is exactly what the
            # CLI's --res does.
            max_height = prompt_for_max_height(console)
            opts["format"] = make_video_format_spec(preset, max_height)
            if preset is VideoPreset.UNIVERSAL:
                opts["merge_output_format"] = "mp4"
                opts["remux_video"] = "mp4"
            cap_note = f" (capped at {max_height}p)" if max_height else " (uncapped)"
            console.print(
                f"[green]✓ {preset.value} preset applied to every video{cap_note}[/green]"
            )
        else:
            choices_list = get_preset_format_choices(info, universal=universal)
            preset_label = (
                "Universal H.264 + AAC" if universal else "Best Quality (AV1/VP9)"
            )
            console.print(
                f"\n[bold]Available {preset_label} formats for this video:[/bold]"
            )
            for idx, (display, _, _) in enumerate(choices_list, 1):
                console.print(f"  [cyan]{idx}[/cyan]. {display}")
            console.print(
                f"  [cyan]{len(choices_list) + 1}[/cyan]. [red]↩ Cancel[/red]"
            )

            while True:
                choice_idx = IntPrompt.ask("Select format number", default=1)
                if 1 <= choice_idx <= len(choices_list) + 1:
                    break
                console.print(
                    f"[bold red]❌ Invalid selection. Please enter a number between 1 and {len(choices_list) + 1}.[/bold red]"
                )

            if choice_idx == len(choices_list) + 1:
                return
            selected_format = choices_list[choice_idx - 1]
            opts["format"] = selected_format[1]
            if selected_format[2]:
                opts["merge_output_format"] = selected_format[2]
                opts["remux_video"] = selected_format[2]
    elif choice == "3":
        format_code = Prompt.ask("Enter Format ID (e.g. '137+140', '22', or 'worst')")
        opts["format"] = format_code
    elif choice == "4" and is_playlist:
        min_sec = IntPrompt.ask(
            "Enter minimum duration in seconds (0 for no limit)", default=60
        )
        max_sec = IntPrompt.ask(
            "Enter maximum duration in seconds (0 for no limit)", default=0
        )
        min_sec_val = min_sec if min_sec > 0 else None
        max_sec_val = max_sec if max_sec > 0 else None
        opts["match_filter"] = make_duration_filter(min_sec_val, max_sec_val)
    else:
        return

    # Ask about SponsorBlock skipping
    skip_sponsors = Confirm.ask(
        "Skip sponsor and self-promotion segments using SponsorBlock?", default=False
    )
    if skip_sponsors:
        opts["sponsorblock_skip"] = ["sponsor", "selfpromo"]

    # Ask about Subtitles (if downloading video)
    if target_dir == get_default_video_dir():
        want_subs = Confirm.ask("Download / embed subtitles?", default=False)
        if want_subs:
            sub_mode = Prompt.ask(
                "Subtitle placement",
                choices=["embed", "external", "both"],
                default="embed",
            )
            sub_langs = Prompt.ask(
                "Subtitle languages (comma-separated, e.g. 'en', 'ar', 'all')",
                default="en",
            )
            sub_format = Prompt.ask(
                "Subtitle format", choices=["srt", "vtt", "best"], default="srt"
            )
            auto_subs = Confirm.ask(
                "Include auto-generated captions if official subtitles are missing?",
                default=True,
            )
            configure_subtitles(
                opts,
                write_subs=(sub_mode in ("external", "both")),
                embed_subs=(sub_mode in ("embed", "both")),
                auto_subs=auto_subs,
                sub_langs=sub_langs,
                sub_format=sub_format,
            )

    console.print(f"[blue]Starting download to [cyan]{target_dir}[/cyan]...[/blue]")
    error_code = download_video(
        url,
        opts_override=opts,
        cookies_from_browser=session_cookies_browser,
        cookie_file=session_cookies_file,
        output_dir=target_dir,
    )
    if error_code:
        console.print("[bold red]❌ Download failed![/bold red]")
    else:
        console.print("[bold green]✓ Download completed successfully![/bold green]")


def run_interactive_menu() -> None:
    """Runs the main CLI prompt-driven dashboard loop."""
    global session_cookies_browser, session_cookies_file
    if session_cookies_browser is None:
        session_cookies_browser = get_saved_cookie_source()
    if session_cookies_file is None:
        session_cookies_file = get_saved_cookie_file()
    console = Console()
    while True:
        active_cookies = [
            part for part in (session_cookies_browser, session_cookies_file) if part
        ]
        cookies_status = (
            " [bold green]+[/bold green] ".join(active_cookies)
            if active_cookies
            else "[yellow]None[/yellow]"
        )

        menu_table = Table(show_header=False, box=box.SIMPLE, border_style="dim cyan")
        menu_table.add_row(
            "[bold cyan]1.[/bold cyan] ℹ️  Extract Video Info",
            "[cyan]Query format resolutions & video metadata[/cyan]",
        )
        menu_table.add_row(
            "[bold green]2.[/bold green] 📥 Download Video / Playlist",
            "[green]Download best quality or select format[/green]",
        )
        menu_table.add_row(
            "[bold magenta]3.[/bold magenta] 🎵 Download Audio",
            "[magenta]Extract and convert audio tracks (M4A/Opus/MP3)[/magenta]",
        )
        menu_table.add_row(
            "[bold yellow]4.[/bold yellow] 💬 Download Subtitles Only",
            "[yellow]Extract subtitles (.srt) without downloading media[/yellow]",
        )
        menu_table.add_row(
            "[bold bright_yellow]5.[/bold bright_yellow] 📄 Download from info.json",
            "[bright_yellow]Download using cached info.json metadata[/bright_yellow]",
        )
        menu_table.add_row(
            "[bold blue]6.[/bold blue] 🍪 Set Cookie Source",
            f"[blue]Use browser cookies or a cookies.txt file (Active: {cookies_status})[/blue]",
        )
        menu_table.add_row(
            "[bold red]7.[/bold red] ❌ Exit", "[red]Close the application[/red]"
        )

        console.print("\n")
        console.print(
            Panel(
                menu_table,
                title="[bold cyan]⚡ ydlx Interactive Dashboard ⚡[/bold cyan]",
                border_style="cyan",
                expand=False,
            )
        )

        choice = Prompt.ask(
            "Select an option",
            choices=["1", "2", "3", "4", "5", "6", "7"],
            default="1",
        )

        if choice == "1":
            url = prompt_for_url(console)
            with console.status("[bold blue]Fetching metadata...[/bold blue]"):
                try:
                    info = get_video_info(
                        url,
                        cookies_from_browser=session_cookies_browser,
                        cookie_file=session_cookies_file,
                    )
                except Exception as e:
                    report_failure(f"Extraction failed: {e}", console, "Error")
                    continue

            print_video_info(info, console)

            item_label = "playlist" if info.get("_type") == "playlist" else "video"
            console.print("\n[bold]Submenu Actions:[/bold]")
            console.print("1. [cyan]💾 Save metadata to info.json[/cyan]")
            console.print(f"2. [green]📥 Download this {item_label}[/green]")
            console.print("3. [yellow]💬 Download subtitles only[/yellow]")
            console.print("4. [magenta]🎵 Download audio (M4A/Opus/MP3)[/magenta]")
            console.print("5. [red]↩ Back to main menu[/red]")
            sub_choice = Prompt.ask(
                "Select action", choices=["1", "2", "3", "4", "5"], default="1"
            )

            if sub_choice == "1":
                title_val = str(info.get("title", item_label))
                title_clean = "".join(
                    [c for c in title_val if c.isalnum() or c in " ._-"]
                ).strip()
                filename = f"{title_clean}.info.json"
                with open(filename, "w", encoding="utf-8") as f:
                    sanitized = yt_dlp.YoutubeDL().sanitize_info(cast(Any, info))
                    json.dump(sanitized, f, indent=4)
                console.print(
                    f"[bold green]✓[/bold green] Metadata saved to [cyan]{filename}[/cyan]"
                )
            elif sub_choice == "2":
                do_download_interactive(url, info, console)
            elif sub_choice == "3":
                sub_langs = Prompt.ask(
                    "Subtitle languages (comma-separated, e.g. 'en', 'ar', 'all')",
                    default="en",
                )
                auto_subs = Confirm.ask(
                    "Include auto-generated captions if official subtitles are missing?",
                    default=True,
                )
                sub_format = Prompt.ask(
                    "Subtitle format", choices=["srt", "vtt", "best"], default="srt"
                )
                video_dir = get_default_video_dir()
                console.print(
                    f"[blue]Downloading subtitles ({sub_langs}) to [cyan]{video_dir}[/cyan]...[/blue]"
                )
                _ = report_subtitle_download(
                    url,
                    video_dir,
                    console,
                    langs=sub_langs,
                    auto_subs=auto_subs,
                    sub_format=sub_format,
                    cookies_from_browser=session_cookies_browser,
                    cookie_file=session_cookies_file,
                )
            elif sub_choice == "4":
                codec = Prompt.ask(
                    "Select audio format",
                    choices=["m4a", "opus", "mp3", "wav", "flac"],
                    default="m4a",
                )
                skip_sponsors = Confirm.ask(
                    "Skip sponsor and self-promotion segments using SponsorBlock?",
                    default=False,
                )
                music_dir = get_default_music_dir()
                console.print(
                    f"[blue]Starting audio download ({codec}) to [cyan]{music_dir}[/cyan]...[/blue]"
                )
                _ = download_audio(
                    url,
                    codec,
                    cookies_from_browser=session_cookies_browser,
                    cookie_file=session_cookies_file,
                    output_dir=music_dir,
                    sponsorblock=skip_sponsors,
                )

        elif choice == "2":
            url = prompt_for_url(console)
            do_download_interactive(url, None, console)

        elif choice == "3":
            url = prompt_for_url(console)
            codec = Prompt.ask(
                "Select audio format",
                choices=["m4a", "opus", "mp3", "wav", "flac"],
                default="m4a",
            )
            skip_sponsors = Confirm.ask(
                "Skip sponsor and self-promotion segments using SponsorBlock?",
                default=False,
            )
            music_dir = get_default_music_dir()
            console.print(
                f"[blue]Starting audio download ({codec}) to [cyan]{music_dir}[/cyan]...[/blue]"
            )
            _ = download_audio(
                url,
                codec,
                cookies_from_browser=session_cookies_browser,
                cookie_file=session_cookies_file,
                output_dir=music_dir,
                sponsorblock=skip_sponsors,
            )

        elif choice == "4":
            url = prompt_for_url(console)
            sub_langs = Prompt.ask(
                "Subtitle languages (comma-separated, e.g. 'en', 'ar', 'all')",
                default="en",
            )
            auto_subs = Confirm.ask(
                "Include auto-generated captions if official subtitles are missing?",
                default=True,
            )
            sub_format = Prompt.ask(
                "Subtitle format", choices=["srt", "vtt", "best"], default="srt"
            )
            video_dir = get_default_video_dir()
            console.print(
                f"[blue]Downloading subtitles ({sub_langs}) to [cyan]{video_dir}[/cyan]...[/blue]"
            )
            _ = report_subtitle_download(
                url,
                video_dir,
                console,
                langs=sub_langs,
                auto_subs=auto_subs,
                sub_format=sub_format,
                cookies_from_browser=session_cookies_browser,
                cookie_file=session_cookies_file,
            )

        elif choice == "5":
            info_file = Prompt.ask("Enter path to info.json file")
            if not os.path.exists(info_file):
                console.print(f"[bold red]File not found: {info_file}[/bold red]")
                continue
            video_dir = get_default_video_dir()
            console.print(
                f"[blue]Downloading to [cyan]{video_dir}[/cyan] using {info_file}...[/blue]"
            )
            _ = download_from_info_json(
                info_file,
                cookies_from_browser=session_cookies_browser,
                cookie_file=session_cookies_file,
                output_dir=video_dir,
            )

        elif choice == "6":
            source = Prompt.ask(
                "Choose a cookie source",
                choices=["browser", "file", "none"],
                default="browser",
            )
            if source == "none":
                session_cookies_browser = None
                session_cookies_file = None
                save_settings({"cookies_from_browser": None, "cookiefile": None})
                console.print("[bold green]✓ Cookies disabled (persisted)[/bold green]")
            elif source == "browser":
                console.print(
                    "[dim]chrome and edge encrypt cookies with App-Bound Encryption "
                    "on Windows and cannot be read; prefer firefox, brave, vivaldi, "
                    "opera, or chromium.[/dim]"
                )
                browser = Prompt.ask(
                    "Select browser to load cookies from (helps avoid 403 Forbidden errors)",
                    choices=["none", *SUPPORTED_BROWSERS],
                    default=session_cookies_browser or "none",
                )
                session_cookies_browser = None if browser == "none" else browser
                # Switching source kinds: clear the other one so the active
                # source shown in the menu stays unambiguous.
                session_cookies_file = None
                save_settings(
                    {
                        "cookies_from_browser": session_cookies_browser,
                        "cookiefile": None,
                    }
                )
                source_label = session_cookies_browser or "None"
                console.print(
                    f"[bold green]✓ Browser cookies source set to: {source_label} (persisted)[/bold green]"
                )
            else:
                raw_path = Prompt.ask(
                    "Path to cookies.txt",
                    default=session_cookies_file or "",
                ).strip()
                if not raw_path:
                    console.print(
                        "[bold red]❌ No path given. Re-run and pick 'none' to clear cookies.[/bold red]"
                    )
                    continue
                cookie_path = Path(os.path.expanduser(raw_path))
                if not cookie_path.is_file():
                    console.print(
                        f"[bold red]❌ File not found: {cookie_path}[/bold red]"
                    )
                    continue
                session_cookies_file = str(cookie_path)
                session_cookies_browser = None
                save_settings(
                    {"cookies_from_browser": None, "cookiefile": session_cookies_file}
                )
                console.print(
                    f"[bold green]✓ Cookies file set to: {session_cookies_file} (persisted)[/bold green]"
                )

        elif choice == "7":
            console.print("[yellow]Goodbye![/yellow]")
            break


# =====================================================================
# Typer Commands & Callbacks
# =====================================================================


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """
    Interactive yt-dlp interface. Run without arguments to launch the interactive menu.
    """
    if ctx.invoked_subcommand is None:
        run_interactive_menu()


@app.command(name="interactive")
def interactive_cmd() -> None:
    """
    Launch the interactive wizard.
    """
    run_interactive_menu()


@app.command()
def info(
    url: Annotated[str, Argument(help="The video URL to extract info from")],
    save: Annotated[
        bool,
        Option(
            "--save", help="Save info to a .info.json file in the current directory"
        ),
    ] = False,
    output: Annotated[
        str | None,
        Option("--output", "-o", help="Directory to save the .info.json file to"),
    ] = None,
    cookies_from_browser: Annotated[
        str | None,
        Option("--cookies-from-browser", "-b", help=BROWSER_OPTION_HELP),
    ] = None,
    cookies: Annotated[
        str | None,
        Option("--cookies", help=COOKIE_FILE_OPTION_HELP),
    ] = None,
    verbose: Annotated[
        bool, Option("--verbose", "-v", help="Show verbose output")
    ] = False,
) -> None:
    """
    Extract and display information about a video or playlist.
    """
    console = Console()
    url = parse_cli_url(url)
    if cookies:
        cookies = parse_cookie_file(cookies)
    with console.status("[bold blue]Fetching video metadata...[/bold blue]"):
        try:
            video_info = get_video_info(
                url,
                cookies_from_browser=cookies_from_browser,
                cookie_file=cookies,
                verbose=verbose,
            )
        except Exception as e:
            report_failure(f"Failed to extract info: {e}", console, "Error")
            raise typer.Exit(code=1) from e

    print_video_info(video_info, console)

    if save or output:
        sanitized = yt_dlp.YoutubeDL().sanitize_info(cast(Any, video_info))
        # Sanitize the file name only, never the directory: stripping characters
        # from a full path would mangle Windows separators.
        out_filename = "".join(
            c
            for c in f"{video_info.get('title', 'video')}.info.json"
            if c.isalnum() or c in " ._-"
        ).strip()
        out_dir = Path(output) if output else Path.cwd()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / out_filename

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(sanitized, f, indent=4)
        console.print(
            f"\n[bold green]✓[/bold green] Saved metadata to: [cyan]{out_path}[/cyan]"
        )


@app.command()
def download(
    url: Annotated[str | None, Argument(help="The video URL to download")] = None,
    info_json: Annotated[
        str | None,
        Option("--info-json", "-j", help="Path to info.json file to download from"),
    ] = None,
    output_dir: Annotated[
        str | None,
        Option(
            "--output-dir",
            "-o",
            help="Target directory for downloaded file (defaults to ~/Downloads/video)",
        ),
    ] = None,
    preset: Annotated[
        VideoPreset,
        Option(
            "--preset",
            "-p",
            help="Codec preset: 'universal' (H.264 + AAC, plays anywhere) or 'best' (highest quality, native container)",
        ),
    ] = VideoPreset.UNIVERSAL,
    max_height: Annotated[
        int | None,
        Option(
            "--res",
            help="Maximum resolution height (e.g. 720); picks the best available at or below it",
        ),
    ] = None,
    format_code: Annotated[
        str | None,
        Option(
            "--format",
            "-f",
            help="Raw yt-dlp format code or selector (e.g. '137+140'); cannot be combined with --preset/--res",
        ),
    ] = None,
    min_duration: Annotated[
        int | None,
        Option(
            "--min-duration",
            help="Skip videos shorter than this duration in seconds",
        ),
    ] = None,
    max_duration: Annotated[
        int | None,
        Option(
            "--max-duration",
            help="Skip videos longer than this duration in seconds",
        ),
    ] = None,
    cookies_from_browser: Annotated[
        str | None,
        Option("--cookies-from-browser", "-b", help=BROWSER_OPTION_HELP),
    ] = None,
    cookies: Annotated[
        str | None,
        Option("--cookies", help=COOKIE_FILE_OPTION_HELP),
    ] = None,
    sponsorblock: Annotated[
        bool,
        Option(
            "--sponsorblock",
            "-s",
            help="Skip sponsor and self-promotion segments using SponsorBlock",
        ),
    ] = False,
    write_subs: Annotated[
        bool,
        Option(
            "--subs",
            "-S",
            help="Download subtitles file (.srt) alongside the video",
        ),
    ] = False,
    embed_subs: Annotated[
        bool,
        Option(
            "--embed-subs",
            help="Embed subtitles directly into the video container",
        ),
    ] = False,
    auto_subs: Annotated[
        bool,
        Option(
            "--auto-subs/--no-auto-subs",
            help="Include auto-generated subtitles if official subtitles are not found",
        ),
    ] = True,
    sub_langs: Annotated[
        str,
        Option(
            "--sub-langs",
            "-l",
            help="Comma-separated subtitle languages (e.g. 'en', 'ar', 'all')",
        ),
    ] = "en",
    sub_format: Annotated[
        str,
        Option(
            "--sub-format",
            help="Preferred subtitle format (srt, vtt, best)",
        ),
    ] = "srt",
    verbose: Annotated[
        bool, Option("--verbose", "-v", help="Show verbose output")
    ] = False,
) -> None:
    """
    Download a video, playlist, or download using a saved info.json.
    """
    console = Console()

    if not url and not info_json:
        console.print(
            "[bold red]Error: You must provide either a URL or a --info-json file.[/bold red]"
        )
        raise typer.Exit(code=1)

    if max_height is not None and max_height <= 0:
        console.print("[bold red]Error: --res must be a positive number.[/bold red]")
        raise typer.Exit(code=1)

    if format_code and (preset is not VideoPreset.UNIVERSAL or max_height is not None):
        console.print(
            "[bold red]Error: --format cannot be combined with --preset or --res. "
            "Use --format on its own for full control over format selection.[/bold red]"
        )
        raise typer.Exit(code=1)

    if url:
        url = parse_cli_url(url)
    if cookies:
        cookies = parse_cookie_file(cookies)

    target_dir: Path = Path(output_dir) if output_dir else get_default_video_dir()

    opts: dict[str, Any] = {}

    # Setup formats
    if format_code:
        opts["format"] = format_code
    else:
        opts["format"] = make_video_format_spec(preset, max_height)
        if preset is VideoPreset.UNIVERSAL:
            opts["merge_output_format"] = "mp4"
            opts["remux_video"] = "mp4"

    # Setup duration filter
    if min_duration is not None or max_duration is not None:
        opts["match_filter"] = make_duration_filter(min_duration, max_duration)

    # Setup SponsorBlock
    if sponsorblock:
        opts["sponsorblock_skip"] = ["sponsor", "selfpromo"]

    # Setup Subtitles
    if write_subs or embed_subs:
        configure_subtitles(
            opts,
            write_subs=write_subs,
            embed_subs=embed_subs,
            auto_subs=auto_subs,
            sub_langs=sub_langs,
            sub_format=sub_format,
        )

    # Perform download
    if info_json:
        if not os.path.exists(info_json):
            console.print(f"[bold red]Error: File not found: {info_json}[/bold red]")
            raise typer.Exit(code=1)
        console.print(
            f"[blue]Starting download to [cyan]{target_dir}[/cyan] using info file: [cyan]{info_json}[/cyan][/blue]"
        )
        error_code = download_from_info_json(
            info_json,
            opts_override=opts,
            cookies_from_browser=cookies_from_browser,
            cookie_file=cookies,
            output_dir=target_dir,
            verbose=verbose,
        )
    else:
        if not url:
            console.print("[bold red]Error: Video URL is required.[/bold red]")
            raise typer.Exit(code=1)
        console.print(
            f"[blue]Starting download to [cyan]{target_dir}[/cyan] for: [cyan]{url}[/cyan][/blue]"
        )
        error_code = download_video(
            url,
            opts_override=opts,
            cookies_from_browser=cookies_from_browser,
            cookie_file=cookies,
            output_dir=target_dir,
            verbose=verbose,
        )

    if error_code:
        console.print(
            f"[bold red]❌ Download failed with error code: {error_code}[/bold red]"
        )
        raise typer.Exit(code=error_code)
    else:
        console.print("[bold green]✓ Download completed successfully![/bold green]")


@app.command()
def audio(
    url: Annotated[str, Argument(help="The video URL to extract audio from")],
    output_dir: Annotated[
        str | None,
        Option(
            "--output-dir",
            "-o",
            help="Target directory for downloaded audio (defaults to ~/Downloads/audio)",
        ),
    ] = None,
    codec: Annotated[
        str, Option("--codec", "-c", help="Audio codec (m4a, mp3, wav, flac, etc.)")
    ] = "m4a",
    subtitles: Annotated[
        bool,
        Option(
            "--subs",
            "-S",
            help="Download subtitles/lyrics alongside audio if available",
        ),
    ] = False,
    sub_langs: Annotated[
        str,
        Option(
            "--sub-langs",
            "-l",
            help="Comma-separated subtitle languages (e.g. 'en', 'ar', 'all')",
        ),
    ] = "en",
    auto_subs: Annotated[
        bool,
        Option(
            "--auto-subs/--no-auto-subs",
            help="Include auto-generated captions if official subtitles are missing",
        ),
    ] = True,
    cookies_from_browser: Annotated[
        str | None,
        Option("--cookies-from-browser", "-b", help=BROWSER_OPTION_HELP),
    ] = None,
    cookies: Annotated[
        str | None,
        Option("--cookies", help=COOKIE_FILE_OPTION_HELP),
    ] = None,
    sponsorblock: Annotated[
        bool,
        Option(
            "--sponsorblock",
            "-s",
            help="Skip sponsor and self-promotion segments using SponsorBlock",
        ),
    ] = False,
    verbose: Annotated[
        bool, Option("--verbose", "-v", help="Show verbose output")
    ] = False,
) -> None:
    """
    Extract and download audio from a video.
    """
    console = Console()
    url = parse_cli_url(url)
    if cookies:
        cookies = parse_cookie_file(cookies)
    target_dir = Path(output_dir) if output_dir else get_default_music_dir()
    console.print(
        f"[blue]Extracting audio ({codec}) to [cyan]{target_dir}[/cyan] from: [cyan]{url}[/cyan][/blue]"
    )
    opts: dict[str, Any] = {}
    if subtitles:
        configure_subtitles(
            opts,
            write_subs=True,
            embed_subs=False,
            auto_subs=auto_subs,
            sub_langs=sub_langs,
            sub_format="srt",
        )

    error_code = download_audio(
        url,
        format_codec=codec,
        cookies_from_browser=cookies_from_browser,
        cookie_file=cookies,
        output_dir=target_dir,
        opts_override=opts if opts else None,
        sponsorblock=sponsorblock,
        verbose=verbose,
    )
    if error_code:
        console.print("[bold red]❌ Audio extraction failed![/bold red]")
        raise typer.Exit(code=error_code)
    else:
        console.print(
            "[bold green]✓ Audio extraction completed successfully![/bold green]"
        )


@app.command(name="subs")
def subs(
    url: Annotated[str, Argument(help="The video URL to download subtitles from")],
    output_dir: Annotated[
        str | None,
        Option(
            "--output-dir",
            "-o",
            help="Target directory for downloaded subtitles (defaults to ~/Downloads/video)",
        ),
    ] = None,
    sub_langs: Annotated[
        str,
        Option(
            "--sub-langs",
            "-l",
            help="Comma-separated subtitle languages (e.g. 'en', 'ar', 'all')",
        ),
    ] = "en",
    auto_subs: Annotated[
        bool,
        Option(
            "--auto-subs/--no-auto-subs",
            help="Include auto-generated subtitles if official not available",
        ),
    ] = True,
    sub_format: Annotated[
        str,
        Option("--sub-format", "-f", help="Subtitle format (srt, vtt, best)"),
    ] = "srt",
    cookies_from_browser: Annotated[
        str | None,
        Option("--cookies-from-browser", "-b", help=BROWSER_OPTION_HELP),
    ] = None,
    cookies: Annotated[
        str | None,
        Option("--cookies", help=COOKIE_FILE_OPTION_HELP),
    ] = None,
    verbose: Annotated[
        bool, Option("--verbose", "-v", help="Show verbose output")
    ] = False,
) -> None:
    """
    Download subtitles only without downloading the video or audio media.
    """
    console = Console()
    url = parse_cli_url(url)
    if cookies:
        cookies = parse_cookie_file(cookies)
    target_dir = Path(output_dir) if output_dir else get_default_video_dir()
    console.print(
        f"[blue]Downloading subtitles ({sub_langs}, {sub_format}) to [cyan]{target_dir}[/cyan] from: [cyan]{url}[/cyan][/blue]"
    )
    error_code = report_subtitle_download(
        url,
        target_dir,
        console,
        langs=sub_langs,
        auto_subs=auto_subs,
        sub_format=sub_format,
        cookies_from_browser=cookies_from_browser,
        cookie_file=cookies,
        verbose=verbose,
    )
    if error_code:
        raise typer.Exit(code=error_code)


if __name__ == "__main__":
    app()
