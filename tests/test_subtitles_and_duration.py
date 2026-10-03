"""Tests for configure_subtitles and make_duration_filter."""

from typing import Any

from ydlx import configure_subtitles, make_duration_filter


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


def test_configure_subtitles_falls_back_to_english_for_blank_langs() -> None:
    opts: dict[str, Any] = {}
    configure_subtitles(opts, write_subs=True, sub_langs=" , ")
    assert opts["subtitleslangs"] == ["en"]


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
