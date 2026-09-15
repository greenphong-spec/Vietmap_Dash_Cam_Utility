"""Tests for core/ordering.py (Design Section 5.6)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from dashcam_merger.core.models import CameraGroup, ClipInfo, ProbeResult, StreamSignature
from dashcam_merger.core.ordering import (
    annotate,
    detect_gaps,
    order_group,
    parse_sequence,
    parse_timestamp,
    parse_timestamp_14,
    parse_timestamp_span,
)

SIG = StreamSignature("h264", 1920, 1080, "30/1", "yuv420p", "aac", 48000, 2)


def clip(name: str, mtime: float = 0.0, size: int = 1000) -> ClipInfo:
    return ClipInfo(path=Path("C:/x") / name, size_bytes=size, mtime=mtime)


def test_parse_timestamp_14_digits():
    assert parse_timestamp("20260831152516_0015340F") == datetime(2026, 8, 31, 15, 25, 16)


def test_parse_timestamp_14_helper():
    assert parse_timestamp_14("20260831152516") == datetime(2026, 8, 31, 15, 25, 16)


def test_parse_timestamp_separated_forms():
    assert parse_timestamp("2026_0831_152516_001") == datetime(2026, 8, 31, 15, 25, 16)
    assert parse_timestamp("2026-08-31_15-25-16") == datetime(2026, 8, 31, 15, 25, 16)
    assert parse_timestamp("REC_2026-08-31 15:25:16") == datetime(2026, 8, 31, 15, 25, 16)


def test_invalid_date_is_skipped():
    assert parse_timestamp("20261399235959") is None


def test_invalid_date_then_valid_one_is_found():
    assert parse_timestamp("20261399235959_20260831152516") == datetime(2026, 8, 31, 15, 25, 16)


def test_no_timestamp_returns_none():
    assert parse_timestamp("clip_front") is None


def test_parse_sequence_takes_last_run_outside_timestamp():
    stem = "20260831152516_0015340F"
    _, span = parse_timestamp_span(stem)
    assert parse_sequence(stem, span) == 15340


def test_parse_sequence_ignores_short_runs():
    assert parse_sequence("clip_12", None) is None
    assert parse_sequence("clip_123", None) == 123


def test_annotate_fills_timestamp_and_sequence():
    clips = [clip("20260831152516_0015340F.MP4")]
    annotate(clips)
    assert clips[0].timestamp == datetime(2026, 8, 31, 15, 25, 16)
    assert clips[0].sequence == 15340


def test_order_by_timestamp_regardless_of_input_order():
    clips = [
        clip("20260831152616_0000002F.MP4"),
        clip("20260831152516_0000001F.MP4"),
        clip("20260831152716_0000003F.MP4"),
    ]
    annotate(clips)
    group = CameraGroup(camera="Front", clips=clips)
    order_group(group)
    assert group.order_source == "timestamp"
    assert [c.path.name[:14] for c in group.clips] == [
        "20260831152516",
        "20260831152616",
        "20260831152716",
    ]


def test_sequence_fallback_when_no_timestamps():
    clips = [clip("REC_0003.MP4"), clip("REC_0001.MP4"), clip("REC_0002.MP4")]
    annotate(clips)
    group = CameraGroup(camera="Front", clips=clips)
    order_group(group)
    assert group.order_source == "sequence"
    assert [c.sequence for c in group.clips] == [1, 2, 3]


def test_mtime_fallback_when_one_clip_lacks_timestamp():
    a = clip("20260831152516_0000001F.MP4", mtime=300.0)
    b = clip("randomname.MP4", mtime=100.0)
    annotate([a, b])
    group = CameraGroup(camera="Front", clips=[a, b])
    order_group(group)
    assert group.order_source == "mtime"
    assert group.clips[0] is b


def test_order_never_depends_on_listing_order():
    names = ["20260831152716_3F.MP4", "20260831152516_1F.MP4", "20260831152616_2F.MP4"]
    forward = [clip(n) for n in names]
    backward = [clip(n) for n in reversed(names)]
    for clips in (forward, backward):
        annotate(clips)
    g1, g2 = CameraGroup("Front", forward), CameraGroup("Front", backward)
    order_group(g1)
    order_group(g2)
    assert [c.path.name for c in g1.clips] == [c.path.name for c in g2.clips]


def test_gap_of_ten_minutes_detected_with_sixty_second_clips():
    a = clip("20260831152500_0000001F.MP4")
    b = clip("20260831153600_0000002F.MP4")  # 11 min later, clip a is 60 s long
    annotate([a, b])
    group = CameraGroup(camera="Front", clips=[a, b])
    order_group(group)
    detect_gaps(group, warn_seconds=300)
    assert len(group.gaps) == 1
    before, after, gap = group.gaps[0]
    assert before is a and after is b
    assert gap == 600.0


def test_no_gap_for_consecutive_clips():
    a = clip("20260831152500_0000001F.MP4")
    b = clip("20260831152600_0000002F.MP4")
    annotate([a, b])
    group = CameraGroup(camera="Front", clips=[a, b])
    order_group(group)
    detect_gaps(group, warn_seconds=300)
    assert group.gaps == []


def test_gap_uses_probed_duration_when_available():
    a = clip("20260831152500_0000001F.MP4")
    b = clip("20260831153000_0000002F.MP4")  # 5 min later
    a.probe = ProbeResult(duration=180.0, size_bytes=1000, signature=SIG)
    annotate([a, b])
    group = CameraGroup(camera="Front", clips=[a, b])
    order_group(group)
    detect_gaps(group, warn_seconds=100)
    assert group.gaps[0][2] == 120.0  # 300 s apart minus a 180 s clip


def test_gaps_not_detected_for_mtime_ordering():
    a = clip("noname_a.MP4", mtime=0.0)
    b = clip("noname_b.MP4", mtime=99999.0)
    annotate([a, b])
    group = CameraGroup(camera="Front", clips=[a, b])
    order_group(group)
    detect_gaps(group, warn_seconds=1)
    assert group.gaps == []
