package com.phong.dashgps.core

import java.io.OutputStream
import java.time.Duration
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

enum class ExportFormat(val ext: String, val mime: String, val label: String, val suffix: String = "") {
    KMZ("kmz", "application/vnd.google-earth.kmz", "KMZ (Google Earth)"),
    KML("kml", "application/vnd.google-earth.kml+xml", "KML"),
    GPX("gpx", "application/gpx+xml", "GPX"),
    CSV("csv", "text/csv", "CSV (spreadsheet)"),
    OCR_LOG("csv", "text/csv", "OCR log (every frame, for checking)", "_ocr_log"),
}

/**
 * File writers. Overlay times are camera-local; [zone] converts them to UTC for GPX/KML,
 * which require UTC timestamps.
 */
class Exporters(private val zone: ZoneId, private val appName: String = "DashCam GPS Extractor") {
    companion object {
        const val TOUR_MAX_STEPS = 400
        /** The tour plays the drive this many times faster (38 min -> ~2 min). */
        const val TOUR_SPEEDUP = 20.0
        /** A recording gap longer than this (s) is flown over quickly, not waited out. */
        const val TOUR_MAX_GAP_S = 60.0
        const val TOUR_TILT = 65
        const val TOUR_RANGE_M = 350
    }

    private val utc = DateTimeFormatter.ISO_INSTANT
    private val local = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")

    private fun iso(t: LocalDateTime) = utc.format(t.atZone(zone).toInstant())
    private fun coord(v: Double) = String.format(Locale.US, "%.6f", v)
    private fun esc(s: String) = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;")

    /** [timed] = false for a route that came without timestamps: no times or speeds then. */
    fun summary(track: Track, title: String, timed: Boolean = true): String {
        val s = track.stats
        return buildString {
            appendLine(title)
            if (!timed) {
                appendLine(String.format(Locale.US, "Distance: %.2f km   Points: %d   Segments: %d", s.distanceM / 1000, s.points, s.segments))
                return@buildString
            }
            appendLine("Start: ${s.start?.format(local) ?: "-"}   End: ${s.end?.format(local) ?: "-"}   (camera time, $zone)")
            appendLine(String.format(Locale.US, "Distance: %.2f km   Points: %d   Segments: %d", s.distanceM / 1000, s.points, s.segments))
            appendLine(String.format(Locale.US, "Max speed: %d km/h   Average: %.1f km/h", s.maxSpeedKmh, s.avgSpeedKmh))
        }
    }

    /**
     * By default the KML holds exactly three features - the route line, a Start pin and an
     * End pin - so every map app (Geo Tracker, OsmAnd, My Maps, Google Earth) shows the same
     * clean result. [googleEarthExtras] adds a timed track for Google Earth's time slider and
     * speed labels every 10 s; apps that ignore KML visibility show those as extra points.
     */
    fun kml(track: Track, title: String, googleEarthExtras: Boolean = false, timed: Boolean = true): String = buildString {
        appendLine("""<?xml version="1.0" encoding="UTF-8"?>""")
        appendLine("""<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">""")
        appendLine("<Document>")
        appendLine("<name>${esc(title)}</name>")
        appendLine("<description>${esc(summary(track, title, timed))}Created by $appName.</description>")
        appendLine("""<Style id="route"><LineStyle><color>ff0033ff</color><width>5</width></LineStyle></Style>""")
        appendLine("""<Style id="start"><IconStyle><Icon><href>http://maps.google.com/mapfiles/kml/paddle/grn-circle.png</href></Icon></IconStyle></Style>""")
        appendLine("""<Style id="end"><IconStyle><Icon><href>http://maps.google.com/mapfiles/kml/paddle/red-square.png</href></Icon></IconStyle></Style>""")

        // Route: plain LineString(s), understood by every KML viewer. Named "Route" so it is
        // not confused with the document (same title) when selecting it in Google Earth.
        appendLine("<Placemark><name>Route</name><styleUrl>#route</styleUrl>")
        if (track.segments.size > 1) append("<MultiGeometry>")
        for (seg in track.segments) {
            append("<LineString><tessellate>1</tessellate><coordinates>")
            seg.forEach { append("${coord(it.lon)},${coord(it.lat)},0 ") }
            appendLine("</coordinates></LineString>")
        }
        if (track.segments.size > 1) append("</MultiGeometry>")
        appendLine("</Placemark>")

        track.fixes.firstOrNull()?.let { appendLine(pin("Start", "#start", it, timed)) }
        track.fixes.lastOrNull()?.let { appendLine(pin("End", "#end", it, timed)) }

        appendTour(track, timed, clock = googleEarthExtras && timed)
        if (googleEarthExtras && timed) appendGoogleEarthExtras(track)
        appendLine("</Document>")
        appendLine("</kml>")
    }

    /**
     * A ready-made Google Earth tour that flies along the route behind the car (double-click
     * "Fly along the route" in Places). The drive plays TOUR_SPEEDUP times faster, with at
     * most TOUR_MAX_STEPS camera moves. It holds camera positions only - no Placemark or
     * geometry - so map apps that ignore tours show nothing extra. With [clock] every camera
     * move also sets Google Earth's time, so the timed track's marker (Google Earth extras)
     * drives along the route in front of the camera.
     */
    private fun StringBuilder.appendTour(track: Track, timed: Boolean, clock: Boolean = false) {
        val pts = track.fixes
        if (pts.size < 2) return
        val stride = maxOf(1, (pts.size + TOUR_MAX_STEPS - 1) / TOUR_MAX_STEPS)
        val stops = pts.indices.step(stride).map { pts[it] }.let { if (it.last() !== pts.last()) it + pts.last() else it }
        appendLine("<gx:Tour><name>Fly along the route</name><gx:Playlist>")
        var heading = 0.0
        stops.forEachIndexed { i, f ->
            val ahead = stops.getOrNull(i + 1)
            if (ahead != null && Geo.distanceM(f, ahead) > 1) {
                // Keep the heading continuous (no 359 -> 0 spin).
                val b = bearing(f, ahead)
                heading += ((b - heading) % 360 + 540) % 360 - 180
            }
            val seconds = if (i == 0) 3.0 else {
                val prev = stops[i - 1]
                val dt = if (timed) Duration.between(prev.time, f.time).seconds.toDouble() else stride.toDouble()
                (dt.coerceAtMost(TOUR_MAX_GAP_S) / TOUR_SPEEDUP).coerceAtLeast(0.2)
            }
            appendLine(
                "<gx:FlyTo><gx:duration>${String.format(Locale.US, "%.2f", seconds)}</gx:duration>" +
                    "<gx:flyToMode>${if (i == 0) "bounce" else "smooth"}</gx:flyToMode>" +
                    "<LookAt>" + (if (clock) "<gx:TimeStamp><when>${iso(f.time)}</when></gx:TimeStamp>" else "") + "<longitude>${coord(f.lon)}</longitude><latitude>${coord(f.lat)}</latitude><altitude>0</altitude>" +
                    "<heading>${String.format(Locale.US, "%.1f", heading)}</heading><tilt>$TOUR_TILT</tilt>" +
                    "<range>$TOUR_RANGE_M</range><altitudeMode>relativeToGround</altitudeMode></LookAt></gx:FlyTo>"
            )
        }
        appendLine("</gx:Playlist></gx:Tour>")
    }

    private fun bearing(a: Fix, b: Fix): Double {
        val p1 = Math.toRadians(a.lat)
        val p2 = Math.toRadians(b.lat)
        val dl = Math.toRadians(b.lon - a.lon)
        val y = Math.sin(dl) * Math.cos(p2)
        val x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl)
        return Math.toDegrees(Math.atan2(y, x))
    }

    private fun StringBuilder.appendGoogleEarthExtras(track: Track) {
        appendLine("""<Style id="pt"><IconStyle><scale>0.6</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/shaded_dot.png</href></Icon></IconStyle><LabelStyle><scale>0.7</scale></LabelStyle></Style>""")
        // Timed track: Google Earth moves its arrow along the route with the time slider and
        // during the tour. Visible - this whole section is opt-in for Google Earth.
        appendLine(
            """<Style id="car"><IconStyle><scale>1.3</scale><Icon><href>http://earth.google.com/images/kml-icons/track-directional/track-0.png</href></Icon></IconStyle>""" +
                // No name label or (empty) balloon over the moving arrow during the tour.
                """<LabelStyle><scale>0</scale></LabelStyle><LineStyle><color>ff00ffff</color><width>3</width></LineStyle>""" +
                """<BalloonStyle><displayMode>hide</displayMode></BalloonStyle></Style>"""
        )
        appendLine("<Placemark><name>Moving marker (time slider / tour)</name><styleUrl>#car</styleUrl><gx:MultiTrack>")
        for (seg in track.segments) {
            appendLine("<gx:Track>")
            seg.forEach { appendLine("<when>${iso(it.time)}</when>") }
            seg.forEach { appendLine("<gx:coord>${coord(it.lon)} ${coord(it.lat)} 0</gx:coord>") }
            appendLine("</gx:Track>")
        }
        appendLine("</gx:MultiTrack></Placemark>")

        // Speed/time labels every 10 s, hidden by default in Google Earth.
        appendLine("<Folder><name>Speed every 10 s</name><visibility>0</visibility>")
        var last: LocalDateTime? = null
        for (f in track.fixes) {
            if (last != null && Duration.between(last, f.time).seconds < 10) continue
            last = f.time
            appendLine(
                "<Placemark><name>${f.speedKmh} km/h</name><visibility>0</visibility><styleUrl>#pt</styleUrl>" +
                    "<TimeStamp><when>${iso(f.time)}</when></TimeStamp>" +
                    "<description>${esc(f.time.format(local))} · ${f.speedKmh} km/h · ${esc(f.videoName)} @ ${f.posSec.toInt()} s</description>" +
                    "<Point><coordinates>${coord(f.lon)},${coord(f.lat)},0</coordinates></Point></Placemark>"
            )
        }
        appendLine("</Folder>")
    }

    private fun pin(name: String, style: String, f: Fix, timed: Boolean = true) =
        "<Placemark><name>$name</name><styleUrl>$style</styleUrl>" +
            (if (timed) "<description>${esc(f.time.format(local))} · ${f.speedKmh} km/h</description>" else "") +
            "<Point><coordinates>${coord(f.lon)},${coord(f.lat)},0</coordinates></Point></Placemark>"

    /** KMZ = zip containing doc.kml (what Google Earth opens by default). */
    fun writeKmz(track: Track, title: String, out: OutputStream, googleEarthExtras: Boolean = false, timed: Boolean = true) {
        ZipOutputStream(out).use { zip ->
            zip.putNextEntry(ZipEntry("doc.kml"))
            zip.write(kml(track, title, googleEarthExtras, timed).toByteArray(Charsets.UTF_8))
            zip.closeEntry()
        }
    }

    /** GPX 1.1; speed (m/s) in the Garmin TrackPointExtension v2, understood by most apps. */
    fun gpx(track: Track, title: String, timed: Boolean = true): String = buildString {
        appendLine("""<?xml version="1.0" encoding="UTF-8"?>""")
        appendLine(
            """<gpx version="1.1" creator="${esc(appName)}" xmlns="http://www.topografix.com/GPX/1/1" """ +
                """xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v2">"""
        )
        append("<metadata><name>${esc(title)}</name>")
        if (timed) track.fixes.firstOrNull()?.let { append("<time>${iso(it.time)}</time>") }
        appendLine("</metadata>")
        appendLine("<trk><name>${esc(title)}</name>")
        for (seg in track.segments) {
            appendLine("<trkseg>")
            for (f in seg) {
                if (!timed) {
                    appendLine("""<trkpt lat="${coord(f.lat)}" lon="${coord(f.lon)}"></trkpt>""")
                    continue
                }
                appendLine(
                    """<trkpt lat="${coord(f.lat)}" lon="${coord(f.lon)}"><time>${iso(f.time)}</time>""" +
                        "<extensions><gpxtpx:TrackPointExtension><gpxtpx:speed>" +
                        String.format(Locale.US, "%.2f", f.speedKmh / 3.6) +
                        "</gpxtpx:speed></gpxtpx:TrackPointExtension></extensions></trkpt>"
                )
            }
            appendLine("</trkseg>")
        }
        appendLine("</trk>")
        appendLine("</gpx>")
    }

    fun csv(track: Track): String = buildString {
        appendLine("time_local,time_utc,latitude,longitude,speed_kmh,video,video_position_s")
        for (f in track.fixes) {
            val video = "\"" + f.videoName.replace("\"", "\"\"") + "\""
            appendLine(
                "${f.time.format(local)},${iso(f.time)},${coord(f.lat)},${coord(f.lon)},${f.speedKmh},$video," +
                    String.format(Locale.US, "%.1f", f.posSec)
            )
        }
    }

    /** Every sampled frame with its raw OCR text and whether it was kept or why not. */
    fun ocrLog(track: Track): String = buildString {
        appendLine("video,video_position_s,status,ocr_text")
        track.sampleList.forEachIndexed { i, s ->
            val status = track.sampleStatus[i]?.name ?: "KEPT"
            fun q(v: String) = "\"" + v.replace("\"", "\"\"") + "\""
            appendLine("${q(s.videoName)},${String.format(Locale.US, "%.1f", s.posSec)},$status,${q(s.text)}")
        }
    }

    fun write(format: ExportFormat, track: Track, title: String, out: OutputStream, googleEarthExtras: Boolean = false, timed: Boolean = true) {
        when (format) {
            ExportFormat.KMZ -> writeKmz(track, title, out, googleEarthExtras, timed)
            ExportFormat.KML -> out.write(kml(track, title, googleEarthExtras, timed).toByteArray(Charsets.UTF_8))
            ExportFormat.GPX -> out.write(gpx(track, title, timed).toByteArray(Charsets.UTF_8))
            ExportFormat.CSV -> out.write(csv(track).toByteArray(Charsets.UTF_8))
            ExportFormat.OCR_LOG -> out.write(ocrLog(track).toByteArray(Charsets.UTF_8))
        }
    }
}
