package com.phong.dashcammerger.core

import java.time.LocalDateTime

/** Shared data passed between every pipeline stage (port of core/models.py). */

enum class Camera { Front, Rear, Interior, Skip, Unclassified }

/** Output order of the camera groups, as on the desktop version. */
val CAMERA_ORDER = listOf(Camera.Front, Camera.Rear, Camera.Interior)

/**
 * Everything that must match for clips to be stream-copied into one file.
 * Includes the codec config (SPS/PPS) hash: MediaMuxer takes the track format
 * from the first clip only, so a mismatch there would produce a broken file.
 */
data class StreamSignature(val video: String, val audio: String)

class ProbeInfo(
    val durationUs: Long,
    val signature: StreamSignature,
    /** Human-readable codec summary for the plan screen, e.g. "H.264 1920x1080 + AAC". */
    val description: String,
    /** Set when the source has audio the MP4 muxer cannot carry (e.g. PCM); video is still copied. */
    val audioDropped: String? = null,
)

class ClipInfo(
    /** Document URI (string form so the core stays free of android.* types). */
    val uri: String,
    val name: String,
    val parentName: String,
    val sizeBytes: Long,
    val mtimeMs: Long,
) {
    var camera: Camera = Camera.Unclassified
    var cameraToken: String = ""
    var timestamp: LocalDateTime? = null
    var sequence: Long? = null
    var probe: ProbeInfo? = null
    var unreadableReason: String? = null

    val unreadable: Boolean get() = unreadableReason != null
    val stem: String get() = name.substringBeforeLast('.', name)

    override fun toString() = name
}

enum class OrderSource { Timestamp, Sequence, Mtime }

class Gap(val before: ClipInfo, val after: ClipInfo, val seconds: Double)

class CameraGroup(val camera: Camera, var clips: MutableList<ClipInfo>) {
    var orderSource: OrderSource = OrderSource.Timestamp
    var gaps: List<Gap> = emptyList()
}

class OutputPart(
    val camera: Camera,
    val partIndex: Int,
    val partCount: Int,
    val clips: List<ClipInfo>,
    val splitReason: String,
    val expectedDurationUs: Long,
    val expectedSize: Long,
    /** FR-3.5: 1-based trip of this camera, trips of this camera, part within the trip, parts in it. */
    val tripIndex: Int = 1,
    val tripCount: Int = 1,
    val tripPart: Int = 1,
    val tripParts: Int = 1,
) {
    /** Final file name inside the merged/ folder, e.g. Front_20260101120000_to_20260101130000.mp4 */
    var outputName: String = ""
    val partialName: String get() = outputName.removeSuffix(".mp4") + ".partial.mp4"
}

data class PlanOptions(
    val outputSubfolder: String = "merged",
    val gapWarningSeconds: Int = 300,
    /** FR-3.5: a break of more than this starts a new trip (default 15 min). */
    val gapSplitSeconds: Int = 900,
    val splitOnGap: Boolean = true,
    val maxPartGb: Double = 0.0,
)

class MergePlan(
    val groups: List<CameraGroup>,
    val parts: List<OutputPart>,
    val unclassified: List<ClipInfo>,
    val unreadable: List<ClipInfo>,
    val warnings: List<String>,
)

enum class PartStatus { PASS, FAIL, CANCELLED }

class PartResult(
    val part: OutputPart,
    val status: PartStatus,
    /** Name the file ended up with (final name on PASS, INCOMPLETE_DO_NOT_USE_* otherwise). */
    val finalName: String?,
    val actualDurationUs: Long,
    val actualSize: Long,
    val reason: String,
    val elapsedMs: Long,
)

class RunReport(
    val plan: MergePlan,
    val results: List<PartResult>,
    val outputFolder: String,
    val sourcesUntouched: Boolean,
    val sourceProblems: List<String>,
    /** Planned parts the user unticked before merging (AR-10.12); nothing was written for them. */
    val skipped: List<OutputPart> = emptyList(),
)

const val INCOMPLETE_PREFIX = "INCOMPLETE_DO_NOT_USE_"
