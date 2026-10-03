package com.phong.dashgps.core

import java.time.LocalDateTime

/**
 * Where a camera burns its telemetry into the picture, and how to read it back.
 * One profile per camera model; adding a camera = adding a profile (see OverlayProfiles).
 */
class OverlayProfile(
    val id: String,
    val name: String,
    /** Region to OCR, as fractions of the frame (0..1): left, top, right, bottom. */
    val crop: CropRect,
    /** Turns the OCR text of one frame into a reading, or null if the text is not usable. */
    val parse: (String) -> Reading?,
    /** Short note shown in the UI, e.g. which camera of a dual-cam system carries the data. */
    val note: String = "",
)

data class CropRect(val left: Float, val top: Float, val right: Float, val bottom: Float)

/**
 * What one frame's overlay says, before any plausibility checks. Time is camera-local;
 * null when the date/time text was damaged - then [secondHint] holds the seconds digits
 * that were still readable, so the time can be rebuilt from the video clock.
 */
data class Reading(
    val time: LocalDateTime?,
    val lat: Double,
    val lon: Double,
    val speedKmh: Int,
    val secondHint: Int? = time?.second,
)

/** One sampled frame: where it came from and what OCR read there. */
class Sample(
    val videoIndex: Int,
    val videoName: String,
    /** Position of the frame inside its video, seconds. */
    val posSec: Double,
    val text: String,
)

/** A reading that passed every check, tied back to its video frame. */
data class Fix(
    /** Index of the [Sample] this fix came from (for the OCR log). */
    val sampleIndex: Int,
    val videoIndex: Int,
    val videoName: String,
    val posSec: Double,
    val time: LocalDateTime,
    val lat: Double,
    val lon: Double,
    val speedKmh: Int,
    val raw: String,
)

enum class RejectReason(val label: String) {
    Unreadable("text not readable"),
    NoGpsFix("no GPS fix (0,0)"),
    OutOfRange("value out of range"),
    TimeMismatch("time inconsistent with video"),
    Duplicate("same second as another frame"),
    PositionJump("impossible position jump"),
    Spike("sideways spike off the route"),
}

class TrackStats(
    val points: Int,
    val segments: Int,
    val distanceM: Double,
    val start: LocalDateTime?,
    val end: LocalDateTime?,
    /** Time actually covered by the segments (gaps between segments excluded), seconds. */
    val movingSeconds: Long,
    val maxSpeedKmh: Int,
) {
    val avgSpeedKmh: Double get() = if (movingSeconds > 0) distanceM / movingSeconds * 3.6 else 0.0
}

class Track(
    /** Accepted fixes sorted by time. */
    val fixes: List<Fix>,
    /** Fixes split where the recording has a gap (see TrackCleaner.SEGMENT_GAP_SECONDS). */
    val segments: List<List<Fix>>,
    val rejected: Map<RejectReason, Int>,
    /** Every sampled frame, in input order, and what happened to it (null = kept). */
    val sampleList: List<Sample>,
    val sampleStatus: List<RejectReason?>,
    val stats: TrackStats,
    /** Fixes whose damaged time text was rebuilt from the video clock (see TrackCleaner). */
    val timeRepaired: Int = 0,
    /** Fixes whose misread speed was replaced by the speed their positions travel. */
    val speedCorrected: Int = 0,
) {
    val samples: Int get() = sampleList.size
}
