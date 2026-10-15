"""URL normalization, validation, and the interactive URL prompt."""

from urllib.parse import urlparse

import typer
from rich.console import Console
from rich.prompt import Prompt

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
            "[bold red]❌ Invalid URL. Provide a full video link, e.g. https://www.youtube.com/watch?v=VIDEO_ID[/bold red]"
        )
        raise typer.Exit(code=1)
    return normalized


# Words that abandon a URL prompt and return to the previous menu. Checked
# before validation: none of them can be a URL (normalize_url rejects them all).
URL_PROMPT_BACK_WORDS = ("back", "q")


def prompt_for_url(console: Console) -> str | None:
    """Prompts until the user enters a usable video URL, or None on 'back'."""
    while True:
        url = Prompt.ask("Enter video URL (or 'back')").strip()
        if url.casefold() in URL_PROMPT_BACK_WORDS:
            return None
        normalized = normalize_url(url)
        if normalized:
            return normalized
        console.print(
            "[bold red]❌ Empty or invalid URL. Use a full video link, e.g. https://www.youtube.com/watch?v=VIDEO_ID[/bold red]"
        )
