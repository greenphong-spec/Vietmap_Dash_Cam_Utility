"""Tests for core/verify.py (Design Section 5.11)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from dashcam_merger.core import verify as verify_mod
from dashcam_merger.core.models import ClipInfo, OutputPart, ProbeResult, StreamSignature
from dashcam_merger.core.probe import ProbeError
from dashcam_merger.core.verify import sources_untouched, verify_part

SIG = StreamSignature("h264", 1920, 1080, "30/1", "yuv420p", "aac", 48000, 2)


def make_part(tmp_path, clip_count=2, duration=60.0, size=1000, reencode=False):
    clips = []
    for i in range(clip_count):
        clip = ClipInfo(path=tmp_path / f"c{i}.mp4", size_bytes=size, mtime=0.0)
        clip.probe = ProbeResult(duration=duration, size_bytes=size, signature=SIG)
        clips.append(clip)
    return OutputPart(
        camera="Front",
        part_index=1,
        part_count=1,
        clips=clips,
        signature=SIG,
        reencode=reencode,
        partial_path=tmp_path / "Front.partial.mp4",
        expected_duration=duration * clip_count,
        expected_size=size * clip_count,
    )


@pytest.fixture()
def fake_probe(monkeypatch):
    def install(result):
        def fake(_ffprobe, _file):
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(verify_mod, "probe_output", fake)

    return install


def test_pass_within_tolerance(tmp_path, fake_probe):
    part = make_part(tmp_path)  # expected 120.0 s, 2000 bytes
    fake_probe((120.4, 2000))
    result = verify_part(Path("ffprobe"), part)
    assert result.passed is True and result.reason == ""


def test_tolerance_is_one_second_for_short_outputs(tmp_path, fake_probe):
    part = make_part(tmp_path, clip_count=2, duration=50.0)  # expected 100 s -> tol 1.0
    fake_probe((101.0, 2000))
    assert verify_part(Path("ffprobe"), part).passed is True
    fake_probe((101.5, 2000))
    assert verify_part(Path("ffprobe"), part).passed is False


def test_tolerance_scales_for_long_outputs(tmp_path, fake_probe):
    part = make_part(tmp_path, clip_count=100, duration=50.0)  # expected 5000 s -> tol 5.0
    fake_probe((5005.0, 100_000))
    assert verify_part(Path("ffprobe"), part).passed is True
    fake_probe((5006.0, 100_000))
    assert verify_part(Path("ffprobe"), part).passed is False


def test_size_below_ninety_percent_fails(tmp_path, fake_probe):
    part = make_part(tmp_path)  # expected size 2000
    fake_probe((120.0, 1780))  # 89 %
    result = verify_part(Path("ffprobe"), part)
    assert result.passed is False and "size" in result.reason


def test_size_at_ninety_percent_passes(tmp_path, fake_probe):
    part = make_part(tmp_path)
    fake_probe((120.0, 1800))
    assert verify_part(Path("ffprobe"), part).passed is True


def test_size_check_skipped_for_reencode(tmp_path, fake_probe):
    part = make_part(tmp_path, reencode=True)
    fake_probe((120.0, 100))
    assert verify_part(Path("ffprobe"), part).passed is True


def test_probe_error_fails_with_reason(tmp_path, fake_probe):
    part = make_part(tmp_path)
    fake_probe(ProbeError("moov atom not found"))
    result = verify_part(Path("ffprobe"), part)
    assert result.passed is False
    assert result.reason.startswith("ffprobe:")
    assert "moov atom not found" in result.reason


def test_duration_and_size_reasons_combined(tmp_path, fake_probe):
    part = make_part(tmp_path)
    fake_probe((10.0, 100))
    reason = verify_part(Path("ffprobe"), part).reason
    assert "duration" in reason and "size" in reason and "; " in reason


def test_sources_untouched_ok(tmp_path):
    clips = []
    for i in range(3):
        p = tmp_path / f"s{i}.mp4"
        p.write_bytes(b"\0" * 100)
        st = p.stat()
        clips.append(ClipInfo(path=p, size_bytes=st.st_size, mtime=st.st_mtime))
    ok, problems = sources_untouched(clips)
    assert ok is True and problems == []


def test_sources_untouched_detects_size_change(tmp_path):
    p = tmp_path / "s.mp4"
    p.write_bytes(b"\0" * 100)
    st = p.stat()
    clip = ClipInfo(path=p, size_bytes=st.st_size, mtime=st.st_mtime)
    p.write_bytes(b"\0" * 200)
    os.utime(p, (st.st_atime, st.st_mtime))
    ok, problems = sources_untouched([clip])
    assert ok is False and "changed or missing" in problems[0]


def test_sources_untouched_detects_missing_file(tmp_path):
    clip = ClipInfo(path=tmp_path / "gone.mp4", size_bytes=1, mtime=time.time())
    ok, problems = sources_untouched([clip])
    assert ok is False and len(problems) == 1
