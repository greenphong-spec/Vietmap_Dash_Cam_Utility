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
    private val utc = DateTimeFormatter.ISO_INSTANT
    private val local = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")

    private fun iso(t: LocalDateTime) = utc.format(t.atZone(zone).toInstant())
    private fun coord(v: Double) = String.format(Locale.US, "%.6f", v)
    private fun esc(s: String) = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;")

    fun summary(track: Track, title: String): String {
        val s = track.stats
        return buildString {
            appendLine(title)
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
    fun kml(track: Track, title: String, googleEarthExtras: Boolean = false): String = buildString {
        appendLine("""<?xml version="1.0" encoding="UTF-8"?>""")
        appendLine("""<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">""")
        appendLine("<Document>")
        appendLine("<name>${esc(title)}</name>")
        appendLine("<description>${esc(summary(track, title))}Created by $appName.</description>")
        appendLine("""<Style id="route"><LineStyle><color>ff0033ff</color><width>5</width></LineStyle></Style>""")
        appendLine("""<Style id="start"><IconStyle><Icon><href>http://maps.google.com/mapfiles/kml/paddle/grn-circle.png</href></Icon></IconStyle></Style>""")
        appendLine("""<Style id="end"><IconStyle><Icon><href>http://maps.google.com/mapfiles/kml/paddle/red-square.png</href></Icon></IconStyle></Style>""")

        // Route: plain LineString(s), understood by every KML viewer.
        appendLine("<Placemark><name>${esc(title)}</name><styleUrl>#route</styleUrl>")
        if (track.segments.size > 1) append("<MultiGeometry>")
        for (seg in track.segments) {
            append("<LineString><tessellate>1</tessellate><coordinates>")
            seg.forEach { append("${coord(it.lon)},${coord(it.lat)},0 ") }
            appendLine("</coordinates></LineString>")
        }
        if (track.segments.size > 1) append("</MultiGeometry>")
        appendLine("</Placemark>")

        track.fixes.firstOrNull()?.let { appendLine(pin("Start", "#start", it)) }
        track.fixes.lastOrNull()?.let { appendLine(pin("End", "#end", it)) }

        if (googleEarthExtras) appendGoogleEarthExtras(track)
        appendLine("</Document>")
        appendLine("</kml>")
    }

    private fun StringBuilder.appendGoogleEarthExtras(track: Track) {
        appendLine("""<Style id="pt"><IconStyle><scale>0.6</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/shaded_dot.png</href></Icon></IconStyle><LabelStyle><scale>0.7</scale></LabelStyle></Style>""")
        // Timed track: lets Google Earth animate the drive with its time slider.
        appendLine("<Placemark><name>Timed track (time slider)</name><visibility>0</visibility><styleUrl>#route</styleUrl><gx:MultiTrack>")
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

    private fun pin(name: String, style: String, f: Fix) =
        "<Placemark><name>$name</name><styleUrl>$style</styleUrl>" +
            "<description>${esc(f.time.format(local))} · ${f.speedKmh} km/h</description>" +
            "<Point><coordinates>${coord(f.lon)},${coord(f.lat)},0</coordinates></Point></Placemark>"

    /** KMZ = zip containing doc.kml (what Google Earth opens by default). */
    fun writeKmz(track: Track, title: String, out: OutputStream, googleEarthExtras: Boolean = false) {
        ZipOutputStream(out).use { zip ->
            zip.putNextEntry(ZipEntry("doc.kml"))
            zip.write(kml(track, title, googleEarthExtras).toByteArray(Charsets.UTF_8))
            zip.closeEntry()
        }
    }

    /** GPX 1.1; speed (m/s) in the Garmin TrackPointExtension v2, understood by most apps. */
    fun gpx(track: Track, title: String): String = buildString {
        appendLine("""<?xml version="1.0" encoding="UTF-8"?>""")
        appendLine(
            """<gpx version="1.1" creator="${esc(appName)}" xmlns="http://www.topografix.com/GPX/1/1" """ +
                """xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v2">"""
        )
        append("<metadata><name>${esc(title)}</name>")
        track.fixes.firstOrNull()?.let { append("<time>${iso(it.time)}</time>") }
        appendLine("</metadata>")
        appendLine("<trk><name>${esc(title)}</name>")
        for (seg in track.segments) {
            appendLine("<trkseg>")
            for (f in seg) {
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

    fun write(format: ExportFormat, track: Track, title: String, out: OutputStream, googleEarthExtras: Boolean = false) {
        when (format) {
            ExportFormat.KMZ -> writeKmz(track, title, out, googleEarthExtras)
            ExportFormat.KML -> out.write(kml(track, title, googleEarthExtras).toByteArray(Charsets.UTF_8))
            ExportFormat.GPX -> out.write(gpx(track, title).toByteArray(Charsets.UTF_8))
            ExportFormat.CSV -> out.write(csv(track).toByteArray(Charsets.UTF_8))
            ExportFormat.OCR_LOG -> out.write(ocrLog(track).toByteArray(Charsets.UTF_8))
        }
    }
}
