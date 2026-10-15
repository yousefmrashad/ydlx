"""Shared yt-dlp option builders and postprocessor queueing."""

from typing import Any

from ydlx.errors import MyLogger

# =====================================================================
# API / Core Functions
# =====================================================================


def make_extract_opts(
    verbose: bool = False, logger: MyLogger | None = None
) -> dict[str, Any]:
    """The YoutubeDL options shared by every extraction and download path."""
    return {
        "logger": logger or MyLogger(verbose=verbose),
        "quiet": not verbose,
        # yt-dlp colors ERROR:/WARNING: prefixes whenever stderr is a TTY,
        # even when a custom logger is set; our logger does its own styling.
        "color": "never",
        # Fetch yt-dlp's official challenge solver scripts so YouTube's JS
        # challenges get solved when a JS runtime (e.g. Deno) is available,
        # instead of silently accepting throttled/missing formats. On the
        # info.json path this only matters for the re-extract fallback in
        # download_with_info_file; the formats it reads are already solved.
        "remote_components": ["ejs:github"],
    }


# The keys the extract and download builders own (see make_extract_opts here
# and make_download_opts in downloads.py). add_opts rejects any caller options
# naming one of these, so the sets must stay in step: test_ydl_opts asserts
# this equals what the builders actually return.
BASE_OPT_KEYS = frozenset(
    {
        # make_extract_opts
        "logger",
        "quiet",
        "color",
        "remote_components",
        # make_download_opts
        "progress_hooks",
        "paths",
    }
)


def add_opts(ydl_opts: dict[str, Any], extra_opts: dict[str, Any] | None) -> None:
    """
    Adds caller-supplied options to the ones the builders produced.

    The two key sets are disjoint by design, so this only ever adds. Rather than
    trust that, it rejects caller options naming a key make_extract_opts or
    make_download_opts owns: a plain update would silently replace the logger,
    the progress bar, or the cookie keys apply_cookie_opts just wrote, and the
    download would carry on with no indication anything had been dropped.
    """
    if not extra_opts:
        return
    if clobbered := sorted(BASE_OPT_KEYS.intersection(extra_opts)):
        raise ValueError(
            f"extra_opts may not replace the base options: {', '.join(clobbered)}"
        )
    ydl_opts.update(extra_opts)


def queue_postprocessors(opts: dict[str, Any], *postprocessors: dict[str, Any]) -> None:
    """Appends postprocessors to opts, creating the list when it is absent."""
    opts["postprocessors"] = [*(opts.get("postprocessors") or []), *postprocessors]
