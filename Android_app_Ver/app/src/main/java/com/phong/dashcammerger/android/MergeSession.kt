package com.phong.dashcammerger.android

import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.phong.dashcammerger.core.Camera
import com.phong.dashcammerger.core.Cameras
import com.phong.dashcammerger.core.ClipInfo
import com.phong.dashcammerger.core.MergePlan
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
        // Output is about the size of the sources; warn before a merge that cannot fit.
        val needed = p.parts.sumOf { it.expectedSize }
        val free = out.availableBytes()
        val spaceNote = if (free != null && needed > free) {
            "NOT ENOUGH SPACE: needs about ${Report.formatSize(needed)}, only ${Report.formatSize(free)} free " +
                "in the output folder. Free up space or choose another output folder.\n\n"
        } else {
            ""
        }
        val summary = spaceNote + Report.planSummary(p, out.displayPath) +
            (free?.let { "Free space there: ${Report.formatSize(it)}\n" } ?: "")
        ui {
            text = summary
            progress = 0f
            if (p.parts.isEmpty()) {
                phase = Phase.Error
                status = "Nothing to merge"
            } else {
                phase = Phase.Planned
                status = "Plan ready - review it, then tap Merge"
            }
        }
    }

    /** Steps 5-7: write each part, verify it, then write the report. */
    fun merge(ctx: Context) {
        val p = plan ?: return
        val out = output ?: return
        if (busy) return
        val app = ctx.applicationContext
        cancel.set(false)
        phase = Phase.Merging
        progress = 0f
        Thread {
            try {
                val totalBytes = p.parts.sumOf { it.expectedSize }.coerceAtLeast(1)
                var doneBytes = 0L
                var lastUiUpdate = 0L
                val results = mutableListOf<PartResult>()
                for ((i, part) in p.parts.withIndex()) {
                    if (cancel.get()) break
                    val label = "Part ${i + 1}/${p.parts.size}: ${part.outputName}"
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
                val problems = p.parts.flatMap { it.clips }.mapNotNull { clip ->
                    val st = SafScanner.stat(app, Uri.parse(clip.uri))
                    if (st == null || st.first != clip.sizeBytes) "${clip.name}: changed or missing" else null
                }
                val report = RunReport(p, results, out.displayPath, problems.isEmpty(), problems)
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
                        cancel.get() -> "Cancelled - $passed of ${p.parts.size} part(s) finished"
                        passed == p.parts.size -> "Done - all ${p.parts.size} part(s) verified"
                        else -> "Finished with problems - $passed of ${p.parts.size} part(s) passed"
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
