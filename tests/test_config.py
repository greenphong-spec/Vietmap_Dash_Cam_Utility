"""Tests for core/config.py (Design Section 5.1)."""

from __future__ import annotations

import json

import pytest

from dashcam_merger.core.config import ConfigError, DEFAULTS, app_dir, load_config


def write_cfg(tmp_path, data) -> object:
    path = tmp_path / "config.json"
    path.write_text(
        data if isinstance(data, str) else json.dumps(data, indent=2), encoding="utf-8"
    )
    return path


def test_defaults_when_file_missing(tmp_path):
    cfg = load_config(tmp_path / "does_not_exist.json")
    assert cfg.video_extensions == DEFAULTS["video_extensions"]
    assert cfg.output_subfolder == "merged"
    assert cfg.probe_sample_every == 20
    assert cfg.parallel_jobs == 1


def test_invalid_json_reports_line_number(tmp_path):
    path = write_cfg(tmp_path, '{\n  "output_subfolder": "merged",\n  oops\n}')
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert "line 3" in str(excinfo.value)


def test_unknown_key_ignored(tmp_path):
    path = write_cfg(tmp_path, {"totally_unknown": 5, "output_subfolder": "out"})
    cfg = load_config(path)
    assert cfg.output_subfolder == "out"


def test_parallel_jobs_out_of_range(tmp_path):
    path = write_cfg(tmp_path, {"parallel_jobs": 9})
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert "parallel_jobs" in str(excinfo.value)


def test_custom_profile_without_cam_group(tmp_path):
    path = write_cfg(
        tmp_path,
        {"camera_profiles": [{"name": "MyBrand", "regex": r"_(CH[12])\.mp4$", "map": {"CH1": "Front"}}]},
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert "cam" in str(excinfo.value)


def test_custom_profile_loaded_and_uppercased(tmp_path):
    path = write_cfg(
        tmp_path,
        {
            "camera_profiles": [
                {
                    "name": "MyBrand",
                    "regex": r"_(?P<cam>ch[12])\.mp4$",
                    "map": {"ch1": "front", "ch2": "rear"},
                }
            ]
        },
    )
    cfg = load_config(path)
    assert len(cfg.camera_profiles) == 1
    profile = cfg.camera_profiles[0]
    assert profile.scope == "name"
    assert profile.map == {"CH1": "Front", "CH2": "Rear"}


def test_bad_profile_label_rejected(tmp_path):
    path = write_cfg(
        tmp_path,
        {"camera_profiles": [{"name": "X", "regex": r"(?P<cam>A)", "map": {"A": "Left"}}]},
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_relative_ffmpeg_path_becomes_absolute(tmp_path):
    path = write_cfg(tmp_path, {"ffmpeg_path": "ffmpeg\\ffmpeg.exe"})
    cfg = load_config(path)
    assert cfg.ffmpeg_path.is_absolute()
    assert cfg.ffmpeg_path.parent.parent == app_dir()


def test_extensions_lowercased_and_validated(tmp_path):
    path = write_cfg(tmp_path, {"video_extensions": [".MP4", ".TS"]})
    assert load_config(path).video_extensions == [".mp4", ".ts"]
    path2 = tmp_path / "bad.json"
    path2.write_text(json.dumps({"video_extensions": ["mp4"]}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path2)


def test_max_part_gb_negative_rejected(tmp_path):
    path = write_cfg(tmp_path, {"max_part_gb": -1})
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert "max_part_gb" in str(excinfo.value)
