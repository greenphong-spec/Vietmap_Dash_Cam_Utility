package com.phong.dashcammerger.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime

/** FR-3.7 time range (port of tests/test_timerange.py plus planner cases). */
class TimeRangeTest {
    private val day = LocalDate.of(2026, 11, 2)
    private fun at(h: Int, m: Int, d: LocalDate? = null) = TimePoint(LocalTime.of(h, m), d)
    private fun dt(h: Int, m: Int, dayOfMonth: Int = 2) = LocalDateTime.of(2026, 11, dayOfMonth, h, m)

    @Test
    fun parsesTimePoints() {
        assertEquals(at(14, 5), TimeRange.parse("14:05"))
        assertEquals(at(7, 30), TimeRange.parse(" 7:30 "))
        assertEquals(at(14, 5), TimeRange.parse("14h05"))
        assertEquals(TimePoint(LocalTime.of(14, 5, 30)), TimeRange.parse("14:05:30"))
        assertEquals(at(14, 5, day), TimeRange.parse("2026-11-02 14:05"))
        assertEquals(at(14, 5, day), TimeRange.parse("2026-11-02T14:05"))
        assertEquals(at(9, 0, day), TimeRange.parse("2026/11/2 9:00"))
        assertEquals(null, TimeRange.parseOptional("  "))
    }

    @Test
    fun rejectsBadText() {
        for (text in listOf("", "14", "25:00", "14:60", "2026-13-01 10:00", "noon", "2026-11-02")) {
            assertThrows(text, TimeRangeError::class.java) { TimeRange.parse(text) }
        }
    }

    @Test
    fun resolvesAgainstTheFootage() {
        assertEquals(null to null, TimeRange.resolve(null, null, setOf(day, day.plusDays(1))))
        assertEquals(dt(14, 0) to dt(14, 30), TimeRange.resolve(at(14, 0), at(14, 30), setOf(day)))
        val e = assertThrows(TimeRangeError::class.java) { TimeRange.resolve(at(14, 0), null, setOf(day, day.plusDays(1))) }
        assertTrue(e.message!!.contains("several days"))
        val none = assertThrows(TimeRangeError::class.java) { TimeRange.resolve(null, at(14, 0), emptySet()) }
        assertTrue(none.message!!.contains("no timestamps"))
        // One date is enough on a multi-day card.
        val days = setOf(day, day.plusDays(1))
        assertEquals(dt(14, 0) to dt(14, 30), TimeRange.resolve(at(14, 0, day), at(14, 30), days))
        assertEquals(dt(14, 0) to dt(14, 30), TimeRange.resolve(at(14, 0), at(14, 30, day), days))
        // A time-only end before the start is the next day; two dated ends must be in order.
        assertEquals(dt(0, 30, 3), TimeRange.resolve(at(23, 30, day), at(0, 30), setOf(day)).second)
        val back = assertThrows(TimeRangeError::class.java) { TimeRange.resolve(at(15, 0, day), at(14, 0, day), setOf(day)) }
        assertTrue(back.message!!.contains("before it starts"))
    }

    private fun clipAt(h: Int, m: Int, seconds: Long = 180) =
        ClipInfo("content://x/a", "a.mp4", "Movie", 1, 0).also {
            it.timestamp = dt(h, m)
            it.probe = ProbeInfo(seconds * 1_000_000, StreamSignature("v", "a"), "H.264")
        }

    @Test
    fun overlapKeepsWholeClipsTouchingTheRange() {
        val (b, s) = dt(14, 5) to dt(14, 10)
        assertTrue(TimeRange.overlaps(clipAt(14, 3), b, s)) // 14:03-14:06 reaches into the range
        assertTrue(TimeRange.overlaps(clipAt(14, 6), b, s))
        assertTrue(TimeRange.overlaps(clipAt(14, 9), b, s)) // starts inside, ends after
        assertFalse(TimeRange.overlaps(clipAt(14, 2), b, s)) // ends exactly at 14:05
        assertFalse(TimeRange.overlaps(clipAt(14, 10), b, s)) // starts exactly at the end
        assertTrue(TimeRange.overlaps(clipAt(3, 0), null, dt(14, 0)))
        assertTrue(TimeRange.overlaps(clipAt(23, 0), dt(14, 0), null))
        assertFalse(TimeRange.overlaps(ClipInfo("u", "b.mp4", "Movie", 1, 0), null, dt(14, 0)))
    }

    @Test
    fun describesTheRange() {
        assertEquals("2026-11-02 14:05 to 14:30", TimeRange.describe(dt(14, 5), dt(14, 30)))
        assertEquals("2026-11-02 14:05 to 2026-11-03 00:30", TimeRange.describe(dt(14, 5), dt(0, 30, 3)))
        assertEquals("from 2026-11-02 14:05", TimeRange.describe(dt(14, 5), null))
        assertEquals("until 2026-11-02 14:30", TimeRange.describe(null, dt(14, 30)))
        assertEquals("", TimeRange.describe(null, null))
    }

    // ---- planner (FR-3.7b-e)

    /** 60 s Front + Rear clips: 15:25-15:27 | 19 min break | 15:47-15:49 | 14 min | 16:03-16:05. */
    private fun cardClips(): List<ClipInfo> =
        listOf("152500_0001", "152600_0002", "154700_0003", "154800_0004", "160300_0005", "160400_0006")
            .flatMap { listOf("F", "R").map { cam -> ClipInfo("content://x/$it$cam", "20260831$it$cam.mp4", "Movie", 100, 0) } }

    private fun plan(from: String, to: String, clips: List<ClipInfo> = cardClips()): MergePlan {
        Cameras.detect(clips)
        return Planner.buildPlan(
            clips, PlanOptions(timeFrom = TimeRange.parseOptional(from), timeTo = TimeRange.parseOptional(to)),
            probe = { it.probe = ProbeInfo(60_000_000, StreamSignature("v", "a"), "H.264") },
            nameExists = { false },
        )
    }

    @Test
    fun rangeKeepsWholeClipsAndWholeCardTripNumbers() {
        val p = plan("15:48", "16:03")
        // 15:47 ends exactly at 15:48 and 16:03 starts exactly at the end: only 15:48 is kept.
        assertEquals(listOf(Camera.Front, Camera.Rear), p.parts.map { it.camera })
        assertEquals(listOf("20260831154800_0004F.mp4"), p.parts[0].clips.map { it.name })
        assertEquals(listOf(2 to 2, 2 to 2), p.parts.map { it.tripIndex to it.tripCount })
        assertEquals("Front_20260831_154800_to_20260831_154800_trip2.mp4", p.parts[0].outputName)
        assertEquals("2026-08-31 15:48 to 16:03", p.timeRange)
        assertTrue("Time range: 2026-08-31 15:48 to 16:03 (whole clips)" in Report.planSummary(p, "out"))
        assertTrue(p.warnings.isEmpty()) // the 14-min stop is outside the kept clips
    }

    @Test
    fun rangeAcrossATripGapGivesOneFilePerTrip() {
        val p = plan("15:26:30", "15:47:30")
        assertEquals(listOf(1, 2, 1, 2), p.parts.map { it.tripIndex })
        assertEquals(listOf(1, 1, 1, 1), p.parts.map { it.clips.size })
        assertEquals(listOf("_trip1.mp4", "_trip2.mp4"), p.parts.take(2).map { it.outputName.substring(it.outputName.indexOf("_trip")) })
    }

    @Test
    fun openEndsAndEmptyRange() {
        assertEquals(listOf(2, 2), plan("", "15:27").parts.map { it.clips.size })
        assertEquals(listOf(2, 2), plan("16:00", "").parts.map { it.clips.size })
        val none = plan("10:00", "11:00")
        assertTrue(none.parts.isEmpty())
        assertEquals(listOf("Front: no clips in the time range", "Rear: no clips in the time range"), none.warnings)
        assertEquals("", plan("", "").timeRange)
        assertEquals(12, plan("", "").parts.sumOf { it.clips.size })
    }

    @Test
    fun rangeOnFootageWithoutTimestampsIsAnError() {
        val clips = listOf(ClipInfo("content://x/a", "VID0001.mp4", "Front", 1, 0))
        val e = assertThrows(TimeRangeError::class.java) { plan("14:00", "", clips) }
        assertTrue(e.message!!.contains("no timestamps"))
    }
}
