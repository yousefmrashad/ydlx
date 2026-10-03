"""Tests for the format-spec builders.

The selector-resolution tests are the important ones: they run the generated
selectors through yt-dlp's own parser against a fabricated YouTube-like format
list, so a change that still produces a plausible-looking string but resolves to
the wrong formats gets caught.
"""

from typing import Any, cast

import pytest
import yt_dlp

from ydlx import VideoPreset, make_audio_format_spec, make_video_format_spec

# Mirrors a typical YouTube format list: pre-merged legacy stream, H.264 and
# AV1 video-only streams, VP9 video-only, plus m4a/opus audio-only streams.
FORMATS: list[dict[str, Any]] = [
    {
        "format_id": "139",
        "ext": "m4a",
        "vcodec": "none",
        "acodec": "mp4a.40.2",
        "abr": 48,
        "filesize": 3_500_000,
        "protocol": "https",
    },
    {
        "format_id": "140",
        "ext": "m4a",
        "vcodec": "none",
        "acodec": "mp4a.40.2",
        "abr": 128,
        "filesize": 9_800_000,
        "protocol": "https",
    },
    {
        "format_id": "251",
        "ext": "webm",
        "vcodec": "none",
        "acodec": "opus",
        "abr": 160,
        "filesize": 12_000_000,
        "protocol": "https",
    },
    {
        "format_id": "18",
        "ext": "mp4",
        "vcodec": "avc1.42001E",
        "acodec": "mp4a.40.2",
        "height": 360,
        "tbr": 500,
        "filesize": 10_000_000,
        "protocol": "https",
    },
    {
        "format_id": "136",
        "ext": "mp4",
        "vcodec": "avc1.4d401f",
        "acodec": "none",
        "height": 720,
        "tbr": 1500,
        "filesize": 40_000_000,
        "protocol": "https",
    },
    {
        "format_id": "137",
        "ext": "mp4",
        "vcodec": "avc1.640028",
        "acodec": "none",
        "height": 1080,
        "tbr": 4000,
        "filesize": 90_000_000,
        "protocol": "https",
    },
    {
        "format_id": "248",
        "ext": "webm",
        "vcodec": "vp9",
        "acodec": "none",
        "height": 1080,
        "tbr": 3500,
        "filesize": 70_000_000,
        "protocol": "https",
    },
    {
        "format_id": "399",
        "ext": "mp4",
        "vcodec": "av01.0.08M.08",
        "acodec": "none",
        "height": 1080,
        "tbr": 2500,
        "filesize": 55_000_000,
        "protocol": "https",
    },
]


def resolve(spec: str) -> str:
    """Runs a selector through yt-dlp and returns the chosen format ids."""
    ydl_params: dict[str, Any] = {"format": spec, "simulate": True, "quiet": True}
    ydl = yt_dlp.YoutubeDL(cast(Any, ydl_params))
    selector = ydl.build_format_selector(spec)
    ctx: dict[str, Any] = {
        "formats": FORMATS,
        "incomplete": False,
        "incomplete_formats": False,
    }
    chosen = [f for f in selector(ctx) if f.get("format_id") != "0"]
    return "+".join(str(f["format_id"]) for f in chosen) if chosen else "<none>"


@pytest.mark.parametrize(
    ("preset", "max_height", "expected"),
    [
        (
            VideoPreset.UNIVERSAL,
            None,
            "bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        ),
        (
            VideoPreset.UNIVERSAL,
            720,
            "bestvideo[vcodec^=avc1][height<=720]+bestaudio[ext=m4a]"
            "/best[ext=mp4][height<=720]/best",
        ),
        (VideoPreset.BEST, None, "bestvideo+bestaudio/best"),
        (
            VideoPreset.BEST,
            720,
            "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        ),
        (
            VideoPreset.BEST,
            100,
            "bestvideo[height<=100]+bestaudio/best[height<=100]/best",
        ),
    ],
)
def test_video_format_spec_strings(
    preset: VideoPreset, max_height: int | None, expected: str
) -> None:
    assert make_video_format_spec(preset, max_height) == expected


@pytest.mark.parametrize(
    ("preset", "max_height", "expected"),
    [
        # Universal picks H.264 video + m4a audio, so the merge lands in MP4.
        (VideoPreset.UNIVERSAL, None, "137+140"),
        (VideoPreset.UNIVERSAL, 720, "136+140"),
        # Only 360p H.264 exists at/below 360, and it is already muxed.
        (VideoPreset.UNIVERSAL, 360, "18"),
        # Best takes yt-dlp's highest ranked formats (here AV1 + opus).
        (VideoPreset.BEST, None, "399+251"),
        (VideoPreset.BEST, 720, "136+251"),
    ],
)
def test_video_format_spec_resolves_expected_formats(
    preset: VideoPreset, max_height: int | None, expected: str
) -> None:
    assert resolve(make_video_format_spec(preset, max_height)) == expected


@pytest.mark.parametrize("preset", list(VideoPreset))
def test_video_format_spec_never_errors_on_impossible_cap(preset: VideoPreset) -> None:
    """An over-tight --res degrades to the nearest available, never to a failure."""
    assert resolve(make_video_format_spec(preset, 1)) != "<none>"


def test_video_format_spec_avoids_duplicate_uncapped_fallback() -> None:
    """Regression: the uncapped 'best' branch must not emit '/best/best'."""
    assert not make_video_format_spec(VideoPreset.BEST, None).endswith("/best/best")


def test_video_format_spec_caps_every_branch() -> None:
    """Every capped branch must carry the height filter, including fallbacks."""
    spec = make_video_format_spec(VideoPreset.UNIVERSAL, 480)
    assert spec.count("[height<=480]") == 2


def test_audio_format_spec_prefers_native_m4a() -> None:
    assert make_audio_format_spec("m4a") == "bestaudio[ext=m4a]/bestaudio/best"


@pytest.mark.parametrize("codec", ["mp3", "opus", "wav", "flac"])
def test_audio_format_spec_other_codecs_use_plain_bestaudio(codec: str) -> None:
    assert make_audio_format_spec(codec) == "bestaudio/best"
