"""Tests for core/ordering.py (Design Section 5.6)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

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


def probed(name: str, seconds: float) -> ClipInfo:
    c = clip(name)
    c.probe = ProbeResult(duration=seconds, size_bytes=1000, signature=SIG)
    return c


def gaps_of(*clips: ClipInfo, warn_seconds: int = 300) -> list[tuple[ClipInfo, ClipInfo, float]]:
    annotate(list(clips))
    group = CameraGroup(camera="Front", clips=list(clips))
    order_group(group)
    detect_gaps(group, warn_seconds=warn_seconds)
    return group.gaps


def test_gap_of_ten_minutes_detected_with_sixty_second_clips():
    a = probed("20260831152500_0000001F.MP4", 60.0)
    b = probed("20260831153600_0000002F.MP4", 60.0)  # 11 min later, clip a is 60 s long
    gaps = gaps_of(a, b)
    assert len(gaps) == 1
    before, after, gap = gaps[0]
    assert before is a and after is b
    assert gap == 600.0


def test_no_gap_for_consecutive_clips():
    assert gaps_of(
        probed("20260831152500_0000001F.MP4", 60.0),
        probed("20260831152600_0000002F.MP4", 60.0),
    ) == []


def test_gap_uses_probed_duration_when_available():
    a = probed("20260831152500_0000001F.MP4", 180.0)
    b = clip("20260831153000_0000002F.MP4")  # 5 min later
    gaps = gaps_of(a, b, warn_seconds=100)
    assert gaps[0][2] == 120.0  # 300 s apart minus a 180 s clip


def test_unprobed_clip_length_is_not_assumed():
    # FR-3.4a: no 60 s default - without a duration the pair is not judged.
    assert gaps_of(
        clip("20260831152500_0000001F.MP4"),
        clip("20261001080000_0000002F.MP4"),
        warn_seconds=1,
    ) == []


def test_three_minute_clips_with_name_jitter_have_no_gap():
    # Vietmap M1, 3-min setting: whole-second names drift by up to 2 s.
    assert gaps_of(
        probed("20261017130506_0000205F.MP4", 180.0),
        probed("20261017130809_0000207F.MP4", 180.0),  # 183 s later
        probed("20261017131109_0000209F.MP4", 180.0),
        probed("20261017131409_0000211F.MP4", 180.0),
    ) == []


def test_five_minute_clips_are_not_flagged_at_default_threshold():
    # Start-to-start is 300-302 s; end-to-start is ~0 s (FR-3.4b).
    assert gaps_of(
        probed("20261017080000_0000001F.MP4", 300.0),
        probed("20261017080501_0000003F.MP4", 300.0),
        probed("20261017081002_0000005F.MP4", 300.0),
        probed("20261017081502_0000007F.MP4", 300.0),
    ) == []


def test_short_last_clip_before_a_break():
    # The last clip of a drive stops with the engine: 8 s, not 180 s.
    a = probed("20261014075736_0000015F.MP4", 180.0)
    b = probed("20261014080036_0000017F.MP4", 8.32)
    c = probed("20261014154240_0000019F.MP4", 180.0)
    gaps = gaps_of(a, b, c)
    assert len(gaps) == 1
    assert gaps[0][0] is b and gaps[0][1] is c
    assert gaps[0][2] == pytest.approx(27715.68)


def test_clip_length_changed_between_drives():
    # 1-min clips, a 1 h stop, then 3-min clips: only the stop is a gap.
    gaps = gaps_of(
        probed("20261013070000_0000001F.MP4", 60.0),
        probed("20261013070100_0000003F.MP4", 60.0),
        probed("20261013080200_0000005F.MP4", 180.0),
        probed("20261013080500_0000007F.MP4", 180.0),
    )
    assert [round(g[2]) for g in gaps] == [3600]


def test_gaps_not_detected_for_mtime_ordering():
    a = clip("noname_a.MP4", mtime=0.0)
    b = clip("noname_b.MP4", mtime=99999.0)
    annotate([a, b])
    group = CameraGroup(camera="Front", clips=[a, b])
    order_group(group)
    detect_gaps(group, warn_seconds=1)
    assert group.gaps == []
