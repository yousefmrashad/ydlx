"""Tests that pin the option surface each CLI command exposes."""

import inspect
from typing import Any, cast

import pytest

from ydlx import app

# Subtitle-related option parameters, tracked by name across every command.
SUBTITLE_PARAMS = frozenset(
    {"write_subs", "embed_subs", "auto_subs", "sub_langs", "sub_format"}
)

# The full option surface of every command, so a flag change has to be
# deliberate rather than an accident of a refactor.
COMMAND_PARAMS = {
    "info": (
        "url",
        "save",
        "output",
        "cookies_from_browser",
        "cookies",
        "verbose",
    ),
    "download": (
        "url",
        "info_json",
        "output_dir",
        "preset",
        "max_height",
        "format_code",
        "min_duration",
        "max_duration",
        "cookies_from_browser",
        "cookies",
        "sponsorblock",
        "write_subs",
        "embed_subs",
        "auto_subs",
        "sub_langs",
        "sub_format",
        "verbose",
    ),
    "audio": (
        "url",
        "output_dir",
        "codec",
        "cookies_from_browser",
        "cookies",
        "sponsorblock",
        "verbose",
    ),
    "subs": (
        "url",
        "output_dir",
        "sub_langs",
        "auto_subs",
        "sub_format",
        "cookies_from_browser",
        "cookies",
        "verbose",
    ),
}


def command_params(name: str) -> tuple[str, ...]:
    """Returns the option parameter names the named command declares."""
    for command in app.registered_commands:
        callback = cast(Any, command.callback)
        if command.name == name or callback.__name__ == name:
            return tuple(inspect.signature(callback).parameters)
    raise AssertionError(f"no such command: {name}")


@pytest.mark.parametrize("name", sorted(COMMAND_PARAMS))
def test_command_option_surface_is_stable(name: str) -> None:
    assert sorted(command_params(name)) == sorted(COMMAND_PARAMS[name])


def test_audio_declares_no_subtitle_options() -> None:
    assert not SUBTITLE_PARAMS.intersection(command_params("audio"))


def test_info_declares_no_subtitle_options() -> None:
    assert not SUBTITLE_PARAMS.intersection(command_params("info"))
