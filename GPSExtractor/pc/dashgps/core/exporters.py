"""
File writers. Port of core/Exporters.kt: byte-for-byte the same KML / KMZ / GPX / CSV /
OCR log text as the Android app. Overlay times are camera-local; the zone converts them
to UTC for GPX/KML, which require UTC timestamps.
"""
from __future__ import annotations

import io
import math
import zipfile
from datetime import datetime
from enum import Enum
from typing import BinaryIO

from .cleaner import fix_distance_m, seconds_between
from .jvm import jfmt
from .models import Fix, Track
from .timezone import Zone


class ExportFormat(Enum):
    KMZ = ("kmz", "application/vnd.google-earth.kmz", "KMZ (Google Earth)", "")
    KML = ("kml", "application/vnd.google-earth.kml+xml", "KML", "")
    GPX = ("gpx", "application/gpx+xml", "GPX", "")
    CSV = ("csv", "text/csv", "CSV (spreadsheet)", "")
    OCR_LOG = ("csv", "text/csv", "OCR log (every frame, for checking)", "_ocr_log")

    def __init__(self, ext, mime, label, suffix):
        self.ext = ext
        self.mime = mime
        self.label = label
        self.suffix = suffix


TOUR_MAX_STEPS = 400
# The tour plays the drive this many times faster (38 min -> ~2 min).
TOUR_SPEEDUP = 20.0
# A recording gap longer than this (s) is flown over quickly, not waited out.
TOUR_MAX_GAP_S = 60.0
TOUR_TILT = 65
TOUR_RANGE_M = 350


def _coord(v: float) -> str:
    return jfmt(v, 6)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _local(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S")


def _bearing(a: Fix, b: Fix) -> float:
    p1 = math.radians(a.lat)
    p2 = math.radians(b.lat)
    dl = math.radians(b.lon - a.lon)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x))


class Exporters:
    def __init__(self, zone: Zone, app_name: str = "DashCam GPS Extractor"):
        self.zone = zone
        self.app_name = app_name

    def _iso(self, t: datetime) -> str:
        """DateTimeFormatter.ISO_INSTANT of the camera-local time."""
        u = self.zone.to_utc(t)
        s = u.strftime("%Y-%m-%dT%H:%M:%S")
        if u.microsecond:
            ms, us = divmod(u.microsecond, 1000)
            s += f".{ms:03d}" if us == 0 else f".{u.microsecond:06d}"
        return s + "Z"

    def summary(self, track: Track, title: str, timed: bool = True) -> str:
        """timed = False for a route that came without timestamps: no times or speeds then."""
        s = track.stats
        lines = [title]
        dist = f"Distance: {jfmt(s.distance_m / 1000, 2)} km   Points: {s.points}   Segments: {s.segments}"
        if not timed:
            lines.append(dist)
        else:
            start = _local(s.start) if s.start else "-"
            end = _local(s.end) if s.end else "-"
            lines.append(f"Start: {start}   End: {end}   (camera time, {self.zone})")
            lines.append(dist)
            lines.append(f"Max speed: {s.max_speed_kmh} km/h   Average: {jfmt(s.avg_speed_kmh, 1)} km/h")
        return "".join(l + "\n" for l in lines)

    def kml(self, track: Track, title: str, google_earth_extras: bool = False, timed: bool = True) -> str:
        """
        By default the KML holds exactly three features - the route line, a Start pin and an
        End pin - so every map app (Geo Tracker, OsmAnd, My Maps, Google Earth) shows the same
        clean result. google_earth_extras adds a timed track for Google Earth's time slider
        and speed labels every 10 s; apps that ignore KML visibility show those as extra points.
        """
        o = io.StringIO()
        ln = lambda s="": o.write(s + "\n")  # noqa: E731
        ln('<?xml version="1.0" encoding="UTF-8"?>')
        ln('<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">')
        ln("<Document>")
        ln(f"<name>{_esc(title)}</name>")
        ln(f"<description>{_esc(self.summary(track, title, timed))}Created by {self.app_name}.</description>")
        ln('<Style id="route"><LineStyle><color>ff0033ff</color><width>5</width></LineStyle></Style>')
        ln('<Style id="start"><IconStyle><Icon><href>http://maps.google.com/mapfiles/kml/paddle/grn-circle.png</href></Icon></IconStyle></Style>')
        ln('<Style id="end"><IconStyle><Icon><href>http://maps.google.com/mapfiles/kml/paddle/red-square.png</href></Icon></IconStyle></Style>')

        # Route: plain LineString(s), understood by every KML viewer. Named "Route" so it is
        # not confused with the document (same title) when selecting it in Google Earth.
        ln("<Placemark><name>Route</name><styleUrl>#route</styleUrl>")
        multi = len(track.segments) > 1
        if multi:
            o.write("<MultiGeometry>")
        for seg in track.segments:
            o.write("<LineString><tessellate>1</tessellate><coordinates>")
            for f in seg:
                o.write(f"{_coord(f.lon)},{_coord(f.lat)},0 ")
            ln("</coordinates></LineString>")
        if multi:
            o.write("</MultiGeometry>")
        ln("</Placemark>")

        if track.fixes:
            ln(self._pin("Start", "#start", track.fixes[0], timed))
            ln(self._pin("End", "#end", track.fixes[-1], timed))

        self._tour(o, track, timed, clock=google_earth_extras and timed)
        if google_earth_extras and timed:
            self._google_earth_extras(o, track)
        ln("</Document>")
        ln("</kml>")
        return o.getvalue()

    def _tour(self, o, track: Track, timed: bool, clock: bool = False) -> None:
        """
        A ready-made Google Earth tour that flies along the route behind the car. The drive
        plays TOUR_SPEEDUP times faster, with at most TOUR_MAX_STEPS camera moves. It holds
        camera positions only, so map apps that ignore tours show nothing extra. With clock
        every camera move also sets Google Earth's time, so the timed track's marker drives
        along the route in front of the camera.
        """
        pts = track.fixes
        if len(pts) < 2:
            return
        stride = max(1, (len(pts) + TOUR_MAX_STEPS - 1) // TOUR_MAX_STEPS)
        stops = pts[::stride]
        if stops[-1] is not pts[-1]:
            stops = stops + [pts[-1]]
        o.write("<gx:Tour><name>Fly along the route</name><gx:Playlist>\n")
        heading = 0.0
        for i, f in enumerate(stops):
            ahead = stops[i + 1] if i + 1 < len(stops) else None
            if ahead is not None and fix_distance_m(f, ahead) > 1:
                # Keep the heading continuous (no 359 -> 0 spin).
                b = _bearing(f, ahead)
                heading += math.fmod(math.fmod(b - heading, 360) + 540, 360) - 180
            if i == 0:
                seconds = 3.0
            else:
                prev = stops[i - 1]
                dt = float(seconds_between(prev.time, f.time)) if timed else float(stride)
                seconds = max(min(dt, TOUR_MAX_GAP_S) / TOUR_SPEEDUP, 0.2)
            when = f"<gx:TimeStamp><when>{self._iso(f.time)}</when></gx:TimeStamp>" if clock else ""
            o.write(
                f"<gx:FlyTo><gx:duration>{jfmt(seconds, 2)}</gx:duration>"
                f"<gx:flyToMode>{'bounce' if i == 0 else 'smooth'}</gx:flyToMode>"
                f"<LookAt>{when}<longitude>{_coord(f.lon)}</longitude><latitude>{_coord(f.lat)}</latitude><altitude>0</altitude>"
                f"<heading>{jfmt(heading, 1)}</heading><tilt>{TOUR_TILT}</tilt>"
                f"<range>{TOUR_RANGE_M}</range><altitudeMode>relativeToGround</altitudeMode></LookAt></gx:FlyTo>\n"
            )
        o.write("</gx:Playlist></gx:Tour>\n")

    def _google_earth_extras(self, o, track: Track) -> None:
        ln = lambda s: o.write(s + "\n")  # noqa: E731
        ln('<Style id="pt"><IconStyle><scale>0.6</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/shaded_dot.png</href></Icon></IconStyle><LabelStyle><scale>0.7</scale></LabelStyle></Style>')
        # Timed track: Google Earth moves its arrow along the route with the time slider and
        # during the tour. Visible - this whole section is opt-in for Google Earth.
        ln(
            '<Style id="car"><IconStyle><scale>1.3</scale><Icon><href>http://earth.google.com/images/kml-icons/track-directional/track-0.png</href></Icon></IconStyle>'
            # No name label or (empty) balloon over the moving arrow during the tour.
            '<LabelStyle><scale>0</scale></LabelStyle><LineStyle><color>ff00ffff</color><width>3</width></LineStyle>'
            '<BalloonStyle><displayMode>hide</displayMode></BalloonStyle></Style>'
        )
        ln("<Placemark><name>Moving marker (time slider / tour)</name><styleUrl>#car</styleUrl><gx:MultiTrack>")
        for seg in track.segments:
            ln("<gx:Track>")
            for f in seg:
                ln(f"<when>{self._iso(f.time)}</when>")
            for f in seg:
                ln(f"<gx:coord>{_coord(f.lon)} {_coord(f.lat)} 0</gx:coord>")
            ln("</gx:Track>")
        ln("</gx:MultiTrack></Placemark>")

        # Speed/time labels every 10 s, hidden by default in Google Earth.
        ln("<Folder><name>Speed every 10 s</name><visibility>0</visibility>")
        last = None
        for f in track.fixes:
            if last is not None and seconds_between(last, f.time) < 10:
                continue
            last = f.time
            ln(
                f"<Placemark><name>{f.speed_kmh} km/h</name><visibility>0</visibility><styleUrl>#pt</styleUrl>"
                f"<TimeStamp><when>{self._iso(f.time)}</when></TimeStamp>"
                f"<description>{_esc(_local(f.time))} · {f.speed_kmh} km/h · {_esc(f.video_name)} @ {int(f.pos_sec)} s</description>"
                f"<Point><coordinates>{_coord(f.lon)},{_coord(f.lat)},0</coordinates></Point></Placemark>"
            )
        ln("</Folder>")

    def _pin(self, name: str, style: str, f: Fix, timed: bool = True) -> str:
        desc = f"<description>{_esc(_local(f.time))} · {f.speed_kmh} km/h</description>" if timed else ""
        return (f"<Placemark><name>{name}</name><styleUrl>{style}</styleUrl>{desc}"
                f"<Point><coordinates>{_coord(f.lon)},{_coord(f.lat)},0</coordinates></Point></Placemark>")

    def write_kmz(self, track: Track, title: str, out: BinaryIO, google_earth_extras: bool = False, timed: bool = True) -> None:
        """KMZ = zip containing doc.kml (what Google Earth opens by default)."""
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("doc.kml", self.kml(track, title, google_earth_extras, timed).encode("utf-8"))

    def gpx(self, track: Track, title: str, timed: bool = True) -> str:
        """GPX 1.1; speed (m/s) in the Garmin TrackPointExtension v2, understood by most apps."""
        o = io.StringIO()
        ln = lambda s: o.write(s + "\n")  # noqa: E731
        ln('<?xml version="1.0" encoding="UTF-8"?>')
        ln(f'<gpx version="1.1" creator="{_esc(self.app_name)}" xmlns="http://www.topografix.com/GPX/1/1" '
           'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v2">')
        o.write(f"<metadata><name>{_esc(title)}</name>")
        if timed and track.fixes:
            o.write(f"<time>{self._iso(track.fixes[0].time)}</time>")
        ln("</metadata>")
        ln(f"<trk><name>{_esc(title)}</name>")
        for seg in track.segments:
            ln("<trkseg>")
            for f in seg:
                if not timed:
                    ln(f'<trkpt lat="{_coord(f.lat)}" lon="{_coord(f.lon)}"></trkpt>')
                    continue
                ln(
                    f'<trkpt lat="{_coord(f.lat)}" lon="{_coord(f.lon)}"><time>{self._iso(f.time)}</time>'
                    "<extensions><gpxtpx:TrackPointExtension><gpxtpx:speed>"
                    + jfmt(f.speed_kmh / 3.6, 2)
                    + "</gpxtpx:speed></gpxtpx:TrackPointExtension></extensions></trkpt>"
                )
            ln("</trkseg>")
        ln("</trk>")
        ln("</gpx>")
        return o.getvalue()

    def csv(self, track: Track) -> str:
        o = io.StringIO()
        o.write("time_local,time_utc,latitude,longitude,speed_kmh,video,video_position_s\n")
        for f in track.fixes:
            video = '"' + f.video_name.replace('"', '""') + '"'
            o.write(f"{_local(f.time)},{self._iso(f.time)},{_coord(f.lat)},{_coord(f.lon)},{f.speed_kmh},{video},"
                    f"{jfmt(f.pos_sec, 1)}\n")
        return o.getvalue()

    def ocr_log(self, track: Track) -> str:
        """Every sampled frame with its raw OCR text and whether it was kept or why not."""
        def q(v: str) -> str:
            return '"' + v.replace('"', '""') + '"'
        o = io.StringIO()
        o.write("video,video_position_s,status,ocr_text\n")
        for s, st in zip(track.sample_list, track.sample_status):
            o.write(f"{q(s.video_name)},{jfmt(s.pos_sec, 1)},{st.name if st else 'KEPT'},{q(s.text)}\n")
        return o.getvalue()

    def write(self, fmt: ExportFormat, track: Track, title: str, out: BinaryIO,
              google_earth_extras: bool = False, timed: bool = True) -> None:
        if fmt is ExportFormat.KMZ:
            self.write_kmz(track, title, out, google_earth_extras, timed)
        elif fmt is ExportFormat.KML:
            out.write(self.kml(track, title, google_earth_extras, timed).encode("utf-8"))
        elif fmt is ExportFormat.GPX:
            out.write(self.gpx(track, title, timed).encode("utf-8"))
        elif fmt is ExportFormat.CSV:
            out.write(self.csv(track).encode("utf-8"))
        else:
            out.write(self.ocr_log(track).encode("utf-8"))
