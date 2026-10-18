package com.phong.dashgps.android

import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.google.android.gms.tasks.Task
import com.google.mlkit.vision.text.Text
import com.phong.dashgps.core.ExportFormat
import com.phong.dashgps.core.Exporters
import com.phong.dashgps.core.OverlayProfile
import com.phong.dashgps.core.Sample
import com.phong.dashgps.core.Track
import com.phong.dashgps.core.TrackCleaner
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.ArrayDeque
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean

enum class Phase { Idle, Testing, Extracting, Exporting, Done, Error }

class TestResult(val bitmap: Bitmap, val posSec: Double, val text: String, val parsed: String)

class ExportedFile(val uri: Uri, val name: String, val format: ExportFormat)

/**
 * Orchestrates test frame -> extraction -> cleaning -> export. Process-level so rotation
 * does not lose work; heavy work on a background thread, UI state changed on the main thread.
 */
object ExtractSession {
    var phase by mutableStateOf(Phase.Idle); private set
    var status by mutableStateOf(""); private set
    var progress by mutableStateOf(0f); private set
    var test by mutableStateOf<TestResult?>(null); private set
    var track by mutableStateOf<Track?>(null); private set
    var summary by mutableStateOf(""); private set
    var exported by mutableStateOf<List<ExportedFile>>(emptyList()); private set

    private const val OCR_IN_FLIGHT = 4
    private val main = Handler(Looper.getMainLooper())
    private val cancel = AtomicBoolean(false)
    private var samples: List<Sample> = emptyList()
    private var profile: OverlayProfile? = null
    private var videos: List<VideoItem> = emptyList()
    val zone: ZoneId = ZoneId.systemDefault()

    val busy get() = phase == Phase.Testing || phase == Phase.Extracting || phase == Phase.Exporting

    private fun ui(block: () -> Unit) = main.post(block)

    private fun fail(e: Throwable) = ui {
        phase = Phase.Error
        status = "Error: ${e.message ?: e.javaClass.simpleName}"
    }

    fun reset() {
        if (busy) return
        phase = Phase.Idle
        status = ""
        progress = 0f
        track = null
        summary = ""
        exported = emptyList()
        test = null
    }

    fun cancel() {
        cancel.set(true)
        status = "Cancelling…"
    }

    /** Decode one frame ~[atSec] into the video, OCR it and show what was read. */
    fun testFrame(ctx: Context, video: VideoItem, profile: OverlayProfile, atSec: Double) {
        if (busy) return
        val app = ctx.applicationContext
        phase = Phase.Testing
        status = "Reading one frame of ${video.name}…"
        cancel.set(false)
        Thread {
            try {
                Ocr().use { ocr ->
                    var result: TestResult? = null
                    FrameSampler.sample(app, video.uri, 1.0, profile.crop, cancel, startSec = atSec, maxFrames = 1) { pos, crop ->
                        val text = ocr.read(crop)
                        val r = profile.parse(text)
                        val parsed = r?.let {
                            String.format(
                                Locale.US, "%s   %d km/h   %.5f, %.5f",
                                it.time?.format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"))
                                    ?: "time unreadable (…:${"%02d".format(it.secondHint)})",
                                it.speedKmh, it.lat, it.lon,
                            )
                        } ?: "not recognised"
                        result = TestResult(crop.toBitmap(), pos, text, parsed)
                    }
                    ui {
                        test = result
                        phase = Phase.Idle
                        status = if (result == null) "No frame could be decoded" else "Test frame read at ${result.posSec.toInt()} s"
                    }
                }
            } catch (e: Throwable) {
                fail(e)
            }
        }.start()
    }

    /** Sample every video, OCR every sampled frame, then clean everything into one track. */
    fun extract(ctx: Context, items: List<VideoItem>, prof: OverlayProfile, intervalSec: Double) {
        if (busy || items.isEmpty()) return
        val app = ctx.applicationContext
        phase = Phase.Extracting
        cancel.set(false)
        progress = 0f
        track = null
        exported = emptyList()
        summary = ""
        profile = prof
        videos = items
        Thread {
            try {
                val started = SystemClock.elapsedRealtime()
                val durations = items.map { runCatching { FrameSampler.info(app, it.uri).durationSec }.getOrDefault(0.0) }
                val total = durations.sum().coerceAtLeast(1.0)
                val collected = mutableListOf<Sample>()
                var lastUi = 0L
                Ocr().use { ocr ->
                    items.forEachIndexed { index, video ->
                        if (cancel.get()) return@forEachIndexed
                        val before = durations.take(index).sum()
                        val pending = ArrayDeque<Pair<Double, Task<Text>>>()
                        fun drainOne() {
                            val (pos, task) = pending.removeFirst()
                            collected.add(Sample(index, video.name, pos, ocr.await(task)))
                        }
                        FrameSampler.sample(
                            app, video.uri, intervalSec, prof.crop, cancel,
                            onProgress = { pos ->
                                val now = SystemClock.elapsedRealtime()
                                if (now - lastUi > 250) {
                                    lastUi = now
                                    val frac = ((before + pos) / total).toFloat()
                                    val n = collected.size
                                    ui {
                                        progress = frac
                                        status = "Video ${index + 1}/${items.size}: ${video.name}\n" +
                                            "${pos.toInt()} / ${durations[index].toInt()} s · $n frames read"
                                    }
                                }
                            },
                        ) { pos, crop ->
                            pending.addLast(pos to ocr.submit(crop))
                            if (pending.size >= OCR_IN_FLIGHT) drainOne()
                        }
                        while (pending.isNotEmpty()) drainOne()
                    }
                }
                samples = collected
                val t = TrackCleaner.clean(collected, prof)
                val secs = (SystemClock.elapsedRealtime() - started) / 1000
                val text = buildSummary(t, items) + "\nRead ${collected.size} frames in ${secs / 60} min ${secs % 60} s" +
                    (if (cancel.get()) " (cancelled - partial result)" else "")
                ui {
                    track = t
                    summary = text
                    progress = 1f
                    phase = Phase.Done
                    status = if (t.fixes.isEmpty()) "No GPS data found - check the camera profile with Test frame" else "Extraction finished"
                }
            } catch (e: Throwable) {
                fail(e)
            }
        }.start()
    }

    private fun title(t: Track, items: List<VideoItem>): String {
        val fmt = DateTimeFormatter.ofPattern("yyyyMMddHHmmss")
        return if (items.size == 1) items.first().stem
        else "Track_${t.stats.start?.format(fmt) ?: "start"}_to_${t.stats.end?.format(fmt) ?: "end"}"
    }

    private fun buildSummary(t: Track, items: List<VideoItem>): String = buildString {
        append(Exporters(zone).summary(t, title(t, items)))
        val good = t.fixes.size
        append(String.format(Locale.US, "Frames with valid GPS: %d of %d (%.0f%%)\n", good, t.samples, if (t.samples > 0) 100.0 * good / t.samples else 0.0))
        if (t.timeRepaired > 0) append("Time rebuilt from video clock: ${t.timeRepaired} (damaged time text)\n")
        if (t.speedCorrected > 0) append("Speed corrected from GPS: ${t.speedCorrected} (misread speed text)\n")
        if (t.rejected.isNotEmpty()) {
            append("Dropped: ")
            append(t.rejected.entries.joinToString(", ") { "${it.value} ${it.key.label}" })
            append('\n')
        }
    }

    /**
     * Write the chosen formats. [combine] = one file set for all videos; otherwise one set
     * per video (each cleaned on its own).
     */
    fun export(ctx: Context, folderUri: Uri, formats: Set<ExportFormat>, combine: Boolean, googleEarthExtras: Boolean) {
        val prof = profile ?: return
        val all = track ?: return
        if (busy || formats.isEmpty()) return
        val app = ctx.applicationContext
        phase = Phase.Exporting
        status = "Writing files…"
        Thread {
            try {
                val folder = ExportFolder(app, folderUri)
                val exporters = Exporters(zone)
                val jobs: List<Pair<String, Track>> = if (combine || videos.size == 1) {
                    listOf(title(all, videos) to all)
                } else {
                    videos.indices.map { i ->
                        videos[i].stem to TrackCleaner.clean(samples.filter { it.videoIndex == i }, prof)
                    }.filter { it.second.fixes.isNotEmpty() }
                }
                val written = mutableListOf<ExportedFile>()
                for ((name, t) in jobs) {
                    for (format in ExportFormat.entries.filter { it in formats }) {
                        val fileName = folder.uniqueName(name + format.suffix, format.ext)
                        val uri = folder.create(fileName, format.mime) { out -> exporters.write(format, t, name, out, googleEarthExtras) }
                        written.add(ExportedFile(uri, fileName, format))
                    }
                }
                ui {
                    exported = written
                    phase = Phase.Done
                    status = "Saved ${written.size} file(s) to ${Storage.describeTree(folderUri)}"
                }
            } catch (e: Throwable) {
                fail(e)
            }
        }.start()
    }
}
