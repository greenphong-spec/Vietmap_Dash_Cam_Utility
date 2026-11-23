package com.phong.dashcammerger.core

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.ceil

/** AR-14.1: what the UI and the notification show; immutable, published by the worker. */
data class ProgressSnapshot(
    val startMs: Long,
    val doneBytes: Long,
    val totalBytes: Long,
    /** Wall-clock time of the last copied byte (AR-14.4). */
    val lastByteMs: Long,
    /** Bytes per second, or null while unknown (AR-14.2). */
    val speedBps: Double?,
    val partIndex: Int,
    val partCount: Int,
    val partDoneBytes: Long,
    val partTotalBytes: Long,
    val clipIndex: Int,
    val clipCount: Int,
    val finishing: Boolean = false,
) {
    /** AR-14.5: floor, capped at 99 until the run is over. */
    val percent: Int
        get() = if (totalBytes <= 0) 0 else (doneBytes * 100 / totalBytes).toInt().coerceIn(0, 99)

    val partPercent: Int
        get() = if (partTotalBytes <= 0) 0 else (partDoneBytes * 100 / partTotalBytes).toInt().coerceIn(0, 100)

    fun stalledMs(nowMs: Long): Long = nowMs - lastByteMs

    fun isStalled(nowMs: Long): Boolean = stalledMs(nowMs) >= Progress.STALL_MS

    /** AR-14.3: null while the speed is unknown or the run is stalled. */
    fun remainingMs(nowMs: Long): Long? {
        val speed = speedBps ?: return null
        if (speed <= 0 || isStalled(nowMs)) return null
        return ((totalBytes - doneBytes).coerceAtLeast(0) / speed * 1000).toLong()
    }
}

/**
 * AR-14.1/14.2: counts copied bytes for one merge run and measures the speed over a sliding
 * window. Used from the worker thread only; the UI sees [snapshot]s. Times are wall-clock ms
 * passed in by the caller, so it is testable without a clock.
 */
class ProgressTracker(
    private val totalBytes: Long,
    private val startMs: Long,
    private val windowMs: Long = Progress.WINDOW_MS,
) {
    var doneBytes = 0L; private set
    private var lastByteMs = startMs
    private var completedBytes = 0L // Σ expectedSize of finished parts
    private val samples = ArrayDeque<Pair<Long, Long>>().apply { add(startMs to 0L) }

    private var partIndex = 0
    private var partCount = 0
    private var partStart = 0L
    private var partTotal = 0L
    private var clipIndex = 0
    private var clipCount = 0
    private var finishing = false

    fun startPart(index: Int, count: Int, expectedBytes: Long) {
        partIndex = index
        partCount = count
        partStart = doneBytes
        partTotal = expectedBytes
        clipIndex = 0
        clipCount = 0
        finishing = false
    }

    fun clip(index: Int, count: Int) {
        clipIndex = index
        clipCount = count
    }

    fun finishing() {
        finishing = true
    }

    fun add(bytes: Long, nowMs: Long) {
        if (bytes <= 0) return
        doneBytes += bytes
        lastByteMs = nowMs
        if (nowMs - samples.last().first >= 1000) samples.addLast(nowMs to doneBytes)
        // Keep exactly one sample at least windowMs old as the window's start.
        while (samples.size > 1 && nowMs - samples[1].first >= windowMs) samples.removeFirst()
    }

    /** AR-14.1: a finished part counts as its full expected size, whatever was copied. */
    fun endPart(expectedBytes: Long) {
        completedBytes += expectedBytes
        doneBytes = maxOf(doneBytes, completedBytes)
        finishing = false
    }

    fun speedBps(nowMs: Long): Double? {
        if (nowMs - startMs < Progress.MIN_ELAPSED_MS || doneBytes < Progress.MIN_BYTES) return null
        val (t0, b0) = samples.first()
        val dt = nowMs - t0
        if (dt <= 0) return null
        return (doneBytes - b0) * 1000.0 / dt
    }

    fun snapshot(nowMs: Long) = ProgressSnapshot(
        startMs = startMs,
        doneBytes = doneBytes,
        totalBytes = totalBytes,
        lastByteMs = lastByteMs,
        speedBps = speedBps(nowMs),
        partIndex = partIndex,
        partCount = partCount,
        partDoneBytes = (doneBytes - partStart).coerceIn(0, maxOf(partTotal, 0)),
        partTotalBytes = partTotal,
        clipIndex = clipIndex,
        clipCount = clipCount,
        finishing = finishing,
    )
}

/** AR-14.6: state of each part of the run, for the Details tab. */
enum class PartRunState { WAITING, RUNNING, PASS, FAIL, CANCELLED }

/** AR-14.2-14.8 texts. */
object Progress {
    const val WINDOW_MS = 30_000L
    const val MIN_ELAPSED_MS = 5_000L
    const val MIN_BYTES = 1024L * 1024L
    const val STALL_MS = 15_000L

    private const val MIB = 1024.0 * 1024.0
    private val CLOCK = DateTimeFormatter.ofPattern("HH:mm")
    private val DAY_TIME = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")
    private val TIME = DateTimeFormatter.ofPattern("HH:mm:ss")

    /** Elapsed time: `45 s`, `6 min 12 s`, `1 h 05 min`. */
    fun elapsed(ms: Long): String {
        val s = maxOf(0L, ms) / 1000
        return when {
            s < 60 -> "$s s"
            s < 3600 -> String.format(Locale.US, "%d min %02d s", s / 60, s % 60)
            else -> String.format(Locale.US, "%d h %02d min", s / 3600, (s % 3600) / 60)
        }
    }

    /** AR-14.3: `< 1 min`, `N min` (rounded up), `H h MM min`. */
    fun remaining(ms: Long): String {
        if (ms < 60_000) return "< 1 min"
        val min = ceil(ms / 60_000.0).toLong()
        return if (min < 60) "$min min" else String.format(Locale.US, "%d h %02d min", min / 60, min % 60)
    }

    fun speed(bps: Double): String = String.format(Locale.US, "%.1f MB/s", bps / MIB)

    /** AR-14.3: `21:47`, or `tomorrow 01:10` when it falls on the next day. */
    fun finishClock(finishMs: Long, nowMs: Long, zone: ZoneId = ZoneId.systemDefault()): String {
        val finish = Instant.ofEpochMilli(finishMs).atZone(zone)
        val today: LocalDate = Instant.ofEpochMilli(nowMs).atZone(zone).toLocalDate()
        val clock = finish.format(CLOCK)
        return when (finish.toLocalDate()) {
            today -> clock
            today.plusDays(1) -> "tomorrow $clock"
            else -> finish.format(DateTimeFormatter.ofPattern("MM-dd HH:mm"))
        }
    }

    private fun amounts(p: ProgressSnapshot) =
        "${p.percent} % · ${Report.formatSize(p.doneBytes)} / ${Report.formatSize(p.totalBytes)}"

    /** AR-14.5 line 2: `43 % · 2.10 / 4.90 GB · 9.2 MB/s · 5 min left (done ≈ 21:47)`. */
    fun line(p: ProgressSnapshot, nowMs: Long, zone: ZoneId = ZoneId.systemDefault()): String {
        if (p.isStalled(nowMs)) return "${amounts(p)} · no progress for ${elapsed(p.stalledMs(nowMs))}"
        val left = p.remainingMs(nowMs) ?: return "${amounts(p)} · estimating time left…"
        return "${amounts(p)} · ${speed(p.speedBps!!)} · ${remaining(left)} left " +
            "(done ≈ ${finishClock(nowMs + left, nowMs, zone)})"
    }

    /** AR-14.5 line 4: `Elapsed 6 min 12 s · this file 61 % · clip 3/20`. */
    fun detail(p: ProgressSnapshot, nowMs: Long): String = buildString {
        append("Elapsed ${elapsed(nowMs - p.startMs)}")
        if (p.partCount > 1) append(" · this file ${p.partPercent} %")
        if (p.clipCount > 0) append(" · clip ${p.clipIndex}/${p.clipCount}")
    }

    /** AR-14.7 notification title. */
    fun notificationTitle(p: ProgressSnapshot, nowMs: Long): String {
        val left = p.remainingMs(nowMs)
        return if (left == null) "Merging ${p.percent} %" else "Merging ${p.percent} % · ${remaining(left)} left"
    }

    /** AR-14.7 notification text. */
    fun notificationText(p: ProgressSnapshot, nowMs: Long, zone: ZoneId = ZoneId.systemDefault()): String {
        val head = "Part ${p.partIndex}/${p.partCount} · " +
            "${Report.formatSize(p.doneBytes)} / ${Report.formatSize(p.totalBytes)}"
        if (p.isStalled(nowMs)) return "$head · no progress for ${elapsed(p.stalledMs(nowMs))}"
        val left = p.remainingMs(nowMs) ?: return head
        return "$head · done ≈ ${finishClock(nowMs + left, nowMs, zone)}"
    }

    /** AR-14.8: final report line. */
    fun reportTimeLine(startMs: Long, endMs: Long, bytes: Long, zone: ZoneId = ZoneId.systemDefault()): String {
        val start = Instant.ofEpochMilli(startMs).atZone(zone)
        val end = Instant.ofEpochMilli(endMs).atZone(zone)
        val endText = if (end.toLocalDate() == start.toLocalDate()) end.format(TIME) else end.format(DAY_TIME)
        val secs = (endMs - startMs) / 1000.0
        val avg = if (secs > 0) " at ${speed(bytes / secs)} average" else ""
        return "Time: ${start.format(DAY_TIME)} - $endText (${elapsed(endMs - startMs)}), " +
            "${Report.formatSize(bytes)}$avg"
    }
}
