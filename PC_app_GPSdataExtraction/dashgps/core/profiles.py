"""Built-in camera overlay profiles. Port of core/OverlayProfiles.kt: same regexes, same order."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .models import CropRect, OverlayProfile, Reading

# Characters OCR commonly returns in place of digits, and the digit they stand for.
_DIGIT_LOOKALIKES = {
    "O": "0", "Q": "0", "D": "0", "U": "0",
    "I": "1", "L": "1", "|": "1", "!": "1", "T": "1",
    "Z": "2", "S": "5", "B": "8", "G": "6",
}

# A "digit" as OCR may return it.
_D = "[0-9OQDUIL|!TZSBG]"
_F = re.ASCII  # Java's \s is ASCII-only

# dd/MM/yyyy; OCR may turn "/" into f, l, I, 1 or 7 ("19/0972026"), or drop it.
_DATE_DMY = re.compile(rf"({_D}{{2}})\s*[/\\FLI17|]?\s*({_D}{{2}})\s*[/\\FLI17|]?\s*({_D}{{4}})", _F)
_TIME_HMS = re.compile(rf"(?<![0-9])({_D}{{1,2}})\s*[:;.]\s*({_D}{{2}})\s*[:;.]\s*({_D}{{2}})(?![0-9])", _F)
# Looser time shape, only used to recover the seconds when a digit is lost ("07:1:00").
_TIME_LOOSE = re.compile(rf"(?<![0-9])({_D}{{1,2}})\s*[:;.]\s*({_D}{{1,2}})\s*[:;.]\s*({_D}{{2}})(?![0-9])", _F)
# "39KM/H"; OCR also gives "6KNM/H", "20kKM/H", "35KMWH", "0KMIA": the number + KM is what counts.
_SPEED_KMH = re.compile(rf"({_D}{{1,3}})\s*K{{1,2}}\s*N?\s*M(?:\s*[/\\|IWFA1]{{0,2}}\s*H)?", _F)
_WS = re.compile(r"\s+", _F)


def _coord_regex(hemispheres: str, int_digits: str, decimals: Optional[int]) -> re.Pattern:
    """
    Hemisphere letter, integer degrees, decimal point, decimals - not followed by another
    digit. With a fixed decimal count the point may be misread (":", ",", ")", ...) or
    missing, and a stray space may split the decimals: the last N digits are the decimals.
    """
    if decimals is not None:
        body = rf"({_D}{{{int_digits}}})\s*[.,:;)(/]{{0,2}}\s*({_D}(?: ?{_D}){{{decimals - 1}}})"
    else:
        body = rf"({_D}{{{int_digits}}})\s*[.,]\s*({_D}{{3,7}})"
    # No digit may touch the last decimal (that would be a 6th decimal); anything after a
    # space is ignored ("E 106.63704 !" is fine).
    return re.compile(rf"([{hemispheres}])\s*{body}(?!{_D})", _F)


def _map_digits(s: str) -> str:
    return "".join(_DIGIT_LOOKALIKES.get(c, c) for c in s if c != " ")


def _digits(s: str) -> Optional[int]:
    v = _map_digits(s)
    return int(v) if v and v.isascii() and v.isdigit() else None


def _decimal(int_part: str, frac: str) -> Optional[float]:
    i = _digits(int_part)
    if i is None:
        return None
    f = _map_digits(frac)
    if not (f.isascii() and f.isdigit()):
        return None
    return float(f"{i}.{f}")


def normalize(text: str) -> str:
    """Uppercase, one line, single spaces (OCR may return one line per text block)."""
    return _WS.sub(" ", text.upper()).strip()


def _overlaps(a: re.Match, others) -> bool:
    s, e = a.span()
    return any(s < oe and os_ < e for os_, oe in others)


def parse_dmy_time_speed_lat_lon(text: str, coord_decimals: Optional[int] = None) -> Optional[Reading]:
    """
    "dd/MM/yyyy HH:mm:ss <speed>KM/H N<lat> E<lon>" in any order, tolerant of the usual OCR
    confusions (O/0, l/1, S/5, extra or missing spaces). coord_decimals = exact number of
    decimals the camera prints (None = accept 3..7 with a proper decimal point).

    Position and speed are required. If the date or time cannot be read, the reading is
    still returned with time = None and the seconds that could be read in second_hint,
    so the cleaner can rebuild the time from the video clock; with no seconds either,
    the text is unusable (None).
    """
    t = normalize(text)
    # The coordinates are matched first so their digits are not mistaken for the date/time.
    lat = _coord_regex("NS", "1,2", coord_decimals).search(t)
    if lat is None:
        return None
    lon_regex = _coord_regex("EW", "1,3", coord_decimals)
    lon = lon_regex.search(t, lat.end()) or lon_regex.search(t)
    if lon is None:
        return None
    coords = [lat.span(), lon.span()]
    speed = next((m for m in _SPEED_KMH.finditer(t) if not _overlaps(m, coords)), None)
    if speed is None:
        return None
    taken = coords + [speed.span()]
    date = next((m for m in _DATE_DMY.finditer(t) if not _overlaps(m, taken)), None)
    taken_d = taken + ([date.span()] if date else [])
    time = next((m for m in _TIME_HMS.finditer(t) if not _overlaps(m, taken_d)), None)

    lat_abs = _decimal(lat.group(2), lat.group(3))
    lon_abs = _decimal(lon.group(2), lon.group(3))
    if lat_abs is None or lon_abs is None:
        return None
    stamp = None
    if date is not None and time is not None:
        def d(s, default):
            v = _digits(s)
            return default if v is None else v
        try:
            stamp = datetime(d(date.group(3), 0), d(date.group(2), 0), d(date.group(1), 0),
                             d(time.group(1), -1), d(time.group(2), -1), d(time.group(3), -1))
        except ValueError:
            stamp = None
    if stamp is not None:
        second_hint = stamp.second
    else:
        m = time or next((m for m in _TIME_LOOSE.finditer(t) if not _overlaps(m, taken)), None)
        second_hint = _digits(m.group(3)) if m else None
        if second_hint is None or not 0 <= second_hint <= 59:
            return None
    speed_kmh = _digits(speed.group(1))
    if speed_kmh is None:
        return None
    return Reading(
        time=stamp,
        lat=-lat_abs if lat.group(1) == "S" else lat_abs,
        lon=-lon_abs if lon.group(1) == "W" else lon_abs,
        speed_kmh=speed_kmh,
        second_hint=second_hint,
    )


# Vietmap SpeedMap M1, front camera (2560x1440). Bottom strip, left of centre:
#   "Vietmap SpeedMap M1 13/09/2026 11:31:13 39KM/H N10.76736 E106.63187"
# The rear camera only shows a date/time, no GPS.
VIETMAP_M1_FRONT = OverlayProfile(
    id="vietmap_m1_front",
    name="Vietmap SpeedMap M1 – front camera",
    crop=CropRect(left=0.0, top=0.90, right=0.80, bottom=1.0),
    # Vietmap always prints exactly 5 decimals; OCR sometimes drops or adds one
    # ("106.63376" read as "106.633"), which would move the point ~80 m.
    parse=lambda text: parse_dmy_time_speed_lat_lon(text, coord_decimals=5),
    note="Use the Front video: the rear camera does not show GPS.",
)

ALL = [VIETMAP_M1_FRONT]


def by_id(profile_id: Optional[str]) -> OverlayProfile:
    return next((p for p in ALL if p.id == profile_id), ALL[0])
