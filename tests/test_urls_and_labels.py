"""Tests for URL normalization/validation and codec labelling."""

import pytest
import typer

import ydlx
from ydlx import _video_codec_label, normalize_url, parse_cli_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.youtube.com/watch?v=abc", "https://www.youtube.com/watch?v=abc"),
        ("http://youtu.be/abc", "http://youtu.be/abc"),
        # Bare hosts gain an https scheme.
        ("youtube.com/watch?v=abc", "https://youtube.com/watch?v=abc"),
        ("youtu.be/abc", "https://youtu.be/abc"),
        # Surrounding whitespace is stripped.
        ("  https://youtu.be/abc  ", "https://youtu.be/abc"),
    ],
)
def test_normalize_url_accepts_valid_input(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        # No scheme and no dot in the first segment: not a hostname.
        "not a url",
        "abc",
        # Unsupported scheme.
        "ftp://example.com/file",
        "file:///etc/passwd",
        # Scheme present but no host.
        "https://",
    ],
)
def test_normalize_url_rejects_invalid_input(raw: str) -> None:
    assert normalize_url(raw) is None


def test_parse_cli_url_returns_normalized_url() -> None:
    assert parse_cli_url("youtu.be/abc") == "https://youtu.be/abc"


def test_parse_cli_url_exits_on_invalid_input() -> None:
    with pytest.raises(typer.Exit) as exc_info:
        parse_cli_url("nonsense")
    assert exc_info.value.exit_code == 1


@pytest.mark.parametrize(
    ("vcodec", "expected"),
    [
        ("av01.0.08M.08", "AV1"),
        ("vp9", "VP9"),
        ("vp09.00.10.08", "VP9"),
        ("avc1.640028", "H.264"),
        ("h264", "H.264"),
        ("hvc1.1.6.L93.B0", "H.265"),
        ("hev1.1.6.L93.B0", "H.265"),
        ("vp8", "VP8"),
        ("none", "none"),
        ("", "unknown"),
        ("somecodec", "somecodec"),
    ],
)
def test_video_codec_label(vcodec: str, expected: str) -> None:
    assert _video_codec_label(vcodec) == expected


def test_strip_ansi_removes_escape_sequences() -> None:
    assert ydlx.strip_ansi("\x1b[1;31mERROR\x1b[0m: boom") == "ERROR: boom"


def test_strip_ansi_leaves_plain_text_untouched() -> None:
    assert ydlx.strip_ansi("plain text") == "plain text"
