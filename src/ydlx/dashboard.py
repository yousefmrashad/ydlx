"""Interactive dashboard menus and session cookie state."""

import json
import os
from pathlib import Path
from typing import Any, cast

import yt_dlp
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from ydlx.cookies import (
    CUSTOM_BASE_CHOICES,
    GECKO_BROWSER,
    SUPPORTED_BROWSERS,
    build_browser_spec,
    cookie_source_choice,
    format_cookie_file,
    format_cookie_source,
    is_profile_path,
    normalize_profile_arg,
    profile_arg_help,
)
from ydlx.downloads import download_audio, download_from_info_json, download_video
from ydlx.errors import MyLogger, report_failure
from ydlx.formats import (
    VideoPreset,
    get_preset_format_choices,
    make_duration_filter,
    make_video_format_spec,
)
from ydlx.info import get_video_info, print_video_info
from ydlx.paths import get_default_music_dir, get_default_video_dir
from ydlx.prompts import (
    prompt_audio_options,
    prompt_for_max_height,
    prompt_subtitle_options,
)
from ydlx.settings import (
    get_saved_cookie_file,
    get_saved_cookie_profile,
    get_saved_cookie_source,
    save_settings,
)
from ydlx.sponsorblock import maybe_configure_sponsorblock, should_offer_sponsorblock
from ydlx.subtitles import (
    configure_requested_subtitles,
    parse_sub_langs,
    report_subtitle_download,
    resolvable_sub_langs,
)
from ydlx.urls import prompt_for_url

# Global session variables for cookies in interactive mode. The browser and its
# profile are kept apart, matching the tuple yt-dlp documents as its browser
# spec; only the CLI flag and the settings file are text.
session_cookies_browser: str | None = None

session_cookies_profile: str | None = None

session_cookies_file: str | None = None


def session_browser_spec() -> tuple[str, ...] | None:
    """The dashboard's current browser source in yt-dlp's tuple form."""
    return build_browser_spec(session_cookies_browser, session_cookies_profile)


def do_download_interactive(
    url: str, info: dict[str, Any] | None = None, console: Console | None = None
) -> None:
    """Sub-menu to choose download settings interactively."""
    if console is None:
        console = Console()
    if info is None:
        with console.status("[bold blue]Fetching metadata...[/bold blue]"):
            fetch_logger = MyLogger()
            try:
                info = get_video_info(
                    url,
                    session_browser_spec(),
                    session_cookies_file,
                    False,
                    fetch_logger,
                )
            except Exception as e:
                report_failure(
                    str(e),
                    console,
                    "Failed to fetch metadata",
                    fetch_logger.reported_errors,
                )
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

    # Ask about SponsorBlock skipping (YouTube only). Off-YouTube the question
    # is skipped entirely; the CLI flag path still warns via maybe_configure.
    if should_offer_sponsorblock(url=url, info=info):
        skip_sponsors = Confirm.ask(
            "Skip sponsor and self-promotion segments using SponsorBlock? (YouTube only)",
            default=False,
        )
        if skip_sponsors:
            maybe_configure_sponsorblock(opts, console, url=url, info=info)

    # Ask about Subtitles. This flow only ever downloads media to the video
    # directory, so the prompt always applies; it used to be guarded by
    # `target_dir == get_default_video_dir()`, which was always true.
    want_subs = Confirm.ask("Download / embed subtitles?", default=False)
    if want_subs:
        sub_mode = Prompt.ask(
            "Subtitle placement",
            choices=["embed", "external", "both"],
            default="embed",
        )
        subs = prompt_subtitle_options()
        language_tokens = resolvable_sub_langs(parse_sub_langs(subs.langs))
        configure_requested_subtitles(
            opts,
            info,
            console,
            language_tokens=language_tokens,
            write_subs=(sub_mode in ("external", "both")),
            embed_subs=(sub_mode in ("embed", "both")),
            auto_subs=subs.auto_subs,
            sub_langs=subs.langs,
            sub_format=subs.sub_format,
        )

    console.print(f"[blue]Starting download to [cyan]{target_dir}[/cyan]...[/blue]")
    error_code = download_video(
        url,
        extra_opts=opts,
        browser_spec=session_browser_spec(),
        cookie_file=session_cookies_file,
        output_dir=target_dir,
        info=info,
    )
    if error_code:
        console.print("[bold red]❌ Download failed![/bold red]")
    else:
        console.print("[bold green]✓ Download completed successfully![/bold green]")


def download_subtitles_interactively(
    url: str, console: Console, info: dict[str, Any] | None = None
) -> None:
    """Asks for subtitle options and downloads them into the video directory."""
    options = prompt_subtitle_options()
    video_dir = get_default_video_dir()
    console.print(
        f"[blue]Downloading subtitles ({options.langs}) to [cyan]{video_dir}[/cyan]...[/blue]"
    )
    _ = report_subtitle_download(
        url,
        video_dir,
        console,
        langs=options.langs,
        auto_subs=options.auto_subs,
        sub_format=options.sub_format,
        browser_spec=session_browser_spec(),
        cookie_file=session_cookies_file,
        info=info,
    )


def download_audio_interactively(url: str, console: Console) -> None:
    """Asks for audio options and downloads into the music directory."""
    options = prompt_audio_options(url)
    music_dir = get_default_music_dir()
    console.print(
        f"[blue]Starting audio download ({options.codec}) to [cyan]{music_dir}[/cyan]...[/blue]"
    )
    _ = download_audio(
        url,
        options.codec,
        browser_spec=session_browser_spec(),
        cookie_file=session_cookies_file,
        output_dir=music_dir,
        sponsorblock=options.sponsorblock,
    )


def run_interactive_menu() -> None:
    """Runs the main CLI prompt-driven dashboard loop."""
    global session_cookies_browser, session_cookies_profile, session_cookies_file
    if session_cookies_browser is None:
        session_cookies_browser = get_saved_cookie_source()
        # Only restore the profile alongside the browser it belongs to. Reading
        # it unconditionally would reattach a saved path to a browser picked
        # later in this session, or keep one after the user switches away.
        session_cookies_profile = get_saved_cookie_profile()
    if session_cookies_file is None:
        session_cookies_file = get_saved_cookie_file()
    console = Console()
    while True:
        active_cookies = [
            label
            for label in (
                format_cookie_source(session_cookies_browser, session_cookies_profile)
                if session_cookies_browser
                else None,
                format_cookie_file(session_cookies_file)
                if session_cookies_file
                else None,
            )
            if label
        ]
        cookies_status = (
            " [bold green]+[/bold green] ".join(
                f"[bold green]{label}[/bold green]" for label in active_cookies
            )
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
            "[yellow]Extract subtitles (.vtt) without downloading media[/yellow]",
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
            if url is None:
                continue
            menu_logger = MyLogger()
            with console.status("[bold blue]Fetching metadata...[/bold blue]"):
                try:
                    info = get_video_info(
                        url,
                        session_browser_spec(),
                        session_cookies_file,
                        False,
                        menu_logger,
                    )
                except Exception as e:
                    report_failure(
                        str(e),
                        console,
                        "Extraction failed",
                        menu_logger.reported_errors,
                    )
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
                download_subtitles_interactively(url, console, info)
            elif sub_choice == "4":
                download_audio_interactively(url, console)

        elif choice == "2":
            url = prompt_for_url(console)
            if url is None:
                continue
            do_download_interactive(url, None, console)

        elif choice == "3":
            url = prompt_for_url(console)
            if url is None:
                continue
            download_audio_interactively(url, console)

        elif choice == "4":
            url = prompt_for_url(console)
            if url is None:
                continue
            download_subtitles_interactively(url, console)

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
                browser_spec=session_browser_spec(),
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
                session_cookies_profile = None
                session_cookies_file = None
                save_settings(
                    {
                        "cookies_from_browser": None,
                        "cookies_profile": None,
                        "cookiefile": None,
                    }
                )
                console.print("[bold green]✓ Cookies disabled (persisted)[/bold green]")
            elif source == "browser":
                console.print(
                    "[dim]On Windows, Chromium browsers may encrypt cookies with "
                    "App-Bound Encryption, which yt-dlp cannot read — this affects "
                    "recent chrome, edge, and brave installs. Firefox is the most "
                    "reliable, or use a cookies.txt file.[/dim]"
                )
                browser = Prompt.ask(
                    "Select browser to load cookies from (helps avoid 403 Forbidden errors)",
                    choices=["none", *SUPPORTED_BROWSERS, "custom"],
                    default=cookie_source_choice(
                        session_cookies_browser, session_cookies_profile
                    ),
                )
                if browser == "custom":
                    # The base name is chosen explicitly rather than inferred:
                    # ydlx cannot know which yt-dlp reader matches an unknown
                    # browser, and guessing would give wrong advice.
                    console.print(
                        "[dim]Pick the cookie reader that matches your browser. "
                        "'firefox' reads any Firefox-family profile "
                        "(cookies.sqlite); 'chrome' reads any Chromium-family "
                        "profile (brave, edge, opera, vivaldi and others all use "
                        "the same reader once a path is given).[/dim]"
                    )
                    base = Prompt.ask(
                        "Cookie reader to use",
                        choices=list(CUSTOM_BASE_CHOICES),
                        default=(
                            session_cookies_browser
                            if session_cookies_browser in CUSTOM_BASE_CHOICES
                            else GECKO_BROWSER
                        ),
                    )
                    # No fallback default: the home directory is never a browser
                    # profile, and pre-filling it invited a path that cannot work.
                    raw_profile = Prompt.ask(
                        f"{base} {profile_arg_help(base)}",
                        default=session_cookies_profile or "",
                    ).strip()
                    if not raw_profile:
                        console.print(
                            "[bold red]❌ No profile given. Re-run and pick 'none' "
                            "to clear cookies.[/bold red]"
                        )
                        continue
                    profile = normalize_profile_arg(raw_profile)
                    # A bare Chromium profile name ("Default") is not a folder, so
                    # it is passed through; anything that is a path must exist.
                    if is_profile_path(raw_profile) and not Path(profile).is_dir():
                        console.print(
                            f"[bold red]❌ Not a folder: {profile}[/bold red]"
                        )
                        continue
                    # Held separately, as yt-dlp's tuple documents it; never
                    # rejoined into a colon string.
                    session_cookies_browser = base
                    session_cookies_profile = profile
                else:
                    session_cookies_browser = None if browser == "none" else browser
                    session_cookies_profile = None
                # Switching source kinds: clear the other one so the active
                # source shown in the menu stays unambiguous.
                session_cookies_file = None
                save_settings(
                    {
                        "cookies_from_browser": session_cookies_browser,
                        "cookies_profile": session_cookies_profile,
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
                session_cookies_profile = None
                save_settings(
                    {
                        "cookies_from_browser": None,
                        "cookies_profile": None,
                        "cookiefile": session_cookies_file,
                    }
                )
                console.print(
                    f"[bold green]✓ Cookies file set to: {session_cookies_file} (persisted)[/bold green]"
                )

        elif choice == "7":
            console.print("[yellow]Goodbye![/yellow]")
            break
