package com.phong.dashcammerger.core

import java.time.format.DateTimeFormatter
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger

/**
 * Turn scanned clips into a concrete merge plan (port of core/planner.py).
 * Covers FR-2.4, FR-3.3-3.5, FR-4.3-4.5, FR-6.2, FR-6.3, FR-6.8.
 *
 * Android-specific I/O is injected so this stays unit-testable:
 *  - [probe] fills in ClipInfo.probe or ClipInfo.unreadableReason,
 *  - [nameExists] tells whether a file of that name already exists in the output folder.
 */
object Planner {
    private const val GIB = 1024.0 * 1024.0 * 1024.0
    private const val PROBE_WORKERS = 4
    private val TS_FMT = DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss") // date_time, easier to read (AR-6.3)

    /** FR-6.3: timestamp when known, otherwise the file stem. */
    private fun timestampToken(clip: ClipInfo): String = clip.timestamp?.format(TS_FMT) ?: clip.stem

    /** Probe every clip (FR-4.1 as amended on desktop: sampling misses corrupt clips). */
    private fun probeAll(clips: List<ClipInfo>, probe: (ClipInfo) -> Unit, onProgress: (Int, Int) -> Unit) {
        val pending = clips.filter { it.probe == null && !it.unreadable }
        if (pending.isEmpty()) return
        val done = AtomicInteger(0)
        val pool = Executors.newFixedThreadPool(minOf(PROBE_WORKERS, pending.size))
        try {
            pending.map { clip ->
                pool.submit {
                    probe(clip)
                    onProgress(done.incrementAndGet(), pending.size)
                }
            }.forEach { it.get() }
        } finally {
            pool.shutdown()
        }
    }

    /** Walk the ordered clips and start a new part whenever a rule fires. */
    fun splitGroup(group: CameraGroup, opt: PlanOptions): List<Pair<List<ClipInfo>, String>> {
        val maxBytes = if (opt.maxPartGb > 0) opt.maxPartGb * GIB else 0.0
        val gapAfter = group.gaps.associate { it.after to it.seconds }

        val parts = mutableListOf<Pair<List<ClipInfo>, String>>()
        var current = mutableListOf<ClipInfo>()
        var currentBytes = 0L
        var currentSig: StreamSignature? = null
        var reason = ""

        for (clip in group.clips) {
            var newReason = ""
            if (current.isNotEmpty()) {
                val sig = clip.probe?.signature
                newReason = when {
                    sig != null && currentSig != null && sig != currentSig -> "codec change" // FR-4.4
                    opt.splitOnGap && (gapAfter[clip] ?: 0.0) > opt.gapSplitSeconds -> "trip gap" // FR-3.5
                    maxBytes > 0 && currentBytes + clip.sizeBytes > maxBytes -> "max part size" // FR-6.8
                    else -> ""
                }
            }
            if (newReason.isNotEmpty()) {
                parts.add(current to reason)
                current = mutableListOf()
                currentBytes = 0
                currentSig = null
                reason = newReason
            }
            current.add(clip)
            currentBytes += clip.sizeBytes
            if (currentSig == null) currentSig = clip.probe?.signature
        }
        if (current.isNotEmpty()) parts.add(current to reason)
        return parts
    }

    fun buildPlan(
        clips: List<ClipInfo>,
        opt: PlanOptions,
        probe: (ClipInfo) -> Unit,
        nameExists: (String) -> Boolean,
        onProgress: (String, Int, Int) -> Unit = { _, _, _ -> },
    ): MergePlan {
        val warnings = mutableListOf<String>()
        val unreadable = mutableListOf<ClipInfo>()

        // 1. Group.
        val (groups, unclassified) = Cameras.groupByCamera(clips)

        // 2. Annotate and order.
        Ordering.annotate(clips)
        for (group in groups) {
            Ordering.orderGroup(group)
            when (group.orderSource) {
                OrderSource.Mtime ->
                    warnings.add("${group.camera}: order based on file modification time, please spot-check")
                OrderSource.Sequence ->
                    warnings.add("${group.camera}: order based on sequence number in filename")
                OrderSource.Timestamp -> Unit
            }
        }

        val parts = mutableListOf<OutputPart>()
        for (group in groups) {
            // 3. Probe.
            probeAll(group.clips, probe) { done, total -> onProgress("Reading ${group.camera} clips", done, total) }

            // 4. Unreadable files (FR-4.5) and audio the MP4 muxer cannot carry.
            val bad = group.clips.filter { it.unreadable }
            if (bad.isNotEmpty()) {
                unreadable.addAll(bad)
                group.clips = group.clips.filter { !it.unreadable }.toMutableList()
                for (clip in bad) {
                    warnings.add("${group.camera}: skipped unreadable file ${clip.name} (${clip.unreadableReason})")
                }
            }
            group.clips.firstNotNullOfOrNull { it.probe?.audioDropped }?.let {
                warnings.add("${group.camera}: audio not copied ($it cannot be stored in MP4), video only")
            }
            if (group.clips.isEmpty()) {
                warnings.add("${group.camera}: no readable clips left, nothing to merge")
                continue
            }

            // 5. Gaps (FR-3.4).
            Ordering.detectGaps(group, opt.gapWarningSeconds)
            for (gap in group.gaps) {
                warnings.add(
                    "${group.camera}: gap of ${"%.1f".format(gap.seconds / 60)} min between " +
                        "${gap.before.name} and ${gap.after.name}"
                )
            }

            // 6. Split into parts.
            val chunks = splitGroup(group, opt)
            chunks.forEachIndexed { i, (chunkClips, reason) ->
                val index = i + 1
                parts.add(
                    OutputPart(
                        camera = group.camera,
                        partIndex = index,
                        partCount = chunks.size,
                        clips = chunkClips,
                        splitReason = reason,
                        expectedDurationUs = chunkClips.sumOf { it.probe?.durationUs ?: 0L },
                        expectedSize = chunkClips.sumOf { it.sizeBytes },
                    )
                )
                if (reason.isNotEmpty()) {
                    warnings.add("${group.camera} part $index: new part started because of $reason")
                }
            }
        }

        // 7. Name outputs (FR-6.3), never overwriting an existing file (FR-6.2).
        val taken = mutableSetOf<String>()
        for (part in parts) {
            var stem = "${part.camera}_${timestampToken(part.clips.first())}_to_${timestampToken(part.clips.last())}"
            if (part.partCount > 1) stem += "_part${part.partIndex}"
            part.outputName = uniqueName(stem, ".mp4") { it in taken || nameExists(it) }
            taken.add(part.outputName)
        }

        return MergePlan(groups, parts, unclassified, unreadable, warnings)
    }

    /** First free name: stem.ext, then stem_2.ext, stem_3.ext, ... (FR-6.2). */
    fun uniqueName(stem: String, ext: String, exists: (String) -> Boolean): String {
        var candidate = stem + ext
        var n = 2
        while (exists(candidate)) {
            candidate = "${stem}_$n$ext"
            n++
        }
        return candidate
    }
}
