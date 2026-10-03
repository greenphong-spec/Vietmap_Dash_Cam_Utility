package com.phong.dashgps.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/** Cleaning exported route files: reading KML / KMZ / GPX, cleaning, writing back. */
class RouteFilesTest {
    private val zone = ZoneId.of("Asia/Ho_Chi_Minh")
    private val t0 = LocalDateTime.of(2026, 9, 19, 8, 41, 0)

    /** A drive east at 36 km/h (10 m/s), one point per second, with three misreads. */
    private class P(val lat: Double, val lon: Double, val speed: Int)

    private fun noisyDrive(): List<P> = (0 until 60).map { sec ->
        val lon = 106.63187 + sec * 0.0000915
        when (sec) {
            20 -> P(0.76736, lon, 36) // "10.7" read as "0.7": 1100 km away
            30 -> P(10.76736 + 0.00008, lon, 36) // 9 m sideways kink
            40 -> P(10.76736, lon, 300) // speed digit misread
            else -> P(10.76736, lon, 36)
        }
    }

    private fun gpx(points: List<P>): String = buildString {
        append("""<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1" """)
        append("""xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v2"><trk><trkseg>""")
        points.forEachIndexed { i, p ->
            val t = t0.plusSeconds(i.toLong()).atZone(zone).toInstant()
            append(String.format(Locale.US, """<trkpt lat="%.6f" lon="%.6f"><time>%s</time>""", p.lat, p.lon, t))
            append(String.format(Locale.US, "<extensions><gpxtpx:TrackPointExtension><gpxtpx:speed>%.2f</gpxtpx:speed></gpxtpx:TrackPointExtension></extensions></trkpt>", p.speed / 3.6))
        }
        append("</trkseg></trk></gpx>")
    }

    private fun kml(points: List<P>): String =
        """<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><LineString><coordinates>""" +
            points.joinToString(" ") { String.format(Locale.US, "%.6f,%.6f,0", it.lon, it.lat) } +
            "</coordinates></LineString></Placemark></Document></kml>"

    private fun kmz(kml: String): ByteArray = ByteArrayOutputStream().also { bo ->
        ZipOutputStream(bo).use { z ->
            z.putNextEntry(ZipEntry("doc.kml"))
            z.write(kml.toByteArray())
            z.closeEntry()
        }
    }.toByteArray()

    private fun read(name: String, bytes: ByteArray) = RouteReader.read(name, ByteArrayInputStream(bytes), zone)
    private fun resource(name: String) = javaClass.getResourceAsStream("/$name")!!.readBytes()

    @Test
    fun gpxWithTimesAndSpeedsGetsEveryCheck() {
        val route = read("drive.gpx", gpx(noisyDrive()).toByteArray())
        assertTrue(route.hasTime && route.hasSpeed)
        assertEquals(t0, route.points.first().time) // UTC in the file, camera-local here

        val clean = TrackCleaner.cleanRoute(route)
        assertEquals(58, clean.track.fixes.size)
        assertEquals(RejectReason.PositionJump, clean.track.sampleStatus[20])
        assertEquals(RejectReason.Spike, clean.track.sampleStatus[30])
        assertEquals(1, clean.track.speedCorrected)
        assertTrue(clean.before.maxSpeedKmh == 300 && clean.track.stats.maxSpeedKmh <= 38)
        assertNull(clean.assumedStepS)

        // Written back as GPX and read again: same points, still timed, speeds kept.
        val out = ByteArrayOutputStream()
        Exporters(zone).write(ExportFormat.GPX, clean.track, "drive_clean", out, timed = true)
        val again = read("drive_clean.gpx", out.toByteArray())
        assertEquals(58, again.points.size)
        assertTrue(again.hasTime && again.hasSpeed)
        assertEquals(0, TrackCleaner.cleanRoute(again).let { it.route.points.size - it.track.fixes.size })
    }

    @Test
    fun plainKmlAndKmzWithoutTimesGetPositionChecks() {
        val text = kml(noisyDrive())
        for (route in listOf(read("drive.kml", text.toByteArray()), read("drive.kmz", kmz(text)))) {
            assertFalse(route.hasTime || route.hasSpeed)
            val clean = TrackCleaner.cleanRoute(route)
            assertEquals(1L, clean.assumedStepS) // ~10 m per point -> 1 s
            assertEquals(58, clean.track.fixes.size) // the speed misread is not in a KML
            assertEquals(RejectReason.PositionJump, clean.track.sampleStatus[20])
            assertEquals(RejectReason.Spike, clean.track.sampleStatus[30])

            // Written without invented times or speeds.
            val written = Exporters(zone).kml(clean.track, "drive_clean", timed = false)
            assertFalse(written.contains("km/h"))
            assertFalse(written.contains("2000-01-01"))
            assertEquals(58, read("drive_clean.kml", written.toByteArray()).points.size)
        }
    }

    @Test
    fun sparseKmlFromAnotherAppIsNotEmptied() {
        // A planned route with a point every ~500 m and no times must keep every point.
        val points = (0 until 40).map { P(10.76736 + (it % 2) * 0.001, 106.63187 + it * 0.0045, 0) }
        val clean = TrackCleaner.cleanRoute(read("planned.kml", kml(points).toByteArray()))
        assertEquals(40, clean.track.fixes.size)
    }

    @Test
    fun googleEarthTimedTrackIsUsed() {
        val track = TrackCleaner.cleanRoute(read("drive.gpx", gpx(noisyDrive().filterIndexed { i, _ -> i != 20 && i != 30 }).toByteArray())).track
        val withExtras = Exporters(zone).kml(track, "drive", googleEarthExtras = true)
        val route = read("drive.kml", withExtras.toByteArray())
        assertTrue(route.hasTime)
        assertEquals(track.fixes.size, route.points.size)
        assertEquals(track.fixes.first().time, route.points.first().time)
    }

    @Test
    fun realExportedGpxLosesExactlyItsTwoMisreads() {
        // The Front video export of 2026-09-19 08:41-09:19, saved before steps 8-9 existed:
        // 01:58:03 lon "106.33858" read as "106.33850" (9 m kink) and 02:04:17 lat
        // "10.37568" read as "10.37588" (22 m spike).
        val clean = TrackCleaner.cleanRoute(read("front.gpx", resource("vietmap_front_route_noisy.gpx")))
        val fmt = DateTimeFormatter.ofPattern("HH:mm:ss")
        val dropped = clean.track.sampleStatus.indices.filter { clean.track.sampleStatus[it] != null }
            .map { clean.route.points[it].time!!.format(fmt) }
        println("real gpx: ${clean.route.points.size} -> ${clean.track.fixes.size}, dropped $dropped, speedCorrected=${clean.track.speedCorrected}")
        assertEquals(listOf("08:58:03", "09:04:17"), dropped)
    }

    @Test
    fun realExportedKmzWithoutTimesLosesItsMisreads() {
        val clean = TrackCleaner.cleanRoute(read("front.kmz", resource("vietmap_front_route_noisy.kmz")))
        val dropped = clean.track.sampleStatus.indices.filter { clean.track.sampleStatus[it] != null }
        println("real kmz: ${clean.route.points.size} -> ${clean.track.fixes.size}, step=${clean.assumedStepS}s, dropped indices $dropped")
        assertTrue("dropped $dropped", dropped.containsAll(listOf(1001, 1329)))
        assertTrue("dropped $dropped", dropped.size <= 4)
    }
}
