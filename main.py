import json
import os
from typing import Optional

import typer
import yt_dlp
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import IntPrompt, Prompt, Confirm
from rich.table import Table
from typer import Argument, Option, Typer

app = Typer(
    name="yt-dlp-cli",
    help="An interactive and feature-rich CLI wrapper for yt-dlp.",
    no_args_is_help=False,
)

# Global session variable for browser cookies in interactive mode
SESSION_COOKIES_BROWSER = None

# =====================================================================
# Custom Logger and Progress Tracker
# =====================================================================


class MyLogger:
    """Custom logger to pipe yt-dlp output to Rich formatting."""

    def __init__(self, verbose: bool = False):
        self.console = Console()
        self.verbose = verbose

    def debug(self, msg: str):
        # yt-dlp outputs debug and info messages through debug()
        if msg.startswith("[debug] "):
            if self.verbose:
                self.console.print(f"[grey50]{msg}[/grey50]")
        else:
            self.info(msg)

    def info(self, msg: str):
        if self.verbose:
            self.console.print(f"[cyan]{msg}[/cyan]")

    def warning(self, msg: str):
        self.console.print(f"[yellow]⚠️  Warning: {msg}[/yellow]")

    def error(self, msg: str):
        self.console.print(f"[bold red]❌ Error: {msg}[/bold red]")


class DownloadTracker:
    """Manages a beautiful, real-time Rich progress bar for downloads."""

    def __init__(self):
        self.progress = None
        self.task_id = None
        self.current_filename = None

    def hook(self, d: dict):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            filename = d.get("filename", "Unknown File")
            display_name = os.path.basename(filename)

            if not self.progress:
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

            if filename != self.current_filename:
                # Update task description and total for new file (e.g. video merge or audio extraction)
                self.current_filename = filename
                display_name = os.path.basename(filename)
                self.progress.update(
                    self.task_id,
                    description=f"Downloading {display_name[:30]}...",
                    total=total,
                    completed=downloaded,
                )
            else:
                self.progress.update(self.task_id, completed=downloaded, total=total)

        elif d["status"] == "finished":
            if self.progress:
                self.progress.stop()
                self.progress = None
                self.task_id = None
            filename = d.get("filename", "Unknown File")
            display_name = os.path.basename(filename)
            rich_console = Console()
            rich_console.print(
                f"[bold green]✓[/bold green] Finished downloading: [cyan]{display_name}[/cyan]"
            )


# =====================================================================

# Format Selectors and Filters
# =====================================================================


def format_selector(ctx: dict):
    """
    Select the best video and the best audio that won't result in an mkv.
    (Custom format selector example)
    """
    # formats are already sorted worst to best
    formats = ctx.get("formats")[::-1]

    # Find best video
    try:
        best_video = next(
            f
            for f in formats
            if f.get("vcodec") != "none" and f.get("acodec") == "none"
        )
    except StopIteration:
        # Fallback to any best video
        best_video = next((f for f in formats if f.get("vcodec") != "none"), None)

    if not best_video:
        return

    # Find compatible audio extension (mp4 -> m4a, webm -> webm)
    video_ext = best_video.get("ext")
    audio_ext = {"mp4": "m4a", "webm": "webm"}.get(video_ext, "m4a")

    try:
        best_audio = next(
            f
            for f in formats
            if (
                f.get("acodec") != "none"
                and f.get("vcodec") == "none"
                and f.get("ext") == audio_ext
            )
        )
    except StopIteration:
        # Fallback to any audio
        best_audio = next((f for f in formats if f.get("acodec") != "none"), None)

    if not best_audio:
        yield best_video
        return

    yield {
        "format_id": f"{best_video['format_id']}+{best_audio['format_id']}",
        "ext": best_video["ext"],
        "requested_formats": [best_video, best_audio],
        "protocol": f"{best_video.get('protocol', '')}+{best_audio.get('protocol', '')}",
    }


def get_interactive_format_choices(info: dict):
    """
    Parses formats list from video info metadata and compiles a list of
    clean, user-friendly download options (e.g. resolutions and audio-only).
    """
    formats = info.get("formats", [])

    choices = []
    resolutions = {}  # resolution string -> best format dict
    audio_formats = []

    for f in formats:
        vcodec = f.get("vcodec", "none")
        acodec = f.get("acodec", "none")

        if vcodec != "none":
            height = f.get("height")
            if height:
                res_str = f"{height}p"
                resolutions[res_str] = f
        elif acodec != "none" and vcodec == "none":
            audio_formats.append(f)

    # Sort resolutions (highest first)
    sorted_res = sorted(
        resolutions.keys(),
        key=lambda x: int(x[:-1]) if x[:-1].isdigit() else 0,
        reverse=True,
    )

    # Build choices list
    choices.append(("🚀 Best Quality (Auto)", "bestvideo+bestaudio/best", None))

    # Video choices
    for res in sorted_res:
        f = resolutions[res]
        fid = f.get("format_id")
        ext = f.get("ext")
        acodec = f.get("acodec", "none")

        size_bytes = f.get("filesize") or f.get("filesize_approx")
        size_str = f" (~{size_bytes / (1024 * 1024):.1f} MB)" if size_bytes else ""

        if acodec != "none":
            choices.append((f"📺 Video: {res} ({ext}){size_str}", fid, ext))
        else:
            choices.append(
                (
                    f"📺 Video: {res} ({ext}) + Best Audio{size_str}",
                    f"{fid}+bestaudio/best",
                    ext,
                )
            )

    # Audio choices (Keep top 3 audio formats)
    for af in audio_formats[-3:]:
        fid = af.get("format_id")
        ext = af.get("ext")
        abr = af.get("abr")
        abr_str = f" @ {abr}kbps" if abr else ""
        size_bytes = af.get("filesize") or af.get("filesize_approx")
        size_str = f" (~{size_bytes / (1024 * 1024):.1f} MB)" if size_bytes else ""
        choices.append((f"🎵 Audio Only: {ext}{abr_str}{size_str}", fid, None))

    return choices


def get_universal_mp4_choices(info: dict):
    """
    Finds available resolutions that support H.264 (avc1) video codec
    and formats them as user-friendly options merged with AAC audio.
    """
    formats = info.get("formats", [])
    choices = []
    resolutions = {}

    # H.264 codec in YouTube starts with avc1
    for f in formats:
        vcodec = f.get("vcodec", "none")

        if vcodec != "none" and vcodec.startswith("avc1"):
            height = f.get("height")
            if height:
                res_str = f"{height}p"
                resolutions[res_str] = f

    # Sort resolutions (highest first)
    sorted_res = sorted(
        resolutions.keys(),
        key=lambda x: int(x[:-1]) if x[:-1].isdigit() else 0,
        reverse=True,
    )

    choices.append(
        (
            "🚀 Best Universal Quality (Auto H.264 + AAC)",
            "bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        )
    )

    for res in sorted_res:
        f = resolutions[res]
        fid = f.get("format_id")
        size_bytes = f.get("filesize") or f.get("filesize_approx")
        size_str = f" (~{size_bytes / (1024 * 1024):.1f} MB)" if size_bytes else ""

        choices.append(
            (
                f"📺 Video: {res} (H.264) + AAC Audio{size_str}",
                f"{fid}+bestaudio[ext=m4a]/best[ext=mp4]/best",
            )
        )

    return choices


def make_duration_filter(min_len: Optional[int] = None, max_len: Optional[int] = None):
    """Generates a duration matching filter callback."""

    def duration_filter(info: dict, *, incomplete: bool):
        duration = info.get("duration")
        if duration:
            if min_len is not None and duration < min_len:
                return f"The video is too short ({duration}s < {min_len}s)"
            if max_len is not None and duration > max_len:
                return f"The video is too long ({duration}s > {max_len}s)"
        return None

    return duration_filter


# =====================================================================
# API / Core Functions
# =====================================================================


def get_video_info(
    url: str, cookies_from_browser: Optional[str] = None, verbose: bool = False
) -> dict:
    """Extract video metadata without downloading it."""
    ydl_opts = {
        "logger": MyLogger(verbose=verbose),
        "quiet": not verbose,
    }
    if cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_from_browser,)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def download_video(
    url: str,
    opts_override: Optional[dict] = None,
    cookies_from_browser: Optional[str] = None,
    verbose: bool = False,
) -> int:
    """Download a video with optional custom configurations."""
    tracker = DownloadTracker()
    ydl_opts = {
        "logger": MyLogger(verbose=verbose),
        "progress_hooks": [tracker.hook],
        "quiet": True,
    }

    if cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_from_browser,)

    if opts_override:
        ydl_opts.update(opts_override)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.download([url])
    except Exception as e:
        Console().print(f"[bold red]❌ Download error: {e}[/bold red]")
        return 1


def download_audio(
    url: str,
    format_codec: str = "m4a",
    cookies_from_browser: Optional[str] = None,
    sponsorblock: bool = False,
    verbose: bool = False,
) -> int:
    """Download and extract audio format only."""
    opts = {
        "format": "m4a/bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": format_codec,
            }
        ],
    }
    if sponsorblock:
        opts["sponsorblock_skip"] = ["sponsor", "selfpromo"]
        
    return download_video(
        url,
        opts_override=opts,
        cookies_from_browser=cookies_from_browser,
        verbose=verbose,
    )


def download_from_info_json(
    info_file: str,
    opts_override: Optional[dict] = None,
    cookies_from_browser: Optional[str] = None,
    verbose: bool = False,
) -> int:
    """Download video using an existing info.json file."""
    tracker = DownloadTracker()
    ydl_opts = {
        "logger": MyLogger(verbose=verbose),
        "progress_hooks": [tracker.hook],
        "quiet": True,
    }

    if cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_from_browser,)

    if opts_override:
        ydl_opts.update(opts_override)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.download_with_info_file(info_file)
    except Exception as e:
        Console().print(f"[bold red]❌ Download error: {e}[/bold red]")
        return 1


# =====================================================================
# UI Printing & Menus
# =====================================================================


def print_video_info(info: dict, console: Console):
    """Print video/playlist metadata beautifully using Rich panels and tables."""
    _type = info.get("_type", "video")

    if _type == "playlist":
        title = info.get("title", "Unknown Playlist")
        uploader = info.get("uploader") or info.get("uploader_id") or "Unknown"
        entries = info.get("entries", [])
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
                    f"{dur_secs // 60}:{dur_secs % 60:02d}" if dur_secs else "Unknown"
                )
                table.add_row(str(idx), entry.get("title", "Unknown"), duration)

        console.print(table)
        if video_count > 10:
            console.print(f"[dim]... and {video_count - 10} more videos[/dim]")
    else:
        title = info.get("title", "Unknown Title")
        uploader = info.get("uploader", "Unknown Uploader")
        duration_secs = info.get("duration")
        duration = (
            f"{duration_secs // 60}:{duration_secs % 60:02d}"
            if duration_secs
            else "Unknown"
        )
        views = (
            f"{info.get('view_count', 0):,}" if info.get("view_count") else "Unknown"
        )
        upload_date = info.get("upload_date", "Unknown")
        if len(upload_date) == 8:
            upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

        description = info.get("description", "")
        desc_lines = description.split("\n")
        desc_summary = "\n".join(desc_lines[:3])
        if len(desc_lines) > 3 or len(desc_summary) > 200:
            desc_summary = desc_summary[:200] + "..."

        console.print(
            Panel(
                f"[bold cyan]{title}[/bold cyan]\n"
                f"[bold]Channel:[/bold] {uploader}\n"
                f"[bold]Duration:[/bold] {duration} | [bold]Views:[/bold] {views} | [bold]Uploaded:[/bold] {upload_date}\n\n"
                f"[dim]{desc_summary}[/dim]",
                title="Video Metadata",
                expand=False,
            )
        )

        formats = info.get("formats", [])
        table = Table(title="Available Formats", box=box.ROUNDED)
        table.add_column("Format ID", style="cyan")
        table.add_column("Ext", style="green")
        table.add_column("Resolution", style="yellow")
        table.add_column("Codec", style="magenta")
        table.add_column("Size", style="blue")

        # Show the 15 formats (worst to best)
        for f in formats[-15:]:
            fid = f.get("format_id", "N/A")
            ext = f.get("ext", "N/A")
            res = f.get("resolution") or f"{f.get('width', '?')}x{f.get('height', '?')}"
            if res == "?x?":
                res = "audio only" if f.get("vcodec") == "none" else "N/A"

            vcodec = f.get("vcodec", "none")
            acodec = f.get("acodec", "none")
            codec = f"V:{vcodec.split('.')[0]} A:{acodec.split('.')[0]}"

            size_bytes = f.get("filesize") or f.get("filesize_approx")
            if size_bytes:
                size_mb = size_bytes / (1024 * 1024)
                size = f"{size_mb:.1f} MB"
            else:
                size = "Unknown"

            table.add_row(fid, ext, res, codec, size)

        console.print(table)


def do_download_interactive(
    url: str, info: Optional[dict] = None, console: Optional[Console] = None
):
    """Sub-menu to choose download settings interactively."""
    global SESSION_COOKIES_BROWSER
    if not console:
        console = Console()
    if not info:
        with console.status("[bold blue]Fetching metadata...[/bold blue]"):
            try:
                info = get_video_info(url, cookies_from_browser=SESSION_COOKIES_BROWSER)
            except Exception as e:
                console.print(
                    f"[bold red]Error: Failed to fetch metadata: {e}[/bold red]"
                )
                return

    menu_table = Table(show_header=False, box=box.SIMPLE, border_style="dim magenta")
    menu_table.add_row(
        "[bold cyan]1.[/bold cyan] 🚀 Best Quality",
        "[cyan]Default Video + Audio combined (Auto)[/cyan]",
    )
    menu_table.add_row(
        "[bold green]2.[/bold green] 📱 Universal MP4",
        "[green]Force standard H.264/AAC codecs (Plays anywhere)[/green]",
    )
    menu_table.add_row(
        "[bold magenta]3.[/bold magenta] 🎨 Interactive Format Selector",
        "[magenta]Choose video resolution or audio track[/magenta]",
    )
    menu_table.add_row(
        "[bold bright_yellow]4.[/bold bright_yellow] 🎯 Choose Specific Format ID",
        "[bright_yellow]Manually input a format code[/bright_yellow]",
    )
    menu_table.add_row(
        "[bold orange3]5.[/bold orange3] ⏱️  Filter by Duration",
        "[orange3]Filter downloads based on duration limits[/orange3]",
    )
    menu_table.add_row(
        "[bold red]6.[/bold red] ↩ Back", "[red]Return to main menu[/red]"
    )

    console.print("\n")
    console.print(
        Panel(
            menu_table,
            title=f"[bold magenta]📥 Download Settings for: {info.get('title', 'Video')[:50]}...[/bold magenta]",
            border_style="magenta",
            expand=False,
        )
    )

    choice = Prompt.ask(
        "Select download type", choices=["1", "2", "3", "4", "5", "6"], default="1"
    )

    opts = {}
    if choice == "1":
        pass
    elif choice == "2":
        if info.get("_type") == "playlist":
            console.print(
                "[yellow]⚠️ Playlist downloads will use the best compatible universal quality automatically.[/yellow]"
            )
            opts["format"] = (
                "bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            )
            opts["merge_output_format"] = "mp4"
        else:
            choices = get_universal_mp4_choices(info)
            console.print("\n[bold]Available Universal H.264 + AAC resolutions:[/bold]")
            for idx, (display, code) in enumerate(choices, 1):
                console.print(f"  [cyan]{idx}[/cyan]. {display}")
            console.print(f"  [cyan]{len(choices) + 1}[/cyan]. [red]↩ Cancel[/red]")

            while True:
                choice_idx = IntPrompt.ask("Select resolution number", default=1)
                if 1 <= choice_idx <= len(choices) + 1:
                    break
                console.print(
                    f"[bold red]❌ Invalid selection. Please enter a number between 1 and {len(choices) + 1}.[/bold red]"
                )

            if choice_idx == len(choices) + 1:
                return
            selected_choice = choices[choice_idx - 1]
            opts["format"] = selected_choice[1]
            opts["merge_output_format"] = "mp4"
    elif choice == "3":
        if info.get("_type") == "playlist":
            console.print(
                "[yellow]⚠️ Interactive format selection is only supported for single videos. Downloading best quality instead.[/yellow]"
            )
        else:
            choices = get_interactive_format_choices(info)
            console.print("\n[bold]Available formats for this video:[/bold]")
            for idx, (display, code, merge_ext) in enumerate(choices, 1):
                console.print(f"  [cyan]{idx}[/cyan]. {display}")
            console.print(f"  [cyan]{len(choices) + 1}[/cyan]. [red]↩ Cancel[/red]")

            while True:
                choice_idx = IntPrompt.ask("Select format number", default=1)
                if 1 <= choice_idx <= len(choices) + 1:
                    break
                console.print(
                    f"[bold red]❌ Invalid selection. Please enter a number between 1 and {len(choices) + 1}.[/bold red]"
                )

            if choice_idx == len(choices) + 1:
                return
            selected_choice = choices[choice_idx - 1]
            opts["format"] = selected_choice[1]
            if selected_choice[2]:
                opts["merge_output_format"] = selected_choice[2]
                opts["remux_video"] = selected_choice[2]
    elif choice == "4":
        format_code = Prompt.ask("Enter Format ID (e.g. '137+140', '22', or 'worst')")
        opts["format"] = format_code
    elif choice == "5":
        min_sec = IntPrompt.ask(
            "Enter minimum duration in seconds (0 for no limit)", default=60
        )
        max_sec = IntPrompt.ask(
            "Enter maximum duration in seconds (0 for no limit)", default=0
        )
        min_sec = min_sec if min_sec > 0 else None
        max_sec = max_sec if max_sec > 0 else None
        opts["match_filter"] = make_duration_filter(min_sec, max_sec)
    elif choice == "6":
        return

    # Ask about SponsorBlock skipping
    skip_sponsors = Confirm.ask(
        "Skip sponsor and self-promotion segments using SponsorBlock?", default=False
    )
    if skip_sponsors:
        opts["sponsorblock_skip"] = ["sponsor", "selfpromo"]

    console.print("[blue]Starting download...[/blue]")
    error_code = download_video(
        url, opts_override=opts, cookies_from_browser=SESSION_COOKIES_BROWSER
    )
    if error_code:
        console.print("[bold red]❌ Download failed![/bold red]")
    else:
        console.print("[bold green]✓ Download completed successfully![/bold green]")


def run_interactive_menu():
    """Runs the main CLI prompt-driven dashboard loop."""
    global SESSION_COOKIES_BROWSER
    console = Console()
    while True:
        cookies_status = (
            f"[bold green]{SESSION_COOKIES_BROWSER}[/bold green]"
            if SESSION_COOKIES_BROWSER
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
            "[magenta]Extract and convert audio tracks (MP3/M4A)[/magenta]",
        )
        menu_table.add_row(
            "[bold bright_yellow]4.[/bold bright_yellow] 📄 Download from info.json",
            "[bright_yellow]Download using cached info.json metadata[/bright_yellow]",
        )
        menu_table.add_row(
            "[bold blue]5.[/bold blue] 🍪 Set Browser Cookies Source",
            f"[blue]Load cookies from browser (Active: {cookies_status})[/blue]",
        )
        menu_table.add_row(
            "[bold red]6.[/bold red] ❌ Exit", "[red]Close the application[/red]"
        )

        console.print("\n")
        console.print(
            Panel(
                menu_table,
                title="[bold cyan]⚡ yt-dlp Interactive Dashboard ⚡[/bold cyan]",
                border_style="cyan",
                expand=False,
            )
        )

        choice = Prompt.ask(
            "Select an option", choices=["1", "2", "3", "4", "5", "6"], default="1"
        )

        if choice == "1":
            url = Prompt.ask("Enter video URL")
            with console.status("[bold blue]Fetching metadata...[/bold blue]"):
                try:
                    info = get_video_info(
                        url, cookies_from_browser=SESSION_COOKIES_BROWSER
                    )
                except Exception as e:
                    console.print(f"[bold red]Error: Extraction failed: {e}[/bold red]")
                    continue

            print_video_info(info, console)

            console.print("\n[bold]Submenu Actions:[/bold]")
            console.print("1. [green]💾 Save metadata to info.json[/green]")
            console.print("2. [green]📥 Download this video[/green]")
            console.print("3. [red]↩ Back to main menu[/red]")
            sub_choice = Prompt.ask(
                "Select action", choices=["1", "2", "3"], default="1"
            )

            if sub_choice == "1":
                title_clean = "".join(
                    [
                        c
                        for c in info.get("title", "video")
                        if c.isalnum() or c in " ._-"
                    ]
                ).strip()
                filename = f"{title_clean}.info.json"
                with open(filename, "w", encoding="utf-8") as f:
                    json.dump(yt_dlp.YoutubeDL().sanitize_info(info), f, indent=4)
                console.print(
                    f"[bold green]✓[/bold green] Metadata saved to [cyan]{filename}[/cyan]"
                )
            elif sub_choice == "2":
                do_download_interactive(url, info, console)

        elif choice == "2":
            url = Prompt.ask("Enter video URL")
            do_download_interactive(url, None, console)

        elif choice == "3":
            url = Prompt.ask("Enter video URL")
            codec = Prompt.ask(
                "Select audio format",
                choices=["m4a", "mp3", "wav", "flac"],
                default="m4a",
            )
            console.print(f"[blue]Starting audio download ({codec})...[/blue]")
            download_audio(url, codec, cookies_from_browser=SESSION_COOKIES_BROWSER)

        elif choice == "4":
            info_file = Prompt.ask("Enter path to info.json file")
            if not os.path.exists(info_file):
                console.print(f"[bold red]File not found: {info_file}[/bold red]")
                continue
            console.print(f"[blue]Downloading using {info_file}...[/blue]")
            download_from_info_json(
                info_file, cookies_from_browser=SESSION_COOKIES_BROWSER
            )

        elif choice == "5":
            browser = Prompt.ask(
                "Select browser to load cookies from (helps avoid 403 Forbidden errors)",
                choices=[
                    "none",
                    "chrome",
                    "firefox",
                    "edge",
                    "brave",
                    "safari",
                    "opera",
                ],
                default="none",
            )
            SESSION_COOKIES_BROWSER = None if browser == "none" else browser
            console.print(
                f"[bold green]✓ Session browser cookies set to: {SESSION_COOKIES_BROWSER}[/bold green]"
            )

        elif choice == "6":
            console.print("[yellow]Goodbye![/yellow]")
            break


# =====================================================================
# Typer Commands & Callbacks
# =====================================================================


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """
    Interactive yt-dlp interface. Run without arguments to launch the interactive menu.
    """
    if ctx.invoked_subcommand is None:
        run_interactive_menu()


@app.command(name="interactive")
def interactive_cmd():
    """
    Launch the interactive wizard.
    """
    run_interactive_menu()


@app.command()
def info(
    url: str = Argument(..., help="The video URL to extract info from"),
    save: bool = Option(False, "--save", "-s", help="Save info to a .info.json file"),
    output: Optional[str] = Option(
        None, "--output", "-o", help="Custom output filename for the JSON"
    ),
    cookies_from_browser: Optional[str] = Option(
        None,
        "--cookies-from-browser",
        "-b",
        help="Extract cookies from browser (chrome, firefox, edge, brave, safari, opera)",
    ),
    verbose: bool = Option(False, "--verbose", "-v", help="Show verbose output"),
):
    """
    Extract and display information about a video or playlist.
    """
    console = Console()
    with console.status("[bold blue]Fetching video metadata...[/bold blue]"):
        try:
            video_info = get_video_info(
                url, cookies_from_browser=cookies_from_browser, verbose=verbose
            )
        except Exception as e:
            console.print(f"[bold red]Error: Failed to extract info: {e}[/bold red]")
            raise typer.Exit(code=1)

    print_video_info(video_info, console)

    if save or output:
        sanitized = yt_dlp.YoutubeDL().sanitize_info(video_info)
        out_filename = output or f"{video_info.get('title', 'video')}.info.json"
        out_filename = "".join(
            [c for c in out_filename if c.isalnum() or c in " ._-"]
        ).strip()

        with open(out_filename, "w", encoding="utf-8") as f:
            json.dump(sanitized, f, indent=4)
        console.print(
            f"\n[bold green]✓[/bold green] Saved metadata to: [cyan]{out_filename}[/cyan]"
        )


@app.command()
def download(
    url: Optional[str] = Argument(None, help="The video URL to download"),
    info_json: Optional[str] = Option(
        None, "--info-json", "-j", help="Path to info.json file to download from"
    ),
    format_code: Optional[str] = Option(
        None, "--format", "-f", help="Format code (e.g. 'bestvideo+bestaudio')"
    ),
    custom_format: bool = Option(
        False,
        "--custom-selector",
        "-c",
        help="Use custom format selector to prefer MP4/WebM and avoid MKV",
    ),
    audio_only: bool = Option(
        False, "--audio", "-a", help="Download and extract audio only"
    ),
    audio_codec: str = Option(
        "m4a", "--audio-format", help="Audio format codec to extract (e.g. m4a, mp3)"
    ),
    min_duration: Optional[int] = Option(
        None, "--min-duration", help="Skip videos shorter than this duration in seconds"
    ),
    max_duration: Optional[int] = Option(
        None, "--max-duration", help="Skip videos longer than this duration in seconds"
    ),
    cookies_from_browser: Optional[str] = Option(
        None,
        "--cookies-from-browser",
        "-b",
        help="Extract cookies from browser (chrome, firefox, edge, brave, safari, opera)",
    ),
    sponsorblock: bool = Option(
        False,
        "--sponsorblock",
        "-s",
        help="Skip sponsor and self-promotion segments using SponsorBlock",
    ),
    verbose: bool = Option(False, "--verbose", "-v", help="Show verbose output"),
):
    """
    Download a video, playlist, or download using a saved info.json.
    """
    console = Console()

    if not url and not info_json:
        console.print(
            "[bold red]Error: You must provide either a URL or a --info-json file.[/bold red]"
        )
        raise typer.Exit(code=1)

    opts = {}

    # Setup formats
    if audio_only:
        opts["format"] = "m4a/bestaudio/best"
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_codec,
            }
        ]
    elif custom_format:
        opts["format"] = format_selector
    elif format_code:
        opts["format"] = format_code

    # Setup duration filter
    if min_duration is not None or max_duration is not None:
        opts["match_filter"] = make_duration_filter(min_duration, max_duration)

    # Setup SponsorBlock
    if sponsorblock:
        opts["sponsorblock_skip"] = ["sponsor", "selfpromo"]

    # Perform download
    if info_json:
        if not os.path.exists(info_json):
            console.print(f"[bold red]Error: File not found: {info_json}[/bold red]")
            raise typer.Exit(code=1)
        console.print(
            f"[blue]Starting download using info file: [cyan]{info_json}[/cyan][/blue]"
        )
        error_code = download_from_info_json(
            info_json,
            opts_override=opts,
            cookies_from_browser=cookies_from_browser,
            verbose=verbose,
        )
    else:
        console.print(f"[blue]Starting download for: [cyan]{url}[/cyan][/blue]")
        error_code = download_video(
            url,
            opts_override=opts,
            cookies_from_browser=cookies_from_browser,
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
    url: str = Argument(..., help="The video URL to extract audio from"),
    codec: str = Option(
        "m4a", "--codec", "-c", help="Audio codec (m4a, mp3, wav, flac, etc.)"
    ),
    cookies_from_browser: Optional[str] = Option(
        None,
        "--cookies-from-browser",
        "-b",
        help="Extract cookies from browser (chrome, firefox, edge, brave, safari, opera)",
    ),
    sponsorblock: bool = Option(
        False,
        "--sponsorblock",
        "-s",
        help="Skip sponsor and self-promotion segments using SponsorBlock",
    ),
    verbose: bool = Option(False, "--verbose", "-v", help="Show verbose output"),
):
    """
    Extract and download audio from a video.
    """
    console = Console()
    console.print(f"[blue]Extracting audio ({codec}) from: [cyan]{url}[/cyan][/blue]")
    error_code = download_audio(
        url,
        format_codec=codec,
        cookies_from_browser=cookies_from_browser,
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


if __name__ == "__main__":
    app()
