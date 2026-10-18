"""Tests for core/concat.py (Design Section 5.10)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from dashcam_merger.core.concat import (
    build_command,
    escape_concat_path,
    parse_progress_line,
    run_ffmpeg,
    write_concat_list,
)
from dashcam_merger.core.models import ClipInfo, OutputPart
from dashcam_merger.core.progress import NullSink
from dashcam_merger.core.safe_io import WriteGuard


def make_part(tmp_path, reencode=False):
    return OutputPart(
        camera="Front",
        part_index=1,
        part_count=1,
        clips=[],
        signature=None,
        reencode=reencode,
        list_path=tmp_path / "Front.txt",
        output_path=tmp_path / "Front.mp4",
        partial_path=tmp_path / "Front.partial.mp4",
    )


def test_escape_concat_path_quotes_single_quote(tmp_path):
    target = tmp_path / "it's.mp4"
    target.write_bytes(b"x")
    line = escape_concat_path(target)
    assert line.startswith("file '") and line.endswith("'")
    assert "'\\''" in line
    assert "\\" not in line.replace("'\\''", "")  # forward slashes only


def test_escape_concat_path_is_absolute_posix(tmp_path):
    target = tmp_path / "clip.mp4"
    target.write_bytes(b"x")
    line = escape_concat_path(target)
    assert line == f"file '{target.resolve().as_posix()}'"
    assert ":" in line  # drive letter survives on Windows


def test_write_concat_list_utf8_no_bom_lf(tmp_path):
    guard = WriteGuard([tmp_path])
    clips = []
    for name in ["xe ô tô 'test'_F.MP4", "b.mp4"]:
        p = tmp_path / name
        p.write_bytes(b"x")
        clips.append(ClipInfo(path=p, size_bytes=1, mtime=0.0))
    list_path = tmp_path / "list.txt"
    write_concat_list(guard, list_path, clips)
    raw = list_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
    text = raw.decode("utf-8")
    assert text.count("\n") == 2 and text.endswith("\n")
    assert "ô tô" in text and "'\\''" in text


def test_build_command_stream_copy_is_normative(tmp_path):
    part = make_part(tmp_path)
    cmd = build_command(Path("C:/app/ffmpeg/ffmpeg.exe"), part)
    assert cmd == [
        "C:\\app\\ffmpeg\\ffmpeg.exe",
        "-y",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(part.list_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(part.partial_path),
    ]


def test_build_command_reencode_branch(tmp_path):
    cmd = build_command(Path("ffmpeg.exe"), make_part(tmp_path, reencode=True))
    assert "-c" not in cmd and "copy" not in cmd
    joined = " ".join(cmd)
    assert "-c:v libx264 -crf 18 -preset medium -c:a aac" in joined
    assert cmd[-2:] == ["+faststart", str(make_part(tmp_path).partial_path)]


@pytest.mark.parametrize(
    "line, expected",
    [
        ("out_time_ms=90000000\n", ("out_time_ms", "90000000")),
        ("total_size=1048576", ("total_size", "1048576")),
        ("progress=continue", ("progress", "continue")),
        ("", None),
        ("noequalshere", None),
    ],
)
def test_parse_progress_line(line, expected):
    assert parse_progress_line(line) == expected


def test_out_time_ms_is_microseconds():
    key, value = parse_progress_line("out_time_ms=90000000")
    assert key == "out_time_ms"
    assert int(value) / 1_000_000 == 90.0


def test_run_ffmpeg_returns_minus_one_when_already_cancelled(tmp_path):
    part = make_part(tmp_path)
    cancel = threading.Event()
    cancel.set()
    import sys

    cmd = [sys.executable, "-c", "import time; time.sleep(5)"]
    assert run_ffmpeg(cmd, 10.0, cancel, NullSink(), part) == -1


def test_run_ffmpeg_reports_exit_code(tmp_path):
    import sys

    part = make_part(tmp_path)
    cmd = [sys.executable, "-c", "raise SystemExit(3)"]
    assert run_ffmpeg(cmd, 1.0, threading.Event(), NullSink(), part) == 3
