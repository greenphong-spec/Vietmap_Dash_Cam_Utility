package com.phong.dashgps.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.time.LocalDateTime
import java.time.ZoneId
import java.util.zip.ZipInputStream
import javax.xml.parsers.DocumentBuilderFactory

class CoreTest {
    private val vietmap = OverlayProfiles.VIETMAP_M1_FRONT
    private val parse = vietmap.parse

    // ---- Parser -------------------------------------------------------------------------

    @Test
    fun parsesCleanVietmapOverlay() {
        val r = parse("Vietmap SpeedMap M1 13/09/2026 11:31:13 39KM/H N10.76736 E106.63187")!!
        assertEquals(LocalDateTime.of(2026, 9, 13, 11, 31, 13), r.time)
        assertEquals(39, r.speedKmh)
        assertEquals(10.76736, r.lat, 1e-9)
        assertEquals(106.63187, r.lon, 1e-9)
    }

    @Test
    fun toleratesTypicalOcrNoise() {
        // Split lines, lower case, O for 0, l for 1, missing and extra spaces.
        val r = parse("Vietmap SpeedMap M1\n13/O9/2026  1l:31:13 39 km/h\nN10.76736E106.63187")!!
        assertEquals(LocalDateTime.of(2026, 9, 13, 11, 31, 13), r.time)
        assertEquals(10.76736, r.lat, 1e-9)
        assertEquals(106.63187, r.lon, 1e-9)
        assertEquals(39, r.speedKmh)
    }

    @Test
    fun vietmapRejectsCoordinatesWithDroppedOrExtraDigits() {
        // Seen on a real video: "106.63376" read as "106.633", and "106.63289" as "106.632891".
        assertNull(parse("24/09/2026 07:27:52 0KM/H N10.77630 E106.633"))
        assertNull(parse("24/09/2026 07:29:54 27KM/H N10.77254 E106.632891"))
        assertNotNull(parse("24/09/2026 07:27:52 0KM/H N10.77630 E106.63376"))
    }

    @Test
    fun recoversDamagedSeparatorsSeenOnRealVideo() {
        // Each of these comes from the OCR log of a real Vietmap M1 front video.
        fun p(s: String) = parse("VIETMAP Vietmap SpeedMap M1 $s")
        assertEquals(LocalDateTime.of(2026, 9, 24, 7, 8, 53), p("24/09f2026 07:08:53 10KM/H N10.77265 E 106.63410")!!.time)
        assertEquals(LocalDateTime.of(2026, 9, 24, 7, 9, 15), p("24/0912026 07:09:15 11KM/H N10.77224 E 106.63387")!!.time)
        assertEquals(LocalDateTime.of(2026, 9, 24, 7, 9, 23), p("24/092026 07:09:23 7KM/H N10.77582 E 106.63354")!!.time)
        assertEquals(10.77752, p("24/09/2026 07:12:02 39KM/H N10)77752 E 106.63467")!!.lat, 1e-9)
        assertEquals(10.78921, p("24/09/2026 07:16:46 8KM/H N10:78921 E 106.63698")!!.lat, 1e-9)
        assertEquals(10.78921, p("24/09/2026 07:16:47 9KM/H N1078921 E 106.63696")!!.lat, 1e-9)
        assertEquals(10.78477, p("24/09/2026 07:21:29 1KM/H N10.,78477 E 106.63633")!!.lat, 1e-9)
        assertEquals(106.63719, p("24/09/2026 07:15:35 38KM/H N10.78936 E 106.63 719")!!.lon, 1e-9)
        assertEquals(106.63696, p("24/09/2026 07:17:53 OKM/H N10.78908 E 10663696")!!.lon, 1e-9)
        // "/" read as "7" (ML Kit and PP-OCR both do it); was year 7202 / day 97.
        assertEquals(LocalDateTime.of(2026, 9, 19, 18, 55, 45), p("19/0972026 18:55:45 4KM/H N10.35906 E106.35456")!!.time)
        assertEquals(LocalDateTime.of(2026, 9, 19, 19, 0, 50), p("M119709/2026 19:00:50 1KM/H N10.36376 E106.35228")!!.time)
        // Truncated coordinate stays unusable.
        assertNull(p("24/09/2026 07:26:24 34KM/H N10.78033 E 106.63"))
    }

    @Test
    fun collapsedMinuteGivesPartialReadingWithSeconds() {
        // ML Kit merges the thin "11" of 07:11:00 into "1".
        val r = parse("VIETMAP Vietmap SpeedMap M1 24/09/2026 07:1:04 OKM/H N10.77551 E 106.63328")!!
        assertNull(r.time)
        assertEquals(4, r.secondHint)
        assertEquals(10.77551, r.lat, 1e-9)
        assertEquals(0, r.speedKmh)
    }

    @Test
    fun southWestHemispheresAreNegative() {
        val r = parse("01/02/2026 03:04:05 0KM/H S33.86785 W151.20732")!!
        assertEquals(-33.86785, r.lat, 1e-9)
        assertEquals(-151.20732, r.lon, 1e-9)
        assertEquals(0, r.speedKmh)
    }

    @Test
    fun rejectsIncompleteOrImpossibleText() {
        assertNull(parse("Vietmap SpeedMap M1 13/09/2026 11:31:13 39KM/H")) // no GPS
        // Month 13: position is still usable, the time is left for TrackCleaner to rebuild.
        assertNull(parse("13/13/2026 11:31:13 39KM/H N10.76736 E106.63187")!!.time)
        assertNull(parse(""))
    }

    // ---- Cleaner ------------------------------------------------------------------------

    private val t0 = LocalDateTime.of(2026, 9, 13, 11, 31, 0)

    /** A drive heading east at ~36 km/h (10 m/s): 0.00009 deg lon per second at 10.77 N. */
    private fun overlay(sec: Int, lat: Double = 10.76736, lon: Double = 106.63187 + sec * 0.0000915, speed: Int = 36): String {
        val t = t0.plusSeconds(sec.toLong())
        return String.format(
            java.util.Locale.US, "Vietmap SpeedMap M1 %02d/%02d/%04d %02d:%02d:%02d %dKM/H N%.5f E%.5f",
            t.dayOfMonth, t.monthValue, t.year, t.hour, t.minute, t.second, speed, lat, lon,
        )
    }

    private fun samples(n: Int, text: (Int) -> String = { overlay(it) }) =
        (0 until n).map { Sample(0, "Front.mp4", it.toDouble(), text(it)) }

    @Test
    fun cleanTrackKeepsEveryGoodFrame() {
        val track = TrackCleaner.clean(samples(60), vietmap)
        assertEquals(60, track.fixes.size)
        assertEquals(1, track.segments.size)
        assertEquals(59 * 10.0, track.stats.distanceM, 15.0) // ~10 m/s for 59 s
        assertTrue(track.rejected.isEmpty())
    }

    @Test
    fun misreadTimeAndPositionAreDropped() {
        val list = samples(30) { sec ->
            when (sec) {
                10 -> overlay(sec).replace("11:31:10", "11:31:40") // misread seconds digit
                20 -> overlay(sec, lat = 16.76736) // misread latitude digit
                25 -> "Vietmap SpeedMap M1 ####" // unreadable
                else -> overlay(sec)
            }
        }
        val track = TrackCleaner.clean(list, vietmap)
        assertEquals(27, track.fixes.size)
        assertEquals(1, track.rejected[RejectReason.TimeMismatch])
        assertEquals(1, track.rejected[RejectReason.PositionJump])
        assertEquals(1, track.rejected[RejectReason.Unreadable])
    }

    @Test
    fun sidewaysSpikeWithinReachIsDroppedButCornersAreKept() {
        // Frame 15: latitude misread by 0.0008 deg (~89 m north) - reachable in 1 s at
        // 250 km/h, so only the spike check can catch it.
        val list = samples(30) { sec -> if (sec == 15) overlay(sec, lat = 10.76736 + 0.0008) else overlay(sec) }
        val track = TrackCleaner.clean(list, vietmap)
        assertEquals(29, track.fixes.size)
        assertEquals(1, track.rejected[RejectReason.Spike])
        assertEquals(RejectReason.Spike, track.sampleStatus[15])
        assertNull(track.sampleStatus[14])

        // A real 90-degree turn (east, then north) at 10 m/s is not a spike.
        val turn = (0 until 20).map { sec ->
            val text = if (sec <= 10) overlay(sec) else overlay(sec, lat = 10.76736 + (sec - 10) * 0.00009, lon = 106.63187 + 10 * 0.0000915)
            Sample(0, "Front.mp4", sec.toDouble(), text)
        }
        assertEquals(20, TrackCleaner.clean(turn, vietmap).fixes.size)
    }

    @Test
    fun sameMisreadOnConsecutiveFramesIsDropped() {
        // Seen on a real merged Front video: "10.3" read as "0.3" on two frames in a row
        // (1112 km away and back), and "106.35" as "106.85" on two frames (55 km).
        val list = samples(60) { sec ->
            when (sec) {
                20, 21 -> overlay(sec, lat = 0.76736)
                40, 41 -> overlay(sec, lon = 106.63187 + sec * 0.0000915 + 0.5)
                else -> overlay(sec)
            }
        }
        val track = TrackCleaner.clean(list, vietmap)
        assertEquals(56, track.fixes.size)
        assertEquals(4, track.rejected[RejectReason.PositionJump])
        listOf(20, 21, 40, 41).forEach { assertEquals(RejectReason.PositionJump, track.sampleStatus[it]) }
        assertEquals(59 * 10.0, track.stats.distanceM, 15.0)
    }

    @Test
    fun repeatedMisreadWhileStoppedAndAtTrackStartIsDropped() {
        // Parked: the overlay text barely changes, so the same misread repeats for 8 frames.
        val parked = samples(60) { sec ->
            if (sec in 30..37) overlay(sec, lat = 16.76736, lon = 106.632, speed = 0) else overlay(sec, lon = 106.632, speed = 0)
        }
        assertEquals(52, TrackCleaner.clean(parked, vietmap).fixes.size)

        // The first 3 frames misread, nothing before them to compare against.
        val start = samples(30) { sec -> if (sec < 3) overlay(sec, lat = 0.76736) else overlay(sec) }
        val track = TrackCleaner.clean(start, vietmap)
        assertEquals(27, track.fixes.size)
        assertEquals(3, track.fixes.first().sampleIndex)
    }

    @Test
    fun misreadSpeedIsReplacedByGpsSpeedAndFixIsKept() {
        // Driving at 36 km/h: frame 20 reads "300", frames 30-31 read "86" ("3" -> "8").
        val list = samples(60) { sec ->
            when (sec) {
                20 -> overlay(sec, speed = 300)
                30, 31 -> overlay(sec, speed = 86)
                else -> overlay(sec)
            }
        }
        val track = TrackCleaner.clean(list, vietmap)
        assertEquals(60, track.fixes.size)
        assertEquals(3, track.speedCorrected)
        listOf(20, 30, 31).forEach { sec -> assertEquals(36.0, track.fixes[sec].speedKmh.toDouble(), 2.0) }
        assertTrue("max ${track.stats.maxSpeedKmh}", track.stats.maxSpeedKmh <= 38)
    }

    @Test
    fun smallSpikeIsDroppedUsingPrintedSpeed() {
        // Real case: lat "10.37568" read as "10.37588" - 22 m off the road at 48 km/h,
        // under the 40 m limit of the plain spike check.
        val list = samples(30) { sec -> if (sec == 15) overlay(sec, lat = 10.76736 + 0.0002) else overlay(sec) }
        val track = TrackCleaner.clean(list, vietmap)
        assertEquals(29, track.fixes.size)
        assertEquals(RejectReason.Spike, track.sampleStatus[15])

        // Parked with ~4 m of GPS wander: jitter, not a misread.
        val parked = samples(30) { sec ->
            overlay(sec, lat = 10.76736 + (if (sec % 3 == 0) 0.00004 else 0.0), lon = 106.632, speed = 0)
        }
        assertEquals(30, TrackCleaner.clean(parked, vietmap).fixes.size)
    }

    @Test
    fun sidewaysKinkIsDropped() {
        // Real case: lon "106.33858" read as "106.33850" - 9 m sideways at 48 km/h. The
        // distance driven barely changes, so only the curve fit catches it.
        val list = samples(30) { sec -> if (sec == 15) overlay(sec, lat = 10.76736 + 0.00008) else overlay(sec) }
        val track = TrackCleaner.clean(list, vietmap)
        assertEquals(29, track.fixes.size)
        assertEquals(RejectReason.Spike, track.sampleStatus[15])
    }

    @Test
    fun realHardBrakingKeepsItsSpeeds() {
        // 90 km/h for 20 s, brake to a stop in 5 s (18 km/h per second), stand for 20 s.
        // Positions follow the speeds, so the GPS speed agrees with every printed value.
        var lon = 106.63187
        val speeds = List(20) { 90 } + listOf(72, 54, 36, 18, 0) + List(20) { 0 }
        val list = speeds.mapIndexed { sec, v ->
            if (sec > 0) lon += (speeds[sec - 1] + v) / 2 / 3.6 * 0.00000915
            Sample(0, "Front.mp4", sec.toDouble(), overlay(sec, lon = lon, speed = v))
        }
        val track = TrackCleaner.clean(list, vietmap)
        assertEquals(speeds.size, track.fixes.size)
        assertEquals(0, track.speedCorrected)
        assertEquals(speeds, track.fixes.map { it.speedKmh })
    }

    @Test
    fun ocrLogListsEveryFrameWithStatus() {
        val list = samples(5) + Sample(0, "Front.mp4", 5.0, "garbage")
        val log = exporters.ocrLog(TrackCleaner.clean(list, vietmap)).trim().lines()
        assertEquals(7, log.size)
        assertTrue(log[1].contains(",KEPT,"))
        assertTrue(log[6].contains(",Unreadable,\"garbage\""))
    }

    @Test
    fun recordingGapInsideMergedVideoIsKeptAsNewSegment() {
        // 20 frames, then the overlay clock jumps 11 minutes while the video continues.
        val list = (0 until 40).map { i ->
            val sec = if (i < 20) i else i + 660
            Sample(0, "Front_merged.mp4", i.toDouble(), overlay(sec))
        }
        val track = TrackCleaner.clean(list, vietmap)
        assertEquals(40, track.fixes.size)
        assertEquals(2, track.segments.size)
    }

    @Test
    fun noFixAndDuplicatesAreDropped() {
        val list = samples(10) + Sample(0, "Front.mp4", 10.4, overlay(9)) +
            Sample(0, "Front.mp4", 11.0, overlay(11, lat = 0.0, lon = 0.0))
        val track = TrackCleaner.clean(list, vietmap)
        assertEquals(10, track.fixes.size)
        assertEquals(1, track.rejected[RejectReason.Duplicate])
        assertEquals(1, track.rejected[RejectReason.NoGpsFix])
    }

    // ---- Exporters ----------------------------------------------------------------------

    private val exporters = Exporters(ZoneId.of("Asia/Ho_Chi_Minh"))
    private val track by lazy { TrackCleaner.clean(samples(30), vietmap) }

    private fun parseXml(text: String) =
        DocumentBuilderFactory.newInstance().apply { isNamespaceAware = true }
            .newDocumentBuilder().parse(ByteArrayInputStream(text.toByteArray()))

    private val KML_NS = "http://www.opengis.net/kml/2.2"

    @Test
    fun defaultKmlIsJustRouteStartAndEnd() {
        // Map apps such as Geo Tracker ignore <visibility>, so the default file must not
        // contain anything but the route and the two pins.
        val kml = exporters.kml(track, "Trip <1> & more")
        val doc = parseXml(kml)
        assertEquals("kml", doc.documentElement.localName)
        val placemarks = doc.getElementsByTagNameNS(KML_NS, "Placemark")
        assertEquals(3, placemarks.length)
        assertEquals(2, doc.getElementsByTagNameNS(KML_NS, "Point").length)
        assertEquals(1, doc.getElementsByTagNameNS(KML_NS, "LineString").length)
        assertEquals(0, doc.getElementsByTagNameNS(KML_NS, "MultiGeometry").length) // one segment
        assertTrue(!kml.contains("gx:Track"))
        assertTrue(kml.contains("106.631870,10.767360,0"))
        assertTrue(kml.contains("Trip &lt;1&gt; &amp; more"))
        assertTrue(kml.contains("<Placemark><name>Route</name>"))

        // A Google Earth tour along the route: camera moves only, no extra geometry.
        val gx = "http://www.google.com/kml/ext/2.2"
        assertEquals(1, doc.getElementsByTagNameNS(gx, "Tour").length)
        val flyTos = doc.getElementsByTagNameNS(gx, "FlyTo").length
        assertEquals(track.fixes.size, flyTos) // 30 fixes, under the step limit
    }

    @Test
    fun tourOfLongDriveIsCappedAndEndsAtTheEnd() {
        val long = TrackCleaner.clean(samples(1000), vietmap)
        val kml = exporters.kml(long, "t")
        val doc = parseXml(kml)
        val flyTos = doc.getElementsByTagNameNS("http://www.google.com/kml/ext/2.2", "FlyTo")
        assertTrue("${flyTos.length}", flyTos.length <= Exporters.TOUR_MAX_STEPS + 1)
        val last = flyTos.item(flyTos.length - 1).textContent
        assertTrue(last.contains(String.format(java.util.Locale.US, "%.6f", long.fixes.last().lon)))
        // The route reader still finds only the route line, not the tour's camera positions.
        val route = RouteReader.read("t.kml", ByteArrayInputStream(kml.toByteArray()), ZoneId.of("Asia/Ho_Chi_Minh"))
        assertEquals(1000, route.points.size)
    }

    @Test
    fun googleEarthExtrasAddTimedTrackInUtc() {
        val kml = exporters.kml(track, "t", googleEarthExtras = true)
        parseXml(kml)
        assertTrue(kml.contains("<gx:Track>"))
        assertTrue(kml.contains("<when>2026-09-13T04:31:00Z</when>")) // 11:31 at UTC+7

        // The moving marker is visible, and the tour sets the time at every camera move so
        // the marker drives along in front of the camera.
        assertTrue(kml.contains("<Placemark><name>Moving marker (time slider / tour)</name><styleUrl>#car</styleUrl>"))
        assertTrue(kml.contains("track-directional"))
        assertTrue(kml.contains("<BalloonStyle><displayMode>hide</displayMode></BalloonStyle>"))
        val gx = "http://www.google.com/kml/ext/2.2"
        val doc = parseXml(kml)
        val flyTos = doc.getElementsByTagNameNS(gx, "FlyTo").length
        assertEquals(flyTos, kml.split("<LookAt><gx:TimeStamp><when>").size - 1)

        // Without extras the tour moves the camera only (no time, no marker).
        assertTrue(!exporters.kml(track, "t").contains("gx:TimeStamp"))
    }

    @Test
    fun multiSegmentRouteUsesMultiGeometry() {
        val list = (0 until 20).map { i -> Sample(0, "v.mp4", i.toDouble(), overlay(if (i < 10) i else i + 600)) }
        val doc = parseXml(exporters.kml(TrackCleaner.clean(list, vietmap), "t"))
        assertEquals(3, doc.getElementsByTagNameNS(KML_NS, "Placemark").length)
        assertEquals(2, doc.getElementsByTagNameNS(KML_NS, "LineString").length)
    }

    @Test
    fun kmzContainsDocKml() {
        val bytes = ByteArrayOutputStream().also { exporters.writeKmz(track, "t", it) }.toByteArray()
        ZipInputStream(ByteArrayInputStream(bytes)).use { zip ->
            assertEquals("doc.kml", zip.nextEntry!!.name)
            assertNotNull(parseXml(zip.readBytes().decodeToString()))
        }
    }

    @Test
    fun gpxIsWellFormedWithSpeedInMetresPerSecond() {
        val gpx = exporters.gpx(track, "t")
        val doc = parseXml(gpx)
        assertEquals(30, doc.getElementsByTagNameNS("http://www.topografix.com/GPX/1/1", "trkpt").length)
        assertTrue(gpx.contains("<gpxtpx:speed>10.00</gpxtpx:speed>")) // 36 km/h
    }

    @Test
    fun csvHasHeaderAndOneRowPerFix() {
        val lines = exporters.csv(track).trim().lines()
        assertEquals(31, lines.size)
        assertTrue(lines[1].startsWith("2026-09-13 11:31:00,2026-09-13T04:31:00Z,10.767360,106.631870,36,"))
    }
}
