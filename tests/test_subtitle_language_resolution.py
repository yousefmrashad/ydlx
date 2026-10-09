from collections import OrderedDict
from io import StringIO
from pathlib import Path
from typing import Any, cast

import pytest
from rich.console import Console

import ydlx
from ydlx import DownloadRecord, report_subtitle_download, resolve_sub_langs


@pytest.mark.parametrize(
    ("requested", "official", "automatic", "expected"),
    [
        (
            ["en"],
            {"en-ehkg1hFWq8A": [{}]},
            {"en": [{}]},
            (["en-ehkg1hFWq8A"], []),
        ),
        (
            ["en"],
            {"en-US": [{}], "en": [{}]},
            {},
            (["en"], []),
        ),
        (["en"], {}, {"en": [{}]}, (["en"], [])),
        (["en"], {}, {"en-US": [{}]}, (["en-US"], [])),
        (["EN"], {"en": [{}]}, {}, (["en"], [])),
        (["en"], {}, {}, ([], ["en"])),
        (["en", "en-US"], {"en-US": [{}]}, {}, (["en-US"], [])),
        (
            ["en"],
            OrderedDict((("en-US", [{}]), ("en-GB", [{}]))),
            {},
            (["en-US"], []),
        ),
    ],
)
def test_resolve_sub_langs(
    requested: list[str],
    official: dict[str, Any],
    automatic: dict[str, Any],
    expected: tuple[list[str], list[str]],
) -> None:
    assert resolve_sub_langs(requested, official, automatic) == expected


@pytest.mark.parametrize("raw", ["en", "pt-BR", "zh-Hans", "es-419"])
def test_resolvable_sub_langs_accepts_common_bcp47_tags(raw: str) -> None:
    assert ydlx.resolvable_sub_langs([raw]) == [raw]


@pytest.mark.parametrize(
    "raw", ["all", "ALL", "en.*", "(en|ar)", "-en", "en_US", "en US"]
)
def test_resolvable_sub_langs_preserves_legacy_syntax(raw: str) -> None:
    assert ydlx.resolvable_sub_langs([raw]) is None


def test_resolvable_sub_langs_bypasses_the_whole_mixed_request() -> None:
    assert ydlx.resolvable_sub_langs(["en", "en.*"]) is None


def test_parse_sub_langs_deduplicates_case_insensitively() -> None:
    assert ydlx.parse_sub_langs("en,EN,ar,en") == ["en", "ar"]


def _report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    info: dict[str, Any],
    *,
    langs: str = "en",
    auto_subs: bool = True,
) -> tuple[int, str, list[list[str] | None]]:
    pins: list[list[str] | None] = []

    monkeypatch.setattr(ydlx, "get_video_info", lambda *_args: info)

    def fake_download(_url: str, **kwargs: Any) -> int:
        record = cast(DownloadRecord, kwargs["record"])
        info_dict = cast(dict[str, Any] | None, kwargs.get("info")) or {}
        pin = kwargs.get("pin_sub_langs")
        if pin is None:
            pin = ydlx.requested_sub_langs(cast(str, kwargs.get("sub_langs", "en")))
        pins.append(pin)
        if info_dict.get("_type") == "playlist":
            record.subtitle_count = int(
                any(
                    entry and (entry.get("subtitles") or entry.get("automatic_captions"))
                    for entry in info_dict.get("entries", [])
                )
            )
        else:
            record.subtitle_count = len(pin) if pin is not None else 1
        return 0

    monkeypatch.setattr(ydlx, "download_subtitles_only", fake_download)
    output = StringIO()
    code = report_subtitle_download(
        "https://youtu.be/test",
        tmp_path,
        Console(file=output, width=200),
        langs=langs,
        auto_subs=auto_subs,
        sub_format="vtt",
        browser_spec=None,
    )
    return code, output.getvalue(), pins


def test_single_video_reports_partial_language_misses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, output, pins = _report(
        tmp_path,
        monkeypatch,
        {"subtitles": {"en-US": [{}]}, "automatic_captions": {"en": [{}]}},
        langs="en,ar",
    )

    assert code == 0
    assert pins == [["en-US"]]
    assert "No track found for requested language 'ar'" in output
    assert "Downloaded 1 subtitle file(s)" in output


def test_default_english_does_not_select_an_unrelated_track(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, output, pins = _report(
        tmp_path, monkeypatch, {"subtitles": {"fr": [{}]}}
    )

    assert code == 1
    assert pins == []
    assert "No subtitles found for 'en'" in output


def test_auto_captions_are_not_considered_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, output, pins = _report(
        tmp_path,
        monkeypatch,
        {"subtitles": {}, "automatic_captions": {"en": [{}]}},
        auto_subs=False,
    )
    assert code == 1
    assert pins == []
    assert "No subtitles found" in output


def test_playlist_keeps_one_legacy_global_language_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    playlist = {
        "_type": "playlist",
        "entries": [
            {"title": "first", "subtitles": {"en-US": [{}]}},
            {"title": "second", "subtitles": {"en": [{}]}},
        ],
    }
    code, output, pins = _report(
        tmp_path, monkeypatch, playlist, langs="en,ar"
    )

    assert code == 0
    assert pins == [["en", "ar"]]
    assert "no track on" not in output
    assert "Downloaded 1 subtitle file(s)" in output


def test_playlist_default_leaves_selection_to_ytdlp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    playlist = {"_type": "playlist", "entries": [{"subtitles": {"fr": [{}]}}]}
    code, output, pins = _report(tmp_path, monkeypatch, playlist)

    assert code == 0
    assert pins == [None]
    assert "Downloaded 1 subtitle file(s)" in output


def test_combined_download_resolves_a_single_video_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    info = {
        "id": "video",
        "subtitles": {"ar-SA": [{}]},
        "automatic_captions": {"ar": [{}]},
    }
    captured: dict[str, Any] = {}
    monkeypatch.setattr(ydlx, "get_video_info", lambda *_args, **_kwargs: info)

    def fake_download(url: str, **kwargs: Any) -> int:
        captured["url"] = url
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(ydlx, "download_video", fake_download)

    ydlx.download(
        url="https://youtu.be/test",
        output_dir=str(tmp_path),
        write_subs=True,
        sub_langs="ar",
    )

    assert captured["info"] is info
    assert captured["extra_opts"]["subtitleslangs"] == ["ar-SA"]


def test_combined_download_skips_unmatched_subtitles_but_downloads_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    info = {"id": "video", "subtitles": {"fr": [{}]}}
    captured: dict[str, Any] = {}
    monkeypatch.setattr(ydlx, "get_video_info", lambda *_args, **_kwargs: info)
    monkeypatch.setattr(
        ydlx,
        "download_video",
        lambda _url, **kwargs: captured.update(kwargs) or 0,
    )

    ydlx.download(
        url="https://youtu.be/test",
        output_dir=str(tmp_path),
        write_subs=True,
        sub_langs="ar",
    )

    assert captured["info"] is info
    assert "writesubtitles" not in captured["extra_opts"]
    assert "subtitleslangs" not in captured["extra_opts"]


def test_combined_playlist_keeps_one_global_language_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    info = {
        "_type": "playlist",
        "entries": [
            {"subtitles": {"ar-SA": [{}]}},
            {"subtitles": {"ar": [{}]}},
        ],
    }
    captured: dict[str, Any] = {}
    monkeypatch.setattr(ydlx, "get_video_info", lambda *_args, **_kwargs: info)
    monkeypatch.setattr(
        ydlx,
        "download_video",
        lambda _url, **kwargs: captured.update(kwargs) or 0,
    )

    ydlx.download(
        url="https://youtu.be/playlist",
        output_dir=str(tmp_path),
        write_subs=True,
        sub_langs="ar",
    )

    assert captured["info"] is info
    assert captured["extra_opts"]["subtitleslangs"] == ["ar"]


def test_interactive_combined_download_resolves_against_existing_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    info = {"id": "video", "subtitles": {"ar-SA": [{}]}}
    captured: dict[str, Any] = {}
    confirm_calls = 0

    def prompt_ask(message: str, **_kwargs: Any) -> str:
        if message == "Select download type":
            return "1"
        return "external"

    def confirm_ask(*_args: Any, **_kwargs: Any) -> bool:
        nonlocal confirm_calls
        confirm_calls += 1
        return confirm_calls == 2

    monkeypatch.setattr(ydlx.Prompt, "ask", prompt_ask)
    monkeypatch.setattr(ydlx.Confirm, "ask", confirm_ask)
    monkeypatch.setattr(
        ydlx,
        "prompt_subtitle_options",
        lambda: ydlx.SubtitleOptions("ar", "vtt", True),
    )
    monkeypatch.setattr(ydlx, "get_default_video_dir", lambda: tmp_path)

    def fake_download(url: str, **kwargs: Any) -> int:
        captured["url"] = url
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(ydlx, "download_video", fake_download)
    ydlx.do_download_interactive(
        "https://youtu.be/test", info, Console(file=StringIO())
    )

    assert confirm_calls == 2
    assert captured["info"] is info
    assert captured["extra_opts"]["subtitleslangs"] == ["ar-SA"]


def test_media_downloader_processes_preextracted_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    info = {"id": "video"}
    received: list[dict[str, Any]] = []

    class FakeDownloader:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def process_ie_result(self, result: dict[str, Any], download: bool) -> None:
            assert download is True
            received.append(result)

        def download(self, _urls: list[str]) -> int:
            pytest.fail("pre-extracted info should not trigger a second extraction")

    monkeypatch.setattr(ydlx, "make_download_opts", lambda *_args: {})
    monkeypatch.setattr(ydlx, "apply_cookie_opts", lambda *_args: None)
    monkeypatch.setattr(ydlx, "build_downloader", lambda *_args: FakeDownloader())

    code = ydlx.download_video(
        "https://youtu.be/video",
        output_dir=tmp_path,
        info=info,
    )

    assert code == 0
    assert received == [info]


def test_bypass_tokens_use_legacy_url_path_without_pre_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []

    def fail_extraction(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        pytest.fail("bypass requests must not pre-extract")

    def legacy_download(_url: str, **kwargs: Any) -> int:
        called.append("legacy")
        cast(DownloadRecord, kwargs["record"]).subtitle_count = 1
        return 0

    monkeypatch.setattr(ydlx, "get_video_info", fail_extraction)
    monkeypatch.setattr(ydlx, "download_subtitles_only", legacy_download)
    code = report_subtitle_download(
        "https://youtu.be/test",
        tmp_path,
        Console(file=StringIO()),
        langs="en.*",
        auto_subs=True,
        sub_format="vtt",
        browser_spec=None,
    )

    assert code == 0
    assert called == ["legacy"]


def test_interactive_metadata_is_reused_without_reextracting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    info = {"subtitles": {"en": [{}]}}
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        ydlx,
        "get_video_info",
        lambda *_args: pytest.fail("passed metadata must be reused"),
    )

    def fake_download(_url: str, **kwargs: Any) -> int:
        captured["info"] = kwargs["info"]
        cast(DownloadRecord, kwargs["record"]).subtitle_count = 1
        return 0

    monkeypatch.setattr(ydlx, "download_subtitles_only", fake_download)
    code = report_subtitle_download(
        "https://youtu.be/test",
        tmp_path,
        Console(file=StringIO()),
        langs="en",
        auto_subs=True,
        sub_format="vtt",
        browser_spec=None,
        info=info,
    )

    assert code == 0
    assert captured["info"] is info


def test_extraction_failure_does_not_fall_back_to_url_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = StringIO()
    monkeypatch.setattr(
        ydlx, "get_video_info", lambda *_args: (_ for _ in ()).throw(RuntimeError("no"))
    )
    monkeypatch.setattr(
        ydlx,
        "download_subtitles_only",
        lambda *_args, **_kwargs: pytest.fail("must not fall back"),
    )

    code = report_subtitle_download(
        "https://youtu.be/test",
        tmp_path,
        Console(file=output),
        langs="en",
        auto_subs=True,
        sub_format="vtt",
        browser_spec=None,
    )

    assert code == 1
    assert "Extraction failed" in output.getvalue()


def test_preextracted_downloader_processes_the_same_info_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    info = {"id": "video", "webpage_url": "https://youtu.be/video"}
    captured: dict[str, Any] = {}

    class FakeDownloader:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def process_ie_result(self, received_info: dict[str, Any], download: bool) -> None:
            captured["info"] = received_info
            captured["download"] = download
            cast(DownloadRecord, captured["record"]).subtitle_count = 1

    monkeypatch.setattr(ydlx, "make_download_opts", lambda *_args: {"quiet": True})
    monkeypatch.setattr(ydlx, "apply_cookie_opts", lambda *_args: None)

    def build(opts: dict[str, Any], record: DownloadRecord) -> FakeDownloader:
        captured["opts"] = opts
        captured["record"] = record
        return FakeDownloader()

    monkeypatch.setattr(ydlx, "build_downloader", build)
    record = DownloadRecord()

    code = ydlx.download_subtitles_only(
        "https://youtu.be/video",
        pin_sub_langs=["en-US"],
        output_dir=tmp_path,
        record=record,
        info=info,
    )

    assert code == 0
    assert captured["info"] is info
    assert captured["download"] is True
    assert captured["opts"]["subtitleslangs"] == ["en-US"]
    assert captured["opts"]["skip_download"] is True
    assert record.subtitle_count == 1


def test_url_downloader_passes_only_subtitle_options_to_download_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_download(_url: str, **kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(ydlx, "download_video", fake_download)

    code = ydlx.download_subtitles_only(
        "https://youtu.be/video",
        output_dir=tmp_path,
        pin_sub_langs=["en-US"],
    )

    assert code == 0
    opts = cast(dict[str, Any], captured["extra_opts"])
    assert opts["subtitleslangs"] == ["en-US"]
    assert opts["skip_download"] is True
    assert "paths" not in opts
    assert "progress_hooks" not in opts
