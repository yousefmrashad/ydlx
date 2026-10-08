"""Tests for the already-downloaded advisory."""

from io import StringIO

from rich.console import Console

from ydlx import ALREADY_DOWNLOADED_HINT, MyLogger, report_already_downloaded


def _capture(logger: MyLogger) -> str:
    buffer = StringIO()
    report_already_downloaded(logger, Console(file=buffer, width=200))
    return buffer.getvalue()


def test_report_already_downloaded_is_silent_when_nothing_was_skipped() -> None:
    assert _capture(MyLogger()) == ""


def test_report_already_downloaded_lists_every_skipped_file() -> None:
    logger = MyLogger()
    logger.already_downloaded = [
        "one.mp4 has already been downloaded",
        "two.mp4 has already been downloaded",
    ]

    output = _capture(logger)

    assert "one.mp4 has already been downloaded" in output
    assert "two.mp4 has already been downloaded" in output


def test_report_already_downloaded_explains_the_cause_once() -> None:
    logger = MyLogger()
    logger.already_downloaded = [
        "one.mp4 has already been downloaded",
        "two.mp4 has already been downloaded",
    ]

    output = _capture(logger)

    assert output.count(ALREADY_DOWNLOADED_HINT) == 1
    assert "matched by name" in output
    assert "force a redownload" in output


def test_report_already_downloaded_warns_even_when_not_verbose() -> None:
    """The skipped files are dropped by info() unless -v, so this must not be."""
    logger = MyLogger(verbose=False)
    logger.already_downloaded = ["one.mp4 has already been downloaded"]

    assert "one.mp4" in _capture(logger)


def test_report_already_downloaded_collapses_repeated_skips() -> None:
    """The subtitle retry reuses the logger, so attempt two re-appends."""
    logger = MyLogger()
    line = "one.mp4 has already been downloaded"
    logger.already_downloaded = [
        line,
        "two.mp4 has already been downloaded",
        line,
        line,
    ]

    output = _capture(logger)

    assert output.count(line) == 1
    assert output.count("two.mp4") == 1
    assert output.count(ALREADY_DOWNLOADED_HINT) == 1


def test_report_already_downloaded_keeps_first_seen_order() -> None:
    logger = MyLogger()
    logger.already_downloaded = [
        "two.mp4 has already been downloaded",
        "one.mp4 has already been downloaded",
        "two.mp4 has already been downloaded",
    ]

    output = _capture(logger)

    assert output.index("two.mp4") < output.index("one.mp4")
