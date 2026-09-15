"""Tests for cli.py and pipeline.py wiring (Design Sections 6 and 8)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from dashcam_merger.cli import build_parser, options_from_args
from dashcam_merger.core.config import load_config
from dashcam_merger.core.models import RunOptions
from dashcam_merger.core.pipeline import (
    AutoConfirmer,
    NoInputError,
    ToolMissingError,
    UsageError,
    run,
)
from dashcam_merger.core.progress import NullSink


def parse(argv):
    return options_from_args(build_parser().parse_args(argv))


def test_parser_requires_input():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_parser_defaults():
    opt = parse(["--input", "D:/x"])
    assert opt.input_dir == Path("D:/x")
    assert opt.recursive is True
    assert opt.dry_run is False and opt.assume_yes is False
    assert opt.camera_map is None and opt.split_on_gap is None


def test_parser_all_flags():
    opt = parse(
        [
            "--input", "D:/x",
            "--output", "E:/y",
            "--yes",
            "--no-recursive",
            "--camera-map", "F=Front,R=Rear",
            "--split-on-gap",
            "--max-part-gb", "3.5",
            "--reencode",
            "--overwrite",
            "--keep-lists",
            "--parallel", "2",
            "--dry-run",
            "--verbose",
        ]
    )
    assert opt.output_dir == Path("E:/y")
    assert opt.assume_yes is True and opt.recursive is False
    assert opt.camera_map == {"F": "Front", "R": "Rear"}
    assert opt.split_on_gap is True and opt.max_part_gb == 3.5
    assert opt.reencode and opt.overwrite and opt.keep_lists
    assert opt.parallel == 2 and opt.dry_run and opt.verbose


def test_invalid_camera_map_rejected():
    with pytest.raises(ValueError):
        parse(["--input", "D:/x", "--camera-map", "F=Left"])


def test_version_flag_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--version"])
    assert excinfo.value.code == 0
    assert "DashCamMerger" in capsys.readouterr().out


@pytest.fixture()
def cfg_with_fake_tools(tmp_path):
    cfg = load_config(tmp_path / "missing.json")
    fake = tmp_path / "tools"
    fake.mkdir()
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        (fake / name).write_bytes(b"not a real tool")
    cfg.ffmpeg_path = fake / "ffmpeg.exe"
    cfg.ffprobe_path = fake / "ffprobe.exe"
    return cfg


def test_missing_ffmpeg_raises_tool_missing(tmp_path):
    cfg = load_config(tmp_path / "missing.json")
    cfg.ffmpeg_path = tmp_path / "nope" / "ffmpeg.exe"
    opt = RunOptions(input_dir=tmp_path)
    with pytest.raises(ToolMissingError) as excinfo:
        run(cfg, opt, AutoConfirmer(), NullSink(), threading.Event())
    assert "ffmpeg not found at" in str(excinfo.value)


def test_output_equal_to_input_is_usage_error(cfg_with_fake_tools, tmp_path):
    source = tmp_path / "clips"
    source.mkdir()
    opt = RunOptions(input_dir=source, output_dir=source)
    with pytest.raises(UsageError) as excinfo:
        run(cfg_with_fake_tools, opt, AutoConfirmer(), NullSink(), threading.Event())
    assert "not the input folder itself" in str(excinfo.value)


def test_output_above_input_is_usage_error(cfg_with_fake_tools, tmp_path):
    source = tmp_path / "clips" / "inner"
    source.mkdir(parents=True)
    opt = RunOptions(input_dir=source, output_dir=tmp_path / "clips")
    with pytest.raises(UsageError):
        run(cfg_with_fake_tools, opt, AutoConfirmer(), NullSink(), threading.Event())


def test_no_video_files_raises_no_input(cfg_with_fake_tools, tmp_path):
    source = tmp_path / "clips"
    source.mkdir()
    (source / "readme.txt").write_text("no videos here", encoding="utf-8")
    opt = RunOptions(input_dir=source)
    with pytest.raises(NoInputError) as excinfo:
        run(cfg_with_fake_tools, opt, AutoConfirmer(), NullSink(), threading.Event())
    assert "No video files found" in str(excinfo.value)


def test_missing_input_folder_is_usage_error(cfg_with_fake_tools, tmp_path):
    opt = RunOptions(input_dir=tmp_path / "does_not_exist")
    with pytest.raises(UsageError):
        run(cfg_with_fake_tools, opt, AutoConfirmer(), NullSink(), threading.Event())
