"""Persistent settings stored under the platform config directory."""

import json
import os
import sys
from pathlib import Path
from typing import Any

# =====================================================================
# Persistent Settings
# =====================================================================


def get_config_dir() -> Path:
    """Returns the platform-agnostic config directory (%APPDATA%/ydlx or ~/.config/ydlx)."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    config_dir = Path(base) / "ydlx"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def load_settings() -> dict[str, Any]:
    """Loads persisted settings, tolerating a missing or corrupt config file."""
    try:
        with open(get_config_dir() / "settings.json", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(settings: dict[str, Any]) -> None:
    """Merges settings into the config file; None values remove their key."""
    current = load_settings()
    for key, value in settings.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    with open(get_config_dir() / "settings.json", "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)


def get_saved_cookie_source() -> str | None:
    """Returns the persisted cookie-source browser, if any."""
    value = load_settings().get("cookies_from_browser")
    return str(value) if value else None


def get_saved_cookie_file() -> str | None:
    """Returns the persisted cookies.txt path, if any."""
    value = load_settings().get("cookiefile")
    return str(value) if value else None


def get_saved_cookie_profile() -> str | None:
    """Returns the persisted cookie-source profile path or name, if any."""
    value = load_settings().get("cookies_profile")
    return str(value) if value else None
