"""Tests for the shared YoutubeDL option builders and the caller-options merge."""

from pathlib import Path
from typing import Any

import pytest

from ydlx import (
    BASE_OPT_KEYS,
    add_opts,
    apply_cookie_opts,
    make_download_opts,
    make_extract_opts,
    queue_postprocessors,
)


def test_make_extract_opts_allows_the_challenge_solver() -> None:
    assert make_extract_opts()["remote_components"] == ["ejs:github"]


def test_make_extract_opts_never_colours_ytdlp_output() -> None:
    assert make_extract_opts()["color"] == "never"


def test_make_extract_opts_follows_verbose() -> None:
    assert make_extract_opts(verbose=False)["quiet"] is True
    assert make_extract_opts(verbose=True)["quiet"] is False


def test_make_extract_opts_has_no_progress_bar_or_output_path() -> None:
    opts = make_extract_opts()
    assert "progress_hooks" not in opts
    assert "paths" not in opts


def test_make_download_opts_adds_progress_bar_and_output_path() -> None:
    target = Path("out")
    opts = make_download_opts(target)

    assert opts["paths"] == {"home": str(target)}
    assert len(opts["progress_hooks"]) == 1


def test_make_download_opts_inherits_the_extraction_options() -> None:
    opts = make_download_opts(Path("out"))

    assert opts["remote_components"] == ["ejs:github"]
    assert opts["color"] == "never"


def test_base_opt_keys_matches_what_the_builders_return() -> None:
    """Keeps the guard in step with the builders it protects."""
    built = set(make_extract_opts()) | set(make_download_opts(Path("out")))

    assert BASE_OPT_KEYS == built


def test_add_opts_is_a_no_op_without_extra_opts() -> None:
    ydl_opts = make_extract_opts()
    before = dict(ydl_opts)

    add_opts(ydl_opts, None)
    add_opts(ydl_opts, {})

    assert ydl_opts == before


@pytest.mark.parametrize("key", sorted(BASE_OPT_KEYS))
def test_add_opts_refuses_to_replace_a_base_key(key: str) -> None:
    """A plain update would drop the logger, the progress bar, or the cookies."""
    # make_download_opts carries every key in BASE_OPT_KEYS.
    ydl_opts = make_download_opts(Path("out"))
    original = ydl_opts[key]

    with pytest.raises(ValueError, match=key):
        add_opts(ydl_opts, {key: "replaced"})

    assert ydl_opts[key] is original


def test_add_opts_names_every_clobbered_key() -> None:
    ydl_opts = make_extract_opts()

    with pytest.raises(ValueError) as excinfo:
        add_opts(ydl_opts, {"logger": None, "paths": {}, "format": "bv"})

    message = str(excinfo.value)
    assert "logger" in message
    assert "paths" in message
    assert "format" not in message


def test_add_opts_adds_caller_keys_and_leaves_the_base_alone() -> None:
    ydl_opts = make_extract_opts()
    apply_cookie_opts(ydl_opts, ("firefox",), None)

    add_opts(ydl_opts, {"format": "bv+ba", "merge_output_format": "mp4"})

    assert ydl_opts["format"] == "bv+ba"
    assert ydl_opts["merge_output_format"] == "mp4"
    assert ydl_opts["remote_components"] == ["ejs:github"]
    assert ydl_opts["cookiesfrombrowser"] == ("firefox",)


def test_add_opts_adds_postprocessors_because_the_base_has_none() -> None:
    """The caller is the only source of postprocessors in a download."""
    assert "postprocessors" not in BASE_OPT_KEYS

    ydl_opts = make_download_opts(Path("out"))
    add_opts(ydl_opts, {"postprocessors": [{"key": "SponsorBlock"}]})

    assert [pp["key"] for pp in ydl_opts["postprocessors"]] == ["SponsorBlock"]


def test_queue_postprocessors_creates_the_list() -> None:
    opts: dict[str, Any] = {}

    queue_postprocessors(opts, {"key": "SponsorBlock"})

    assert [pp["key"] for pp in opts["postprocessors"]] == ["SponsorBlock"]


def test_queue_postprocessors_appends_to_the_existing_list() -> None:
    opts: dict[str, Any] = {"postprocessors": [{"key": "FFmpegExtractAudio"}]}

    queue_postprocessors(opts, {"key": "SponsorBlock"}, {"key": "ModifyChapters"})

    assert [pp["key"] for pp in opts["postprocessors"]] == [
        "FFmpegExtractAudio",
        "SponsorBlock",
        "ModifyChapters",
    ]
