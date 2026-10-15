"""Subtitle language resolution, configuration, and downloads."""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rich.console import Console

from ydlx.cookies import apply_cookie_opts
from ydlx.downloads import (
    DownloadRecord,
    build_downloader,
    download_video,
    make_download_opts,
)
from ydlx.errors import MyLogger, _logger_reported, report_failure
from ydlx.info import get_video_info
from ydlx.opts import queue_postprocessors
from ydlx.paths import get_default_video_dir

SUBTITLE_FORMAT_CHOICES: tuple[str, ...] = ("srt", "vtt", "best")

DEFAULT_SUB_LANGS = "en"

DEFAULT_SUB_FORMAT = "vtt"


def parse_sub_langs(sub_langs: str) -> list[str]:
    """Splits the comma-separated --sub-langs value, defaulting to English."""
    langs = [lang.strip() for lang in sub_langs.split(",") if lang.strip()]
    # Language requests are case-insensitive. Keep the first spelling so legacy
    # patterns and yt-dlp aliases remain otherwise untouched.
    unique: dict[str, str] = {}
    for lang in langs:
        unique.setdefault(lang.casefold(), lang)
    return list(unique.values()) or [DEFAULT_SUB_LANGS]


def resolvable_sub_langs(tokens: list[str]) -> list[str] | None:
    """Return ordinary BCP 47-style language tags, or None for legacy syntax."""
    if any(token.casefold() == "all" for token in tokens):
        return None
    pattern = r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*"
    if all(re.fullmatch(pattern, token, flags=re.ASCII) for token in tokens):
        return tokens
    return None


def resolve_sub_langs(
    requested: list[str],
    official: Mapping[str, Any],
    automatic: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    """Resolve one official-first concrete track key per requested language."""
    resolved: list[str] = []
    unmatched: list[str] = []
    resolved_keys: set[str] = set()

    def find_exact(tracks: Mapping[str, Any], language: str) -> str | None:
        return next(
            (key for key in tracks if key.casefold() == language),
            None,
        )

    def find_prefix(tracks: Mapping[str, Any], language: str) -> str | None:
        return next(
            (key for key in tracks if key.casefold().startswith(language)),
            None,
        )

    for requested_lang in requested:
        language = requested_lang.casefold()
        subtags = language.split("-")
        base_languages = [
            "-".join(subtags[:length]) for length in range(len(subtags) - 1, 0, -1)
        ]
        selected = find_exact(official, language) or find_prefix(official, language)
        if selected is None:
            selected = next(
                (key for base in base_languages if (key := find_exact(official, base))),
                None,
            )
        if selected is None:
            selected = find_exact(automatic, language) or find_prefix(
                automatic, language
            )
        if selected is None:
            selected = next(
                (
                    key
                    for base in base_languages
                    if (key := find_exact(automatic, base))
                ),
                None,
            )
        if selected is None:
            unmatched.append(requested_lang)
        elif selected not in resolved_keys:
            resolved.append(selected)
            resolved_keys.add(selected)

    return resolved, unmatched


def requested_sub_langs(sub_langs: str) -> list[str] | None:
    """
    Returns the languages to pin, or None to let yt-dlp choose.

    yt-dlp fullmatch-es subtitleslangs, and only runs its own fallback chain when
    the option is left unset. That chain is the better selector: it checks the
    official tracks before the automatic captions, so a video whose official
    English track is named "en-ehkg1hFWq8A" still resolves to that track. Pinning
    "en" instead suppresses the chain and misses it entirely.

    Pinning only when the user asked for something other than the default keeps
    that behaviour intact for the common case, while an explicit -l ar is still
    honoured exactly.
    """
    langs = parse_sub_langs(sub_langs)
    return None if langs == [DEFAULT_SUB_LANGS] else langs


def apply_sub_langs(opts: dict[str, Any], sub_langs: str) -> None:
    """Pins subtitleslangs, or leaves it unset so yt-dlp selects the track."""
    langs = requested_sub_langs(sub_langs)
    if langs is not None:
        opts["subtitleslangs"] = langs


def make_subtitle_convertor(sub_format: str) -> dict[str, Any] | None:
    """
    Returns a converter postprocessor for the target format, or None when the
    format needs no conversion.

    The CLI registers the converter as "before_dl": that phase runs right after
    subtitle files are written, including under skip_download, where later PP
    phases never run.
    """
    fmt = sub_format.lower()
    if fmt not in ("srt", "vtt"):
        return None
    return {"key": "FFmpegSubtitlesConvertor", "format": fmt, "when": "before_dl"}


def configure_subtitles(
    opts: dict[str, Any],
    *,
    write_subs: bool = False,
    embed_subs: bool = False,
    auto_subs: bool = False,
    sub_langs: str = "en",
    sub_format: str = DEFAULT_SUB_FORMAT,
    pin_sub_langs: list[str] | None = None,
) -> None:
    """Configures subtitle download, conversion, and embedding options in ydl_opts."""
    if not (write_subs or embed_subs):
        return

    # VTT is the interoperable format for embedding across the containers this
    # app produces (including WebM). The user's selected format still applies
    # to sidecar-only downloads; embedding always fetches/converts to VTT.
    effective_sub_format = "vtt" if embed_subs else sub_format

    opts["writesubtitles"] = True
    if auto_subs:
        opts["writeautomaticsub"] = True

    if pin_sub_langs is None:
        apply_sub_langs(opts, sub_langs)
    else:
        opts["subtitleslangs"] = list(pin_sub_langs)

    # "srt" is rarely native; pick the best source and let the convertor below
    # produce the requested format (or VTT when embedding).
    opts["subtitlesformat"] = (
        "best" if effective_sub_format.lower() == "srt" else effective_sub_format
    )

    convertor = make_subtitle_convertor(effective_sub_format)
    if convertor:
        queue_postprocessors(opts, convertor)

    if embed_subs:
        queue_postprocessors(
            opts,
            {
                "key": "FFmpegEmbedSubtitle",
                "already_have_subtitle": write_subs,
            },
        )


def configure_requested_subtitles(
    opts: dict[str, Any],
    info: dict[str, Any] | None,
    console: Console,
    *,
    language_tokens: list[str] | None,
    write_subs: bool,
    embed_subs: bool,
    auto_subs: bool,
    sub_langs: str,
    sub_format: str,
) -> None:
    """Configure subtitles, resolving language tags for single-video metadata."""
    if not (write_subs or embed_subs):
        return

    pin_sub_langs: list[str] | None = None
    is_playlist = bool(info and (info.get("_type") == "playlist" or "entries" in info))
    if language_tokens is not None and not is_playlist:
        if info is None:
            console.print(
                "[yellow]⚠️  Subtitle languages could not be resolved; skipping subtitles.[/yellow]"
            )
            return
        pin_sub_langs, unmatched = resolve_sub_langs(
            language_tokens,
            info.get("subtitles") or {},
            (info.get("automatic_captions") or {}) if auto_subs else {},
        )
        for language in unmatched:
            console.print(
                f"[yellow]⚠️  No track found for requested language '{language}'.[/yellow]"
            )
        if not pin_sub_langs:
            console.print(
                "[yellow]⚠️  Continuing without subtitles because no requested language matched.[/yellow]"
            )
            return

    configure_subtitles(
        opts,
        write_subs=write_subs,
        embed_subs=embed_subs,
        auto_subs=auto_subs,
        sub_langs=sub_langs,
        sub_format=sub_format,
        pin_sub_langs=pin_sub_langs,
    )


def download_subtitles_only(
    url: str,
    sub_langs: str = "en",
    auto_subs: bool = True,
    sub_format: str = DEFAULT_SUB_FORMAT,
    browser_spec: tuple[str, ...] | None = None,
    cookie_file: str | None = None,
    output_dir: str | Path | None = None,
    verbose: bool = False,
    record: DownloadRecord | None = None,
    pin_sub_langs: list[str] | None = None,
    info: dict[str, Any] | None = None,
) -> int:
    """Download subtitles from a URL, or reuse metadata already extracted."""
    target_dir = Path(output_dir) if output_dir else get_default_video_dir()
    opts: dict[str, Any] = {"skip_download": True, "ignoreerrors": True}
    configure_subtitles(
        opts,
        write_subs=True,
        auto_subs=auto_subs,
        sub_langs=sub_langs,
        sub_format=sub_format,
        pin_sub_langs=pin_sub_langs,
    )
    if info is None:
        return download_video(
            url,
            extra_opts=opts,
            browser_spec=browser_spec,
            cookie_file=cookie_file,
            output_dir=target_dir,
            verbose=verbose,
            record=record,
        )

    target_dir.mkdir(parents=True, exist_ok=True)
    ydl_opts = make_download_opts(target_dir, verbose)
    apply_cookie_opts(ydl_opts, browser_spec, cookie_file)
    ydl_opts.update(opts)
    if record is None:
        record = DownloadRecord()
    try:
        with build_downloader(ydl_opts, record) as ydl:
            ydl.process_ie_result(info, download=True)
    except Exception as error:
        # Retry through yt-dlp's URL path if it cannot reprocess the info dict.
        if retry_url := info.get("webpage_url") or info.get("original_url"):
            return download_subtitles_only(
                str(retry_url),
                sub_langs=sub_langs,
                auto_subs=auto_subs,
                sub_format=sub_format,
                browser_spec=browser_spec,
                cookie_file=cookie_file,
                output_dir=target_dir,
                verbose=verbose,
                record=record,
                pin_sub_langs=pin_sub_langs,
            )
        report_failure(
            str(error), Console(), "Subtitle download error", _logger_reported(ydl_opts)
        )
        return 1
    return 0


def report_subtitle_download(
    url: str,
    target_dir: Path,
    console: Console,
    *,
    langs: str,
    auto_subs: bool,
    sub_format: str,
    browser_spec: tuple[str, ...] | None,
    cookie_file: str | None = None,
    verbose: bool = False,
    info: dict[str, Any] | None = None,
) -> int:
    """Download subtitles and report how many files landed; returns an exit code."""
    requested = parse_sub_langs(langs)
    resolvable = resolvable_sub_langs(requested)

    def finish(
        info_dict: dict[str, Any] | None, pin_sub_langs: list[str] | None = None
    ) -> int:
        record = DownloadRecord()
        error_code = download_subtitles_only(
            url,
            sub_langs=langs,
            auto_subs=auto_subs,
            sub_format=sub_format,
            browser_spec=browser_spec,
            cookie_file=cookie_file,
            output_dir=target_dir,
            verbose=verbose,
            record=record,
            pin_sub_langs=pin_sub_langs,
            info=info_dict,
        )
        if error_code:
            console.print("[bold red]❌ Subtitles download failed![/bold red]")
            return error_code
        return _report_subtitle_count(record.subtitle_count, langs, target_dir, console)

    if resolvable is None:
        # Keep aliases, negations, and yt-dlp regex syntax on the legacy path.
        return finish(info)

    if info is None:
        logger = MyLogger(verbose=verbose)
        try:
            info = get_video_info(url, browser_spec, cookie_file, verbose, logger)
        except Exception as error:
            report_failure(
                str(error), console, "Extraction failed", logger.reported_errors
            )
            console.print("[bold red]❌ Subtitles download failed![/bold red]")
            return 1

    if info.get("_type") == "playlist" or "entries" in info:
        # Playlists retain yt-dlp's one-global-option behavior.
        return finish(info)

    resolved, unmatched = resolve_sub_langs(
        resolvable,
        info.get("subtitles") or {},
        (info.get("automatic_captions") or {}) if auto_subs else {},
    )
    for language in unmatched:
        console.print(
            f"[yellow]⚠️  No track found for requested language '{language}'.[/yellow]"
        )
    if not resolved:
        return _report_subtitle_count(0, langs, target_dir, console)
    return finish(info, resolved)


def _report_subtitle_count(
    written: int, langs: str, target_dir: Path, console: Console
) -> int:
    """Report success or the existing no-subtitles failure for one run."""
    if not written:
        console.print(
            f"[bold red]❌ No subtitles found for '{langs}' on this video.[/bold red]"
        )
        console.print(
            "[dim]Tip: try common languages like 'en', or allow auto-generated captions.[/dim]"
        )
        return 1
    console.print(
        f"[bold green]✓ Downloaded {written} subtitle file(s) to [cyan]{target_dir}[/cyan][/bold green]"
    )
    return 0
