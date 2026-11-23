package com.phong.dashcammerger.core

import java.time.Duration
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/**
 * Trip trim (FR-3.8), the logic of the trim dialog (port of gui.TrimDialog without the
 * widgets): a Start and an End position over one trip, in seconds from the trip start,
 * that snap to clip boundaries, because whole clips are merged (lossless). What they show
 * is exactly what the merge keeps.
 *
 * [parts] are all cameras' scanned (untrimmed) parts of the trip; [current] is the trip's
 * trim so far as (first kept clip start, last kept clip end), or null for the whole trip.
 */
class TrimModel(val parts: List<OutputPart>, current: Pair<LocalDateTime, LocalDateTime>?) {
    private val clips = parts.flatMap { it.clips }.filter { it.timestamp != null }
    val t0: LocalDateTime
    /** Seconds from the trip start to its end (at least 1). */
    val total: Double
    /** Snap points of the Start and of the End slider, ascending. */
    val starts: List<Double>
    val ends: List<Double>
    var start: Double; private set
    var end: Double; private set

    init {
        require(clips.isNotEmpty()) { "no timestamps in the file names, so the trip cannot be trimmed" }
        val (first, last) = Planner.clipsSpan(clips)!!
        t0 = first
        total = maxOf(1.0, offset(last))
        starts = snapPoints(clips.map { offset(it.timestamp!!) }, last = true)
        ends = snapPoints(clips.map { offset(Planner.clipEnd(it)) }, last = false)
        start = starts.first()
        end = ends.last()
        if (current != null) {
            start = snap(starts, offset(current.first))
            end = snap(ends, offset(current.second))
        }
    }

    val cameras: List<Camera> get() = parts.map { it.camera }.distinct()

    fun offset(moment: LocalDateTime): Double = Duration.between(t0, moment).toNanos() / 1e9

    fun clock(offset: Double): String = t0.plusNanos((offset * 1e9).toLong()).format(CLOCK)

    /** Nearest start before the end (never past it). */
    fun setStart(value: Double) {
        start = snap(starts.filter { it < end }.ifEmpty { starts.take(1) }, value)
    }

    /** Nearest end after the start (never before it). */
    fun setEnd(value: Double) {
        end = snap(ends.filter { it > start }.ifEmpty { ends.takeLast(1) }, value)
    }

    /** Move the start (or end) by one clip; [direction] -1 or +1. */
    fun step(startSlider: Boolean, direction: Int) {
        val points = if (startSlider) starts else ends
        val index = points.indexOf(if (startSlider) start else end) + direction
        if (index !in points.indices) return
        if (startSlider) setStart(points[index]) else setEnd(points[index])
    }

    fun whole() {
        start = starts.first()
        end = ends.last()
    }

    val isWhole: Boolean get() = start <= starts.first() && end >= ends.last()

    /** First kept clip start and last kept clip end, as set by the sliders. */
    fun bounds(): Pair<LocalDateTime, LocalDateTime> =
        t0.plusNanos((start * 1e9).toLong()) to t0.plusNanos((end * 1e9).toLong())

    /** What OK stores: the bounds, or null when the whole trip is kept. */
    fun result(): Pair<LocalDateTime, LocalDateTime>? = if (isWhole) null else bounds()

    fun isKept(clip: ClipInfo): Boolean {
        val (begin, stop) = TimeRange.clipWindow(bounds().first, bounds().second)
        return TimeRange.overlaps(clip, begin, stop)
    }

    fun kept(part: OutputPart): List<ClipInfo> = part.clips.filter(::isKept)

    val startText: String get() = "${clock(start)}   +${offsetText(start)} min"
    val endText: String get() = "${clock(end)}   +${offsetText(end)} min of ${offsetText(total)}"

    /** "Keeps 47:58 min, cuts 30:04 min (Front 16/26 files, Rear 16/26 files)". */
    val summary: String
        get() {
            val keep = end - start
            val counts = parts.joinToString(", ") { "${it.camera} ${kept(it).size}/${it.clips.size} files" }
            return "Keeps ${offsetText(keep)} min, cuts ${offsetText(total - keep)} min ($counts)"
        }

    /** "Trip 2/10 (Front, Rear): 2026-11-02 14:03:00 to 2026-11-02 15:21:02 - 78:02 min". */
    fun heading(title: String): String =
        "${title.replaceFirstChar { it.uppercase() }} (${cameras.joinToString(", ")}): " +
            "${clock(0.0)} to ${clock(total)} - ${offsetText(total)} min"

    companion object {
        /** Seconds; Front and Rear clips start a second or two apart. */
        const val SNAP_TOLERANCE = 5.0
        private val CLOCK = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")

        /** 750.0 -> "12:30" (minutes:seconds from the trip start). */
        fun offsetText(seconds: Double): String {
            val total = Math.round(maxOf(0.0, seconds))
            return "${total / 60}:${"%02d".format(total % 60)}"
        }

        /**
         * Clip boundaries the sliders snap to, one per boundary shared by the cameras.
         * Boundaries closer than SNAP_TOLERANCE are one point: for starts the latest of them
         * (so no camera keeps the clip before), for ends the earliest (so none keeps the next).
         */
        fun snapPoints(offsets: List<Double>, last: Boolean): List<Double> {
            val groups = mutableListOf<MutableList<Double>>()
            for (value in offsets.sorted()) {
                if (groups.isNotEmpty() && value - groups.last().first() <= SNAP_TOLERANCE) {
                    groups.last().add(value)
                } else {
                    groups.add(mutableListOf(value))
                }
            }
            return groups.map { if (last) it.last() else it.first() }
        }

        private fun snap(points: List<Double>, value: Double): Double = points.minBy { Math.abs(it - value) }
    }
}
