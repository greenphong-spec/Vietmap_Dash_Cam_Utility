package com.phong.dashcammerger.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.ZoneOffset

/** AR-14: progress tracking maths and texts. */
class ProgressTest {
    private val zone: ZoneId = ZoneOffset.UTC
    private val mib = 1024L * 1024L
    private val gib = 1024L * mib

    private fun ms(h: Int, m: Int, s: Int = 0) =
        LocalDateTime.of(2026, 11, 2, h, m, s).atZone(zone).toInstant().toEpochMilli()

    /** Feeds [mbPerSec] MiB every second for [seconds] seconds starting at [fromMs]. */
    private fun ProgressTracker.feed(fromMs: Long, seconds: Int, mbPerSec: Long): Long {
        var t = fromMs
        repeat(seconds) {
            t += 1000
            add(mbPerSec * mib, t)
        }
        return t
    }

    @Test
    fun speedIsUnknownAtFirstThenAveraged() {
        val start = ms(21, 0)
        val tr = ProgressTracker(10 * gib, start)
        tr.add(512 * 1024, start + 500)
        assertNull(tr.speedBps(start + 500)) // < 5 s and < 1 MiB
        val t = tr.feed(start, 10, 10)
        assertEquals(10.0 * mib, tr.speedBps(t)!!, 0.6 * mib) // average since start
    }

    @Test
    fun speedUsesTheLastThirtySeconds() {
        val start = ms(21, 0)
        val tr = ProgressTracker(100 * gib, start)
        var t = tr.feed(start, 60, 20) // fast at first
        t = tr.feed(t, 60, 5) // then slow
        assertEquals(5.0 * mib, tr.speedBps(t)!!, 0.3 * mib)
    }

    @Test
    fun remainingTimeAndFinishClock() {
        val start = ms(21, 0)
        val tr = ProgressTracker(4 * gib, start)
        val t = tr.feed(start, 60, 10) // 600 MiB at 10 MiB/s
        val snap = tr.snapshot(t)
        assertEquals(14, snap.percent)
        val left = snap.remainingMs(t)!!
        assertEquals((4 * 1024 - 600) / 10.0, left / 1000.0, 1.0) // ~349 s
        assertEquals("6 min", Progress.remaining(left))
        assertEquals("21:06", Progress.finishClock(t + left, t, zone))
        assertEquals(
            "14 % · 600.0 MB / 4.00 GB · 10.0 MB/s · 6 min left (done ≈ 21:06)",
            Progress.line(snap, t, zone),
        )
        assertEquals("Merging 14 % · 6 min left", Progress.notificationTitle(snap, t))
    }

    @Test
    fun stallHidesTimeLeft() {
        val start = ms(21, 0)
        val tr = ProgressTracker(4 * gib, start)
        val t = tr.feed(start, 60, 10)
        val snap = tr.snapshot(t)
        assertFalse(snap.isStalled(t + 14_000))
        assertTrue(snap.isStalled(t + 15_000))
        assertNull(snap.remainingMs(t + 40_000))
        assertEquals("14 % · 600.0 MB / 4.00 GB · no progress for 40 s", Progress.line(snap, t + 40_000, zone))
        assertEquals("Merging 14 %", Progress.notificationTitle(snap, t + 40_000))
    }

    @Test
    fun estimatingBeforeSpeedIsKnown() {
        val start = ms(21, 0)
        val tr = ProgressTracker(4 * gib, start)
        tr.add(100 * mib, start + 2000)
        val snap = tr.snapshot(start + 2000)
        assertEquals("2 % · 100.0 MB / 4.00 GB · estimating time left…", Progress.line(snap, start + 2000, zone))
    }

    @Test
    fun finishedPartsCountFullyAndPercentStaysBelowHundred() {
        val start = ms(21, 0)
        val tr = ProgressTracker(2 * gib, start)
        tr.startPart(1, 2, gib)
        tr.add(gib - 5 * mib, start + 60_000) // container overhead: a bit less than expected
        assertEquals(99, tr.snapshot(start + 60_000).partPercent)
        tr.endPart(gib)
        assertEquals(gib, tr.doneBytes)
        tr.startPart(2, 2, gib)
        tr.add(gib, start + 120_000)
        tr.endPart(gib)
        val snap = tr.snapshot(start + 120_000)
        assertEquals(99, snap.percent) // capped until the run is over
        assertEquals(100, snap.partPercent)
    }

    @Test
    fun detailLineShowsPartAndClip() {
        val start = ms(21, 0)
        val tr = ProgressTracker(2 * gib, start)
        tr.startPart(2, 3, gib)
        tr.clip(3, 20)
        tr.add(gib * 61 / 100 + 1000, start + 372_000)
        assertEquals(
            "Elapsed 6 min 12 s · this file 61 % · clip 3/20",
            Progress.detail(tr.snapshot(start + 372_000), start + 372_000),
        )
        // 624.6 MiB in 372 s -> ~848 s left -> 21:06:12 + 14 min.
        assertEquals(
            "Part 2/3 · 624.6 MB / 2.00 GB · done ≈ 21:20",
            Progress.notificationText(tr.snapshot(start + 372_000), start + 372_000, zone),
        )
    }

    @Test
    fun durationTexts() {
        assertEquals("45 s", Progress.elapsed(45_000))
        assertEquals("6 min 12 s", Progress.elapsed(372_000))
        assertEquals("1 h 05 min", Progress.elapsed(3_900_000))
        assertEquals("< 1 min", Progress.remaining(59_000))
        assertEquals("2 min", Progress.remaining(61_000))
        assertEquals("1 h 05 min", Progress.remaining(3_900_000))
        assertEquals("9.2 MB/s", Progress.speed(9.2 * mib))
    }

    @Test
    fun finishClockOnTheNextDay() {
        assertEquals("tomorrow 01:10", Progress.finishClock(ms(21, 0) + 4 * 3_600_000 + 600_000, ms(21, 0), zone))
    }

    @Test
    fun reportTimeLine() {
        val start = ms(21, 13, 44)
        val end = ms(21, 29, 18)
        assertEquals(
            "Time: 2026-11-02 21:13:44 - 21:29:18 (15 min 34 s), 1.16 GB at 1.3 MB/s average",
            Progress.reportTimeLine(start, end, (1.16 * gib).toLong(), zone),
        )
    }
}
