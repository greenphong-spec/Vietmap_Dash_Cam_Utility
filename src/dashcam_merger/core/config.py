"""Configuration loading (Design Section 5.1) - FR-10."""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from .models import AppConfig, CameraProfile

log = logging.getLogger("dashcam_merger.config")

DEFAULTS: dict[str, Any] = {
    "video_extensions": [".mp4", ".mov", ".avi", ".ts", ".mkv"],
    "output_subfolder": "merged",
    "probe_sample_every": 20,
    "gap_warning_seconds": 300,
    "gap_split_seconds": 900,
    "split_on_gap": True,
    "max_part_gb": 0,
    "parallel_jobs": 1,
    "ffmpeg_path": "ffmpeg\\ffmpeg.exe",
    "ffprobe_path": "ffmpeg\\ffprobe.exe",
    "camera_profiles": [],
}

_VALID_LABELS = {"FRONT": "Front", "REAR": "Rear", "INTERIOR": "Interior", "SKIP": "Skip"}


class ConfigError(Exception):
    """Invalid configuration; message names the offending key or line."""


def app_dir() -> Path:
    """Application directory: exe folder when frozen, else the repository root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # src/dashcam_merger/core/config.py -> parents[3] == repository root
    return Path(__file__).resolve().parents[3]


def profile_from_dict(d: dict[str, Any]) -> CameraProfile:
    """Build a user CameraProfile from a config.json entry (FR-2.5)."""
    if not isinstance(d, dict):
        raise ConfigError("camera_profiles: each entry must be an object")
    name = d.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError("camera_profiles: 'name' must be a non-empty string")
    pattern = d.get("regex")
    if not isinstance(pattern, str) or not pattern:
        raise ConfigError(f"camera_profiles[{name}]: 'regex' must be a non-empty string")
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise ConfigError(f"camera_profiles[{name}]: invalid regex: {e}") from e
    if "cam" not in regex.groupindex:
        raise ConfigError(
            f"camera_profiles[{name}]: 'regex' must contain a named group (?P<cam>...)"
        )
    raw_map = d.get("map")
    if not isinstance(raw_map, dict) or not raw_map:
        raise ConfigError(f"camera_profiles[{name}]: 'map' must be a non-empty object")
    mapping: dict[str, Any] = {}
    for key, value in raw_map.items():
        if not isinstance(value, str) or value.upper() not in _VALID_LABELS:
            raise ConfigError(
                f"camera_profiles[{name}]: map value '{value}' must be one of "
                "Front, Rear, Interior, Skip"
            )
        mapping[str(key).upper()] = _VALID_LABELS[value.upper()]
    scope = d.get("scope", "name")
    if scope not in ("name", "parent"):
        raise ConfigError(f"camera_profiles[{name}]: 'scope' must be 'name' or 'parent'")
    return CameraProfile(name=name, scope=scope, regex=regex, map=mapping)


def _need_int(data: dict[str, Any], key: str, minimum: int, maximum: int | None = None) -> int:
    value = data.get(key, DEFAULTS[key])
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key}: must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        upper = f" and at most {maximum}" if maximum is not None else ""
        raise ConfigError(f"{key}: must be at least {minimum}{upper}")
    return value


def save_settings(path: Path, updates: dict[str, Any]) -> None:
    """Change a few keys of config.json and keep everything else (GUI settings, FR-10.3).

    Only known keys are accepted. The write goes through a WriteGuard limited to the
    folder of config.json (FR-8).
    """
    from .safe_io import WriteGuard  # local: loading a config needs no write access

    for key in updates:
        if key not in DEFAULTS:
            raise ConfigError(f"{key}: unknown setting")
    raw: dict[str, Any] = dict(DEFAULTS)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ConfigError(f"config.json line {e.lineno}: {e.msg}") from e
        if not isinstance(raw, dict):
            raise ConfigError("config.json: top level must be an object")
    raw.update(updates)
    WriteGuard([path.parent]).write_text(path, json.dumps(raw, indent=2) + "\n")
    log.info("config.json updated: %s", ", ".join(f"{k}={v}" for k, v in updates.items()))


def load_config(path: Path | None = None) -> AppConfig:
    """Load config.json, falling back to DEFAULTS for missing keys (FR-10)."""
    cfg_path = path if path is not None else app_dir() / "config.json"
    data: dict[str, Any] = dict(DEFAULTS)
    if cfg_path.is_file():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ConfigError(f"config.json line {e.lineno}: {e.msg}") from e
        except OSError as e:
            raise ConfigError(f"config.json: {e}") from e
        if not isinstance(raw, dict):
            raise ConfigError("config.json: top level must be an object")
        for key, value in raw.items():
            if key not in DEFAULTS:
                log.warning("config.json: unknown key '%s' ignored", key)
                continue
            data[key] = value
    else:
        log.info("config.json not found, using defaults")

    extensions = data["video_extensions"]
    if not isinstance(extensions, list) or not extensions:
        raise ConfigError("video_extensions: must be a non-empty list of strings")
    normalised: list[str] = []
    for ext in extensions:
        if not isinstance(ext, str) or not ext.startswith("."):
            raise ConfigError(f"video_extensions: '{ext}' must be a string starting with '.'")
        normalised.append(ext.lower())

    subfolder = data["output_subfolder"]
    if not isinstance(subfolder, str) or not subfolder:
        raise ConfigError("output_subfolder: must be a non-empty string")

    probe_sample_every = _need_int(data, "probe_sample_every", 1)
    gap_warning_seconds = _need_int(data, "gap_warning_seconds", 0)
    gap_split_seconds = _need_int(data, "gap_split_seconds", 0)
    parallel_jobs = _need_int(data, "parallel_jobs", 1, 8)

    split_on_gap = data["split_on_gap"]
    if not isinstance(split_on_gap, bool):
        raise ConfigError("split_on_gap: must be true or false")

    max_part_gb = data["max_part_gb"]
    if isinstance(max_part_gb, bool) or not isinstance(max_part_gb, (int, float)):
        raise ConfigError("max_part_gb: must be a number")
    if max_part_gb < 0:
        raise ConfigError("max_part_gb: must be at least 0")

    base = app_dir()
    paths: dict[str, Path] = {}
    for key in ("ffmpeg_path", "ffprobe_path"):
        value = data[key]
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{key}: must be a non-empty string")
        candidate = Path(value)
        # Existence is checked by the pipeline so the message can carry the path.
        paths[key] = candidate if candidate.is_absolute() else (base / candidate)

    profiles_raw = data["camera_profiles"]
    if not isinstance(profiles_raw, list):
        raise ConfigError("camera_profiles: must be a list")
    profiles = [profile_from_dict(p) for p in profiles_raw]

    return AppConfig(
        video_extensions=normalised,
        output_subfolder=subfolder,
        probe_sample_every=probe_sample_every,
        gap_warning_seconds=gap_warning_seconds,
        gap_split_seconds=gap_split_seconds,
        split_on_gap=split_on_gap,
        max_part_gb=float(max_part_gb),
        parallel_jobs=parallel_jobs,
        ffmpeg_path=paths["ffmpeg_path"],
        ffprobe_path=paths["ffprobe_path"],
        camera_profiles=profiles,
    )
