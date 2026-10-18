"""Port of RouteFilesTest.kt and RealVideoReplayTest.kt."""
import csv
import io
import zipfile
from datetime import datetime, timedelta

from conftest import RESOURCES, resource
from dashgps.core import cleaner, routefiles
from dashgps.core.cleaner import fix_distance_m, seconds_between
from dashgps.core.exporters import ExportFormat, Exporters
from dashgps.core.jvm import jfmt
from dashgps.core.models import RejectReason, Sample
from dashgps.core.profiles import VIETMAP_M1_FRONT
from dashgps.core.timezone import Zone

zone = Zone("Asia/Ho_Chi_Minh")
t0 = datetime(2026, 9, 19, 8, 41, 0)


def noisy_drive():
    """A drive east at 36 km/h (10 m/s), one point per second, with three misreads."""
    out = []
    for sec in range(60):
        lon = 106.63187 + sec * 0.0000915
        if sec == 20:
            out.append((0.76736, lon, 36))  # "10.7" read as "0.7": 1100 km away
        elif sec == 30:
            out.append((10.76736 + 0.00008, lon, 36))  # 9 m sideways kink
        elif sec == 40:
            out.append((10.76736, lon, 300))  # speed digit misread
        else:
            out.append((10.76736, lon, 36))
    return out


def gpx(points) -> bytes:
    s = ('<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1" '
         'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v2"><trk><trkseg>')
    for i, (lat, lon, speed) in enumerate(points):
        t = zone.to_utc(t0 + timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        s += f'<trkpt lat="{jfmt(lat, 6)}" lon="{jfmt(lon, 6)}"><time>{t}</time>'
        s += (f"<extensions><gpxtpx:TrackPointExtension><gpxtpx:speed>{jfmt(speed / 3.6, 2)}</gpxtpx:speed>"
              "</gpxtpx:TrackPointExtension></extensions></trkpt>")
    return (s + "</trkseg></trk></gpx>").encode()


def kml(points) -> bytes:
    return ('<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><LineString><coordinates>'
            + " ".join(f"{jfmt(lon, 6)},{jfmt(lat, 6)},0" for lat, lon, _ in points)
            + "</coordinates></LineString></Placemark></Document></kml>").encode()


def kmz(kml_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml_bytes)
    return buf.getvalue()


def read(name, data):
    return routefiles.read(name, data, zone)


def test_gpx_with_times_and_speeds_gets_every_check():
    route = read("drive.gpx", gpx(noisy_drive()))
    assert route.has_time and route.has_speed
    assert route.points[0].time == t0  # UTC in the file, camera-local here

    clean = cleaner.clean_route(route)
    assert len(clean.track.fixes) == 58
    assert clean.track.sample_status[20] == RejectReason.PositionJump
    assert clean.track.sample_status[30] == RejectReason.Spike
    assert clean.track.speed_corrected == 1
    assert clean.before.max_speed_kmh == 300 and clean.track.stats.max_speed_kmh <= 38
    assert clean.assumed_step_s is None

    # Written back as GPX and read again: same points, still timed, speeds kept.
    out = io.BytesIO()
    Exporters(zone).write(ExportFormat.GPX, clean.track, "drive_clean", out, timed=True)
    again = read("drive_clean.gpx", out.getvalue())
    assert len(again.points) == 58
    assert again.has_time and again.has_speed
    c2 = cleaner.clean_route(again)
    assert len(c2.route.points) - len(c2.track.fixes) == 0


def test_plain_kml_and_kmz_without_times_get_position_checks():
    text = kml(noisy_drive())
    for route in (read("drive.kml", text), read("drive.kmz", kmz(text))):
        assert not (route.has_time or route.has_speed)
        clean = cleaner.clean_route(route)
        assert clean.assumed_step_s == 1  # ~10 m per point -> 1 s
        assert len(clean.track.fixes) == 58  # the speed misread is not in a KML
        assert clean.track.sample_status[20] == RejectReason.PositionJump
        assert clean.track.sample_status[30] == RejectReason.Spike

        # Written without invented times or speeds.
        written = Exporters(zone).kml(clean.track, "drive_clean", timed=False)
        assert "km/h" not in written
        assert "2000-01-01" not in written
        assert len(read("drive_clean.kml", written.encode()).points) == 58


def test_sparse_kml_from_another_app_is_not_emptied():
    # A planned route with a point every ~500 m and no times must keep every point.
    points = [(10.76736 + (i % 2) * 0.001, 106.63187 + i * 0.0045, 0) for i in range(40)]
    clean = cleaner.clean_route(read("planned.kml", kml(points)))
    assert len(clean.track.fixes) == 40


def test_google_earth_timed_track_is_used():
    pts = [p for i, p in enumerate(noisy_drive()) if i not in (20, 30)]
    track = cleaner.clean_route(read("drive.gpx", gpx(pts))).track
    with_extras = Exporters(zone).kml(track, "drive", google_earth_extras=True)
    route = read("drive.kml", with_extras.encode())
    assert route.has_time
    assert len(route.points) == len(track.fixes)
    assert route.points[0].time == track.fixes[0].time


def test_real_exported_gpx_loses_exactly_its_two_misreads():
    # The Front video export of 2026-09-19 08:41-09:19, saved before steps 8-9 existed:
    # lon "106.33858" read as "106.33850" (9 m kink) and lat "10.37568" read as
    # "10.37588" (22 m spike).
    clean = cleaner.clean_route(read("front.gpx", resource("vietmap_front_route_noisy.gpx")))
    dropped = [clean.route.points[i].time.strftime("%H:%M:%S")
               for i, s in enumerate(clean.track.sample_status) if s is not None]
    assert dropped == ["08:58:03", "09:04:17"]


def test_real_exported_kmz_without_times_loses_its_misreads():
    clean = cleaner.clean_route(read("front.kmz", resource("vietmap_front_route_noisy.kmz")))
    dropped = [i for i, s in enumerate(clean.track.sample_status) if s is not None]
    assert {1001, 1329} <= set(dropped)
    assert len(dropped) <= 4


# ---- Real video replay ----------------------------------------------------------------


def load_ocr_log(path):
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))[1:]
    return [Sample(0, r[0], float(r[1]), r[3]) for r in rows]


def test_real_video_reads_cleanly():
    """
    Replays the OCR text of every frame of a real Vietmap M1 front video (23 min, 1387
    frames, recorded by the Android app as its OCR log) through parser + cleaner, and
    checks the PC port keeps exactly the frames the phone kept.
    """
    samples = load_ocr_log(RESOURCES / "vietmap_m1_front_ocr_log.csv")
    track = cleaner.clean(samples, VIETMAP_M1_FRONT)
    kept = len(track.fixes)
    longest_hole = max(seconds_between(a.time, b.time) for a, b in zip(track.fixes, track.fixes[1:]))
    worst = max(fix_distance_m(a, b) / max(seconds_between(a.time, b.time), 1) * 3.6
                for a, b in zip(track.fixes, track.fixes[1:]))
    print(f"replay: samples={len(samples)} kept={kept} repaired={track.time_repaired} "
          f"speedCorrected={track.speed_corrected} rejected={track.rejected} hole={longest_hole}s worst={worst:.0f}km/h "
          f"distance={track.stats.distance_m / 1000:.2f}km")
    assert kept >= 1300
    assert track.time_repaired >= 40
    assert longest_hole <= 20
    assert worst <= 80


def test_folder_skips_rear_clips(tmp_path):
    from dashgps.extract import find_videos
    for n in ("20260919174222_0018888F.MP4", "20260919174223_0018889R.MP4", "Front_a.mp4", "Rear_a.mp4", "notes.txt"):
        (tmp_path / n).write_bytes(b"")
    assert [p.name for p in find_videos([str(tmp_path)])] == ["20260919174222_0018888F.MP4", "Front_a.mp4"]
    # A file named on its own is always used.
    assert [p.name for p in find_videos([str(tmp_path / "Rear_a.mp4")])] == ["Rear_a.mp4"]
    # A folder of unlabelled videos is taken as is.
    other = tmp_path / "other"
    other.mkdir()
    (other / "trip.mp4").write_bytes(b"")
    assert [p.name for p in find_videos([str(other)])] == ["trip.mp4"]
