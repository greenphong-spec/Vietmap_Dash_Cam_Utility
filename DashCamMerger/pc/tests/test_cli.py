"""Tests for cli.py and pipeline.py wiring (Design Sections 6 and 8)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from dashcam_merger.cli import CliConfirmer, build_parser, options_from_args
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
    assert opt.trip_gap_minutes is None  # config.json decides


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
            "--delete-source",
        ]
    )
    assert opt.output_dir == Path("E:/y")
    assert opt.assume_yes is True and opt.recursive is False
    assert opt.camera_map == {"F": "Front", "R": "Rear"}
    assert opt.split_on_gap is True and opt.max_part_gb == 3.5
    assert opt.reencode and opt.overwrite and opt.keep_lists
    assert opt.parallel == 2 and opt.dry_run and opt.verbose
    assert opt.delete_source is True


def test_parser_delete_source_defaults_false():
    assert parse(["--input", "D:/x"]).delete_source is False


def test_cli_confirm_delete_with_yes_skips_prompt():
    assert CliConfirmer(assume_yes=True).confirm_delete([]) is True


def test_cli_confirm_delete_without_yes_and_non_interactive_keeps_files(monkeypatch):
    confirmer = CliConfirmer(assume_yes=False)
    monkeypatch.setattr(CliConfirmer, "_interactive", staticmethod(lambda: False))
    assert confirmer.confirm_delete([]) is False


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


def test_no_split_on_gap_and_trip_gap_minutes():
    opt = parse(["--input", "D:/x", "--no-split-on-gap", "--trip-gap-minutes", "20"])
    assert opt.split_on_gap is False and opt.trip_gap_minutes == 20.0


@pytest.mark.parametrize("value", ["0", "-5", "abc"])
def test_trip_gap_minutes_must_be_positive(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--input", "D:/x", "--trip-gap-minutes", value])


def test_split_and_no_split_are_exclusive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--input", "D:/x", "--split-on-gap", "--no-split-on-gap"])


def test_trips_parsed():
    from dashcam_merger.cli import parse_trips

    assert parse_trips("2") == {2}
    assert parse_trips("1,3,5-7") == {1, 3, 5, 6, 7}
    assert parse_trips(" 4 , 2 ") == {2, 4}
    assert parse(["--input", "D:/x", "--trips", "1,3"]).trips == {1, 3}
    assert parse(["--input", "D:/x"]).trips is None


@pytest.mark.parametrize("value", ["0", "a", "3-1", "", "1,-2", "2-x"])
def test_bad_trips_rejected(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--input", "D:/x", "--trips", value])
