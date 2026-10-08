"""Tests for how report_subtitle_download decides what the run achieved.

The count comes from the tracks yt-dlp resolved, read back from the info dict by
a postprocessor. It did not used to: an earlier version diffed the output
directory, and a re-run rewrites the same filenames, so the diff was empty and a
download that had in fact succeeded was reported as a failure.
"""

from io import StringIO
from pathlib import Path
from typing import Any, cast

import pytest
from rich.console import Console

import ydlx
from ydlx import DownloadRecord, SubtitleCollectorPP, report_subtitle_download

URL = "https://youtu.be/abc123"
NO_HINT = "allow auto-generated captions"


def _info(*paths: str | None, key: str = "requested_subtitles") -> dict[str, Any]:
    """Builds the part of an info dict the collector reads."""
    tracks = {
        f"lang{index}": ({"filepath": path} if path else {"ext": "vtt"})
        for index, path in enumerate(paths)
    }
    return {key: tracks}


def _collect(*paths: str | None) -> int:
    record = DownloadRecord()
    SubtitleCollectorPP(record).run(cast(Any, _info(*paths)))

    return record.subtitle_count


# --- The collector --------------------------------------------------------


def test_each_resolved_track_counts_once() -> None:
    assert _collect("video.en.vtt", "video.ar.vtt") == 2


def test_a_track_without_a_path_is_not_counted() -> None:
    """yt-dlp leaves filepath unset for a track it could not resolve."""
    assert _collect("video.en.vtt", None) == 1


def test_a_video_with_no_subtitles_records_nothing() -> None:
    assert _collect() == 0


def test_a_missing_track_key_is_not_an_error() -> None:
    record = DownloadRecord()

    SubtitleCollectorPP(record).run(cast(Any, {"id": "abc123"}))

    assert record.subtitle_count == 0


def test_it_replaces_rather_than_sums() -> None:
    """The count describes one video, so a playlist entry cannot inherit."""
    record = DownloadRecord()
    collector = SubtitleCollectorPP(record)

    collector.run(cast(Any, _info("first.en.vtt", "first.ar.vtt")))
    collector.run(cast(Any, _info("second.en.vtt")))

    assert record.subtitle_count == 1


def test_the_same_language_cannot_be_counted_twice() -> None:
    """Tracks are keyed by language, so a repeat is impossible by construction."""
    info = cast(Any, {"requested_subtitles": {"en": {"filepath": "video.en.vtt"}}})
    record = DownloadRecord()
    SubtitleCollectorPP(record).run(cast(Any, info))
    SubtitleCollectorPP(record).run(info)

    assert record.subtitle_count == 1


def test_the_collector_does_not_delete_or_move_anything() -> None:
    """run() must report no files for yt-dlp to clean up."""
    record = DownloadRecord()

    deleted, info = SubtitleCollectorPP(record).run(cast(Any, _info("video.en.vtt")))

    assert deleted == []
    assert "requested_subtitles" in info


# --- Reporting it ---------------------------------------------------------


def _run(
    target_dir: Path, monkeypatch: pytest.MonkeyPatch, count: int
) -> tuple[int, str]:
    """Runs the report against a stubbed download that resolved `count` tracks."""

    def fake_download(*args: Any, **kwargs: Any) -> int:
        record = cast(DownloadRecord, kwargs["record"])
        record.subtitle_count = count
        return 0

    monkeypatch.setattr(ydlx, "download_subtitles_only", fake_download)

    buffer = StringIO()
    code = report_subtitle_download(
        URL,
        target_dir,
        Console(file=buffer, width=200),
        langs="en",
        auto_subs=True,
        sub_format="srt",
        browser_spec=None,
    )
    return code, buffer.getvalue()


def test_reports_success_when_a_track_was_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, output = _run(tmp_path / "video", monkeypatch, 1)

    assert code == 0
    assert "Downloaded 1 subtitle file(s)" in output


def test_counts_every_written_track(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, output = _run(tmp_path / "video", monkeypatch, 2)

    assert code == 0
    assert "Downloaded 2 subtitle file(s)" in output


def test_a_rewritten_track_is_a_success_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression: a re-run rewrites the same name, so a diff was empty."""
    target_dir = tmp_path / "video"
    target_dir.mkdir()
    (target_dir / "video.en.srt").write_bytes(b"same bytes as last run")

    code, output = _run(target_dir, monkeypatch, 1)

    assert code == 0
    assert "Downloaded 1 subtitle file(s)" in output
    assert "No subtitles found" not in output


def test_reports_failure_when_nothing_was_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, output = _run(tmp_path / "video", monkeypatch, 0)

    assert code == 1
    assert "No subtitles found for 'en'" in output
    assert NO_HINT in output


def test_does_not_read_the_output_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing may depend on the directory existing; only the record is read."""
    target_dir = tmp_path / "never-created"

    code, _ = _run(target_dir, monkeypatch, 1)

    assert code == 0
    assert not target_dir.exists()


def test_a_failed_download_is_reported_before_the_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_download(*args: Any, **kwargs: Any) -> int:
        return 1

    monkeypatch.setattr(ydlx, "download_subtitles_only", fake_download)
    buffer = StringIO()
    code = report_subtitle_download(
        URL,
        tmp_path / "video",
        Console(file=buffer, width=200),
        langs="en",
        auto_subs=True,
        sub_format="srt",
        browser_spec=None,
    )

    assert code == 1
    assert "Subtitles download failed" in buffer.getvalue()
    assert "No subtitles found" not in buffer.getvalue()


# --- Telling a track apart from a media file -----------------------------
# A track fetched over HTTP runs through the same progress hooks as media, so
# without this the run printed a green "Finished downloading" for a .vtt it was
# about to convert and delete. Both flows hit this: downloading subtitles alone,
# and downloading a video with --subs alongside it.


# A media event carries the whole video info; a subtitle event carries the
# track's own dict. Both shapes below are verbatim from yt-dlp 2026.08.19.
MEDIA_INFO = {"id": "dQw4w9WgXcQ", "title": "Never Gonna Give You Up", "ext": "mp4"}
TRACK_INFO = {
    "ext": "vtt",
    "name": "English",
    "url": "https://example.test/timedtext",
    "protocol": "https",
}


def _run_hook(
    monkeypatch: pytest.MonkeyPatch, status: str, info: dict[str, Any]
) -> str:
    """Feeds one progress event through a tracker and returns what it printed."""
    buffer = StringIO()
    monkeypatch.setattr(
        ydlx, "Console", lambda *a, **k: Console(file=buffer, width=200)
    )
    tracker = ydlx.DownloadTracker()
    tracker.hook({"status": status, "filename": "x", "info_dict": info})

    return buffer.getvalue()


def test_a_media_event_is_announced(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "Finished downloading" in _run_hook(monkeypatch, "finished", MEDIA_INFO)


def test_a_track_event_is_not_announced(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_hook(monkeypatch, "finished", TRACK_INFO) == ""


def test_a_track_draws_no_progress_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A track is not media, so the tracker must not claim to be downloading it."""
    monkeypatch.setattr(ydlx, "Console", lambda *a, **k: Console(file=StringIO()))
    tracker = ydlx.DownloadTracker()

    tracker.hook(
        {
            "status": "downloading",
            "filename": "x.en.vtt",
            "info_dict": TRACK_INFO,
            "total_bytes": 10,
        }
    )

    assert tracker.progress is None
    assert tracker.task_id is None


def test_a_track_does_not_close_a_live_media_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Subtitles are written before the media, but the hook order is not ours to
    assume, so a track must leave an in-flight media bar alone.
    """
    monkeypatch.setattr(ydlx, "Console", lambda *a, **k: Console(file=StringIO()))
    tracker = ydlx.DownloadTracker()
    tracker.hook(
        {
            "status": "downloading",
            "filename": "x.mp4",
            "info_dict": MEDIA_INFO,
            "total_bytes": 10,
        }
    )
    assert tracker.progress is not None

    tracker.hook(
        {"status": "finished", "filename": "x.en.vtt", "info_dict": TRACK_INFO}
    )

    assert tracker.progress is not None


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        (TRACK_INFO, True),
        (MEDIA_INFO, False),
        ({}, True),
    ],
)
def test_the_tell_is_the_video_id(info: dict[str, Any], expected: bool) -> None:
    """
    Media always carries the id the output template is built from; a track dict
    never does. Checked structurally because yt-dlp's own subtitle extension list
    covers only ass/lrc/srt/vtt and would miss json, ttml and dfxp.
    """
    assert ydlx.is_subtitle_download({"info_dict": info}) is expected


def test_a_hook_with_no_info_dict_is_treated_as_a_track() -> None:
    """Nothing identifies it as media, so it must not be announced."""
    assert ydlx.is_subtitle_download({}) is True
