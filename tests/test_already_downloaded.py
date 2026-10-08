"""Tests for the already-downloaded advisory and the hooks that feed it."""

from io import StringIO

from rich.console import Console

from ydlx import (
    ALREADY_DOWNLOADED_HINT,
    DownloadRecord,
    MyLogger,
    TrackingYoutubeDL,
    build_downloader,
    report_already_downloaded,
)


class _RecordingLogger(MyLogger):
    """Captures what yt-dlp reports, so delegation can be asserted on."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[str] = []

    def debug(self, msg: str) -> None:
        self.seen.append(msg)


def _capture(record: DownloadRecord) -> str:
    buffer = StringIO()
    report_already_downloaded(record, Console(file=buffer, width=200))
    return buffer.getvalue()


# --- Recording the skip ---------------------------------------------------
# The signal is the method yt-dlp calls when it declines a download, which is
# handed the path it found. Nothing in this section parses a message.


def test_the_override_records_the_path_it_is_given() -> None:
    record = DownloadRecord()

    with TrackingYoutubeDL({}, record) as ydl:
        ydl.report_file_already_downloaded("C:/out/Some Video [abc123].mp4")

    assert record.skipped == ["C:/out/Some Video [abc123].mp4"]


def test_the_override_still_lets_yt_dlp_report_the_skip() -> None:
    """Swallowing the message would lose what yt-dlp prints on its own terms."""
    record = DownloadRecord()
    logger = _RecordingLogger()

    with TrackingYoutubeDL({"logger": logger}, record) as ydl:
        ydl.report_file_already_downloaded("one.mp4")

    assert [line for line in logger.seen if "already been downloaded" in line]


def test_a_log_line_cannot_produce_a_skip() -> None:
    """
    A video titled with the old search phrase must not be reported as skipped.

    The phrase used to be matched anywhere in yt-dlp's output, so any line
    carrying it counted. Recording is driven by the callback alone now, so
    output text is no longer an input to it.
    """
    record = DownloadRecord()
    logger = MyLogger()

    with build_downloader({"logger": logger}, record):
        logger.debug("[download] Has already been downloaded.mp4")
        logger.debug("[info] Some Video [abc123].mp4")

    assert record.skipped == []


def test_every_skipped_file_is_recorded_on_a_playlist() -> None:
    record = DownloadRecord()

    with TrackingYoutubeDL({}, record) as ydl:
        for name in ("one.mp4", "two.mp4", "three.mp4"):
            ydl.report_file_already_downloaded(name)

    assert record.skipped == ["one.mp4", "two.mp4", "three.mp4"]


# --- Reporting it ---------------------------------------------------------


def test_report_already_downloaded_is_silent_when_nothing_was_skipped() -> None:
    assert _capture(DownloadRecord()) == ""


def test_report_already_downloaded_lists_every_skipped_file() -> None:
    record = DownloadRecord()
    record.skipped = ["one.mp4", "two.mp4"]

    output = _capture(record)

    assert "one.mp4" in output
    assert "two.mp4" in output


def test_report_already_downloaded_shows_the_whole_path() -> None:
    """A bare name is ambiguous on a playlist; the path is not."""
    record = DownloadRecord()
    record.skipped = ["/home/me/Downloads/video/Some Video [abc123].mp4"]

    output = _capture(record)

    assert "/home/me/Downloads/video/Some Video [abc123].mp4" in output


def test_report_already_downloaded_says_why_each_file_was_listed() -> None:
    record = DownloadRecord()
    record.skipped = ["one.mp4"]

    assert "Already downloaded" in _capture(record)


def test_report_already_downloaded_explains_the_cause_once() -> None:
    record = DownloadRecord()
    record.skipped = ["one.mp4", "two.mp4"]

    output = _capture(record)

    assert output.count(ALREADY_DOWNLOADED_HINT) == 1
    assert "matched by name" in output
    assert "force a redownload" in output


def test_report_already_downloaded_collapses_repeated_skips() -> None:
    """The subtitle retry reuses the record, so attempt two re-appends."""
    record = DownloadRecord()
    record.skipped = ["one.mp4", "two.mp4", "one.mp4", "one.mp4"]

    output = _capture(record)

    assert output.count("one.mp4") == 1
    assert output.count("two.mp4") == 1
    assert output.count(ALREADY_DOWNLOADED_HINT) == 1


def test_report_already_downloaded_keeps_first_seen_order() -> None:
    record = DownloadRecord()
    record.skipped = ["two.mp4", "one.mp4", "two.mp4"]

    output = _capture(record)

    assert output.index("two.mp4") < output.index("one.mp4")


# --- Wiring the collector in ---------------------------------------------
# Subtitle files are read by a postprocessor, and before_dl is the only phase
# that still runs once skip_download keeps the media from being fetched.


def test_the_collector_is_registered_in_before_dl() -> None:
    with build_downloader({}, DownloadRecord()) as ydl:
        assert [pp.pp_key() for pp in ydl._pps["before_dl"]] == ["SubtitleCollector"]


def test_the_collector_runs_after_the_caller_postprocessors() -> None:
    """
    Being last in the phase is what makes it see the converted subtitle name.

    FFmpegSubtitlesConvertor rewrites the track's filepath to the .srt it
    produced; a collector ahead of it would record the .vtt source instead.
    """
    opts = {
        "postprocessors": [
            {"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"}
        ]
    }

    with build_downloader(opts, DownloadRecord()) as ydl:
        # pp_key() drops the FFmpeg prefix, so the convertor reads as
        # "SubtitlesConvertor" here.
        assert [pp.pp_key() for pp in ydl._pps["before_dl"]] == [
            "SubtitlesConvertor",
            "SubtitleCollector",
        ]


def test_each_downloader_records_into_its_own_record() -> None:
    """Two runs must not see each other's files through a shared collector."""
    first = DownloadRecord()

    with build_downloader({}, first) as ydl:
        collectors = list(ydl._pps["before_dl"])

    second = DownloadRecord()
    with build_downloader({}, second) as ydl:
        assert collectors[0].record is first
        assert ydl._pps["before_dl"][0].record is second
