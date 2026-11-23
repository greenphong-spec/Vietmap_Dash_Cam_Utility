"""Tests for core/timerange.py (FR-3.7). No ffmpeg needed."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import pytest

from dashcam_merger.core.models import ClipInfo, ProbeResult, TimePoint
from dashcam_merger.core.timerange import (
    TimeRangeError,
    describe,
    overlaps,
    parse_time_point,
    resolve_range,
)

DAY = date(2026, 11, 2)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("14:05", TimePoint(time(14, 5))),
        (" 7:30 ", TimePoint(time(7, 30))),
        ("14h05", TimePoint(time(14, 5))),
        ("14:05:30", TimePoint(time(14, 5, 30))),
        ("2026-11-02 14:05", TimePoint(time(14, 5), DAY)),
        ("2026-11-02T14:05", TimePoint(time(14, 5), DAY)),
        ("2026/11/2 9:00", TimePoint(time(9, 0), DAY)),
    ],
)
def test_parse_time_point(text, expected):
    assert parse_time_point(text) == expected


@pytest.mark.parametrize("text", ["", "14", "25:00", "14:60", "2026-13-01 10:00", "noon", "2026-11-02"])
def test_parse_rejects_bad_text(text):
    with pytest.raises(TimeRangeError):
        parse_time_point(text)


def test_no_range_resolves_to_nothing():
    assert resolve_range(None, None, {DAY, date(2026, 11, 3)}) == (None, None)


def test_time_only_uses_the_footage_day():
    begin, stop = resolve_range(TimePoint(time(14, 0)), TimePoint(time(14, 30)), {DAY})
    assert begin == datetime(2026, 11, 2, 14, 0)
    assert stop == datetime(2026, 11, 2, 14, 30)


def test_time_only_on_several_days_needs_a_date():
    with pytest.raises(TimeRangeError, match="several days"):
        resolve_range(TimePoint(time(14, 0)), None, {DAY, date(2026, 11, 3)})


def test_time_only_without_timestamps_is_an_error():
    with pytest.raises(TimeRangeError, match="no timestamps"):
        resolve_range(None, TimePoint(time(14, 0)), set())


def test_one_date_is_enough_on_a_multi_day_card():
    days = {DAY, date(2026, 11, 3)}
    begin, stop = resolve_range(TimePoint(time(14, 0), DAY), TimePoint(time(14, 30)), days)
    assert (begin, stop) == (datetime(2026, 11, 2, 14, 0), datetime(2026, 11, 2, 14, 30))
    begin, stop = resolve_range(TimePoint(time(14, 0)), TimePoint(time(14, 30), DAY), days)
    assert (begin, stop) == (datetime(2026, 11, 2, 14, 0), datetime(2026, 11, 2, 14, 30))


def test_time_only_end_before_start_means_next_day():
    begin, stop = resolve_range(TimePoint(time(23, 30), DAY), TimePoint(time(0, 30)), {DAY})
    assert stop == datetime(2026, 11, 3, 0, 30)


def test_dated_end_before_dated_start_is_an_error():
    with pytest.raises(TimeRangeError, match="before it starts"):
        resolve_range(TimePoint(time(15, 0), DAY), TimePoint(time(14, 0), DAY), {DAY})


def clip_at(hour, minute, seconds=180.0):
    clip = ClipInfo(path=Path("C:/x/a.MP4"), size_bytes=1, mtime=0.0)
    clip.timestamp = datetime(2026, 11, 2, hour, minute)
    clip.probe = ProbeResult(seconds, 1, None)
    return clip


def test_overlap_keeps_whole_clips_touching_the_range():
    begin, stop = datetime(2026, 11, 2, 14, 5), datetime(2026, 11, 2, 14, 10)
    assert overlaps(clip_at(14, 3), begin, stop)  # 14:03-14:06 reaches into the range
    assert overlaps(clip_at(14, 6), begin, stop)
    assert overlaps(clip_at(14, 9), begin, stop)  # starts inside, ends after
    assert not overlaps(clip_at(14, 2), begin, stop)  # ends exactly at 14:05
    assert not overlaps(clip_at(14, 10), begin, stop)  # starts exactly at the end


def test_overlap_open_ends_and_missing_timestamp():
    assert overlaps(clip_at(3, 0), None, datetime(2026, 11, 2, 14, 0))
    assert overlaps(clip_at(23, 0), datetime(2026, 11, 2, 14, 0), None)
    untimed = ClipInfo(path=Path("C:/x/b.MP4"), size_bytes=1, mtime=0.0)
    assert not overlaps(untimed, None, datetime(2026, 11, 2, 14, 0))


def test_describe():
    a, b = datetime(2026, 11, 2, 14, 5), datetime(2026, 11, 2, 14, 30)
    assert describe(a, b) == "2026-11-02 14:05 to 14:30"
    assert describe(a, datetime(2026, 11, 3, 0, 30)) == "2026-11-02 14:05 to 2026-11-03 00:30"
    assert describe(a, None) == "from 2026-11-02 14:05"
    assert describe(None, b) == "until 2026-11-02 14:30"
    assert describe(None, None) == ""


# ---- trip trim window (FR-3.8)
def _clip_at(start: datetime, seconds: float) -> ClipInfo:
    from dashcam_merger.core.models import StreamSignature

    clip = ClipInfo(path=Path("C:/x.MP4"), size_bytes=1, mtime=0.0)
    clip.timestamp = start
    sig = StreamSignature("h264", 1, 1, "30/1", "yuv420p", "none", 0, 0)
    clip.probe = ProbeResult(seconds, 1, sig)
    return clip


def test_clip_window_excludes_touching_neighbours():
    from datetime import timedelta

    from dashcam_merger.core.timerange import clip_window

    t = datetime(2026, 11, 2, 14, 0, 0)
    # three 60.03 s clips: their ends overlap the next start by a few frames
    clips = [_clip_at(t + timedelta(minutes=i), 60.03) for i in range(3)]
    window = clip_window(t + timedelta(minutes=1), t + timedelta(minutes=1, seconds=60.03))
    assert [overlaps(c, *window) for c in clips] == [False, True, False]


def test_clip_window_of_a_tiny_clip_still_keeps_it():
    from datetime import timedelta

    from dashcam_merger.core.timerange import clip_window

    t = datetime(2026, 11, 2, 14, 0, 0)
    clip = _clip_at(t, 1.0)
    assert overlaps(clip, *clip_window(t, t + timedelta(seconds=1)))


def test_describe_trim():
    from dashcam_merger.core.timerange import describe_trim

    begin = datetime(2026, 11, 2, 14, 15, 0)
    assert describe_trim(2, begin, datetime(2026, 11, 2, 15, 2, 10)) == (
        "trip 2: 2026-11-02 14:15:00 to 15:02:10"
    )
    assert describe_trim(3, begin, datetime(2026, 11, 3, 0, 5, 0)).endswith("to 2026-11-03 00:05:00")
