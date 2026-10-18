"""Chronological ordering of clips (Design Section 5.6) - FR-3."""

from __future__ import annotations

import logging
import re
from datetime import datetime

from .models import CameraGroup, ClipInfo

log = logging.getLogger("dashcam_merger.ordering")

TS14 = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?!\d)")
TS_SEP = re.compile(
    r"(?<!\d)(\d{4})[-_]?(\d{2})[-_]?(\d{2})[-_T ](\d{2})[-_:.]?(\d{2})[-_:.]?(\d{2})(?!\d)"
)
SEQ = re.compile(r"(?<!\d)(\d{3,})(?!\d)")

# No clip length is assumed (FR-3.4a): dashcams record 1-, 3- or 5-minute clips, the
# setting can change between drives, and the last clip of a drive is shorter.


def _to_datetime(match: re.Match[str]) -> datetime | None:
    try:
        return datetime(*(int(g) for g in match.groups()))  # type: ignore[arg-type]
    except ValueError:  # e.g. 20261399235959
        return None


def parse_timestamp_14(text: str) -> datetime | None:
    """Parse a bare 14-digit YYYYMMDDHHMMSS token (FR-3.1 a)."""
    match = TS14.fullmatch(text) or TS14.search(text)
    return _to_datetime(match) if match else None


def parse_timestamp_span(name: str) -> tuple[datetime | None, tuple[int, int] | None]:
    """Return (timestamp, span) for the first valid timestamp in a file stem.

    Tries the 14-digit form first (FR-3.1 a), then separated forms (FR-3.1 b).
    Invalid dates are skipped and the next match of the same pattern is tried.

    # DESIGN-GAP: Design 5.6 gives parse_timestamp the plain signature but also
    # requires the match span for parse_sequence. parse_timestamp keeps the
    # normative signature; this helper carries the span.
    """
    for pattern in (TS14, TS_SEP):
        for match in pattern.finditer(name):
            stamp = _to_datetime(match)
            if stamp is not None:
                return stamp, match.span()
    return None, None


def parse_timestamp(name: str) -> datetime | None:
    """First valid timestamp in a file stem, or None (FR-3.1 a/b)."""
    return parse_timestamp_span(name)[0]


def parse_sequence(name: str, timestamp_span: tuple[int, int] | None) -> int | None:
    """Last 3+ digit run that does not overlap the timestamp (FR-3.1 c)."""
    found: int | None = None
    for match in SEQ.finditer(name):
        if timestamp_span is not None:
            start, end = match.span()
            if start < timestamp_span[1] and timestamp_span[0] < end:
                continue  # overlaps the timestamp, not a sequence number
        found = int(match.group(1))
    return found


def annotate(clips: list[ClipInfo]) -> None:
    """Fill in timestamp and sequence for every clip that lacks them."""
    for clip in clips:
        stem = clip.path.stem
        span: tuple[int, int] | None = None
        if clip.timestamp is None:
            clip.timestamp, span = parse_timestamp_span(stem)
        else:
            _, span = parse_timestamp_span(stem)
        if clip.sequence is None:
            clip.sequence = parse_sequence(stem, span)


def order_group(group: CameraGroup) -> None:
    """Sort a group in place and record which key was used (FR-3.1, FR-3.2)."""
    clips = group.clips
    if not clips:
        group.order_source = "timestamp"
        return
    if all(c.timestamp is not None for c in clips):
        group.order_source = "timestamp"
        clips.sort(key=lambda c: (c.timestamp, c.sequence or 0, c.path.name.lower()))
    elif all(c.sequence is not None for c in clips):
        group.order_source = "sequence"
        clips.sort(key=lambda c: (c.sequence, c.path.name.lower()))
    else:
        group.order_source = "mtime"  # FR-3.3 warning is added by the planner
        clips.sort(key=lambda c: (c.mtime, c.path.name.lower()))
    log.info("%s: %d clips ordered by %s", group.camera, len(clips), group.order_source)


def gap_seconds(before: ClipInfo, after: ClipInfo) -> float | None:
    """End of `before` (start + probed duration) to start of `after`, or None if unknown.

    No clip length is assumed (FR-3.4a): without a probed duration there is no gap.
    """
    if before.timestamp is None or after.timestamp is None or before.probe is None:
        return None
    return (after.timestamp - before.timestamp).total_seconds() - before.probe.duration


def detect_gaps(group: CameraGroup, warn_seconds: int) -> None:
    """Record recording gaps between consecutive clips (FR-3.4, FR-3.4a).

    Only meaningful when the group is ordered by timestamp; a gap is the time
    between the end of one clip (its start + its probed duration) and the
    start of the next - never the difference of the two start times, which
    would flag every pair of 5-minute clips. A pair whose first clip has no
    probed duration is skipped: its length is unknown, not 60 s.
    """
    group.gaps = []
    if group.order_source != "timestamp":
        return
    clips = group.clips
    for before, after in zip(clips, clips[1:]):
        gap = gap_seconds(before, after)
        if gap is None:
            log.debug("%s: no time or duration, gap to %s not checked", before.path.name, after.path.name)
            continue
        if gap > warn_seconds:
            group.gaps.append((before, after, gap))
