"""Typer application and CLI commands."""

import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any, cast

import typer
import yt_dlp
from rich.console import Console
from typer import Argument, Option, Typer

from ydlx.cookies import (
    BROWSER_OPTION_HELP,
    COOKIE_FILE_OPTION_HELP,
    parse_browser_spec,
    parse_cookie_file,
)
from ydlx.dashboard import run_interactive_menu
from ydlx.downloads import download_audio, download_from_info_json, download_video
from ydlx.errors import MyLogger, report_failure
from ydlx.formats import VideoPreset, make_duration_filter, make_video_format_spec
from ydlx.info import get_video_info, print_video_info
from ydlx.paths import get_default_music_dir, get_default_video_dir
from ydlx.sponsorblock import maybe_configure_sponsorblock
from ydlx.subtitles import (
    DEFAULT_SUB_FORMAT,
    configure_requested_subtitles,
    parse_sub_langs,
    report_subtitle_download,
    resolvable_sub_langs,
)
from ydlx.urls import parse_cli_url

# Reconfigure stdout/stderr to support UTF-8 characters (like Arabic and emojis) on Windows
if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "reconfigure"):
        cast(Any, sys.stdout).reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        cast(Any, sys.stderr).reconfigure(encoding="utf-8")

app = Typer(
    name="ydlx",
    help="An interactive and feature-rich CLI wrapper and dashboard for yt-dlp.",
    no_args_is_help=False,
)

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
    # The flag is text; yt-dlp wants a tuple. Parse once, here at the boundary.
    browser_spec = (
        parse_browser_spec(cookies_from_browser) if cookies_from_browser else None
    )
    with console.status("[bold blue]Fetching video metadata...[/bold blue]"):
        info_logger = MyLogger(verbose=verbose)
        try:
            video_info = get_video_info(
                url,
                browser_spec,
                cookies,
                verbose,
                info_logger,
            )
        except Exception as e:
            report_failure(
                str(e), console, "Extraction failed", info_logger.reported_errors
            )
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
            help="Skip sponsor and self-promotion segments using SponsorBlock (YouTube only)",
        ),
    ] = False,
    write_subs: Annotated[
        bool,
        Option(
            "--subs",
            "-S",
            help="Save subtitles as separate files alongside the video",
        ),
    ] = False,
    embed_subs: Annotated[
        bool,
        Option(
            "--embed-subs",
            help="Embed subtitles inside the video file (no separate files unless -S is also passed)",
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
            help="Comma-separated subtitle languages (e.g. 'en', 'pt-BR', 'all')",
        ),
    ] = "en",
    sub_format: Annotated[
        str,
        Option(
            "--sub-format",
            help="Sidecar format (srt, vtt, best); embedded subtitles always use vtt",
        ),
    ] = DEFAULT_SUB_FORMAT,
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
    # The flag is text; yt-dlp wants a tuple. Parse once, here at the boundary.
    browser_spec = (
        parse_browser_spec(cookies_from_browser) if cookies_from_browser else None
    )

    target_dir: Path = Path(output_dir) if output_dir else get_default_video_dir()
    if info_json and not os.path.exists(info_json):
        console.print(f"[bold red]Error: File not found: {info_json}[/bold red]")
        raise typer.Exit(code=1)

    opts: dict[str, Any] = {}
    download_info: dict[str, Any] | None = None

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

    # Setup SponsorBlock (YouTube only). The info.json path peeks at the file's
    # extractor key so a non-YouTube metadata file skips the lookup instead of
    # firing a useless API call; an unreadable file falls back to the URL check.
    if sponsorblock:
        sb_info = download_info
        if sb_info is None and info_json:
            try:
                with open(info_json, encoding="utf-8") as _sb_file:
                    _loaded = json.load(_sb_file)
                sb_info = _loaded if isinstance(_loaded, dict) else None
            except (OSError, json.JSONDecodeError):
                sb_info = None
        if sb_info is not None:
            maybe_configure_sponsorblock(opts, console, info=sb_info)
        else:
            maybe_configure_sponsorblock(opts, console, url=url)

    # Setup Subtitles
    if write_subs or embed_subs:
        language_tokens = resolvable_sub_langs(parse_sub_langs(sub_langs))
        if language_tokens is not None:
            if info_json:
                try:
                    with open(info_json, encoding="utf-8") as info_file:
                        loaded_info = json.load(info_file)
                    if isinstance(loaded_info, dict):
                        download_info = loaded_info
                except (OSError, json.JSONDecodeError) as error:
                    report_failure(str(error), console, "Subtitle metadata error")
            elif url:
                sub_logger = MyLogger(verbose=verbose)
                try:
                    download_info = get_video_info(
                        url,
                        browser_spec,
                        cookies,
                        verbose,
                        sub_logger,
                    )
                except Exception as error:
                    report_failure(
                        str(error),
                        console,
                        "Could not inspect subtitle tracks",
                        sub_logger.reported_errors,
                    )

        configure_requested_subtitles(
            opts,
            download_info,
            console,
            language_tokens=language_tokens,
            write_subs=write_subs,
            embed_subs=embed_subs,
            auto_subs=auto_subs,
            sub_langs=sub_langs,
            sub_format=sub_format,
        )

    # Perform download
    if info_json:
        console.print(
            f"[blue]Starting download to [cyan]{target_dir}[/cyan] using info file: [cyan]{info_json}[/cyan][/blue]"
        )
        error_code = download_from_info_json(
            info_json,
            extra_opts=opts,
            browser_spec=browser_spec,
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
            extra_opts=opts,
            browser_spec=browser_spec,
            cookie_file=cookies,
            output_dir=target_dir,
            verbose=verbose,
            info=download_info,
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
            help="Skip sponsor and self-promotion segments using SponsorBlock (YouTube only)",
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
    # The flag is text; yt-dlp wants a tuple. Parse once, here at the boundary.
    browser_spec = (
        parse_browser_spec(cookies_from_browser) if cookies_from_browser else None
    )
    target_dir = Path(output_dir) if output_dir else get_default_music_dir()
    console.print(
        f"[blue]Extracting audio ({codec}) to [cyan]{target_dir}[/cyan] from: [cyan]{url}[/cyan][/blue]"
    )
    error_code = download_audio(
        url,
        format_codec=codec,
        browser_spec=browser_spec,
        cookie_file=cookies,
        output_dir=target_dir,
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
            help="Comma-separated subtitle languages (e.g. 'en', 'pt-BR', 'all')",
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
        Option(
            "--sub-format",
            "-f",
            help="Sidecar format (srt, vtt, best); embedded subtitles always use vtt",
        ),
    ] = DEFAULT_SUB_FORMAT,
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
    # The flag is text; yt-dlp wants a tuple. Parse once, here at the boundary.
    browser_spec = (
        parse_browser_spec(cookies_from_browser) if cookies_from_browser else None
    )
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
        browser_spec=browser_spec,
        cookie_file=cookies,
        verbose=verbose,
    )
    if error_code:
        raise typer.Exit(code=error_code)
