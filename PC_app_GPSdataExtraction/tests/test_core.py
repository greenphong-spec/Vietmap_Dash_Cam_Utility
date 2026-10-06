"""Port of the Android app's CoreTest.kt: same cases, same expected values."""
import io
import zipfile
from datetime import datetime, timedelta
from xml.dom import minidom

import pytest

from dashgps.core import cleaner, routefiles
from dashgps.core.exporters import TOUR_MAX_STEPS, Exporters
from dashgps.core.jvm import jfmt
from dashgps.core.models import RejectReason, Sample
from dashgps.core.profiles import VIETMAP_M1_FRONT
from dashgps.core.timezone import Zone

vietmap = VIETMAP_M1_FRONT
parse = vietmap.parse
approx = pytest.approx

# ---- Parser -------------------------------------------------------------------------


def test_parses_clean_vietmap_overlay():
    r = parse("Vietmap SpeedMap M1 13/09/2026 11:31:13 39KM/H N10.76736 E106.63187")
    assert r.time == datetime(2026, 9, 13, 11, 31, 13)
    assert r.speed_kmh == 39
    assert r.lat == approx(10.76736, abs=1e-9)
    assert r.lon == approx(106.63187, abs=1e-9)


def test_tolerates_typical_ocr_noise():
    # Split lines, lower case, O for 0, l for 1, missing and extra spaces.
    r = parse("Vietmap SpeedMap M1\n13/O9/2026  1l:31:13 39 km/h\nN10.76736E106.63187")
    assert r.time == datetime(2026, 9, 13, 11, 31, 13)
    assert r.lat == approx(10.76736, abs=1e-9)
    assert r.lon == approx(106.63187, abs=1e-9)
    assert r.speed_kmh == 39


def test_vietmap_rejects_coordinates_with_dropped_or_extra_digits():
    # Seen on a real video: "106.63376" read as "106.633", and "106.63289" as "106.632891".
    assert parse("24/09/2026 07:27:52 0KM/H N10.77630 E106.633") is None
    assert parse("24/09/2026 07:29:54 27KM/H N10.77254 E106.632891") is None
    assert parse("24/09/2026 07:27:52 0KM/H N10.77630 E106.63376") is not None


def test_recovers_damaged_separators_seen_on_real_video():
    def p(s):
        return parse(f"VIETMAP Vietmap SpeedMap M1 {s}")
    assert p("24/09f2026 07:08:53 10KM/H N10.77265 E 106.63410").time == datetime(2026, 9, 24, 7, 8, 53)
    assert p("24/0912026 07:09:15 11KM/H N10.77224 E 106.63387").time == datetime(2026, 9, 24, 7, 9, 15)
    assert p("24/092026 07:09:23 7KM/H N10.77582 E 106.63354").time == datetime(2026, 9, 24, 7, 9, 23)
    assert p("24/09/2026 07:12:02 39KM/H N10)77752 E 106.63467").lat == approx(10.77752, abs=1e-9)
    assert p("24/09/2026 07:16:46 8KM/H N10:78921 E 106.63698").lat == approx(10.78921, abs=1e-9)
    assert p("24/09/2026 07:16:47 9KM/H N1078921 E 106.63696").lat == approx(10.78921, abs=1e-9)
    assert p("24/09/2026 07:21:29 1KM/H N10.,78477 E 106.63633").lat == approx(10.78477, abs=1e-9)
    assert p("24/09/2026 07:15:35 38KM/H N10.78936 E 106.63 719").lon == approx(106.63719, abs=1e-9)
    assert p("24/09/2026 07:17:53 OKM/H N10.78908 E 10663696").lon == approx(106.63696, abs=1e-9)
    # "/" read as "7" (ML Kit and PP-OCR both do it); was year 7202 / day 97.
    assert p("19/0972026 18:55:45 4KM/H N10.35906 E106.35456").time == datetime(2026, 9, 19, 18, 55, 45)
    assert p("M119709/2026 19:00:50 1KM/H N10.36376 E106.35228").time == datetime(2026, 9, 19, 19, 0, 50)
    # Truncated coordinate stays unusable.
    assert p("24/09/2026 07:26:24 34KM/H N10.78033 E 106.63") is None


def test_collapsed_minute_gives_partial_reading_with_seconds():
    # The thin "11" of 07:11:00 merged into "1".
    r = parse("VIETMAP Vietmap SpeedMap M1 24/09/2026 07:1:04 OKM/H N10.77551 E 106.63328")
    assert r.time is None
    assert r.second_hint == 4
    assert r.lat == approx(10.77551, abs=1e-9)
    assert r.speed_kmh == 0


def test_south_west_hemispheres_are_negative():
    r = parse("01/02/2026 03:04:05 0KM/H S33.86785 W151.20732")
    assert r.lat == approx(-33.86785, abs=1e-9)
    assert r.lon == approx(-151.20732, abs=1e-9)
    assert r.speed_kmh == 0


def test_rejects_incomplete_or_impossible_text():
    assert parse("Vietmap SpeedMap M1 13/09/2026 11:31:13 39KM/H") is None  # no GPS
    # Month 13: position is still usable, the time is left for the cleaner to rebuild.
    assert parse("13/13/2026 11:31:13 39KM/H N10.76736 E106.63187").time is None
    assert parse("") is None


# ---- Cleaner ------------------------------------------------------------------------

t0 = datetime(2026, 9, 13, 11, 31, 0)


def overlay(sec, lat=10.76736, lon=None, speed=36):
    """A drive heading east at ~36 km/h (10 m/s): 0.0000915 deg lon per second at 10.77 N."""
    if lon is None:
        lon = 106.63187 + sec * 0.0000915
    t = t0 + timedelta(seconds=sec)
    return (f"Vietmap SpeedMap M1 {t.day:02d}/{t.month:02d}/{t.year:04d} {t.hour:02d}:{t.minute:02d}:{t.second:02d} "
            f"{speed}KM/H N{jfmt(lat, 5)} E{jfmt(lon, 5)}")


def samples(n, text=overlay):
    return [Sample(0, "Front.mp4", float(i), text(i)) for i in range(n)]


def test_clean_track_keeps_every_good_frame():
    track = cleaner.clean(samples(60), vietmap)
    assert len(track.fixes) == 60
    assert len(track.segments) == 1
    assert track.stats.distance_m == approx(59 * 10.0, abs=15.0)  # ~10 m/s for 59 s
    assert not track.rejected


def test_misread_time_and_position_are_dropped():
    def text(sec):
        if sec == 10:
            return overlay(sec).replace("11:31:10", "11:31:40")  # misread seconds digit
        if sec == 20:
            return overlay(sec, lat=16.76736)  # misread latitude digit
        if sec == 25:
            return "Vietmap SpeedMap M1 ####"  # unreadable
        return overlay(sec)
    track = cleaner.clean(samples(30, text), vietmap)
    assert len(track.fixes) == 27
    assert track.rejected[RejectReason.TimeMismatch] == 1
    assert track.rejected[RejectReason.PositionJump] == 1
    assert track.rejected[RejectReason.Unreadable] == 1


def test_sideways_spike_within_reach_is_dropped_but_corners_are_kept():
    # Frame 15: latitude misread by 0.0008 deg (~89 m north) - reachable in 1 s at
    # 250 km/h, so only the spike check can catch it.
    track = cleaner.clean(samples(30, lambda s: overlay(s, lat=10.76736 + 0.0008) if s == 15 else overlay(s)), vietmap)
    assert len(track.fixes) == 29
    assert track.rejected[RejectReason.Spike] == 1
    assert track.sample_status[15] == RejectReason.Spike
    assert track.sample_status[14] is None

    # A real 90-degree turn (east, then north) at 10 m/s is not a spike.
    def turn(sec):
        if sec <= 10:
            return overlay(sec)
        return overlay(sec, lat=10.76736 + (sec - 10) * 0.00009, lon=106.63187 + 10 * 0.0000915)
    assert len(cleaner.clean(samples(20, turn), vietmap).fixes) == 20


def test_same_misread_on_consecutive_frames_is_dropped():
    # Seen on a real merged Front video: "10.3" read as "0.3" on two frames in a row
    # (1112 km away and back), and "106.35" as "106.85" on two frames (55 km).
    def text(sec):
        if sec in (20, 21):
            return overlay(sec, lat=0.76736)
        if sec in (40, 41):
            return overlay(sec, lon=106.63187 + sec * 0.0000915 + 0.5)
        return overlay(sec)
    track = cleaner.clean(samples(60, text), vietmap)
    assert len(track.fixes) == 56
    assert track.rejected[RejectReason.PositionJump] == 4
    for s in (20, 21, 40, 41):
        assert track.sample_status[s] == RejectReason.PositionJump
    assert track.stats.distance_m == approx(59 * 10.0, abs=15.0)


def test_repeated_misread_while_stopped_and_at_track_start_is_dropped():
    # Parked: the overlay text barely changes, so the same misread repeats for 8 frames.
    parked = samples(60, lambda s: overlay(s, lat=16.76736, lon=106.632, speed=0) if 30 <= s <= 37
                     else overlay(s, lon=106.632, speed=0))
    assert len(cleaner.clean(parked, vietmap).fixes) == 52

    # The first 3 frames misread, nothing before them to compare against.
    track = cleaner.clean(samples(30, lambda s: overlay(s, lat=0.76736) if s < 3 else overlay(s)), vietmap)
    assert len(track.fixes) == 27
    assert track.fixes[0].sample_index == 3


def test_misread_speed_is_replaced_by_gps_speed_and_fix_is_kept():
    # Driving at 36 km/h: frame 20 reads "300", frames 30-31 read "86" ("3" -> "8").
    def text(sec):
        if sec == 20:
            return overlay(sec, speed=300)
        if sec in (30, 31):
            return overlay(sec, speed=86)
        return overlay(sec)
    track = cleaner.clean(samples(60, text), vietmap)
    assert len(track.fixes) == 60
    assert track.speed_corrected == 3
    for sec in (20, 30, 31):
        assert track.fixes[sec].speed_kmh == approx(36.0, abs=2.0)
    assert track.stats.max_speed_kmh <= 38


def test_small_spike_is_dropped_using_printed_speed():
    # Real case: lat "10.37568" read as "10.37588" - 22 m off the road at 48 km/h,
    # under the 40 m limit of the plain spike check.
    track = cleaner.clean(samples(30, lambda s: overlay(s, lat=10.76736 + 0.0002) if s == 15 else overlay(s)), vietmap)
    assert len(track.fixes) == 29
    assert track.sample_status[15] == RejectReason.Spike

    # Parked with ~4 m of GPS wander: jitter, not a misread.
    parked = samples(30, lambda s: overlay(s, lat=10.76736 + (0.00004 if s % 3 == 0 else 0.0), lon=106.632, speed=0))
    assert len(cleaner.clean(parked, vietmap).fixes) == 30


def test_sideways_kink_is_dropped():
    # Real case: lon "106.33858" read as "106.33850" - 9 m sideways at 48 km/h. The
    # distance driven barely changes, so only the curve fit catches it.
    track = cleaner.clean(samples(30, lambda s: overlay(s, lat=10.76736 + 0.00008) if s == 15 else overlay(s)), vietmap)
    assert len(track.fixes) == 29
    assert track.sample_status[15] == RejectReason.Spike


def test_real_hard_braking_keeps_its_speeds():
    # 90 km/h for 20 s, brake to a stop in 5 s (18 km/h per second), stand for 20 s.
    speeds = [90] * 20 + [72, 54, 36, 18, 0] + [0] * 20
    lon = 106.63187
    lst = []
    for sec, v in enumerate(speeds):
        if sec > 0:
            lon += (speeds[sec - 1] + v) // 2 / 3.6 * 0.00000915  # Kotlin Int division
        lst.append(Sample(0, "Front.mp4", float(sec), overlay(sec, lon=lon, speed=v)))
    track = cleaner.clean(lst, vietmap)
    assert len(track.fixes) == len(speeds)
    assert track.speed_corrected == 0
    assert [f.speed_kmh for f in track.fixes] == speeds


def test_ocr_log_lists_every_frame_with_status():
    lst = samples(5) + [Sample(0, "Front.mp4", 5.0, "garbage")]
    log = exporters.ocr_log(cleaner.clean(lst, vietmap)).strip().splitlines()
    assert len(log) == 7
    assert ",KEPT," in log[1]
    assert ',Unreadable,"garbage"' in log[6]


def test_recording_gap_inside_merged_video_is_kept_as_new_segment():
    # 20 frames, then the overlay clock jumps 11 minutes while the video continues.
    lst = [Sample(0, "Front_merged.mp4", float(i), overlay(i if i < 20 else i + 660)) for i in range(40)]
    track = cleaner.clean(lst, vietmap)
    assert len(track.fixes) == 40
    assert len(track.segments) == 2


def test_no_fix_and_duplicates_are_dropped():
    lst = samples(10) + [Sample(0, "Front.mp4", 10.4, overlay(9)),
                         Sample(0, "Front.mp4", 11.0, overlay(11, lat=0.0, lon=0.0))]
    track = cleaner.clean(lst, vietmap)
    assert len(track.fixes) == 10
    assert track.rejected[RejectReason.Duplicate] == 1
    assert track.rejected[RejectReason.NoGpsFix] == 1


# ---- Exporters ----------------------------------------------------------------------

zone = Zone("Asia/Ho_Chi_Minh")
exporters = Exporters(zone)
KML_NS = "http://www.opengis.net/kml/2.2"
GX_NS = "http://www.google.com/kml/ext/2.2"


@pytest.fixture(scope="module")
def track():
    return cleaner.clean(samples(30), vietmap)


def xml(text):
    return minidom.parseString(text.encode("utf-8"))


def test_default_kml_is_just_route_start_and_end(track):
    # Map apps such as Geo Tracker ignore <visibility>, so the default file must not
    # contain anything but the route and the two pins.
    kml = exporters.kml(track, "Trip <1> & more")
    doc = xml(kml)
    assert doc.documentElement.localName == "kml"
    assert len(doc.getElementsByTagNameNS(KML_NS, "Placemark")) == 3
    assert len(doc.getElementsByTagNameNS(KML_NS, "Point")) == 2
    assert len(doc.getElementsByTagNameNS(KML_NS, "LineString")) == 1
    assert len(doc.getElementsByTagNameNS(KML_NS, "MultiGeometry")) == 0  # one segment
    assert "gx:Track" not in kml
    assert "106.631870,10.767360,0" in kml
    assert "Trip &lt;1&gt; &amp; more" in kml
    assert "<Placemark><name>Route</name>" in kml

    # A Google Earth tour along the route: camera moves only, no extra geometry.
    assert len(doc.getElementsByTagNameNS(GX_NS, "Tour")) == 1
    assert len(doc.getElementsByTagNameNS(GX_NS, "FlyTo")) == len(track.fixes)  # under the step limit


def test_tour_of_long_drive_is_capped_and_ends_at_the_end():
    long = cleaner.clean(samples(1000), vietmap)
    kml = exporters.kml(long, "t")
    fly_tos = xml(kml).getElementsByTagNameNS(GX_NS, "FlyTo")
    assert len(fly_tos) <= TOUR_MAX_STEPS + 1
    assert jfmt(long.fixes[-1].lon, 6) in fly_tos[-1].toxml()
    # The route reader still finds only the route line, not the tour's camera positions.
    route = routefiles.read("t.kml", kml.encode(), zone)
    assert len(route.points) == 1000


def test_google_earth_extras_add_timed_track_in_utc(track):
    kml = exporters.kml(track, "t", google_earth_extras=True)
    doc = xml(kml)
    assert "<gx:Track>" in kml
    assert "<when>2026-09-13T04:31:00Z</when>" in kml  # 11:31 at UTC+7

    # The moving marker is visible, and the tour sets the time at every camera move so
    # the marker drives along in front of the camera.
    assert "<Placemark><name>Moving marker (time slider / tour)</name><styleUrl>#car</styleUrl>" in kml
    assert "track-directional" in kml
    assert "<BalloonStyle><displayMode>hide</displayMode></BalloonStyle>" in kml
    fly_tos = len(doc.getElementsByTagNameNS(GX_NS, "FlyTo"))
    assert fly_tos == kml.count("<LookAt><gx:TimeStamp><when>")

    # Without extras the tour moves the camera only (no time, no marker).
    assert "gx:TimeStamp" not in exporters.kml(track, "t")


def test_multi_segment_route_uses_multi_geometry():
    lst = [Sample(0, "v.mp4", float(i), overlay(i if i < 10 else i + 600)) for i in range(20)]
    doc = xml(exporters.kml(cleaner.clean(lst, vietmap), "t"))
    assert len(doc.getElementsByTagNameNS(KML_NS, "Placemark")) == 3
    assert len(doc.getElementsByTagNameNS(KML_NS, "LineString")) == 2


def test_kmz_contains_doc_kml(track):
    buf = io.BytesIO()
    exporters.write_kmz(track, "t", buf)
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as z:
        assert z.namelist()[0] == "doc.kml"
        xml(z.read("doc.kml").decode("utf-8"))


def test_gpx_is_well_formed_with_speed_in_metres_per_second(track):
    gpx = exporters.gpx(track, "t")
    doc = xml(gpx)
    assert len(doc.getElementsByTagNameNS("http://www.topografix.com/GPX/1/1", "trkpt")) == 30
    assert "<gpxtpx:speed>10.00</gpxtpx:speed>" in gpx  # 36 km/h


def test_csv_has_header_and_one_row_per_fix(track):
    lines = exporters.csv(track).strip().splitlines()
    assert len(lines) == 31
    assert lines[1].startswith("2026-09-13 11:31:00,2026-09-13T04:31:00Z,10.767360,106.631870,36,")
