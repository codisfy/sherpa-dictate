"""Per-user settings shared by the desktop app and background engines."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


APP_DIRECTORY_NAME = "Sherpa"


def user_config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / APP_DIRECTORY_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIRECTORY_NAME
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "sherpa"


def user_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / APP_DIRECTORY_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIRECTORY_NAME
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "sherpa"


def settings_path() -> Path:
    override = os.environ.get("SHERPA_SETTINGS_PATH")
    return Path(override).expanduser() if override else user_config_dir() / "settings.json"


def default_settings() -> dict[str, Any]:
    return {
        "model_storage_dir": str(user_data_dir() / "models"),
        "model_paths": {},
        "active_model": "",
        "dictation": {},
        "tts": {},
        "start_minimized": False,
    }


def _merge_settings(raw: Any) -> dict[str, Any]:
    settings = default_settings()
    if not isinstance(raw, dict):
        return settings
    for key in settings:
        if key in raw:
            settings[key] = raw[key]
    if not isinstance(settings["model_paths"], dict):
        settings["model_paths"] = {}
    if not isinstance(settings["dictation"], dict):
        settings["dictation"] = {}
    if not isinstance(settings["tts"], dict):
        settings["tts"] = {}
    return settings


def load_settings() -> dict[str, Any]:
    path = settings_path()
    try:
        return _merge_settings(json.loads(path.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default_settings()


def save_settings(settings: dict[str, Any]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _merge_settings(settings)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(normalized, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def update_settings(**changes: Any) -> dict[str, Any]:
    settings = load_settings()
    settings.update(changes)
    save_settings(settings)
    return settings


def resolve_model_path(model_id: str, fallback: Path) -> Path:
    paths = load_settings().get("model_paths", {})
    configured = paths.get(model_id) if isinstance(paths, dict) else None
    return Path(str(configured)).expanduser() if configured else fallback.expanduser()


def remember_model_path(model_id: str, path: Path) -> None:
    settings = load_settings()
    model_paths = dict(settings.get("model_paths", {}))
    model_paths[model_id] = str(path.expanduser().resolve())
    settings["model_paths"] = model_paths
    save_settings(settings)
