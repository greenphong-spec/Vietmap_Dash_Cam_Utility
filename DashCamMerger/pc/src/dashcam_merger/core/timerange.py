"""Time range selection (FR-3.7): merge only the clips recorded between two times.

The range is given to the minute and applied to whole clips, so the merge stays a
lossless stream copy: a clip is kept when any part of it lies inside the range.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from .models import ClipInfo, TimePoint

_POINT = re.compile(
    r"^(?:(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})[ T]+)?(\d{1,2})[:.hH](\d{2})(?::(\d{2}))?$"
)
FORMAT_HINT = "use HH:MM or YYYY-MM-DD HH:MM, e.g. 14:05 or 2026-11-02 14:05"


class TimeRangeError(ValueError):
    """The time range cannot be understood or applied to this footage."""


def parse_time_point(text: str) -> TimePoint:
    """'14:05', '2026-11-02 14:05' or '2026-11-02T14:05' -> TimePoint."""
    match = _POINT.match(text.strip())
    if not match:
        raise TimeRangeError(f"not a time: '{text.strip()}' ({FORMAT_HINT})")
    year, month, day, hour, minute, second = match.groups()
    try:
        clock = time(int(hour), int(minute), int(second or 0))
        on = date(int(year), int(month), int(day)) if year else None
    except ValueError:
        raise TimeRangeError(f"not a valid time: '{text.strip()}' ({FORMAT_HINT})") from None
    return TimePoint(clock, on)


def _only_day(days: set[date]) -> date:
    if not days:
        raise TimeRangeError(
            "the file names carry no timestamps, so a time range cannot be applied"
        )
    if len(days) > 1:
        raise TimeRangeError(
            f"the footage covers several days ({min(days)} to {max(days)}); "
            "give the date too, e.g. 2026-11-02 14:05"
        )
    return next(iter(days))


def resolve_range(
    start: TimePoint | None, end: TimePoint | None, days: set[date]
) -> tuple[datetime | None, datetime | None]:
    """Turn the two ends into datetimes; `days` are the dates the footage starts on.

    An end without a date takes the other end's date, else the footage's only day.
    A time-only end at or before the start means the next day (23:30 to 00:30).
    """
    if start is None and end is None:
        return None, None
    anchor = (start.day if start else None) or (end.day if end else None) or _only_day(days)
    begin = datetime.combine(start.day or anchor, start.time) if start else None
    stop = datetime.combine(end.day or anchor, end.time) if end else None
    if begin is not None and stop is not None and stop <= begin:
        if end.day is None:
            stop += timedelta(days=1)
        elif start.day is None:
            begin -= timedelta(days=1)
        else:
            raise TimeRangeError(f"the time range ends ({end}) before it starts ({start})")
    return begin, stop


def overlaps(clip: ClipInfo, begin: datetime | None, stop: datetime | None) -> bool:
    """True when any part of the clip lies inside the range (whole clips only)."""
    if clip.timestamp is None:
        return False
    clip_end = clip.timestamp + timedelta(seconds=clip.probe.duration if clip.probe else 0.0)
    if stop is not None and clip.timestamp >= stop:
        return False
    return begin is None or clip_end > begin


def describe(begin: datetime | None, stop: datetime | None) -> str:
    """'2026-11-02 14:05 to 14:30', 'from 2026-11-02 14:05', 'until 2026-11-02 14:30'."""
    fmt = "%Y-%m-%d %H:%M"
    if begin is not None and stop is not None:
        short = "%H:%M" if stop.date() == begin.date() else fmt
        return f"{begin.strftime(fmt)} to {stop.strftime(short)}"
    if begin is not None:
        return f"from {begin.strftime(fmt)}"
    return f"until {stop.strftime(fmt)}" if stop is not None else ""
