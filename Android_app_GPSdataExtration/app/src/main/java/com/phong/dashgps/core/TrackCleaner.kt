package com.phong.dashgps.core

import java.time.Duration
import java.time.LocalDateTime
import java.time.ZoneOffset
import kotlin.math.abs
import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

object Geo {
    private const val EARTH_RADIUS_M = 6_371_008.8

    /** Great-circle distance in metres. */
    fun distanceM(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
        val p1 = Math.toRadians(lat1)
        val p2 = Math.toRadians(lat2)
        val dp = p2 - p1
        val dl = Math.toRadians(lon2 - lon1)
        val h = sin(dp / 2) * sin(dp / 2) + cos(p1) * cos(p2) * sin(dl / 2) * sin(dl / 2)
        return 2 * EARTH_RADIUS_M * asin(sqrt(h.coerceIn(0.0, 1.0)))
    }

    fun distanceM(a: Fix, b: Fix) = distanceM(a.lat, a.lon, b.lat, b.lon)
}

/**
 * Turns raw OCR samples into a clean track. OCR occasionally misreads a digit, so every
 * reading is cross-checked against its neighbours rather than trusted on its own:
 *
 *  1. parse with the camera profile (unreadable text is dropped);
 *  2. range checks; (0,0) means the camera had no GPS fix;
 *  3. time check: the overlay clock must advance together with the video clock, so
 *     (overlay time - position in video) is nearly constant. A reading whose offset is not
 *     shared by at least 2 of its 6 nearest neighbours is a misread. (A real recording gap
 *     inside a merged video shifts the offset for a whole run of frames, so it passes.)
 *  3b. time repair: a reading whose date/time text was damaged (e.g. "07:1:00" for
 *     "07:11:00") but whose position is readable gets its time rebuilt from the video
 *     clock - position in video + the offset its accepted neighbours agree on - and is kept
 *     only if the rebuilt seconds equal the seconds that were readable;
 *  4. one fix per overlay second;
 *  5. position check: a fix that cannot be reached from either time-neighbour at
 *     <= 250 km/h is a misread;
 *  6. spike check: a fix > 40 m off the line between its neighbours, and further off than
 *     the neighbours are apart, is a misread of the last digits.
 *
 * Every sample's outcome is kept (Track.sampleStatus) for the OCR log.
 */
object TrackCleaner {
    /** Offsets closer than this count as "the same clock" (frame vs. overlay tick phase). */
    const val TIME_TOLERANCE_S = 2.0
    const val TIME_NEIGHBOURS = 3
    const val TIME_MIN_SUPPORT = 2

    /** 250 km/h plus a little GPS jitter. */
    const val MAX_SPEED_MPS = 70.0
    const val JITTER_M = 30.0

    /** A gap longer than this starts a new track segment (no line drawn across it). */
    const val SEGMENT_GAP_SECONDS = 120L

    fun clean(samples: List<Sample>, profile: OverlayProfile): Track {
        val status = arrayOfNulls<RejectReason>(samples.size)
        fun reject(f: Fix, r: RejectReason) { status[f.sampleIndex] = r }

        // 1-2. Parse and range-check. Readings without a usable time wait for step 3b.
        val partial = mutableListOf<Pair<Int, Reading>>()
        var fixes = samples.mapIndexedNotNull { i, s ->
            val r = profile.parse(s.text)
            val reason = when {
                r == null -> RejectReason.Unreadable
                r.lat == 0.0 && r.lon == 0.0 -> RejectReason.NoGpsFix
                abs(r.lat) > 90 || abs(r.lon) > 180 || r.speedKmh !in 0..300 -> RejectReason.OutOfRange
                else -> null
            }
            when {
                reason != null || r == null -> { status[i] = reason; null }
                r.time == null -> { partial.add(i to r); null }
                else -> Fix(i, s.videoIndex, s.videoName, s.posSec, r.time, r.lat, r.lon, r.speedKmh, s.text)
            }
        }

        // 3. Time consistency, per video.
        fixes = fixes.groupBy { it.videoIndex }.values.flatMap { perVideo ->
            val sorted = perVideo.sortedBy { it.posSec }
            if (sorted.size < 4) return@flatMap sorted
            val offsets = sorted.map { epochSeconds(it.time) - it.posSec }
            sorted.filterIndexed { i, f ->
                val lo = maxOf(0, i - TIME_NEIGHBOURS)
                val hi = minOf(sorted.lastIndex, i + TIME_NEIGHBOURS)
                val support = (lo..hi).count { j -> j != i && abs(offsets[j] - offsets[i]) <= TIME_TOLERANCE_S }
                (support >= TIME_MIN_SUPPORT).also { if (!it) reject(f, RejectReason.TimeMismatch) }
            }
        }

        // 3b. Rebuild damaged times from the video clock.
        val byVideo = fixes.groupBy { it.videoIndex }.mapValues { (_, v) -> v.sortedBy { it.posSec } }
        for ((i, r) in partial) {
            val s = samples[i]
            val time = repairTime(byVideo[s.videoIndex].orEmpty(), s.posSec, r.secondHint)
            if (time == null) {
                status[i] = RejectReason.Unreadable
            } else {
                fixes = fixes + Fix(i, s.videoIndex, s.videoName, s.posSec, time, r.lat, r.lon, r.speedKmh, s.text)
            }
        }

        // 4. One fix per overlay second (keep the earliest frame).
        val seen = HashSet<LocalDateTime>()
        fixes = fixes.sortedWith(compareBy<Fix>({ it.time }, { it.videoIndex }, { it.posSec }))
            .filter { f -> seen.add(f.time).also { if (!it) reject(f, RejectReason.Duplicate) } }

        // 5. Position jumps; two passes so a misread next to another misread is caught too.
        repeat(2) {
            if (fixes.size < 2) return@repeat
            fixes = fixes.filterIndexed { i, f ->
                val prev = fixes.getOrNull(i - 1)
                val next = fixes.getOrNull(i + 1)
                ((prev != null && reachable(prev, f)) || (next != null && reachable(f, next)))
                    .also { if (!it) reject(f, RejectReason.PositionJump) }
            }
        }

        // 6. Sideways spikes: a misread last digits moves a point ~10-200 m off the road,
        //    which is still "reachable", but the neighbours on both sides agree with each
        //    other and not with it.
        repeat(2) {
            if (fixes.size < 3) return@repeat
            fixes = fixes.filterIndexed { i, f ->
                if (i == 0 || i == fixes.lastIndex) return@filterIndexed true
                (!isSpike(fixes[i - 1], f, fixes[i + 1])).also { if (!it) reject(f, RejectReason.Spike) }
            }
        }

        val segments = segment(fixes)
        val counts = status.filterNotNull().groupingBy { it }.eachCount()
        val rejected = RejectReason.entries.filter { it in counts }.associateWith { counts.getValue(it) }
        val partialIdx = partial.map { it.first }.toSet()
        return Track(
            fixes, segments, rejected, samples, status.toList(), stats(segments),
            timeRepaired = fixes.count { it.sampleIndex in partialIdx },
        )
    }

    /** How far (s of video) the neighbours used for a time repair may be. */
    const val REPAIR_WINDOW_S = 15.0

    /**
     * Time for a frame at [posSec] from the accepted fixes of the same video around it: they
     * must agree (within TIME_TOLERANCE_S) on overlay-minus-video offset. Of the whole
     * seconds nearest to that estimate, the one whose seconds equal [secondHint] wins.
     */
    fun repairTime(sorted: List<Fix>, posSec: Double, secondHint: Int?): LocalDateTime? {
        if (secondHint == null) return null
        val idx = sorted.binarySearchBy(posSec) { it.posSec }.let { if (it < 0) -it - 1 else it }
        val near = (maxOf(0, idx - TIME_NEIGHBOURS) until minOf(sorted.size, idx + TIME_NEIGHBOURS))
            .map { sorted[it] }
            .filter { abs(it.posSec - posSec) <= REPAIR_WINDOW_S }
        if (near.size < TIME_MIN_SUPPORT) return null
        val offsets = near.map { epochSeconds(it.time) - it.posSec }.sorted()
        if (offsets.last() - offsets.first() > TIME_TOLERANCE_S) return null
        val estimate = Math.round(offsets[offsets.size / 2] + posSec)
        return listOf(0L, -1L, 1L).map { estimate + it }
            .map { LocalDateTime.ofEpochSecond(it, 0, ZoneOffset.UTC) }
            .firstOrNull { it.second == secondHint }
    }

    /** A spike deviates this far (m) from where its neighbours put it ... */
    const val SPIKE_MIN_M = 40.0

    /** ... and further than the neighbours are from each other. */
    private fun isSpike(prev: Fix, f: Fix, next: Fix): Boolean {
        val span = Duration.between(prev.time, next.time).seconds
        if (span <= 0 || span > 10) return false // too far apart to judge
        val w = Duration.between(prev.time, f.time).seconds.toDouble() / span
        val expLat = prev.lat + (next.lat - prev.lat) * w
        val expLon = prev.lon + (next.lon - prev.lon) * w
        val deviation = Geo.distanceM(f.lat, f.lon, expLat, expLon)
        return deviation > SPIKE_MIN_M && deviation > Geo.distanceM(prev, next)
    }

    private fun epochSeconds(t: LocalDateTime) = t.toEpochSecond(ZoneOffset.UTC).toDouble()

    private fun reachable(a: Fix, b: Fix): Boolean {
        val dt = abs(Duration.between(a.time, b.time).seconds).coerceAtLeast(1)
        return Geo.distanceM(a, b) <= MAX_SPEED_MPS * dt + JITTER_M
    }

    fun segment(fixes: List<Fix>): List<List<Fix>> {
        val out = mutableListOf<MutableList<Fix>>()
        for (f in fixes) {
            val last = out.lastOrNull()?.last()
            if (last == null || Duration.between(last.time, f.time).seconds > SEGMENT_GAP_SECONDS) {
                out.add(mutableListOf(f))
            } else {
                out.last().add(f)
            }
        }
        return out
    }

    fun stats(segments: List<List<Fix>>): TrackStats {
        val all = segments.flatten()
        return TrackStats(
            points = all.size,
            segments = segments.size,
            distanceM = segments.sumOf { seg -> seg.zipWithNext().sumOf { (a, b) -> Geo.distanceM(a, b) } },
            start = all.firstOrNull()?.time,
            end = all.lastOrNull()?.time,
            movingSeconds = segments.sumOf { Duration.between(it.first().time, it.last().time).seconds },
            maxSpeedKmh = all.maxOfOrNull { it.speedKmh } ?: 0,
        )
    }
}
