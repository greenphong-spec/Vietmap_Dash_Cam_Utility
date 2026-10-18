package com.phong.dashcammerger.core

import java.time.DateTimeException
import java.time.Duration
import java.time.LocalDateTime
import java.util.regex.Matcher
import java.util.regex.Pattern

/** Chronological ordering of clips (port of core/ordering.py) - FR-3. */
object Ordering {
    private val TS14 = Pattern.compile("(?<!\\d)(\\d{4})(\\d{2})(\\d{2})(\\d{2})(\\d{2})(\\d{2})(?!\\d)")
    private val TS_SEP = Pattern.compile(
        "(?<!\\d)(\\d{4})[-_]?(\\d{2})[-_]?(\\d{2})[-_T ](\\d{2})[-_:.]?(\\d{2})[-_:.]?(\\d{2})(?!\\d)"
    )
    private val SEQ = Pattern.compile("(?<!\\d)(\\d{3,})(?!\\d)")

    // No clip length is assumed (FR-3.4a): dashcams record 1-, 3- or 5-minute clips, the
    // setting can change between drives, and the last clip of a drive is shorter.

    private fun Matcher.int(group: Int): Int = group(group)!!.toInt()

    private fun toDateTime(m: Matcher): LocalDateTime? = try {
        LocalDateTime.of(m.int(1), m.int(2), m.int(3), m.int(4), m.int(5), m.int(6))
    } catch (_: DateTimeException) { // e.g. 20261399235959
        null
    }

    /** Parse a bare 14-digit YYYYMMDDHHMMSS token (FR-3.1 a). */
    fun parseTimestamp14(text: String): LocalDateTime? {
        val m = TS14.matcher(text)
        return if (m.find()) toDateTime(m) else null
    }

    /** First valid timestamp in a file stem and its span; 14-digit form first, then separated forms. */
    fun parseTimestampSpan(name: String): Pair<LocalDateTime?, IntRange?> {
        for (pattern in listOf(TS14, TS_SEP)) {
            val m = pattern.matcher(name)
            while (m.find()) {
                val stamp = toDateTime(m)
                if (stamp != null) return stamp to (m.start() until m.end())
            }
        }
        return null to null
    }

    fun parseTimestamp(name: String): LocalDateTime? = parseTimestampSpan(name).first

    /** Last 3+ digit run that does not overlap the timestamp (FR-3.1 c). */
    fun parseSequence(name: String, timestampSpan: IntRange?): Long? {
        var found: Long? = null
        val m = SEQ.matcher(name)
        while (m.find()) {
            if (timestampSpan != null) {
                val start = m.start()
                val end = m.end()
                if (start < timestampSpan.last + 1 && timestampSpan.first < end) continue
            }
            found = m.group(1)!!.toLongOrNull() ?: continue
        }
        return found
    }

    /** Fill in timestamp and sequence for every clip that lacks them. */
    fun annotate(clips: List<ClipInfo>) {
        for (clip in clips) {
            val (stamp, span) = parseTimestampSpan(clip.stem)
            if (clip.timestamp == null) clip.timestamp = stamp
            if (clip.sequence == null) clip.sequence = parseSequence(clip.stem, span)
        }
    }

    /** Sort a group in place and record which key was used (FR-3.1, FR-3.2). */
    fun orderGroup(group: CameraGroup) {
        val clips = group.clips
        when {
            clips.all { it.timestamp != null } -> {
                group.orderSource = OrderSource.Timestamp
                clips.sortWith(compareBy<ClipInfo>({ it.timestamp }, { it.sequence ?: 0L }, { it.name.lowercase() }))
            }
            clips.all { it.sequence != null } -> {
                group.orderSource = OrderSource.Sequence
                clips.sortWith(compareBy<ClipInfo>({ it.sequence }, { it.name.lowercase() }))
            }
            else -> {
                group.orderSource = OrderSource.Mtime // FR-3.3 warning is added by the planner
                clips.sortWith(compareBy<ClipInfo>({ it.mtimeMs }, { it.name.lowercase() }))
            }
        }
    }

    /**
     * End of [before] (start + probed duration) to start of [after], in seconds; null when a
     * time or the duration is unknown. Never start-to-start: 5-min clips are 300-302 s apart.
     */
    fun gapSeconds(before: ClipInfo, after: ClipInfo): Double? {
        val t0 = before.timestamp ?: return null
        val t1 = after.timestamp ?: return null
        val us = before.probe?.durationUs ?: return null
        return Duration.between(t0, t1).seconds - us / 1e6
    }

    /** Record recording gaps between consecutive clips (FR-3.4); only meaningful for timestamp order. */
    fun detectGaps(group: CameraGroup, warnSeconds: Int) {
        if (group.orderSource != OrderSource.Timestamp) {
            group.gaps = emptyList()
            return
        }
        val gaps = mutableListOf<Gap>()
        for ((before, after) in group.clips.zipWithNext()) {
            val gap = gapSeconds(before, after) ?: continue
            if (gap > warnSeconds) gaps.add(Gap(before, after, gap))
        }
        group.gaps = gaps
    }
}
