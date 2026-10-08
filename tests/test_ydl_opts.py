"""Tests for the shared YoutubeDL option builders and the opts merge."""

from pathlib import Path
from typing import Any

from ydlx import (
    apply_cookie_opts,
    apply_opts_override,
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


def test_apply_opts_override_is_a_no_op_without_an_override() -> None:
    ydl_opts = make_extract_opts()
    before = dict(ydl_opts)

    apply_opts_override(ydl_opts, None)
    apply_opts_override(ydl_opts, {})

    assert ydl_opts == before


def test_apply_opts_override_concatenates_postprocessors() -> None:
    ydl_opts: dict[str, Any] = {
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]
    }

    apply_opts_override(
        ydl_opts,
        {"postprocessors": [{"key": "ModifyChapters", "remove_sponsor_segments": []}]},
    )

    assert [pp["key"] for pp in ydl_opts["postprocessors"]] == [
        "FFmpegExtractAudio",
        "ModifyChapters",
    ]


def test_apply_opts_override_creates_postprocessors_when_absent() -> None:
    ydl_opts = make_extract_opts()

    apply_opts_override(ydl_opts, {"postprocessors": [{"key": "SponsorBlock"}]})

    assert [pp["key"] for pp in ydl_opts["postprocessors"]] == ["SponsorBlock"]


def test_apply_opts_override_leaves_unmentioned_keys_alone() -> None:
    ydl_opts = make_extract_opts()
    apply_cookie_opts(ydl_opts, ("firefox",), None)

    apply_opts_override(ydl_opts, {"format": "bv+ba", "merge_output_format": "mp4"})

    assert ydl_opts["format"] == "bv+ba"
    assert ydl_opts["merge_output_format"] == "mp4"
    assert ydl_opts["remote_components"] == ["ejs:github"]
    assert ydl_opts["cookiesfrombrowser"] == ("firefox",)


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
