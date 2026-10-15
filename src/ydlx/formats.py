"""Video codec presets, format selectors, and duration filters."""

from collections.abc import Callable
from enum import StrEnum
from typing import Any

# Format choices shared by every prompt, so the audio and subtitle flows cannot
# drift apart in wording or in which codecs they offer.
AUDIO_CODEC_CHOICES: tuple[str, ...] = ("m4a", "opus", "mp3", "wav", "flac")

# =====================================================================

# Format Selectors and Filters
# =====================================================================


def _video_codec_label(vcodec: str) -> str:
    """Maps a raw codec string to a human-readable video codec name."""
    lowered = vcodec.lower()
    if lowered.startswith("av01"):
        return "AV1"
    if lowered.startswith("vp9") or lowered.startswith("vp09"):
        return "VP9"
    if lowered.startswith("avc1") or lowered.startswith("h264"):
        return "H.264"
    if lowered.startswith(("h265", "hev1", "hvc1")):
        return "H.265"
    if lowered.startswith("vp8"):
        return "VP8"
    return vcodec or "unknown"


def get_preset_format_choices(
    info: dict[str, Any], universal: bool = False
) -> list[tuple[str, str, str | None]]:
    """
    Builds download choices for the selected codec preset. The quality preset
    keeps yt-dlp's best-ranked format per resolution (usually AV1/VP9) and
    names the codec in the label; the universal preset lists only H.264 video
    paired with AAC audio so results play on any device.
    """
    formats: list[dict[str, Any]] = info.get("formats", [])

    choices: list[tuple[str, str, str | None]] = []
    resolutions: dict[str, dict[str, Any]] = {}

    for f in formats:
        vcodec = str(f.get("vcodec", "none"))
        if universal and vcodec != "none" and not vcodec.startswith("avc1"):
            continue
        if vcodec != "none":
            height = f.get("height")
            if height:
                resolutions[f"{height}p"] = f

    sorted_res = sorted(
        resolutions.keys(),
        key=lambda x: int(x[:-1]) if x[:-1].isdigit() else 0,
        reverse=True,
    )

    if universal:
        choices.append(
            (
                "🚀 Best Universal Quality (Auto H.264 + AAC)",
                "bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "mp4",
            )
        )
    else:
        choices.append(("🚀 Best Quality (Auto)", "bestvideo+bestaudio/best", None))

    for res in sorted_res:
        f = resolutions[res]
        fid = str(f.get("format_id", ""))
        ext = str(f.get("ext", ""))
        size_bytes = f.get("filesize") or f.get("filesize_approx")
        size_str = f" (~{size_bytes / (1024 * 1024):.1f} MB)" if size_bytes else ""
        codec = _video_codec_label(str(f.get("vcodec", "")))

        if universal:
            choices.append(
                (
                    f"📺 Video: {res} (H.264 + AAC){size_str}",
                    f"{fid}+bestaudio[ext=m4a]/best[ext=mp4]/best",
                    "mp4",
                )
            )
        elif str(f.get("acodec", "none")) != "none":
            choices.append(
                (
                    f"📺 Video: {res} ({codec}, {ext}){size_str}",
                    fid,
                    ext,
                )
            )
        else:
            choices.append(
                (
                    f"📺 Video: {res} ({codec}, {ext}) + Best Audio{size_str}",
                    f"{fid}+bestaudio/best",
                    ext,
                )
            )

    return choices


def make_duration_filter(
    min_len: int | None = None, max_len: int | None = None
) -> Callable[..., str | None]:
    """Generates a duration matching filter callback."""

    def duration_filter(
        info: dict[str, Any], *, incomplete: bool = False
    ) -> str | None:
        duration = info.get("duration")
        if duration:
            if min_len is not None and duration < min_len:
                return f"The video is too short ({duration}s < {min_len}s)"
            if max_len is not None and duration > max_len:
                return f"The video is too long ({duration}s > {max_len}s)"
        return None

    return duration_filter


def make_audio_format_spec(format_codec: str) -> str:
    """Returns the download format spec: native best m4a for m4a targets, bestaudio otherwise."""
    if format_codec == "m4a":
        return "bestaudio[ext=m4a]/bestaudio/best"
    return "bestaudio/best"


class VideoPreset(StrEnum):
    """Codec presets for video downloads, mirroring the interactive wizard."""

    UNIVERSAL = "universal"
    BEST = "best"


def make_video_format_spec(preset: VideoPreset, max_height: int | None = None) -> str:
    """
    Builds a video format selector for the given preset, optionally capped to a
    maximum height. The universal preset pairs H.264 video with AAC audio so the
    result merges into MP4; the best preset takes yt-dlp's highest ranked formats
    (usually AV1/VP9) and keeps their native container.

    Each branch ends in an uncapped fallback so an over-tight --res degrades to
    the nearest available quality instead of failing the download.
    """
    cap = f"[height<={max_height}]" if max_height else ""
    if preset is VideoPreset.UNIVERSAL:
        return (
            f"bestvideo[vcodec^=avc1]{cap}+bestaudio[ext=m4a]/best[ext=mp4]{cap}/best"
        )
    if not cap:
        return "bestvideo+bestaudio/best"
    return f"bestvideo{cap}+bestaudio/best{cap}/best"
