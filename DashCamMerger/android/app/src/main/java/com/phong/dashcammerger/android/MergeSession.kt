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
import com.phong.dashcammerger.core.PlanOptions
import com.phong.dashcammerger.core.Planner
import com.phong.dashcammerger.core.Report
import com.phong.dashcammerger.core.RunReport
import java.util.concurrent.atomic.AtomicBoolean

enum class Phase { Idle, Scanning, NeedCameraMap, Planned, Merging, Done, Error }

/**
 * The single orchestrator (Android counterpart of core/pipeline.py).
 * Held at process level so a screen rotation does not lose a running merge.
 * Heavy work runs on a background thread; UI state is only mutated on the main thread.
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

    val selectedParts: List<OutputPart> get() = order.filter { it in selected }.map { planParts[it] }

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
        status = "Error"
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
    fun scan(ctx: Context, tree: Uri, outputTree: Uri?, opt: PlanOptions) {
        if (busy) return
        val app = ctx.applicationContext
        options = opt
        phase = Phase.Scanning
        status = "Scanning folder…"
        progress = 0f
        text = ""
        clearSelection()
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
        Thread {
            try {
                buildPlan(app)
            } catch (e: Throwable) {
                fail(e)
            }
        }.start()
    }

    private fun buildPlan(app: Context) {
        val out = output!!
        val existing = HashSet<String>()
        val p = Planner.buildPlan(
            clips, options,
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
            planParts = p.parts
            selected = p.parts.indices.toSet() // AR-10.6: everything ticked by default
            order = sortedOrder(p.parts)
            partners = Arrange.partners(p.parts)
            if (p.parts.isEmpty()) {
                phase = Phase.Error
                status = "Nothing to merge"
            } else {
                phase = Phase.Planned
                status = "Plan ready - choose files, then tap Merge"
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
        Thread {
            try {
                val totalBytes = parts.sumOf { it.expectedSize }.coerceAtLeast(1)
                var doneBytes = 0L
                var lastUiUpdate = 0L
                val results = mutableListOf<PartResult>()
                for ((i, part) in parts.withIndex()) {
                    if (cancel.get()) break
                    val label = "Part ${i + 1}/${parts.size}: ${part.outputName}"
                    ui { status = label }
                    results.add(
                        Merger.mergePart(app, part, out, cancel) { bytes ->
                            doneBytes += bytes
                            val now = System.currentTimeMillis()
                            if (now - lastUiUpdate > 200) {
                                lastUiUpdate = now
                                val frac = doneBytes.toFloat() / totalBytes
                                ui { progress = frac }
                            }
                        }
                    )
                }

                // FR-7.6: the sources must be exactly as we found them.
                val problems = parts.flatMap { it.clips }.mapNotNull { clip ->
                    val st = SafScanner.stat(app, Uri.parse(clip.uri))
                    if (st == null || st.first != clip.sizeBytes) "${clip.name}: changed or missing" else null
                }
                val report = RunReport(p, results, out.displayPath, problems.isEmpty(), problems, skipped)
                val reportText = Report.reportText(report)
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
                    }
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
