package com.phong.dashgps.core

import org.w3c.dom.Element
import java.io.ByteArrayInputStream
import java.io.InputStream
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.util.zip.ZipInputStream
import javax.xml.XMLConstants
import javax.xml.parsers.DocumentBuilderFactory

/** One point of a route file. Time is camera-local (converted from the file's UTC). */
class RoutePoint(val lat: Double, val lon: Double, val time: LocalDateTime?, val speedKmh: Double?)

/** A route read from a KML, KMZ or GPX file; one list per line / track segment. */
class RouteFile(val name: String, val format: ExportFormat, val segments: List<List<RoutePoint>>) {
    val points: List<RoutePoint> get() = segments.flatten()
    val hasTime: Boolean get() = points.isNotEmpty() && points.all { it.time != null }
    val hasSpeed: Boolean get() = points.isNotEmpty() && points.all { it.speedKmh != null }
    val stem: String get() = name.substringBeforeLast('.', name)
}

/** A cleaned route: the result plus the stats of the route as it was read. */
class RouteClean(
    val route: RouteFile,
    val track: Track,
    val before: TrackStats,
    /** Seconds assumed between points when the file has no timestamps, else null. */
    val assumedStepS: Long?,
)

/**
 * Reads the route out of KML / KMZ / GPX files - this app's exports and those of other
 * apps. GPX: trkpt (or rtept) with time and speed (Garmin TrackPointExtension or GPX 1.0
 * <speed>, m/s). KML: a gx:Track (time per point) when present, else every LineString.
 */
object RouteReader {
    fun formatOf(fileName: String): ExportFormat? = when (fileName.substringAfterLast('.', "").lowercase()) {
        "kmz" -> ExportFormat.KMZ
        "kml" -> ExportFormat.KML
        "gpx" -> ExportFormat.GPX
        else -> null
    }

    fun read(fileName: String, input: InputStream, zone: ZoneId): RouteFile {
        val format = formatOf(fileName) ?: throw IllegalArgumentException("Not a KML, KMZ or GPX file: $fileName")
        val segments = when (format) {
            ExportFormat.KMZ -> parseKml(kmlFromKmz(input), zone)
            ExportFormat.KML -> parseKml(input.readBytes(), zone)
            else -> parseGpx(input.readBytes(), zone)
        }.filter { it.isNotEmpty() }
        if (segments.isEmpty()) throw IllegalArgumentException("No route points found in $fileName")
        return RouteFile(fileName, format, segments)
    }

    /** The KML inside a KMZ: doc.kml if present, else the first .kml entry. */
    private fun kmlFromKmz(input: InputStream): ByteArray {
        var first: ByteArray? = null
        ZipInputStream(input).use { zip ->
            while (true) {
                val e = zip.nextEntry ?: break
                if (e.isDirectory || !e.name.lowercase().endsWith(".kml")) continue
                val bytes = zip.readBytes()
                if (e.name.substringAfterLast('/').equals("doc.kml", ignoreCase = true)) return bytes
                if (first == null) first = bytes
            }
        }
        return first ?: throw IllegalArgumentException("The KMZ contains no KML file")
    }

    private fun parseXml(bytes: ByteArray): Element {
        val f = DocumentBuilderFactory.newInstance()
        // Route files never need DTDs or external entities; refuse them (XXE).
        runCatching { f.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true) }
        runCatching { f.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true) }
        f.isExpandEntityReferences = false
        return f.newDocumentBuilder().parse(ByteArrayInputStream(bytes)).documentElement
    }

    /** Elements below [root] whose name without namespace prefix is [local], in document order. */
    private fun Element.all(local: String): List<Element> {
        val nodes = getElementsByTagName("*")
        return (0 until nodes.length).map { nodes.item(it) as Element }.filter { it.nodeName.substringAfterLast(':') == local }
    }

    private fun Element.firstText(local: String): String? = all(local).firstOrNull()?.textContent?.trim()

    private fun time(text: String?, zone: ZoneId): LocalDateTime? {
        if (text.isNullOrBlank()) return null
        return runCatching { OffsetDateTime.parse(text).atZoneSameInstant(zone).toLocalDateTime() }.getOrNull()
            ?: runCatching { LocalDateTime.parse(text) }.getOrNull()
    }

    private fun parseGpx(bytes: ByteArray, zone: ZoneId): List<List<RoutePoint>> {
        val root = parseXml(bytes)
        fun point(e: Element): RoutePoint? {
            val lat = e.getAttribute("lat").toDoubleOrNull() ?: return null
            val lon = e.getAttribute("lon").toDoubleOrNull() ?: return null
            val speed = e.firstText("speed")?.toDoubleOrNull()?.let { it * 3.6 }
            return RoutePoint(lat, lon, time(e.firstText("time"), zone), speed)
        }
        val segs = root.all("trkseg").map { seg -> seg.all("trkpt").mapNotNull(::point) }
        if (segs.any { it.isNotEmpty() }) return segs
        val trk = root.all("trkpt").mapNotNull(::point)
        if (trk.isNotEmpty()) return listOf(trk)
        return root.all("rte").map { rte -> rte.all("rtept").mapNotNull(::point) }
    }

    private fun parseKml(bytes: ByteArray, zone: ZoneId): List<List<RoutePoint>> {
        val root = parseXml(bytes)
        val tracks = root.all("Track").map { tr ->
            val whens = tr.all("when").map { time(it.textContent.trim(), zone) }
            val coords = tr.all("coord").map { it.textContent.trim().split(Regex("\\s+")) }
            coords.mapIndexedNotNull { i, c ->
                val lon = c.getOrNull(0)?.toDoubleOrNull() ?: return@mapIndexedNotNull null
                val lat = c.getOrNull(1)?.toDoubleOrNull() ?: return@mapIndexedNotNull null
                RoutePoint(lat, lon, whens.getOrNull(i), null)
            }
        }.filter { it.isNotEmpty() }
        if (tracks.isNotEmpty()) return tracks
        return root.all("LineString").map { ls ->
            (ls.firstText("coordinates") ?: "").split(Regex("\\s+")).filter { it.isNotBlank() }.mapNotNull { tuple ->
                val c = tuple.split(',')
                val lon = c.getOrNull(0)?.toDoubleOrNull() ?: return@mapNotNull null
                val lat = c.getOrNull(1)?.toDoubleOrNull() ?: return@mapNotNull null
                RoutePoint(lat, lon, null, null)
            }
        }
    }
}
