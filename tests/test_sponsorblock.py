"""Tests for SponsorBlock postprocessor wiring."""

from typing import Any, cast

from yt_dlp import YoutubeDL

from ydlx import (
    SPONSORBLOCK_CATEGORIES,
    configure_sponsorblock,
    configure_subtitles,
    is_youtube_info,
    is_youtube_url,
    make_audio_format_spec,
    maybe_configure_sponsorblock,
)


def test_configure_sponsorblock_queues_both_postprocessors() -> None:
    opts: dict[str, Any] = {}
    configure_sponsorblock(opts)

    keys = [(pp["key"], pp["when"]) for pp in opts["postprocessors"]]
    assert keys == [
        ("SponsorBlock", "after_filter"),
        ("ModifyChapters", "after_move"),
    ]


def test_configure_sponsorblock_uses_sponsor_and_selfpromo() -> None:
    opts: dict[str, Any] = {}
    configure_sponsorblock(opts)

    fetch, cut = opts["postprocessors"]
    assert fetch["categories"] == ["sponsor", "selfpromo"]
    assert cut["remove_sponsor_segments"] == ["sponsor", "selfpromo"]
    assert SPONSORBLOCK_CATEGORIES == ("sponsor", "selfpromo")


def test_configure_sponsorblock_keeps_existing_postprocessors() -> None:
    opts: dict[str, Any] = {
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]
    }
    configure_sponsorblock(opts)

    assert [pp["key"] for pp in opts["postprocessors"]] == [
        "FFmpegExtractAudio",
        "SponsorBlock",
        "ModifyChapters",
    ]


def test_configure_sponsorblock_does_not_mutate_shared_category_list() -> None:
    opts: dict[str, Any] = {}
    configure_sponsorblock(opts)

    opts["postprocessors"][0]["categories"].append("intro")
    assert SPONSORBLOCK_CATEGORIES == ("sponsor", "selfpromo")


def test_yt_dlp_builds_the_removal_chain() -> None:
    """yt-dlp only honours these as postprocessors; no param does this job."""
    opts: dict[str, Any] = {
        "format": make_audio_format_spec("mp3"),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
    }
    configure_sponsorblock(opts)

    with YoutubeDL(cast(Any, opts)) as ydl:
        chained = {
            when: [type(pp).__name__ for pp in pps]
            for when, pps in cast(Any, ydl)._pps.items()
        }

    assert "SponsorBlockPP" in chained["after_filter"]
    assert "ModifyChaptersPP" in chained["after_move"]
    # Extraction runs in post_process, so the cut lands on the final codec.
    assert chained["post_process"] == ["FFmpegExtractAudioPP"]


def test_sponsorblock_skip_param_is_not_used() -> None:
    """The old dead key: yt-dlp has no such option and silently ignores it."""
    with YoutubeDL(cast(Any, {"sponsorblock_skip": ["sponsor"]})) as ydl:
        assert not any(cast(Any, ydl)._pps.values())


def test_sponsorblock_and_subtitles_compose_in_either_order() -> None:
    """
    Neither helper may drop what the other queued.

    configure_sponsorblock and configure_subtitles both own the
    postprocessors key, so whichever runs second has to see the first one's work.
    Relative order follows call order, so only the contents are pinned here.
    """
    expected = {"FFmpegSubtitlesConvertor", "SponsorBlock", "ModifyChapters"}

    sponsorblock_first: dict[str, Any] = {
        "postprocessors": [{"key": "FFmpegExtractAudio"}]
    }
    configure_sponsorblock(sponsorblock_first)
    configure_subtitles(sponsorblock_first, write_subs=True)

    subtitles_first: dict[str, Any] = {
        "postprocessors": [{"key": "FFmpegExtractAudio"}]
    }
    configure_subtitles(subtitles_first, write_subs=True)
    configure_sponsorblock(subtitles_first)

    for opts in (sponsorblock_first, subtitles_first):
        keys = [pp["key"] for pp in opts["postprocessors"]]
        assert keys[0] == "FFmpegExtractAudio"
        assert set(keys[1:]) == expected


def test_is_youtube_url_matches_youtube_hosts() -> None:
    assert is_youtube_url("https://www.youtube.com/watch?v=abc")
    assert is_youtube_url("https://youtu.be/abc")
    assert is_youtube_url("https://music.youtube.com/watch?v=abc")
    assert is_youtube_url("https://m.youtube.com/watch?v=abc")
    assert is_youtube_url("https://www.youtube.com:443/watch?v=abc")
    assert not is_youtube_url("https://www.fakeyoutube.com/watch?v=abc")
    assert not is_youtube_url("https://youtube.com.evil.com/watch?v=abc")
    assert not is_youtube_url("https://www.instagram.com/reel/abc/")
    assert not is_youtube_url("https://www.tiktok.com/@user/video/123")
    assert not is_youtube_url(None)
    assert not is_youtube_url("")


def test_is_youtube_info_reads_extractor_keys() -> None:
    assert is_youtube_info({"extractor_key": "Youtube"})
    assert is_youtube_info({"extractor": "youtube"})
    assert is_youtube_info({"webpage_url": "https://youtu.be/abc"})
    assert not is_youtube_info({"extractor_key": "Instagram"})
    assert not is_youtube_info({})
    assert not is_youtube_info(None)


def test_maybe_configure_sponsorblock_skips_non_youtube_url() -> None:
    opts: dict[str, Any] = {}
    applied = maybe_configure_sponsorblock(
        opts, url="https://www.instagram.com/reel/abc/"
    )
    assert applied is False
    assert opts.get("postprocessors") is None


def test_maybe_configure_sponsorblock_applies_for_youtube() -> None:
    opts: dict[str, Any] = {}
    applied = maybe_configure_sponsorblock(
        opts, url="https://www.youtube.com/watch?v=abc"
    )
    assert applied is True
    assert [pp["key"] for pp in opts["postprocessors"]] == [
        "SponsorBlock",
        "ModifyChapters",
    ]


def test_maybe_configure_sponsorblock_prefers_info_over_url() -> None:
    opts: dict[str, Any] = {}
    applied = maybe_configure_sponsorblock(
        opts,
        url="https://www.youtube.com/watch?v=abc",
        info={"extractor_key": "Instagram"},
    )
    assert applied is False
    assert opts.get("postprocessors") is None


def test_maybe_configure_sponsorblock_honors_unknown_source() -> None:
    """Undeterminable sources keep the old behavior (e.g. uninspected info.json)."""
    opts: dict[str, Any] = {}
    assert maybe_configure_sponsorblock(opts) is True
    assert "postprocessors" in opts
