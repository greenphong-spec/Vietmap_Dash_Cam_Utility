package com.phong.dashcammerger.android

import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.phong.dashcammerger.core.Arrange
import com.phong.dashcammerger.core.Camera
import com.phong.dashcammerger.core.Cameras
import com.phong.dashcammerger.core.ClipInfo
import com.phong.dashcammerger.core.MergePlan
import com.phong.dashcammerger.core.OutputPart
import com.phong.dashcammerger.core.PartResult
import com.phong.dashcammerger.core.PartStatus
import com.phong.dashcammerger.core.PartRunState
import com.phong.dashcammerger.core.PlanOptions
import com.phong.dashcammerger.core.Progress
import com.phong.dashcammerger.core.ProgressSnapshot
import com.phong.dashcammerger.core.ProgressTracker
import com.phong.dashcammerger.core.Planner
import com.phong.dashcammerger.core.Report
import com.phong.dashcammerger.core.RunReport
import com.phong.dashcammerger.core.TimeRange
import com.phong.dashcammerger.core.TimeRangeError
import java.time.LocalDateTime
import java.util.concurrent.atomic.AtomicBoolean

enum class Phase { Idle, Scanning, NeedCameraMap, Planned, Merging, Done, Error }

/**
 * The single orchestrator (Android counterpart of core/pipeline.py).
 * Held at process level so a screen rotation does not lose a running merge.
 * Heavy work runs on a background thread; UI state is only mutated on the main thread.
 * While busy, [MergeService] keeps the process in the foreground (AR-13).
 */
object MergeSession {
    var phase by mutableStateOf(Phase.Idle); private set
    var status by mutableStateOf(""); private set
    var progress by mutableStateOf(0f); private set
    var text by mutableStateOf(""); private set
    var tokens by mutableStateOf<Map<String, List<ClipInfo>>>(emptyMap()); private set

    /** AR-10.5: the planned parts the user picks from, and the indices of the ticked ones. */
    var planParts by mutableStateOf<List<OutputPart>>(emptyList()); private set
    var selected by mutableStateOf<Set<Int>>(emptySet()); private set
    /** Free bytes in the output folder, or null if unknown (AR-10.2). */
    var freeBytes by mutableStateOf<Long?>(null); private set

    /** AR-10.14: export order, as indices into planParts; merge follows this order. */
    var order by mutableStateOf<List<Int>>(emptyList()); private set
    var orderByTime by mutableStateOf(false); private set
    /** AR-10.16: for each part, the other cameras' parts recorded at the same time. */
    private var partners: List<Set<Int>> = emptyList()

    /** AR-14: the parts of the current/last run (merge order), their states and live progress. */
    var runParts by mutableStateOf<List<OutputPart>>(emptyList()); private set
    var partStates by mutableStateOf<List<PartRunState>>(emptyList()); private set
    var partSeconds by mutableStateOf<List<Long>>(emptyList()); private set
    var snapshot by mutableStateOf<ProgressSnapshot?>(null); private set

    /** FR-3.7: the From/To box texts the shown plan was scanned with, and its range ("" = all). */
    var scannedRange by mutableStateOf("" to ""); private set
    var timeRange by mutableStateOf(""); private set

    /**
     * FR-3.8 trip trims of the shown plan: trip -> (first kept clip start, last kept clip
     * end). They belong to the last scan, so a new Scan clears them.
     */
    var tripTrims by mutableStateOf<Map<Int, Pair<LocalDateTime, LocalDateTime>>>(emptyMap()); private set
    /** The scan's untrimmed parts: what the trim dialog shows for a trip. */
    private var scanParts: List<OutputPart> = emptyList()

    /** All cameras' untrimmed parts of one trip (FR-3.8b). */
    fun tripParts(trip: Int): List<OutputPart> = scanParts.filter { it.tripIndex == trip }

    val selectedParts: List<OutputPart> get() = order.filter { it in selected }.map { planParts[it] }

    /** AR-14.1: at most two UI updates per second from the copy loop. */
    private const val PUBLISH_MS = 500L

    private val main = Handler(Looper.getMainLooper())
    private val cancel = AtomicBoolean(false)
    private var clips: List<ClipInfo> = emptyList()
    private var plan: MergePlan? = null
    private var output: OutputFolder? = null
    private var options = PlanOptions()

    val busy: Boolean get() = phase == Phase.Scanning || phase == Phase.Merging

    private fun ui(block: () -> Unit) = main.post(block)

    private fun fail(e: Throwable) = ui {
        phase = Phase.Error
        // FR-3.7c: a range the footage cannot take (e.g. several days, no date) is a usage
        // error; the status line wraps, the monospace details box does not.
        status = if (e is TimeRangeError) "Time range: ${e.message}" else "Error"
        text = e.message ?: e.javaClass.simpleName
    }

    fun reset() {
        if (busy) return
        phase = Phase.Idle
        status = ""
        text = ""
        progress = 0f
        clearSelection()
    }

    private fun clearSelection() {
        planParts = emptyList()
        selected = emptySet()
        freeBytes = null
        order = emptyList()
        partners = emptyList()
        runParts = emptyList()
        partStates = emptyList()
        partSeconds = emptyList()
        snapshot = null
        timeRange = ""
        tripTrims = emptyMap()
        scanParts = emptyList()
    }

    // AR-10.7: selection edits only apply while a plan is on screen.
    /** With [linked] (AR-10.16) the same-time parts of the other cameras follow this one. */
    fun toggle(index: Int, linked: Boolean = false) {
        if (phase != Phase.Planned) return
        val group = if (linked) partners[index] + index else setOf(index)
        selected = if (index in selected) selected - group else selected + group
    }

    /** AR-10.15: re-sort the list by camera or by time; also remembered for the next plan. */
    fun arrange(byTime: Boolean) {
        orderByTime = byTime
        if (phase == Phase.Planned) order = sortedOrder(planParts)
    }

    /** AR-10.17: manual ▲ / ▼ on the row at position [pos] of [order]. */
    fun move(pos: Int, delta: Int) {
        if (phase == Phase.Planned) order = Arrange.move(order, pos, delta)
    }

    private fun sortedOrder(parts: List<OutputPart>) =
        if (orderByTime) Arrange.byTime(parts) else Arrange.byCamera(parts)

    fun setCamera(camera: Camera, on: Boolean) {
        if (phase != Phase.Planned) return
        val indices = planParts.indices.filter { planParts[it].camera == camera }.toSet()
        selected = if (on) selected + indices else selected - indices
    }

    fun selectAll() {
        if (phase == Phase.Planned) selected = planParts.indices.toSet()
    }

    fun selectNone() {
        if (phase == Phase.Planned) selected = emptySet()
    }

    /** Steps 1-3: scan, detect cameras, then plan (or stop to ask for a camera map). */
    fun scan(ctx: Context, tree: Uri, outputTree: Uri?, opt: PlanOptions, rangeTexts: Pair<String, String> = "" to "") {
        if (busy) return
        val app = ctx.applicationContext
        options = opt
        scannedRange = rangeTexts
        phase = Phase.Scanning
        status = "Scanning folder…"
        progress = 0f
        text = ""
        clearSelection()
        MergeService.start(app) // AR-13
        Thread {
            try {
                val out = OutputFolder.open(app, tree, outputTree, opt.outputSubfolder)
                output = out
                val found = SafScanner.scan(app, tree, opt.outputSubfolder, skipDocIds = setOf(out.dirId))
                clips = found
                if (found.isEmpty()) { // FR-1.5
                    ui {
                        phase = Phase.Error
                        status = "No video files found"
                        text = "No ${SafScanner.VIDEO_EXTENSIONS.joinToString("/")} files in this folder or its subfolders."
                    }
                    return@Thread
                }
                val detection = Cameras.detect(found)
                if (detection.profileName == null) {
                    ui {
                        tokens = detection.tokens
                        phase = Phase.NeedCameraMap
                        status = "Camera naming not recognised - please assign each group"
                    }
                    return@Thread
                }
                ui { status = "Found ${found.size} clips (${detection.profileName} naming). Reading streams…" }
                buildPlan(app)
            } catch (e: Throwable) {
                fail(e)
            }
        }.start()
    }

    /** FR-2.3: the user assigned each token to a camera. */
    fun applyCameraMap(ctx: Context, mapping: Map<String, Camera>) {
        val app = ctx.applicationContext
        Cameras.applyManualMap(tokens, mapping)
        phase = Phase.Scanning
        status = "Reading streams…"
        MergeService.start(app) // AR-13
        Thread {
            try {
                buildPlan(app)
            } catch (e: Throwable) {
                fail(e)
            }
        }.start()
    }

    /**
     * FR-3.8: keep only (first clip start, last clip end) of [trip] for every camera, or the
     * whole trip for null. Re-plans from the probed clips (nothing is read again) and keeps
     * the list's ticks and order.
     */
    fun setTripTrim(ctx: Context, trip: Int, bounds: Pair<LocalDateTime, LocalDateTime>?) {
        if (phase != Phase.Planned) return
        val app = ctx.applicationContext
        tripTrims = if (bounds == null) tripTrims - trip else tripTrims + (trip to bounds)
        phase = Phase.Scanning
        status = "Applying trim…"
        Thread {
            try {
                buildPlan(app, retrim = true)
            } catch (e: Throwable) {
                fail(e)
            }
        }.start()
    }

    private fun buildPlan(app: Context, retrim: Boolean = false) {
        val out = output!!
        val existing = HashSet<String>()
        // FR-3.8: a scan always plans whole trips; trims only narrow what is merged.
        val opt = if (!retrim) options else options.copy(
            tripRanges = tripTrims.mapValues { (_, b) -> TimeRange.clipWindow(b.first, b.second) },
        )
        val p = Planner.buildPlan(
            clips, opt,
            probe = { MediaProbe.probe(app, it) },
            nameExists = { name -> name in existing || out.exists(name).also { if (it) existing.add(name) } },
            onProgress = { what, done, total ->
                ui {
                    status = "$what… $done / $total"
                    progress = done.toFloat() / total
                }
            },
        )
        plan = p
        // Whether the output fits depends on the selection, so the UI checks it live (AR-10.9).
        val free = out.availableBytes()
        val summary = Report.planSummary(p, out.displayPath) +
            (free?.let { "Free space there: ${Report.formatSize(it)}\n" } ?: "")
        ui {
            text = summary
            progress = 0f
            freeBytes = free
            timeRange = p.timeRange
            if (retrim) { // same files, same ticks and order, fewer clips
                val (keptOrder, keptSelected) = Arrange.carryOver(planParts, order, selected, p.parts)
                order = keptOrder
                selected = keptSelected
            } else {
                scanParts = p.parts
                selected = p.parts.indices.toSet() // AR-10.6: everything ticked by default
                order = sortedOrder(p.parts)
            }
            planParts = p.parts
            partners = Arrange.partners(p.parts)
            if (p.parts.isEmpty()) {
                phase = Phase.Error
                status = if (p.timeRange.isEmpty()) "Nothing to merge" else "Nothing to merge in the time range"
            } else {
                phase = Phase.Planned
                status = when {
                    !retrim -> "Plan ready - choose files, then tap Merge"
                    tripTrims.isEmpty() -> "No trips trimmed"
                    else -> "Trimmed trips: ${tripTrims.keys.sorted().joinToString(", ")}"
                }
            }
        }
    }

    /** Steps 5-7: write each part, verify it, then write the report. */
    fun merge(ctx: Context) {
        val p = plan ?: return
        val out = output ?: return
        if (busy) return
        // AR-10.10 / AR-10.14: only the ticked parts, in the order on screen;
        // the rest are listed in the report.
        val parts = order.filter { it in selected }.map { p.parts[it] }
        if (parts.isEmpty()) return
        val skipped = order.filter { it !in selected }.map { p.parts[it] }
        val app = ctx.applicationContext
        cancel.set(false)
        phase = Phase.Merging
        progress = 0f
        MergeService.start(app) // AR-13: keeps running with the screen off
        // AR-14: the run's parts, their states and the live progress.
        runParts = parts
        partStates = List(parts.size) { PartRunState.WAITING }
        partSeconds = List(parts.size) { 0L }
        val startMs = System.currentTimeMillis()
        val tracker = ProgressTracker(parts.sumOf { it.expectedSize }.coerceAtLeast(1), startMs)
        snapshot = tracker.snapshot(startMs)
        Thread {
            try {
                var lastUiUpdate = 0L
                fun publish(force: Boolean = false) {
                    val now = System.currentTimeMillis()
                    if (!force && now - lastUiUpdate < PUBLISH_MS) return
                    lastUiUpdate = now
                    val snap = tracker.snapshot(now)
                    ui {
                        snapshot = snap
                        progress = snap.doneBytes.toFloat() / snap.totalBytes
                    }
                }
                fun setPart(i: Int, state: PartRunState, seconds: Long = 0) = ui {
                    partStates = partStates.toMutableList().also { it[i] = state }
                    if (seconds > 0) partSeconds = partSeconds.toMutableList().also { it[i] = seconds }
                }
                val results = mutableListOf<PartResult>()
                for ((i, part) in parts.withIndex()) {
                    if (cancel.get()) break
                    val label = "Part ${i + 1}/${parts.size}: ${part.outputName}"
                    tracker.startPart(i + 1, parts.size, part.expectedSize)
                    setPart(i, PartRunState.RUNNING)
                    ui { status = label }
                    publish(force = true)
                    val result = Merger.mergePart(
                        app, part, out, cancel,
                        onBytes = { bytes ->
                            tracker.add(bytes, System.currentTimeMillis())
                            publish()
                        },
                        onClip = { j, n ->
                            tracker.clip(j, n)
                            publish(force = true)
                        },
                        onFinishing = {
                            tracker.finishing()
                            ui { status = "Part ${i + 1}/${parts.size}: finishing ${part.outputName}" }
                            publish(force = true)
                        },
                    )
                    results.add(result)
                    tracker.endPart(part.expectedSize)
                    setPart(
                        i,
                        when (result.status) {
                            PartStatus.PASS -> PartRunState.PASS
                            PartStatus.CANCELLED -> PartRunState.CANCELLED
                            else -> PartRunState.FAIL
                        },
                        result.elapsedMs / 1000,
                    )
                    publish(force = true)
                }

                // FR-7.6: the sources must be exactly as we found them.
                val problems = parts.flatMap { it.clips }.mapNotNull { clip ->
                    val st = SafScanner.stat(app, Uri.parse(clip.uri))
                    if (st == null || st.first != clip.sizeBytes) "${clip.name}: changed or missing" else null
                }
                val report = RunReport(p, results, out.displayPath, problems.isEmpty(), problems, skipped)
                val endMs = System.currentTimeMillis()
                val took = Progress.elapsed(endMs - startMs)
                // AR-14.8: Android-only timing line after the shared report text.
                val reportText = Report.reportText(report).trimEnd('\n') + "\n" +
                    Progress.reportTimeLine(startMs, endMs, results.sumOf { it.actualSize }) + "\n"
                try {
                    out.writeText(Report.REPORT_FILENAME, reportText) // FR-9.2
                } catch (_: Exception) {
                }
                val passed = results.count { it.status == PartStatus.PASS }
                ui {
                    progress = 1f
                    phase = Phase.Done
                    status = when {
                        cancel.get() -> "Cancelled - $passed of ${parts.size} part(s) finished"
                        passed == parts.size -> "Done - all ${parts.size} part(s) verified"
                        else -> "Finished with problems - $passed of ${parts.size} part(s) passed"
                    } + " in $took" // AR-14.7
                    text = reportText
                }
            } catch (e: Throwable) {
                fail(e)
            }
        }.start()
    }

    fun cancel() {
        cancel.set(true)
        status = "Cancelling…"
    }
}
