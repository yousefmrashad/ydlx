"""SponsorBlock wiring, gated to YouTube targets."""

from typing import Any
from urllib.parse import urlparse

from rich.console import Console

from ydlx.opts import queue_postprocessors

SPONSORBLOCK_CATEGORIES = ("sponsor", "selfpromo")


def configure_sponsorblock(opts: dict[str, Any]) -> None:
    """
    Queues the two postprocessors that cut SponsorBlock segments out of a file.

    yt-dlp has no `sponsorblock_skip` option. Its CLI builds these two itself, so
    anything driving YoutubeDL through the API has to queue them explicitly:
    SponsorBlock fetches the segments into info["sponsorblock_chapters"], and
    ModifyChapters is what actually rewrites the file. SponsorBlock alone only
    marks chapters, which is why setting one without the other looks like it
    worked when nothing was removed.

    SponsorBlock runs after_filter so the video id and duration are known.
    ModifyChapters runs after_move, which is also where FFmpegExtractAudio lands
    as post_process, so an audio cut is applied to the converted file — the same
    order the yt-dlp CLI uses for --sponsorblock-remove with --extract-audio.
    """
    categories = list(SPONSORBLOCK_CATEGORIES)
    queue_postprocessors(
        opts,
        {"key": "SponsorBlock", "categories": categories, "when": "after_filter"},
        {
            "key": "ModifyChapters",
            "remove_sponsor_segments": categories,
            "when": "after_move",
        },
    )


def is_youtube_url(url: str | None) -> bool:
    """Whether a URL points at YouTube (watch, share, music, etc.)."""
    if not url:
        return False
    try:
        host = urlparse(url.strip()).netloc.lower().split("@")[-1].split(":")[0]
    except ValueError:
        return False
    return (
        host in ("youtu.be", "youtube.com", "music.youtube.com", "m.youtube.com")
        or host.endswith(".youtu.be")
        or host.endswith(".youtube.com")
    )


def is_youtube_info(info: dict[str, Any] | None) -> bool:
    """Whether an extracted info dict came from a YouTube extractor."""
    if not info:
        return False
    for key in ("extractor_key", "extractor", "ie_key"):
        value = info.get(key)
        if isinstance(value, str) and "youtube" in value.lower():
            return True
    for key in ("webpage_url", "original_url"):
        value = info.get(key)
        if isinstance(value, str) and is_youtube_url(value):
            return True
    return False


def _site_identified(info: dict[str, Any]) -> bool:
    """Whether an info dict carries any evidence of which site it came from."""
    for key in ("extractor_key", "extractor", "ie_key", "webpage_url", "original_url"):
        if info.get(key):
            return True
    return False


def _identified_site_is_youtube(
    url: str | None = None, info: dict[str, Any] | None = None
) -> bool | None:
    """
    True/False when the target site is identified, None when it is unknown.

    An info dict takes precedence when it names a site; a dict with no site
    evidence defers to the URL; with neither, the site is unknown and callers
    keep the old behavior rather than guessing.
    """
    if info is not None:
        if is_youtube_info(info):
            return True
        if _site_identified(info):
            return False
    if url is not None:
        return is_youtube_url(url)
    return None


def should_offer_sponsorblock(
    url: str | None = None, info: dict[str, Any] | None = None
) -> bool:
    """Whether to even ask about SponsorBlock: only for YouTube or unknown sites."""
    return _identified_site_is_youtube(url=url, info=info) is not False


def maybe_configure_sponsorblock(
    opts: dict[str, Any],
    console: Console | None = None,
    *,
    url: str | None = None,
    info: dict[str, Any] | None = None,
) -> bool:
    """
    Queues SponsorBlock postprocessors when the target is YouTube.

    SponsorBlock's segment database only covers YouTube, so on any other site
    the lookup is a wasted API call at best. Returns True when the
    postprocessors were queued; otherwise warns and returns False. An info
    dict takes precedence when available, falling back to the URL; when
    neither identifies the site, the request is honored (preserving the old
    behavior for --info-json flows that have not been inspected yet).
    """
    if _identified_site_is_youtube(url=url, info=info) is False:
        (console or Console()).print(
            "[yellow]⚠️  SponsorBlock only supports YouTube — continuing without it.[/yellow]"
        )
        return False
    configure_sponsorblock(opts)
    return True
