"""Download execution, progress tracking, and download records."""

import os
from pathlib import Path
from typing import Any, cast

import yt_dlp
from rich.console import Console
from rich.markup import escape
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadError

from ydlx.cookies import apply_cookie_opts
from ydlx.errors import _logger_reported, report_failure, strip_ansi
from ydlx.formats import make_audio_format_spec
from ydlx.opts import add_opts, make_extract_opts
from ydlx.paths import get_default_music_dir, get_default_video_dir
from ydlx.sponsorblock import maybe_configure_sponsorblock


def is_subtitle_download(status: dict[str, Any]) -> bool:
    """
    Whether a progress-hook event is a subtitle track rather than a media file.

    yt-dlp fetches subtitles through the same downloader and the same hooks as
    media, but it hands the hook the track's own dict rather than the video's:
    ext, name, url, protocol, and no id. A media event always carries the video
    id, since that is what the output template is built from, so its absence is
    the reliable tell. The file extension is not usable here -- yt-dlp's own
    subtitle list covers only ass/lrc/srt/vtt and omits json, ttml and dfxp.

    The tracker has to know because a track is announced under the name the
    convertor is about to replace and delete, so the file it names does not
    exist by the time the run finishes.
    """
    return "id" not in (status.get("info_dict") or {})


class DownloadTracker:
    """Reports media downloads. Subtitle tracks are not media and are skipped."""

    progress: Progress | None
    task_id: TaskID | None
    current_filename: str | None

    def __init__(self):
        self.progress = None
        self.task_id = None
        self.current_filename = None

    def hook(self, d: dict[str, Any]) -> None:
        # Checked before anything else so that a track arriving while a media
        # bar is live neither draws its own bar nor closes that one. The track
        # is summarised by whoever asked for it instead.
        if is_subtitle_download(d):
            return
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
# Download Recording
# =====================================================================

# Both facts below used to be recovered by matching yt-dlp's log lines in
# MyLogger.debug. They are available as data instead: yt-dlp names the method it
# calls when it declines a download, and it records the path of every subtitle
# track it resolved in the info dict. Reading them there means neither depends on
# a message staying worded as it is today, and a video whose title happens to
# contain the old search phrase can no longer be reported as skipped.


class DownloadRecord:
    """What yt-dlp actually did, read back from its own callbacks."""

    skipped: list[str]
    subtitle_count: int

    def __init__(self) -> None:
        self.skipped = []
        self.subtitle_count = 0


class TrackingYoutubeDL(yt_dlp.YoutubeDL):
    """
    A YoutubeDL that records the files it declined to download.

    yt-dlp settles "is this already here?" in exactly two places, and both call
    report_file_already_downloaded with the path they found. Overriding it is
    what makes a skip observable; a progress hook cannot be, because a skipped
    file never reaches dl() and so never fires one.
    """

    record: DownloadRecord

    def __init__(self, params: dict[str, Any], record: DownloadRecord) -> None:
        self.record = record
        super().__init__(cast(Any, params))

    def report_file_already_downloaded(self, file_name: str) -> None:
        self.record.skipped.append(file_name)
        super().report_file_already_downloaded(file_name)


class SubtitleCollectorPP(PostProcessor):
    """
    Counts the subtitle tracks yt-dlp produced, and touches nothing.

    _write_subtitles stores the path it settled on for each track under
    requested_subtitles[lang]["filepath"], whether it fetched the track or found
    it already on disk. The before_dl phase runs after that and before the
    download, so the path is already set, and it runs under skip_download too --
    which is the only phase that does once no media is being fetched.

    Registered last in that phase, so it counts the tracks the convertor left
    rather than the .vtt sources it consumed. The count is replaced rather than
    summed because it describes one video: a playlist entry with no subtitles
    must not inherit the previous entry's tracks.
    """

    record: DownloadRecord

    def __init__(self, record: DownloadRecord) -> None:
        super().__init__(None)
        self.record = record

    def run(self, information: Any) -> tuple[list[str], Any]:
        # The info dict is annotated loosely on purpose: yt-dlp declares a typed
        # alias for it, but only in its type stubs, so naming it here would need
        # an annotation that does not exist at run time.
        info = cast(Any, information)
        self.record.subtitle_count = sum(
            1
            for sub in (info.get("requested_subtitles") or {}).values()
            if sub.get("filepath")
        )
        return [], information


def build_downloader(ydl_opts: dict[str, Any], record: DownloadRecord) -> Any:
    """
    Builds a downloader wired to record into `record`.

    The collector goes in here rather than through the postprocessors option
    because yt-dlp only resolves that option by name, so a class of our own has
    no name to be looked up under. Adding it to the chain after construction
    also puts it last in before_dl, which is where the converted subtitle name
    comes from.
    """
    ydl = TrackingYoutubeDL(ydl_opts, record)
    ydl.add_post_processor(SubtitleCollectorPP(record), when="before_dl")
    return ydl


# Why a skip happened and how to force a real download. The symptom is otherwise
# baffling: request 1080p, receive the 720p already in the directory, exit 0.
ALREADY_DOWNLOADED_HINT = (
    "yt-dlp skips files matched by name, even if the requested format differs. "
    "Delete the file(s) or pick another output directory to force a redownload."
)


def report_already_downloaded(record: DownloadRecord, console: Console) -> None:
    """
    Warns about the files yt-dlp skipped because the target name already existed.

    Nothing else surfaces these: yt-dlp counts a skip as success and returns 0,
    so the run would otherwise end on a green tick having fetched nothing. Each
    skipped file is listed, because on a playlist that is the only way to tell
    which ones.

    Duplicates are collapsed: the subtitle-retry path reuses the same record, so
    the second attempt records the files the first one already skipped.
    """
    skipped = list(dict.fromkeys(record.skipped))
    if not skipped:
        return
    for path in skipped:
        # Escaped because yt-dlp's default output template ends in " [id].ext",
        # so the bracketed video id would otherwise be read as a markup tag and
        # silently dropped from every name printed here.
        console.print(f"[yellow]⚠️  Already downloaded: {escape(path)}[/yellow]")
    console.print(f"[yellow]⚠️  {ALREADY_DOWNLOADED_HINT}[/yellow]")


SPONSORBLOCK_EMBED_RERUN_HINT = (
    "SponsorBlock could not cut this already-downloaded file. Delete it and "
    "retry to apply the cuts and embed subtitles."
)


def is_sponsorblock_embed_rerun(
    message: str, record: DownloadRecord, opts: dict[str, Any]
) -> bool:
    """Whether a failed cut is the already-downloaded SponsorBlock/embed case."""
    pp_keys = {pp.get("key") for pp in opts.get("postprocessors", [])}
    return (
        bool(record.skipped)
        and "Cannot cut video since the real and expected durations mismatch" in message
        and "ModifyChapters" in pp_keys
        and "FFmpegEmbedSubtitle" in pp_keys
    )


def make_download_opts(target_dir: Path, verbose: bool = False) -> dict[str, Any]:
    """Download-path options: extraction base plus progress bar and output path."""
    opts = make_extract_opts(verbose)
    opts["progress_hooks"] = [DownloadTracker().hook]
    opts["paths"] = {"home": str(target_dir)}
    return opts


def download_video(
    url: str,
    extra_opts: dict[str, Any] | None = None,
    browser_spec: tuple[str, ...] | None = None,
    cookie_file: str | None = None,
    output_dir: str | Path | None = None,
    verbose: bool = False,
    record: DownloadRecord | None = None,
    info: dict[str, Any] | None = None,
) -> int:
    """Download a URL or extracted video info with optional configurations.

    A caller that needs to know what yt-dlp did passes a record and keeps it:
    the one built here is written to by the downloader and then discarded, so
    nothing outside this function can see what it captured.
    """
    if record is None:
        record = DownloadRecord()

    target_dir = Path(output_dir) if output_dir else get_default_video_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = make_download_opts(target_dir, verbose)

    apply_cookie_opts(ydl_opts, browser_spec, cookie_file)

    add_opts(ydl_opts, extra_opts)

    try:
        with build_downloader(ydl_opts, record) as ydl:
            if info is None:
                code = int(ydl.download([url]))
            else:
                ydl.process_ie_result(info, download=True)
                code = 0
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
            with build_downloader(retry_opts, record) as ydl:
                # Re-extract on this exceptional retry path so the failed
                # subtitle attempt cannot leave stale state in the info dict.
                code = int(ydl.download([url]))
            # The retry shares the record, so the files the first attempt
            # skipped are in it too; reporting dedupes.
            report_already_downloaded(record, Console())
            return code
        report_failure(msg, Console(), "Download error", _logger_reported(ydl_opts))
        if is_sponsorblock_embed_rerun(msg, record, extra_opts or {}):
            Console().print(f"[yellow]💡 {SPONSORBLOCK_EMBED_RERUN_HINT}[/yellow]")
        return 1
    except Exception as e:
        report_failure(str(e), Console(), "Download error", _logger_reported(ydl_opts))
        return 1

    report_already_downloaded(record, Console())
    return code


def download_audio(
    url: str,
    format_codec: str = "m4a",
    browser_spec: tuple[str, ...] | None = None,
    cookie_file: str | None = None,
    output_dir: str | Path | None = None,
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
        maybe_configure_sponsorblock(opts, url=url)

    return download_video(
        url,
        extra_opts=opts,
        browser_spec=browser_spec,
        cookie_file=cookie_file,
        output_dir=target_dir,
        verbose=verbose,
    )


def download_from_info_json(
    info_file: str,
    extra_opts: dict[str, Any] | None = None,
    browser_spec: tuple[str, ...] | None = None,
    cookie_file: str | None = None,
    output_dir: str | Path | None = None,
    verbose: bool = False,
) -> int:
    """Download video using an existing info.json file."""
    record = DownloadRecord()
    target_dir = Path(output_dir) if output_dir else get_default_video_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = make_download_opts(target_dir, verbose)

    apply_cookie_opts(ydl_opts, browser_spec, cookie_file)

    add_opts(ydl_opts, extra_opts)

    try:
        with build_downloader(ydl_opts, record) as ydl:
            code = int(ydl.download_with_info_file(info_file))
    except DownloadError as e:
        report_failure(str(e), Console(), "Download error", _logger_reported(ydl_opts))
        return 1
    except Exception as e:
        report_failure(str(e), Console(), "Download error", _logger_reported(ydl_opts))
        return 1

    report_already_downloaded(record, Console())
    return code
