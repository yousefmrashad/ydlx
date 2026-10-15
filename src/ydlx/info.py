"""Metadata extraction and Rich display."""

from typing import Any, cast

import yt_dlp
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ydlx.cookies import apply_cookie_opts
from ydlx.errors import MyLogger
from ydlx.opts import make_extract_opts


def get_video_info(
    url: str,
    browser_spec: tuple[str, ...] | None = None,
    cookie_file: str | None = None,
    verbose: bool = False,
    logger: MyLogger | None = None,
) -> dict[str, Any]:
    """
    Extract video metadata without downloading it.

    A caller that reports extraction failures can pass its own logger and read
    back logger.reported_errors, so the except handler does not re-print what
    yt-dlp already logged before raising. Passed positionally to stay
    compatible with *_args test doubles.
    """
    ydl_opts = make_extract_opts(verbose, logger)
    apply_cookie_opts(ydl_opts, browser_spec, cookie_file)

    with yt_dlp.YoutubeDL(cast(Any, ydl_opts)) as ydl:
        info = ydl.extract_info(url, download=False)
        return cast(Any, info)


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
