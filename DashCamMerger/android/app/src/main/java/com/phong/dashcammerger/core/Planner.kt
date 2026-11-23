package com.phong.dashcammerger.core

import java.time.LocalDateTime
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

    /** Walk the ordered clips (default: the whole group); a rule that fires starts a new part. */
    fun splitGroup(
        group: CameraGroup,
        opt: PlanOptions,
        clips: List<ClipInfo> = group.clips,
    ): List<Pair<List<ClipInfo>, String>> {
        val maxBytes = if (opt.maxPartGb > 0) opt.maxPartGb * GIB else 0.0
        // FR-3.5: computed here, not from group.gaps, so the trip gap is independent of the
        // gap-warning threshold; only meaningful when the clips are ordered by time.
        val splitOnGap = opt.splitOnGap && group.orderSource == OrderSource.Timestamp

        val parts = mutableListOf<Pair<List<ClipInfo>, String>>()
        var current = mutableListOf<ClipInfo>()
        var currentBytes = 0L
        var currentSig: StreamSignature? = null
        var reason = ""
        var previous: ClipInfo? = null

        for (clip in clips) {
            var newReason = ""
            if (current.isNotEmpty()) {
                val sig = clip.probe?.signature
                val gap = previous?.let { Ordering.gapSeconds(it, clip) }
                newReason = when {
                    sig != null && currentSig != null && sig != currentSig -> "codec change" // FR-4.4
                    splitOnGap && gap != null && gap > opt.gapSplitSeconds -> "trip gap" // FR-3.5
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
            previous = clip
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

        // 2b. Time range (FR-3.7), resolved before probing so a bad range fails fast.
        val days = groups.flatMap { g -> g.clips.mapNotNull { it.timestamp?.toLocalDate() } }.toSet()
        val (begin, stop) = TimeRange.resolve(opt.timeFrom, opt.timeTo, days)
        val ranged = begin != null || stop != null
        val trimmedSpans = sortedMapOf<Int, MutableList<ClipInfo>>() // FR-3.8

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

            // 5. Gaps (FR-3.4) and 6. split into parts. A gap that starts a new trip is shown
            // by the trip list itself, so only stops inside a trip are warned about (FR-3.5e).
            Ordering.detectGaps(group, opt.gapWarningSeconds)
            var chunks = splitGroup(group, opt)
            // Trip numbers always come from the whole card (FR-3.6c), also inside a range.
            val wholeTrips = numberTrips(chunks.map { it.second })
            val tripOf = HashMap<ClipInfo, Int>()
            chunks.forEachIndexed { i, (chunkClips, _) -> chunkClips.forEach { tripOf[it] = wholeTrips[i][0] } }
            val cardTrips = wholeTrips.lastOrNull()?.get(1) ?: 1
            // Selection keys come from the untrimmed parts, so trimming keeps them (FR-3.6).
            val keyOf = HashMap<ClipInfo, String>()
            for ((chunkClips, _) in chunks) chunkClips.forEach { keyOf[it] = "${group.camera}|${chunkClips.first().name}" }
            var kept: Set<ClipInfo> = group.clips.toSet()
            val tripNumbers = tripOf.values.toSet()
            var trims = opt.tripRanges.filterKeys { it in tripNumbers }
            if (trims.isNotEmpty() && group.orderSource != OrderSource.Timestamp) {
                warnings.add(
                    "${group.camera}: no timestamps in the file names, the trip trim " +
                        "cannot be applied - trip kept whole"
                )
                trims = emptyMap()
            }
            if (ranged || trims.isNotEmpty()) {
                // FR-3.7 / FR-3.8: keep whole clips that overlap the range and their trip's
                // trim window, then split those.
                if (ranged && group.orderSource != OrderSource.Timestamp) {
                    warnings.add(
                        "${group.camera}: no timestamps in the file names, the time range " +
                            "cannot be applied - camera left out"
                    )
                    continue
                }
                val inRange = group.clips.filter { clip ->
                    (!ranged || TimeRange.overlaps(clip, begin, stop)) &&
                        (trims[tripOf.getValue(clip)]?.let { TimeRange.overlaps(clip, it.first, it.second) } ?: true)
                }
                for (trip in trims.keys.sorted()) {
                    val inTrip = inRange.filter { tripOf.getValue(it) == trip }
                    if (inTrip.isEmpty()) warnings.add("${group.camera} trip $trip: no clips in the trimmed time")
                    trimmedSpans.getOrPut(trip) { mutableListOf() }.addAll(inTrip)
                }
                if (inRange.isEmpty()) {
                    if (ranged) warnings.add("${group.camera}: no clips in the time range")
                    continue
                }
                kept = inRange.toSet()
                chunks = splitGroup(group, opt, inRange)
            }
            val tripStarts = chunks.filter { it.second == "trip gap" }.map { it.first.first() }.toSet()
            for (gap in group.gaps) {
                if (gap.after in tripStarts || gap.before !in kept || gap.after !in kept) continue
                warnings.add(
                    "${group.camera}: gap of ${"%.1f".format(gap.seconds / 60)} min between " +
                        "${gap.before.name} and ${gap.after.name}"
                )
            }
            val trips = numberTrips(chunks.map { tripOf.getValue(it.first.first()) }, cardTrips)
            chunks.forEachIndexed { i, (chunkClips, reason) ->
                val index = i + 1
                val (trip, tripCount, tripPart, tripParts) = trips[i]
                parts.add(
                    OutputPart(
                        camera = group.camera,
                        partIndex = index,
                        partCount = chunks.size,
                        clips = chunkClips,
                        splitReason = reason,
                        expectedDurationUs = chunkClips.sumOf { it.probe?.durationUs ?: 0L },
                        expectedSize = chunkClips.sumOf { it.sizeBytes },
                        tripIndex = trip,
                        tripCount = tripCount,
                        tripPart = tripPart,
                        tripParts = tripParts,
                        key = keyOf.getValue(chunkClips.first()),
                    )
                )
                if (reason.isNotEmpty() && reason != "trip gap") {
                    warnings.add("${group.camera} part $index: new part started because of $reason")
                }
            }
        }

        // 7. Name outputs (FR-6.3), never overwriting an existing file (FR-6.2).
        val taken = mutableSetOf<String>()
        for (part in parts) {
            var stem = "${part.camera}_${timestampToken(part.clips.first())}_to_${timestampToken(part.clips.last())}"
            if (part.tripCount > 1) stem += "_trip${part.tripIndex}"
            if (part.tripParts > 1) stem += "_part${part.tripPart}"
            part.outputName = uniqueName(stem, ".mp4") { it in taken || nameExists(it) }
            taken.add(part.outputName)
        }

        val trims = trimmedSpans.filterValues { it.isNotEmpty() }.map { (trip, kept) ->
            clipsSpan(kept)?.let { (a, b) -> TimeRange.describeTrim(trip, a, b) } ?: "trip $trip"
        }
        return MergePlan(groups, parts, unclassified, unreadable, warnings, TimeRange.describe(begin, stop), trims)
    }

    /** Stable id of an output part across a trim (FR-3.6): camera + first clip of the untrimmed part. */
    fun partKey(part: OutputPart): String = part.key.ifEmpty { "${part.camera}|${part.clips.first().name}" }

    /** Recording start of the earliest clip and end of the latest one; null without timestamps. */
    fun clipsSpan(clips: List<ClipInfo>): Pair<LocalDateTime, LocalDateTime>? {
        val stamped = clips.filter { it.timestamp != null }
        if (stamped.isEmpty()) return null
        return stamped.minOf { it.timestamp!! } to stamped.maxOf { clipEnd(it) }
    }

    /** Start plus probed length (no length assumed when unknown, FR-3.4a). */
    fun clipEnd(clip: ClipInfo): LocalDateTime = clip.timestamp!!.plusNanos((clip.probe?.durationUs ?: 0L) * 1000)

    /** (trip, trips, part in trip, parts in trip) per chunk; a `trip gap` reason starts a trip (FR-3.5). */
    fun numberTrips(reasons: List<String>): List<List<Int>> {
        var trip = 0
        val tripOf = reasons.mapIndexed { i, reason ->
            if (i == 0 || reason == "trip gap") trip++
            trip
        }
        return numberTrips(tripOf, trip)
    }

    /** Same, given each chunk's trip number and the whole card's trip count (FR-3.6c / FR-3.7d). */
    fun numberTrips(tripOf: List<Int>, tripCount: Int): List<List<Int>> {
        val counts = tripOf.groupingBy { it }.eachCount()
        val seen = mutableMapOf<Int, Int>()
        return tripOf.map { t ->
            val n = (seen[t] ?: 0) + 1
            seen[t] = n
            listOf(t, tripCount, n, counts.getValue(t))
        }
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
