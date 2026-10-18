"""
The camera's clock zone. Overlay times are camera-local; GPX/KML need UTC. The Android
app uses the phone's zone; here the default is the PC's zone, or an IANA name
(e.g. "Asia/Ho_Chi_Minh") can be given.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


class Zone:
    def __init__(self, name: Optional[str] = None):
        self._tz = None
        if name:
            from zoneinfo import ZoneInfo  # needs the tzdata package on Windows
            self._tz = ZoneInfo(name)
        self.name = name or _local_name()

    def to_utc(self, t: datetime) -> datetime:
        """Camera-local naive time -> aware UTC."""
        aware = t.replace(tzinfo=self._tz) if self._tz else t.astimezone()
        return aware.astimezone(timezone.utc)

    def to_local(self, t: datetime) -> datetime:
        """Aware time -> camera-local naive time."""
        return (t.astimezone(self._tz) if self._tz else t.astimezone()).replace(tzinfo=None)

    def __str__(self) -> str:
        return self.name


def _local_name() -> str:
    try:
        import tzlocal  # optional: gives an IANA name like the Android app shows
        return str(tzlocal.get_localzone_name())
    except Exception:
        off = datetime.now().astimezone().utcoffset()
        mins = int(off.total_seconds() // 60) if off else 0
        sign = "+" if mins >= 0 else "-"
        return f"UTC{sign}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}"
