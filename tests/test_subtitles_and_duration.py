"""Tests for configure_subtitles, sub-language resolution, and make_duration_filter."""

from typing import Any, cast

import pytest
import yt_dlp

from ydlx import (
    configure_subtitles,
    make_duration_filter,
    make_subtitle_convertor,
    parse_sub_langs,
    requested_sub_langs,
)


def test_configure_subtitles_is_noop_without_requests() -> None:
    opts: dict[str, Any] = {}
    configure_subtitles(opts)
    assert opts == {}


def test_configure_subtitles_write_subs_sets_download_options() -> None:
    opts: dict[str, Any] = {}
    configure_subtitles(opts, write_subs=True, auto_subs=True, sub_langs="en,ar")

    assert opts["writesubtitles"] is True
    assert opts["writeautomaticsub"] is True
    assert opts["subtitleslangs"] == ["en", "ar"]


def test_configure_subtitles_omits_automatic_subs_when_disabled() -> None:
    opts: dict[str, Any] = {}
    configure_subtitles(opts, write_subs=True, auto_subs=False)
    assert "writeautomaticsub" not in opts


def test_configure_subtitles_parses_and_trims_language_list() -> None:
    opts: dict[str, Any] = {}
    configure_subtitles(opts, write_subs=True, sub_langs=" en , ar ,,")
    assert opts["subtitleslangs"] == ["en", "ar"]


def test_configure_subtitles_leaves_blank_langs_unpinned() -> None:
    """A blank list is the default request, so yt-dlp still chooses the track."""
    opts: dict[str, Any] = {}
    configure_subtitles(opts, write_subs=True, sub_langs=" , ")
    assert "subtitleslangs" not in opts


def test_configure_subtitles_uses_native_best_source_for_srt() -> None:
    """srt is rarely a native format, so the source track is 'best' and converted."""
    opts: dict[str, Any] = {}
    configure_subtitles(opts, write_subs=True, sub_format="srt")
    assert opts["subtitlesformat"] == "best"


def test_configure_subtitles_keeps_requested_source_format() -> None:
    opts: dict[str, Any] = {}
    configure_subtitles(opts, write_subs=True, sub_format="vtt")
    assert opts["subtitlesformat"] == "vtt"


def _keys(opts: dict[str, Any]) -> list[str]:
    return [pp["key"] for pp in opts.get("postprocessors", [])]


def test_configure_subtitles_converter_runs_before_download() -> None:
    """The converter must run in the before_dl phase or subs are never written."""
    opts: dict[str, Any] = {}
    configure_subtitles(opts, write_subs=True, sub_format="srt")

    converters = [
        pp for pp in opts["postprocessors"] if pp["key"] == "FFmpegSubtitlesConvertor"
    ]
    assert len(converters) == 1
    assert converters[0]["when"] == "before_dl"
    assert converters[0]["format"] == "srt"


def test_configure_subtitles_skips_converter_for_best() -> None:
    opts: dict[str, Any] = {}
    configure_subtitles(opts, write_subs=True, sub_format="best")
    assert "FFmpegSubtitlesConvertor" not in _keys(opts)


def test_configure_subtitles_appends_to_existing_postprocessors() -> None:
    """Must not clobber a postprocessor the caller already installed."""
    opts: dict[str, Any] = {"postprocessors": [{"key": "FFmpegExtractAudio"}]}
    configure_subtitles(opts, write_subs=True)

    assert opts["postprocessors"][0]["key"] == "FFmpegExtractAudio"
    assert "FFmpegSubtitlesConvertor" in _keys(opts)


def test_make_subtitle_convertor_targets_the_requested_format() -> None:
    for fmt in ("srt", "vtt", "SRT"):
        convertor = make_subtitle_convertor(fmt)
        assert convertor is not None
        assert convertor["key"] == "FFmpegSubtitlesConvertor"
        assert convertor["format"] == fmt.lower()
        assert convertor["when"] == "before_dl"


def test_make_subtitle_convertor_skips_non_convertible_formats() -> None:
    assert make_subtitle_convertor("best") is None


def test_configure_subtitles_embed_flags_availability() -> None:
    opts: dict[str, Any] = {}
    configure_subtitles(opts, embed_subs=True, write_subs=False)
    embed = next(
        pp for pp in opts["postprocessors"] if pp["key"] == "FFmpegEmbedSubtitle"
    )
    assert embed["already_have_subtitle"] is False


def test_configure_subtitles_embed_after_write_records_existing_file() -> None:
    opts: dict[str, Any] = {}
    configure_subtitles(opts, embed_subs=True, write_subs=True)
    embed = next(
        pp for pp in opts["postprocessors"] if pp["key"] == "FFmpegEmbedSubtitle"
    )
    assert embed["already_have_subtitle"] is True


@pytest.mark.parametrize("sub_format", ["srt", "best"])
def test_embedding_always_selects_and_converts_to_vtt(sub_format: str) -> None:
    opts: dict[str, Any] = {}

    configure_subtitles(
        opts,
        embed_subs=True,
        sub_format=sub_format,
    )

    assert opts["subtitlesformat"] == "vtt"
    convertor = next(
        pp for pp in opts["postprocessors"] if pp["key"] == "FFmpegSubtitlesConvertor"
    )
    assert convertor["format"] == "vtt"


# --- Sub-language pinning -------------------------------------------------
# yt-dlp fullmatch-es subtitleslangs and only runs its own fallback chain when
# the option is unset. Pinning "en" suppressed that chain, so a video whose only
# English track is named "en-ehkg1hFWq8A" yielded no subtitles at all. Letting
# yt-dlp choose for the default avoids reimplementing a worse version of its own
# selection logic.


def test_default_lang_is_not_pinned() -> None:
    """Unset is what makes yt-dlp prefer an official track over an auto caption."""
    assert requested_sub_langs("en") is None
    assert requested_sub_langs("  en  ") is None
    assert requested_sub_langs("") is None


@pytest.mark.parametrize("raw", ["ar", "en,ar", "ar, en", "pt-BR", "all", "en.*"])
def test_explicit_choice_is_pinned(raw: str) -> None:
    assert requested_sub_langs(raw) == parse_sub_langs(raw)


def test_configure_subtitles_leaves_default_unpinned() -> None:
    opts: dict[str, Any] = {}
    configure_subtitles(opts, write_subs=True, sub_langs="en")
    assert "subtitleslangs" not in opts
    assert opts["writesubtitles"] is True


def test_configure_subtitles_pins_an_explicit_choice() -> None:
    opts: dict[str, Any] = {}
    configure_subtitles(opts, write_subs=True, sub_langs="ar")
    assert opts["subtitleslangs"] == ["ar"]


def test_configure_subtitles_pins_a_multi_language_request() -> None:
    opts: dict[str, Any] = {}
    configure_subtitles(opts, write_subs=True, sub_langs="en,ar")
    assert opts["subtitleslangs"] == ["en", "ar"]


def test_configure_subtitles_accepts_resolved_track_keys() -> None:
    opts: dict[str, Any] = {}
    configure_subtitles(
        opts,
        write_subs=True,
        sub_langs="en",
        pin_sub_langs=["en-US"],
    )
    assert opts["subtitleslangs"] == ["en-US"]


def test_yt_dlp_fallback_finds_the_asr_suffixed_official_track() -> None:
    """The bug that started this: "en" pinned would have matched nothing here.

    With subtitleslangs unset, yt-dlp's own chain checks the official tracks
    first and resolves to "en-ehkg1hFWq8A", the real English track.
    """
    normal = {"en-ehkg1hFWq8A": [{"ext": "vtt", "url": "official"}]}
    ydl = yt_dlp.YoutubeDL(
        cast(Any, {"writesubtitles": True, "quiet": True, "no_warnings": True})
    )
    assert list(ydl.process_subtitles("VID", normal, {}) or {}) == ["en-ehkg1hFWq8A"]


def test_pinning_the_default_would_have_missed_it() -> None:
    """Pins the yt-dlp behaviour the fix relies on, so it cannot silently change."""
    normal = {"en-ehkg1hFWq8A": [{"ext": "vtt", "url": "official"}]}
    ydl = yt_dlp.YoutubeDL(
        cast(
            Any,
            {
                "writesubtitles": True,
                "subtitleslangs": ["en"],
                "quiet": True,
                "no_warnings": True,
            },
        )
    )
    assert (ydl.process_subtitles("VID", normal, {}) or {}) == {}


def test_unpinned_selection_prefers_official_over_translated_auto() -> None:
    """The auto "en" key here holds a Ukrainian ASR track translated to English.

    Key-based matching on automatic_captions cannot tell that apart from a real
    English track, which is why ydlx defers to yt-dlp rather than matching names.
    """
    normal = {"en-ehkg1hFWq8A": [{"ext": "vtt", "url": "official"}]}
    auto = {"en": [{"ext": "vtt", "url": "kind=asr&lang=uk&tlang=en"}]}
    ydl = yt_dlp.YoutubeDL(
        cast(Any, {"writesubtitles": True, "quiet": True, "no_warnings": True})
    )
    chosen = ydl.process_subtitles("VID", normal, auto) or {}
    assert list(chosen) == ["en-ehkg1hFWq8A"]
    assert chosen["en-ehkg1hFWq8A"]["url"] == "official"


@pytest.mark.parametrize("raw", ["en.*", "(en|ar)", "e.*", "all"])
def test_parse_sub_langs_preserves_deliberate_patterns(raw: str) -> None:
    assert parse_sub_langs(raw) == [raw]


@pytest.mark.parametrize("raw", ["en", "pt-BR", "zh-Hans", "es-419"])
def test_parse_sub_langs_keeps_codes_verbatim(raw: str) -> None:
    assert parse_sub_langs(raw) == [raw]


def test_parse_sub_langs_drops_empty_entries() -> None:
    assert parse_sub_langs("en, ,ar,") == ["en", "ar"]


def test_parse_sub_langs_defaults_to_english() -> None:
    assert parse_sub_langs("  ") == ["en"]


def test_duration_filter_passes_when_no_bounds() -> None:
    duration_filter = make_duration_filter()
    assert duration_filter({"duration": 42}) is None


def test_duration_filter_rejects_short_and_long() -> None:
    duration_filter = make_duration_filter(min_len=60, max_len=600)
    assert "too short" in (duration_filter({"duration": 30}) or "")
    assert "too long" in (duration_filter({"duration": 900}) or "")
    assert duration_filter({"duration": 300}) is None


def test_duration_filter_boundaries_are_inclusive() -> None:
    """Exactly-at-the-limit durations must pass."""
    duration_filter = make_duration_filter(min_len=60, max_len=600)
    assert duration_filter({"duration": 60}) is None
    assert duration_filter({"duration": 600}) is None


def test_duration_filter_ignores_missing_duration() -> None:
    """Live/undetermined durations must not be rejected."""
    duration_filter = make_duration_filter(min_len=60, max_len=600)
    assert duration_filter({}) is None
    assert duration_filter({"duration": None}) is None


def test_duration_filter_open_ended_bounds() -> None:
    assert make_duration_filter(min_len=60)({"duration": 30}) is not None
    assert make_duration_filter(max_len=600)({"duration": 900}) is not None
    assert make_duration_filter(max_len=600)({"duration": 300}) is None
