package com.phong.dashcammerger.core

import java.time.LocalDateTime

/**
 * Export order and Front/Rear pairing for the "Files to export" list (AR-10.14-10.17).
 * Works on indices into MergePlan.parts so the UI selection stays index based.
 */
object Arrange {
    /** First clip's timestamp, or null when the naming carries no time. */
    fun start(part: OutputPart): LocalDateTime? = part.clips.first().timestamp

    /** Last clip's timestamp plus its probed duration (60 s if unknown). */
    fun end(part: OutputPart): LocalDateTime? {
        val last = part.clips.last()
        val ts = last.timestamp ?: return null
        val us = last.probe?.durationUs?.takeIf { it > 0 } ?: 60_000_000L
        return ts.plusNanos(us * 1000)
    }

    /** Plan order: grouped by camera (Front, Rear, Interior), parts in time order. */
    fun byCamera(parts: List<OutputPart>): List<Int> = parts.indices.toList()

    /**
     * Trip order: by start time, cameras in CAMERA_ORDER for the same start, so the
     * Front and Rear file of one trip sit next to each other. Parts without a timestamp
     * go last, by part number then camera.
     */
    fun byTime(parts: List<OutputPart>): List<Int> = parts.indices.sortedWith(
        compareBy<Int>({ start(parts[it]) == null }, { start(parts[it]) }, { parts[it].partIndex })
            .thenBy { CAMERA_ORDER.indexOf(parts[it].camera) }
    )

    /**
     * For each part, the parts of *other* cameras recorded at the same time:
     * their [start, end) ranges overlap. When neither part has timestamps,
     * parts with the same part number are paired instead.
     */
    fun partners(parts: List<OutputPart>): List<Set<Int>> = parts.indices.map { i ->
        val a = parts[i]
        parts.indices.filter { j ->
            val b = parts[j]
            j != i && b.camera != a.camera && overlaps(a, b)
        }.toSet()
    }

    private fun overlaps(a: OutputPart, b: OutputPart): Boolean {
        val (sa, ea, sb, eb) = listOf(start(a), end(a), start(b), end(b))
        if (sa == null && sb == null) return a.partIndex == b.partIndex
        if (sa == null || ea == null || sb == null || eb == null) return false
        return sa < eb && sb < ea
    }

    /** Move order[pos] one place up (-1) or down (+1); out-of-range moves are ignored. */
    fun move(order: List<Int>, pos: Int, delta: Int): List<Int> {
        val to = pos + delta
        if (pos !in order.indices || to !in order.indices) return order
        return order.toMutableList().also { it[pos] = order[to]; it[to] = order[pos] }
    }
}
