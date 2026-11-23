package com.phong.dashcammerger.core

import java.time.DateTimeException
import java.time.Duration
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.format.DateTimeFormatter

/**
 * Time range selection (FR-3.7, port of core/timerange.py): merge only the clips recorded
 * between two times. The range is given to the minute and applied to whole clips, so the
 * merge stays a lossless stream copy: a clip is kept when any part of it lies inside the range.
 */
data class TimePoint(val time: LocalTime, val day: LocalDate? = null) {
    override fun toString(): String {
        val text = time.format(HM)
        return if (day != null) "$day $text" else text
    }

    private companion object {
        val HM: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm")
    }
}

/** The time range cannot be understood or applied to this footage. */
class TimeRangeError(message: String) : IllegalArgumentException(message)

object TimeRange {
    private val POINT = Regex(
        """^(?:(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})[ T]+)?(\d{1,2})[:.hH](\d{2})(?::(\d{2}))?$"""
    )
    const val FORMAT_HINT = "use HH:MM or YYYY-MM-DD HH:MM, e.g. 14:05 or 2026-11-02 14:05"
    private val FULL = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
    private val SHORT = DateTimeFormatter.ofPattern("HH:mm")

    /** "14:05", "2026-11-02 14:05" or "2026-11-02T14:05" -> TimePoint. */
    fun parse(text: String): TimePoint {
        val t = text.trim()
        val m = POINT.matchEntire(t) ?: throw TimeRangeError("not a time: '$t' ($FORMAT_HINT)")
        val g = m.groupValues
        return try {
            val clock = LocalTime.of(g[4].toInt(), g[5].toInt(), g[6].ifEmpty { "0" }.toInt())
            val on = if (g[1].isNotEmpty()) LocalDate.of(g[1].toInt(), g[2].toInt(), g[3].toInt()) else null
            TimePoint(clock, on)
        } catch (_: DateTimeException) {
            throw TimeRangeError("not a valid time: '$t' ($FORMAT_HINT)")
        }
    }

    /** An empty box is an open end (null); anything else must parse. */
    fun parseOptional(text: String): TimePoint? = text.trim().takeIf { it.isNotEmpty() }?.let(::parse)

    private fun onlyDay(days: Set<LocalDate>): LocalDate {
        if (days.isEmpty()) {
            throw TimeRangeError("the file names carry no timestamps, so a time range cannot be applied")
        }
        if (days.size > 1) {
            throw TimeRangeError(
                "the footage covers several days (${days.min()} to ${days.max()}); " +
                    "give the date too, e.g. 2026-11-02 14:05"
            )
        }
        return days.first()
    }

    /**
     * Turn the two ends into date-times; [days] are the dates the footage starts on.
     * An end without a date takes the other end's date, else the footage's only day.
     * A time-only end at or before the start means the next day (23:30 to 00:30).
     */
    fun resolve(start: TimePoint?, end: TimePoint?, days: Set<LocalDate>): Pair<LocalDateTime?, LocalDateTime?> {
        if (start == null && end == null) return null to null
        val anchor = start?.day ?: end?.day ?: onlyDay(days)
        var begin = start?.let { LocalDateTime.of(it.day ?: anchor, it.time) }
        var stop = end?.let { LocalDateTime.of(it.day ?: anchor, it.time) }
        if (begin != null && stop != null && stop <= begin) {
            when {
                end!!.day == null -> stop = stop.plusDays(1)
                start!!.day == null -> begin = begin.minusDays(1)
                else -> throw TimeRangeError("the time range ends ($end) before it starts ($start)")
            }
        }
        return begin to stop
    }

    /** True when any part of the clip lies inside the range (whole clips only). */
    fun overlaps(clip: ClipInfo, begin: LocalDateTime?, stop: LocalDateTime?): Boolean {
        val ts = clip.timestamp ?: return false
        val clipEnd = ts.plusNanos((clip.probe?.durationUs ?: 0L) * 1000)
        if (stop != null && ts >= stop) return false
        return begin == null || clipEnd > begin
    }

    /** Clip boundaries touch (or overlap by a few frames), so a trim window is pulled in by this. */
    private val SNAP_MARGIN: Duration = Duration.ofSeconds(1)
    private val SECONDS = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")
    private val SHORT_SECONDS = DateTimeFormatter.ofPattern("HH:mm:ss")

    /**
     * Trim window (FR-3.8) keeping the clips from the one starting at [firstStart] to the one
     * ending at [lastEnd]; a window on the exact boundaries would also catch the neighbours.
     */
    fun clipWindow(firstStart: LocalDateTime, lastEnd: LocalDateTime): Pair<LocalDateTime, LocalDateTime> {
        val begin = firstStart.plus(SNAP_MARGIN)
        val stop = lastEnd.minus(SNAP_MARGIN)
        if (stop > begin) return begin to stop
        // A clip of under two seconds: keep its middle.
        val middle = firstStart.plus(Duration.between(firstStart, lastEnd).dividedBy(2))
        return middle to middle.plusNanos(1000)
    }

    /** "trip 2: 2026-11-02 14:15:00 to 15:02:10" (FR-3.8). */
    fun describeTrim(trip: Int, begin: LocalDateTime, stop: LocalDateTime): String {
        val end = if (stop.toLocalDate() == begin.toLocalDate()) stop.format(SHORT_SECONDS) else stop.format(SECONDS)
        return "trip $trip: ${begin.format(SECONDS)} to $end"
    }

    /** "2026-11-02 14:05 to 14:30", "from 2026-11-02 14:05", "until 2026-11-02 14:30", "". */
    fun describe(begin: LocalDateTime?, stop: LocalDateTime?): String = when {
        begin != null && stop != null -> {
            val end = if (stop.toLocalDate() == begin.toLocalDate()) stop.format(SHORT) else stop.format(FULL)
            "${begin.format(FULL)} to $end"
        }
        begin != null -> "from ${begin.format(FULL)}"
        stop != null -> "until ${stop.format(FULL)}"
        else -> ""
    }
}
