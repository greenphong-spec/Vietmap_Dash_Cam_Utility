package com.phong.dashcammerger.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

class CoreTest {
    private fun clip(name: String, parent: String = "Movie", size: Long = 100, mtime: Long = 0) =
        ClipInfo("content://x/$parent/$name", name, parent, size, mtime)

    private val sig = StreamSignature("video/avc 1920x1080", "audio/mp4a-latm")

    private fun probed(c: ClipInfo, s: StreamSignature = sig, seconds: Long = 60) =
        c.also { it.probe = ProbeInfo(seconds * 1_000_000, s, "H.264") }

    @Test
    fun xforceProfileDetectsCamerasAndTimestamps() {
        val clips = listOf(
            clip("20260101120000_0001F.mp4"), clip("20260101120100_0002F.mp4"),
            clip("20260101120000_0001R.mp4"),
        )
        val result = Cameras.detect(clips)
        assertEquals("Xforce", result.profileName)
        assertEquals(Camera.Front, clips[0].camera)
        assertEquals(Camera.Rear, clips[2].camera)
        assertEquals(LocalDateTime.of(2026, 1, 1, 12, 1, 0), clips[1].timestamp)
        assertEquals(2L, clips[1].sequence)
    }

    @Test
    fun folderProfileUsesParentName() {
        val clips = listOf(clip("a_001.mp4", "Front"), clip("a_002.mp4", "rear"), clip("a_003.mp4", "Inside"))
        assertEquals("Folder", Cameras.detect(clips).profileName)
        assertEquals(listOf(Camera.Front, Camera.Rear, Camera.Interior), clips.map { it.camera })
    }

    @Test
    fun unknownNamingFallsBackToTokens() {
        val clips = listOf(clip("VID0001.mp4"), clip("VID0002.mp4"))
        val result = Cameras.detect(clips)
        assertNull(result.profileName)
        assertEquals(setOf("ALL"), result.tokens.keys)
        Cameras.applyManualMap(result.tokens, mapOf("ALL" to Camera.Front))
        assertTrue(clips.all { it.camera == Camera.Front })
    }

    @Test
    fun timestampAndSequenceParsing() {
        assertEquals(LocalDateTime.of(2026, 3, 4, 5, 6, 7), Ordering.parseTimestamp("NO20260304-050607-000123F"))
        assertNull(Ordering.parseTimestamp("20261399235959")) // invalid month
        val (ts, span) = Ordering.parseTimestampSpan("20260101120000_0042F")
        assertEquals(LocalDateTime.of(2026, 1, 1, 12, 0, 0), ts)
        assertEquals(42L, Ordering.parseSequence("20260101120000_0042F", span))
    }

    @Test
    fun ordersByTimestampAndDetectsGaps() {
        val a = probed(clip("20260101120000_0001F.mp4"))
        val b = probed(clip("20260101120100_0002F.mp4"))
        val c = probed(clip("20260101130000_0003F.mp4"))
        val group = CameraGroup(Camera.Front, mutableListOf(c, a, b))
        Ordering.annotate(group.clips)
        Ordering.orderGroup(group)
        assertEquals(listOf(a, b, c), group.clips)
        Ordering.detectGaps(group, 300)
        assertEquals(1, group.gaps.size)
        assertEquals(58 * 60.0, group.gaps[0].seconds, 0.001)
    }

    @Test
    fun planSplitsOnCodecChangeAndNamesOutputs() {
        val other = StreamSignature("video/hevc 3840x2160", "none")
        val clips = listOf(
            clip("20260101120000_0001F.mp4"), clip("20260101120100_0002F.mp4"),
            clip("20260101120200_0003F.mp4"),
        )
        Cameras.detect(clips)
        val plan2 = Planner.buildPlan(
            clips, PlanOptions(),
            probe = { c -> probed(c, if (c.name.contains("0003")) other else sig) },
            nameExists = { it == "Front_20260101_120200_to_20260101_120200_part2.mp4" },
        )
        assertEquals(2, plan2.parts.size)
        assertEquals("Front_20260101_120000_to_20260101_120100_part1.mp4", plan2.parts[0].outputName)
        assertEquals("Front_20260101_120200_to_20260101_120200_part2_2.mp4", plan2.parts[1].outputName)
        assertEquals(120_000_000L, plan2.parts[0].expectedDurationUs)
        assertTrue(plan2.warnings.any { "codec change" in it })
    }

    @Test
    fun unreadableClipsAreSkippedWithWarning() {
        val clips = listOf(clip("20260101120000_0001F.mp4"), clip("20260101120100_0002F.mp4"))
        Cameras.detect(clips)
        val plan = Planner.buildPlan(
            clips, PlanOptions(),
            probe = { c -> if (c.name.contains("0002")) c.unreadableReason = "moov atom not found" else probed(c) },
            nameExists = { false },
        )
        assertEquals(1, plan.unreadable.size)
        assertEquals(1, plan.parts.single().clips.size)
    }

    @Test
    fun maxPartSizeSplits() {
        val gib = 1024L * 1024 * 1024
        val clips = (1..4).map { clip("2026010112000${it}_000${it}F.mp4", size = gib) }
        Cameras.detect(clips)
        val plan = Planner.buildPlan(clips, PlanOptions(maxPartGb = 2.5), probe = { probed(it) }, nameExists = { false })
        assertEquals(listOf(2, 2), plan.parts.map { it.clips.size })
    }

    private fun twoPartPlan(): MergePlan {
        val clips = listOf(clip("20260101120000_0001F.mp4"), clip("20260101120000_0001R.mp4", size = 300))
        Cameras.detect(clips)
        return Planner.buildPlan(clips, PlanOptions(), probe = { probed(it) }, nameExists = { false })
    }

    @Test
    fun reportListsSkippedParts() {
        val plan = twoPartPlan()
        val (front, rear) = plan.parts
        val result = PartResult(rear, PartStatus.PASS, rear.outputName, rear.expectedDurationUs, rear.expectedSize, "", 0)

        val text = Report.reportText(RunReport(plan, listOf(result), "out", true, emptyList(), skipped = listOf(front)))
        assertTrue("Not selected (skipped by user): 1" in text)
        assertTrue("    ${front.outputName}" in text)

        val all = Report.reportText(RunReport(plan, listOf(result), "out", true, emptyList()))
        assertTrue("Not selected" !in all)
    }

    @Test
    fun selectionSummaryAndSpaceWarning() {
        val plan = twoPartPlan()
        assertEquals("Selected: 1 of 2 file(s), 0.3 KB", Report.selectionSummary(plan.parts.drop(1), plan.parts.size))
        assertEquals("Selected: 0 of 2 file(s), 0.0 KB", Report.selectionSummary(emptyList(), 2))
        assertTrue(Report.spaceWarning(200, 100).startsWith("NOT ENOUGH SPACE"))
        assertEquals("", Report.spaceWarning(100, 200))
        assertEquals("", Report.spaceWarning(100, null))
    }

    /** Front and Rear, two trips each (12:00-12:02 and 14:00), split on the trip gap. */
    private fun twoTripPlan(): MergePlan {
        val clips = listOf("120000_0001", "120100_0002", "140000_0003").flatMap { t ->
            listOf(clip("20260101${t}F.mp4"), clip("20260101${t}R.mp4"))
        }
        Cameras.detect(clips)
        return Planner.buildPlan(clips, PlanOptions(splitOnGap = true), probe = { probed(it) }, nameExists = { false })
    }

    @Test
    fun arrangeOrdersByCameraOrTimeAndMoves() {
        val parts = twoTripPlan().parts
        assertEquals(listOf("Front" to 1, "Front" to 2, "Rear" to 1, "Rear" to 2), parts.map { it.camera.name to it.partIndex })
        assertEquals(listOf(0, 1, 2, 3), Arrange.byCamera(parts))
        assertEquals(listOf(0, 2, 1, 3), Arrange.byTime(parts))
        assertEquals(listOf(1, 0, 2, 3), Arrange.move(listOf(0, 1, 2, 3), 1, -1))
        assertEquals(listOf(0, 1, 2, 3), Arrange.move(listOf(0, 1, 2, 3), 0, -1)) // already first
        assertEquals(listOf(0, 1, 2, 3), Arrange.move(listOf(0, 1, 2, 3), 3, 1)) // already last
    }

    @Test
    fun arrangePairsSameTimePartsAcrossCameras() {
        val parts = twoTripPlan().parts
        assertEquals(listOf(setOf(2), setOf(3), setOf(0), setOf(1)), Arrange.partners(parts))

        // Without timestamps, parts pair by part number.
        val clips = listOf(clip("a.mp4", parent = "Front"), clip("a.mp4", parent = "Rear"))
        Cameras.detect(clips)
        val plain = Planner.buildPlan(clips, PlanOptions(), probe = { probed(it) }, nameExists = { false }).parts
        assertEquals(listOf(setOf(1), setOf(0)), Arrange.partners(plain))
    }

    @Test
    fun formatting() {
        assertEquals("1:01:05", Report.formatDuration(3_665_000_000))
        assertEquals("1.50 GB", Report.formatSize(1024L * 1024 * 1024 * 3 / 2))
    }
}
