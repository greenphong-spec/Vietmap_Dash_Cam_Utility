"""Tests for core/probe.py (Design Section 5.7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashcam_merger.core.models import ClipInfo
from dashcam_merger.core.probe import (
    ProbeError,
    build_probe_command,
    probe_clip,
    sample_indices,
    signature_from_json,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_signature_video_and_audio():
    sig = signature_from_json(load("probe_video_audio.json"))
    assert sig.v_codec == "h264"
    assert (sig.width, sig.height) == (1920, 1080)
    assert sig.r_frame_rate == "30000/1001"
    assert sig.pix_fmt == "yuv420p"
    assert sig.a_codec == "aac"
    assert sig.sample_rate == 48000
    assert sig.channels == 2


def test_signature_video_only_reports_no_audio():
    sig = signature_from_json(load("probe_video_only.json"))
    assert sig.v_codec == "hevc"
    assert (sig.a_codec, sig.sample_rate, sig.channels) == ("none", 0, 0)


def test_signature_without_video_stream_raises():
    with pytest.raises(ProbeError) as excinfo:
        signature_from_json(load("probe_no_video.json"))
    assert "no video stream" in str(excinfo.value)


def test_signatures_are_hashable_and_compare_by_value():
    a = signature_from_json(load("probe_video_audio.json"))
    b = signature_from_json(load("probe_video_audio.json"))
    c = signature_from_json(load("probe_video_only.json"))
    assert a == b and hash(a) == hash(b)
    assert len({a, b, c}) == 2


@pytest.mark.parametrize(
    "n, every, expected",
    [
        (50, 20, [0, 20, 40, 49]),
        (1, 20, [0]),
        (0, 20, []),
        (3, 1, [0, 1, 2]),
        (21, 20, [0, 20]),
    ],
)
def test_sample_indices(n, every, expected):
    assert sample_indices(n, every) == expected


def test_probe_command_is_normative():
    cmd = build_probe_command(Path("C:/app/ffmpeg/ffprobe.exe"), Path(__file__))
    assert cmd[:7] == [
        "C:\\app\\ffmpeg\\ffprobe.exe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
    ]
    assert len(cmd) == 8


def test_probe_clip_marks_unreadable_on_failure(tmp_path):
    fake = tmp_path / "not_ffprobe.exe"
    clip = ClipInfo(path=tmp_path / "x.mp4", size_bytes=10, mtime=0.0)
    assert probe_clip(fake, clip) is None
    assert clip.unreadable is True
    assert clip.unreadable_reason


@pytest.mark.integration
def test_probe_clip_on_text_file_marks_unreadable(ffprobe_path, tmp_path):
    bogus = tmp_path / "bogus.mp4"
    bogus.write_text("this is not a video", encoding="utf-8")
    clip = ClipInfo(path=bogus, size_bytes=bogus.stat().st_size, mtime=0.0)
    assert probe_clip(ffprobe_path, clip) is None
    assert clip.unreadable is True
