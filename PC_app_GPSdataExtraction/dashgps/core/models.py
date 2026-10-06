"""Shared data types. Port of the Android app's core/Models.kt."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Callable, Optional


@dataclass(frozen=True)
class CropRect:
    """Region to OCR, as fractions of the frame (0..1)."""
    left: float
    top: float
    right: float
    bottom: float


@dataclass(frozen=True)
class Reading:
    """
    What one frame's overlay says, before any plausibility checks. Time is camera-local;
    None when the date/time text was damaged - then second_hint holds the seconds digits
    that were still readable, so the time can be rebuilt from the video clock.
    """
    time: Optional[datetime]
    lat: float
    lon: float
    speed_kmh: int
    second_hint: Optional[int] = None


@dataclass(frozen=True)
class OverlayProfile:
    """Where a camera burns its telemetry into the picture, and how to read it back."""
    id: str
    name: str
    crop: CropRect
    parse: Callable[[str], Optional[Reading]] = field(compare=False)
    note: str = ""


@dataclass(frozen=True)
class Sample:
    """One sampled frame: where it came from and what OCR read there."""
    video_index: int
    video_name: str
    pos_sec: float
    text: str


@dataclass(frozen=True, eq=False)
class Fix:
    """A reading that passed every check, tied back to its video frame."""
    sample_index: int
    video_index: int
    video_name: str
    pos_sec: float
    time: datetime
    lat: float
    lon: float
    speed_kmh: int
    raw: str

    def copy(self, **changes) -> "Fix":
        return replace(self, **changes)


class RejectReason(Enum):
    Unreadable = "text not readable"
    NoGpsFix = "no GPS fix (0,0)"
    OutOfRange = "value out of range"
    TimeMismatch = "time inconsistent with video"
    Duplicate = "same second as another frame"
    PositionJump = "impossible position jump"
    Spike = "sideways spike off the route"

    @property
    def label(self) -> str:
        return self.value


@dataclass
class TrackStats:
    points: int
    segments: int
    distance_m: float
    start: Optional[datetime]
    end: Optional[datetime]
    #: Time actually covered by the segments (gaps between segments excluded), seconds.
    moving_seconds: int
    max_speed_kmh: int

    @property
    def avg_speed_kmh(self) -> float:
        return self.distance_m / self.moving_seconds * 3.6 if self.moving_seconds > 0 else 0.0


@dataclass
class Track:
    #: Accepted fixes sorted by time.
    fixes: list
    #: Fixes split where the recording has a gap (see cleaner.SEGMENT_GAP_SECONDS).
    segments: list
    #: RejectReason -> count, in RejectReason order.
    rejected: dict
    #: Every sampled frame, in input order, and what happened to it (None = kept).
    sample_list: list
    sample_status: list
    stats: TrackStats
    #: Fixes whose damaged time text was rebuilt from the video clock.
    time_repaired: int = 0
    #: Fixes whose misread speed was replaced by the speed their positions travel.
    speed_corrected: int = 0

    @property
    def samples(self) -> int:
        return len(self.sample_list)
