"""
Reads the route out of KML / KMZ / GPX files - this app's exports and those of other apps.
Port of core/RouteFiles.kt. GPX: trkpt (or rtept) with time and speed (Garmin
TrackPointExtension or GPX 1.0 <speed>, m/s). KML: a gx:Track (time per point) when
present, else every LineString.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from xml.etree import ElementTree

from .exporters import ExportFormat
from .models import Track, TrackStats
from .timezone import Zone


@dataclass(frozen=True)
class RoutePoint:
    """One point of a route file. Time is camera-local (converted from the file's UTC)."""
    lat: float
    lon: float
    time: Optional[datetime]
    speed_kmh: Optional[float]


@dataclass
class RouteFile:
    """A route read from a KML, KMZ or GPX file; one list per line / track segment."""
    name: str
    format: ExportFormat
    segments: List[List[RoutePoint]]

    @property
    def points(self) -> List[RoutePoint]:
        return [p for seg in self.segments for p in seg]

    @property
    def has_time(self) -> bool:
        pts = self.points
        return bool(pts) and all(p.time is not None for p in pts)

    @property
    def has_speed(self) -> bool:
        pts = self.points
        return bool(pts) and all(p.speed_kmh is not None for p in pts)

    @property
    def stem(self) -> str:
        return self.name.rsplit(".", 1)[0] if "." in self.name else self.name


@dataclass
class RouteClean:
    """A cleaned route: the result plus the stats of the route as it was read."""
    route: RouteFile
    track: Track
    before: TrackStats
    #: Seconds assumed between points when the file has no timestamps, else None.
    assumed_step_s: Optional[int]


_WS = re.compile(r"\s+")


def format_of(file_name: str) -> Optional[ExportFormat]:
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    return {"kmz": ExportFormat.KMZ, "kml": ExportFormat.KML, "gpx": ExportFormat.GPX}.get(ext)


def read(file_name: str, data: bytes, zone: Zone) -> RouteFile:
    fmt = format_of(file_name)
    if fmt is None:
        raise ValueError(f"Not a KML, KMZ or GPX file: {file_name}")
    if fmt is ExportFormat.KMZ:
        segments = _parse_kml(_kml_from_kmz(data), zone)
    elif fmt is ExportFormat.KML:
        segments = _parse_kml(data, zone)
    else:
        segments = _parse_gpx(data, zone)
    segments = [s for s in segments if s]
    if not segments:
        raise ValueError(f"No route points found in {file_name}")
    return RouteFile(file_name, fmt, segments)


def read_path(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _kml_from_kmz(data: bytes) -> bytes:
    """The KML inside a KMZ: doc.kml if present, else the first .kml entry."""
    first = None
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".kml"):
                continue
            content = z.read(info)
            if info.filename.rsplit("/", 1)[-1].lower() == "doc.kml":
                return content
            if first is None:
                first = content
    if first is None:
        raise ValueError("The KMZ contains no KML file")
    return first


def _parse_xml(data: bytes) -> ElementTree.Element:
    # Route files never need DTDs or entities; refuse them (XXE / entity expansion).
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError("Route files with a DOCTYPE are not supported")
    return ElementTree.fromstring(data)


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1] if isinstance(tag, str) else ""


def _all(e: ElementTree.Element, local: str) -> List[ElementTree.Element]:
    """Elements below e (not e itself) whose name without namespace is local, in document order."""
    it = e.iter()
    next(it)
    return [x for x in it if _local(x.tag) == local]


def _text(e: ElementTree.Element) -> str:
    return "".join(e.itertext()).strip()


def _first_text(e: ElementTree.Element, local: str) -> Optional[str]:
    found = _all(e, local)
    return _text(found[0]) if found else None


def _float(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _time(text: Optional[str], zone: Zone) -> Optional[datetime]:
    if not text or not text.strip() or "T" not in text:
        return None
    try:
        t = datetime.fromisoformat(text)
    except ValueError:
        return None
    return zone.to_local(t) if t.tzinfo is not None else t


def _parse_gpx(data: bytes, zone: Zone) -> List[List[RoutePoint]]:
    root = _parse_xml(data)

    def point(e) -> Optional[RoutePoint]:
        lat = _float(e.get("lat", ""))
        lon = _float(e.get("lon", ""))
        if lat is None or lon is None:
            return None
        speed = _float(_first_text(e, "speed"))
        return RoutePoint(lat, lon, _time(_first_text(e, "time"), zone), speed * 3.6 if speed is not None else None)

    segs = [[p for p in map(point, _all(seg, "trkpt")) if p] for seg in _all(root, "trkseg")]
    if any(segs):
        return segs
    trk = [p for p in map(point, _all(root, "trkpt")) if p]
    if trk:
        return [trk]
    return [[p for p in map(point, _all(rte, "rtept")) if p] for rte in _all(root, "rte")]


def _parse_kml(data: bytes, zone: Zone) -> List[List[RoutePoint]]:
    root = _parse_xml(data)
    tracks = []
    for tr in _all(root, "Track"):
        whens = [_time(_text(w), zone) for w in _all(tr, "when")]
        pts = []
        for i, c in enumerate(_WS.split(_text(x)) for x in _all(tr, "coord")):
            lon = _float(c[0]) if len(c) > 0 else None
            lat = _float(c[1]) if len(c) > 1 else None
            if lon is None or lat is None:
                continue
            pts.append(RoutePoint(lat, lon, whens[i] if i < len(whens) else None, None))
        if pts:
            tracks.append(pts)
    if tracks:
        return tracks
    out = []
    for ls in _all(root, "LineString"):
        pts = []
        for tup in (s for s in _WS.split(_first_text(ls, "coordinates") or "") if s.strip()):
            c = tup.split(",")
            lon = _float(c[0]) if len(c) > 0 else None
            lat = _float(c[1]) if len(c) > 1 else None
            if lon is None or lat is None:
                continue
            pts.append(RoutePoint(lat, lon, None, None))
        out.append(pts)
    return out
