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


# --- Resolution prompt ---------------------------------------------------


def answer_prompts(monkeypatch: pytest.MonkeyPatch, responses: list[str]) -> list[str]:
    """Feeds scripted answers to Rich prompts and records what was asked."""
    asked: list[str] = []
    queue = list(responses)

    def fake_ask(self: object, prompt: str = "", **kwargs: object) -> str:
        asked.append(prompt)
        return queue.pop(0)

    monkeypatch.setattr(ydlx.Prompt, "ask", fake_ask, raising=False)
    return asked


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("720p", 720),
        ("720", 720),
        (" 1080P ", 1080),
        ("720 p", 720),
        ("2160P", 2160),
        # 0 and a blank answer both mean "no limit".
        ("0", None),
        ("0p", None),
        ("", None),
    ],
)
def test_prompt_for_max_height_accepts_common_input(
    raw: str, expected: int | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prompt suggests the "p" suffix, so it must also accept it."""
    answer_prompts(monkeypatch, [raw])
    assert ydlx.prompt_for_max_height(ydlx.Console()) == expected


@pytest.mark.parametrize("bad", ["abc", "-5", "1080i", "720pp", "1,080"])
def test_prompt_for_max_height_reprompts_on_bad_input(
    bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = answer_prompts(monkeypatch, [bad, "480p"])
    assert ydlx.prompt_for_max_height(ydlx.Console()) == 480
    assert len(asked) == 2


def test_prompt_for_max_height_suggests_common_heights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-technical users need the common values spelled out."""
    console = ydlx.Console()
    monkeypatch.setattr(ydlx.Prompt, "ask", lambda self, p="", **k: "0")
    with console.capture() as capture:
        ydlx.prompt_for_max_height(console)
    output = capture.get()
    assert "720p" in output
    assert "1080p" in output
    assert "0 = highest available" in output
