package com.phong.dashgps.android

import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.phong.dashgps.core.Exporters
import com.phong.dashgps.core.RouteClean
import com.phong.dashgps.core.RouteReader
import com.phong.dashgps.core.TrackCleaner
import java.util.Locale

/**
 * Cleans an already exported route file (KML / KMZ / GPX): read it, run the same checks
 * as extraction (TrackCleaner steps 4-9), and save the result next to the other exports
 * as <name>_clean.<same format>. The source file is never changed.
 */
object RouteCleanSession {
    private const val TAG = "RouteClean"

    var busy by mutableStateOf(false); private set
    var status by mutableStateOf(""); private set
    var result by mutableStateOf<RouteClean?>(null); private set
    var summary by mutableStateOf(""); private set
    var saved by mutableStateOf<ExportedFile?>(null); private set

    private val main = Handler(Looper.getMainLooper())
    private fun ui(block: () -> Unit) = main.post(block)

    private fun fail(what: String, e: Throwable) {
        Log.e(TAG, what, e)
        ui {
            busy = false
            status = "Error: ${e.message ?: e.javaClass.simpleName}"
        }
    }

    fun load(ctx: Context, uri: Uri) {
        if (busy) return
        val app = ctx.applicationContext
        busy = true
        result = null
        summary = ""
        saved = null
        status = "Reading route…"
        Thread {
            try {
                val name = Storage.videoItem(app, uri).name
                val route = app.contentResolver.openInputStream(uri)!!.use { RouteReader.read(name, it, ExtractSession.zone) }
                val clean = TrackCleaner.cleanRoute(route)
                val text = buildSummary(clean)
                ui {
                    result = clean
                    summary = text
                    busy = false
                    val removed = clean.route.points.size - clean.track.fixes.size
                    status = if (removed == 0 && clean.track.speedCorrected == 0) "No noise found - the route is already clean"
                    else "Cleaned - save to keep the result"
                }
            } catch (e: Throwable) {
                fail("load $uri", e)
            }
        }.start()
    }

    fun save(ctx: Context, folderUri: Uri, googleEarthExtras: Boolean) {
        val clean = result ?: return
        if (busy) return
        val app = ctx.applicationContext
        busy = true
        status = "Writing file…"
        Thread {
            try {
                val format = clean.route.format
                val title = clean.route.stem + "_clean"
                val folder = ExportFolder(app, folderUri)
                val fileName = folder.uniqueName(title, format.ext)
                val uri = folder.create(fileName, format.mime) { out ->
                    Exporters(ExtractSession.zone).write(format, clean.track, title, out, googleEarthExtras, timed = clean.route.hasTime)
                }
                ui {
                    saved = ExportedFile(uri, fileName, format)
                    busy = false
                    status = "Saved $fileName to ${Storage.describeTree(folderUri)}"
                }
            } catch (e: Throwable) {
                fail("save", e)
            }
        }.start()
    }

    private fun buildSummary(c: RouteClean): String = buildString {
        val r = c.route
        val t = c.track
        val removed = r.points.size - t.fixes.size
        append("${r.name}\n")
        append("${r.format.name}: ${r.points.size} points, ${r.segments.size} line(s), ")
        append(if (r.hasTime) "with times" else "no times")
        append(if (r.hasSpeed) ", with speeds\n" else ", no speeds\n")
        append("Removed: $removed point(s)")
        if (t.rejected.isNotEmpty()) append(" - " + t.rejected.entries.joinToString(", ") { "${it.value} ${it.key.label}" })
        append('\n')
        if (t.speedCorrected > 0) append("Speeds corrected from GPS: ${t.speedCorrected}\n")
        append(String.format(Locale.US, "Distance: %.2f km -> %.2f km\n", c.before.distanceM / 1000, t.stats.distanceM / 1000))
        if (r.hasSpeed) append("Max speed: ${c.before.maxSpeedKmh} -> ${t.stats.maxSpeedKmh} km/h\n")
        if (r.hasTime && !r.hasSpeed) append("Speeds in the saved file are computed from the positions.\n")
        c.assumedStepS?.let {
            append("No timestamps in this file: positions checked assuming ~$it s between points; speed checks skipped. ")
            append("Clean the GPX export for the most thorough result.\n")
        }
    }
}
