package com.phong.dashcammerger.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/** FR-3.8 trip trim (port of the trim tests in test_timerange.py, test_planner.py, test_gui.py). */
class TripTrimTest {
    private fun clipAt(start: LocalDateTime, seconds: Double, name: String = "x.mp4") =
        ClipInfo("content://x/$name", name, "Movie", 1000, 0).also {
            it.timestamp = start
            it.probe = ProbeInfo((seconds * 1_000_000).toLong(), StreamSignature("v", "a"), "H.264")
        }

    // ---- window (TimeRange.clipWindow / describeTrim)

    @Test
    fun clipWindowExcludesTouchingNeighbours() {
        val t = LocalDateTime.of(2026, 11, 2, 14, 0, 0)
        // three 60.03 s clips: their ends overlap the next start by a few frames
        val clips = (0..2).map { clipAt(t.plusMinutes(it.toLong()), 60.03) }
        val (b, s) = TimeRange.clipWindow(t.plusMinutes(1), t.plusMinutes(1).plusNanos(60_030_000_000))
        assertEquals(listOf(false, true, false), clips.map { TimeRange.overlaps(it, b, s) })
    }

    @Test
    fun clipWindowOfATinyClipStillKeepsIt() {
        val t = LocalDateTime.of(2026, 11, 2, 14, 0, 0)
        val (b, s) = TimeRange.clipWindow(t, t.plusSeconds(1))
        assertTrue(TimeRange.overlaps(clipAt(t, 1.0), b, s))
    }

    @Test
    fun describesTheTrim() {
        val begin = LocalDateTime.of(2026, 11, 2, 14, 15, 0)
        assertEquals("trip 2: 2026-11-02 14:15:00 to 15:02:10", TimeRange.describeTrim(2, begin, begin.withHour(15).withMinute(2).withSecond(10)))
        assertTrue(TimeRange.describeTrim(3, begin, LocalDateTime.of(2026, 11, 3, 0, 5)).endsWith("to 2026-11-03 00:05:00"))
    }

    // ---- planner (FR-3.8c-e)

    private val start = LocalDateTime.of(2026, 8, 31, 15, 25, 16)
    private val nameFmt = DateTimeFormatter.ofPattern("yyyyMMddHHmmss")

    /** Trip 1: minutes 0-2, trip 2: minutes 60-69 (60 s clips); Rear starts [offset] s later. */
    private fun twoTrips(cam: String = "F", offset: Long = 0): List<ClipInfo> =
        (listOf(0, 1, 2) + (60..69)).mapIndexed { i, m ->
            val ts = start.plusMinutes(m.toLong()).plusSeconds(offset)
            ClipInfo("content://x/$i$cam", "${ts.format(nameFmt)}_${"%04d".format(i)}$cam.mp4", "Movie", 100, 0)
        }

    private fun trim(firstMinute: Double, endMinute: Double) = TimeRange.clipWindow(
        start.plusNanos((firstMinute * 60e9).toLong()), start.plusNanos((endMinute * 60e9).toLong()),
    )

    private fun plan(clips: List<ClipInfo>, trips: Map<Int, Pair<LocalDateTime, LocalDateTime>>, from: String = ""): MergePlan {
        Cameras.detect(clips)
        return Planner.buildPlan(
            clips, PlanOptions(tripRanges = trips, timeFrom = TimeRange.parseOptional(from)),
            probe = { it.probe = ProbeInfo(60_000_000, StreamSignature("v", "a"), "H.264") },
            nameExists = { false },
        )
    }

    private fun seq(part: OutputPart) = part.clips.map { it.name.substringAfter('_').take(4).toInt() }

    @Test
    fun trimKeepsOnlyTheWindowOfThatTrip() {
        val p = plan(twoTrips(), mapOf(2 to trim(62.0, 67.0)))
        val (trip1, trip2) = p.parts
        assertEquals(listOf(0, 1, 2), seq(trip1)) // other trips untouched
        assertEquals(listOf(5, 6, 7, 8, 9), seq(trip2)) // minutes 62..66
        assertEquals(2 to 2, trip2.tripIndex to trip2.tripCount)
        assertEquals(300_000_000L, trip2.expectedDurationUs)
        assertEquals("Front_20260831_162716_to_20260831_163116_trip2.mp4", trip2.outputName)
        assertEquals(listOf("trip 2: 2026-08-31 16:27:16 to 16:32:16"), p.tripTrims)
        assertTrue("Trimmed trip 2: 2026-08-31 16:27:16 to 16:32:16 (whole clips)" in Report.planSummary(p, "out"))
    }

    @Test
    fun trimAppliesToEveryCameraOfTheTrip() {
        // The dialog snaps to the latest camera's start and the earliest camera's end.
        val p = plan(twoTrips() + twoTrips("R", 2), mapOf(2 to trim(62 + 2.0 / 60, 67.0)))
        assertEquals(
            listOf(Triple(Camera.Front, 1, 3), Triple(Camera.Front, 2, 5), Triple(Camera.Rear, 1, 3), Triple(Camera.Rear, 2, 5)),
            p.parts.map { Triple(it.camera, it.tripIndex, it.clips.size) },
        )
    }

    @Test
    fun trimKeepsSelectionKeysOrderAndTicks() {
        val scan = plan(twoTrips() + twoTrips("R", 2), emptyMap())
        val trimmed = plan(twoTrips() + twoTrips("R", 2), mapOf(2 to trim(62 + 2.0 / 60, 67.0)))
        assertEquals(scan.parts.map(Planner::partKey), trimmed.parts.map(Planner::partKey))
        // Order [3, 0, 2, 1] with Front trip 2 (index 1) unticked carries over by key.
        val (order, selected) = Arrange.carryOver(scan.parts, listOf(3, 0, 2, 1), setOf(0, 2, 3), trimmed.parts)
        assertEquals(listOf(3, 0, 2, 1), order)
        assertEquals(setOf(0, 2, 3), selected)
    }

    @Test
    fun trimCombinesWithTimeRange() {
        val p = plan(twoTrips(), mapOf(2 to trim(62.0, 67.0)), from = "16:30")
        assertEquals(listOf(7, 8, 9), p.parts.flatMap { seq(it) }) // 16:29:16 on
    }

    @Test
    fun trimWithNoClipsLeftWarns() {
        val p = plan(twoTrips(), mapOf(2 to (start.plusHours(5) to start.plusHours(6))))
        assertEquals(listOf(1), p.parts.map { it.tripIndex })
        assertTrue("Front trip 2: no clips in the trimmed time" in p.warnings)
    }

    @Test
    fun trimForAMissingTripIsIgnored() {
        val p = plan(twoTrips(), mapOf(7 to trim(62.0, 67.0)))
        assertEquals(listOf(3, 10), p.parts.map { it.clips.size })
        assertEquals(emptyList<String>(), p.tripTrims)
    }

    // ---- dialog model (FR-3.8b/c)

    private fun at(h: Int, m: Int, s: Int = 0) = LocalDateTime.of(2026, 10, 14, h, m, s)

    /** Front and Rear trip 2: ten 3-min clips from 09:00, Rear 2 s later. */
    private fun tripParts(): List<OutputPart> = listOf(Camera.Front to 0L, Camera.Rear to 2L).map { (cam, offset) ->
        val clips = (0 until 10).map { clipAt(at(9, 0).plusSeconds(offset).plusMinutes(3L * it), 180.0, "$cam$it.mp4") }
        OutputPart(cam, 2, 3, clips, "trip gap", 0, 0, tripIndex = 2, tripCount = 3)
    }

    @Test
    fun trimModelSnapsAndShowsOffsets() {
        val parts = tripParts()
        val m = TrimModel(parts, null)
        assertEquals(30 * 60 + 2.0, m.total, 1e-9) // Rear ends 2 s after Front
        assertEquals(10, m.starts.size) // cameras share points
        assertEquals(10, m.ends.size)
        assertTrue(m.isWhole)
        assertEquals(null, m.result())
        m.setStart(7 * 60.0) // nearest start: 6:02 (clip 3, latest camera)
        m.setEnd(20 * 60.0) // nearest end: 21:00 (clip 7, earliest camera)
        assertEquals(6 * 60 + 2.0, m.start, 1e-9)
        assertEquals(21 * 60.0, m.end, 1e-9)
        assertEquals("2026-10-14 09:06:02   +6:02 min", m.startText)
        assertEquals("2026-10-14 09:21:00   +21:00 min of 30:02", m.endText)
        assertEquals(listOf(5, 5), parts.map { m.kept(it).size })
        assertTrue("Front 5/10 files, Rear 5/10 files" in m.summary)
        assertEquals(at(9, 6, 2) to at(9, 21), m.result())
        m.step(startSlider = false, direction = 1)
        assertEquals(24 * 60.0, m.end, 1e-9)
        m.setEnd(0.0) // never before the start
        assertTrue(m.end > m.start)
        m.whole()
        assertTrue(m.isWhole)
    }

    @Test
    fun trimModelReopensAtTheStoredTrim() {
        val m = TrimModel(tripParts(), at(9, 6, 2) to at(9, 21))
        assertEquals(6 * 60 + 2.0, m.start, 1e-9)
        assertEquals(21 * 60.0, m.end, 1e-9)
        assertFalse(m.isWhole)
        assertEquals("Trip 2/3 (Front, Rear): 2026-10-14 09:00:00 to 2026-10-14 09:30:02 - 30:02 min", m.heading("trip 2/3"))
    }

    @Test
    fun offsetTextAndSnapPoints() {
        assertEquals("12:30", TrimModel.offsetText(750.0))
        assertEquals("0:00", TrimModel.offsetText(-3.0))
        assertEquals(listOf(2.0, 180.0), TrimModel.snapPoints(listOf(0.0, 2.0, 180.0), last = true))
        assertEquals(listOf(0.0, 180.0), TrimModel.snapPoints(listOf(2.0, 0.0, 180.0), last = false))
    }
}
