"""Interactive audio/subtitle/resolution questions."""

from typing import NamedTuple

from rich.console import Console
from rich.prompt import Confirm, Prompt

from ydlx.formats import AUDIO_CODEC_CHOICES
from ydlx.sponsorblock import should_offer_sponsorblock
from ydlx.subtitles import DEFAULT_SUB_FORMAT, SUBTITLE_FORMAT_CHOICES


class SubtitleOptions(NamedTuple):
    """The subtitle answers shared by every flow that downloads subtitles."""

    langs: str
    sub_format: str
    auto_subs: bool


class AudioOptions(NamedTuple):
    """The audio answers shared by every flow that downloads audio."""

    codec: str
    sponsorblock: bool


def prompt_subtitle_options() -> SubtitleOptions:
    """Asks the subtitle questions, in the one order every flow uses.

    The languages, format, and auto-caption questions used to be asked in two
    different orders depending on the entry point; one definition keeps the
    wording from drifting too.
    """
    langs = Prompt.ask(
        "Subtitle languages (comma-separated, e.g. 'en', 'pt-BR', 'all')",
        default="en",
    )
    sub_format = Prompt.ask(
        "Subtitle format (embedded subtitles always use vtt)",
        choices=list(SUBTITLE_FORMAT_CHOICES),
        default=DEFAULT_SUB_FORMAT,
    )
    auto_subs = Confirm.ask(
        "Include auto-generated captions if official subtitles are missing?",
        default=True,
    )
    return SubtitleOptions(langs=langs, sub_format=sub_format, auto_subs=auto_subs)


def prompt_audio_options(url: str | None = None) -> AudioOptions:
    """Asks the audio questions shared by every audio download."""
    codec = Prompt.ask(
        "Select audio format",
        choices=list(AUDIO_CODEC_CHOICES),
        default="m4a",
    )
    sponsorblock = False
    if should_offer_sponsorblock(url=url):
        sponsorblock = Confirm.ask(
            "Skip sponsor and self-promotion segments using SponsorBlock? (YouTube only)",
            default=False,
        )
    return AudioOptions(codec=codec, sponsorblock=sponsorblock)


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
