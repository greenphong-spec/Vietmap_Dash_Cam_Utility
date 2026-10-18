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
 *  5b. island check: a short run of fixes that is unreachable from the track on both
 *     sides, while that track reaches across it, is a misread repeated on several frames;
 *  6. spike check: a fix > 40 m off the line between its neighbours, and further off than
 *     the neighbours are apart, is a misread of the last digits;
 *  7. speed check: a speed far from its neighbours' median that also disagrees with the
 *     speed the positions travel is a misread digit; the fix is kept with the GPS speed;
 *  8. small spike check: a fix > 8 m off the line between its neighbours that makes the
 *     route faster than the printed speed, while skipping it matches, is a misread;
 *  9. kink check: a fix > 6 m off a smooth curve through its neighbours, where those
 *     neighbours fit the curve tightly, and > 6 m off the straight road leading in and the
 *     one leading out (so a sharp corner stays), is a misread last digit shifting it sideways.
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

        val (kept, speedCorrected) = cleanFixes(fixes, hasSpeed = true, ::reject)
        fixes = kept

        val segments = segment(fixes)
        val counts = status.filterNotNull().groupingBy { it }.eachCount()
        val rejected = RejectReason.entries.filter { it in counts }.associateWith { counts.getValue(it) }
        val partialIdx = partial.map { it.first }.toSet()
        return Track(
            fixes, segments, rejected, samples, status.toList(), stats(segments),
            timeRepaired = fixes.count { it.sampleIndex in partialIdx },
            speedCorrected = speedCorrected,
        )
    }

    /** Assumed travel per point (m/s) to space out a route file that has no timestamps. */
    const val UNTIMED_SPEED_MPS = 15.0

    /**
     * Cleans a route read from a KML / KMZ / GPX file with steps 4-9. Without timestamps
     * (the plain KML/KMZ export) points get evenly spaced times - one step per point, sized
     * so the typical step is driven at ~54 km/h - and only position checks apply; without
     * speeds the speed checks are skipped and the speed is filled in from the positions.
     */
    fun cleanRoute(route: RouteFile): RouteClean {
        val points = route.points
        val status = arrayOfNulls<RejectReason>(points.size)
        fun reject(f: Fix, r: RejectReason) { status[f.sampleIndex] = r }

        val timed = route.hasTime
        val hasSpeed = route.hasSpeed
        var assumedStepS: Long? = null
        val times: List<LocalDateTime> = if (timed) {
            points.map { it.time!! }
        } else {
            val steps = route.segments.flatMap { seg -> seg.zipWithNext { a, b -> Geo.distanceM(a.lat, a.lon, b.lat, b.lon) } }
                .filter { it > 0.5 }.sorted()
            val step = maxOf(1L, Math.ceil((steps.getOrNull(steps.size / 2) ?: 0.0) / UNTIMED_SPEED_MPS).toLong())
            assumedStepS = step
            var t = LocalDateTime.of(2000, 1, 1, 0, 0)
            route.segments.flatMapIndexed { si, seg ->
                if (si > 0) t = t.plusSeconds(SEGMENT_GAP_SECONDS + 3600) // keep the file's line breaks
                seg.map { _ -> t.also { t = t.plusSeconds(step) } }
            }
        }

        val samples = points.mapIndexed { i, p ->
            val text = String.format(java.util.Locale.US, "%.6f,%.6f", p.lat, p.lon) +
                (p.time?.let { ",$it" } ?: "") + (p.speedKmh?.let { String.format(java.util.Locale.US, ",%.1f km/h", it) } ?: "")
            Sample(0, route.name, i.toDouble(), text)
        }
        val raw = points.mapIndexedNotNull { i, p ->
            val reason = when {
                p.lat == 0.0 && p.lon == 0.0 -> RejectReason.NoGpsFix
                abs(p.lat) > 90 || abs(p.lon) > 180 || (p.speedKmh ?: 0.0) !in 0.0..300.0 -> RejectReason.OutOfRange
                else -> null
            }
            if (reason != null) {
                status[i] = reason
                null
            } else {
                Fix(i, 0, route.name, i.toDouble(), times[i], p.lat, p.lon, Math.round(p.speedKmh ?: 0.0).toInt(), samples[i].text)
            }
        }
        val before = stats(segment(raw.sortedBy { it.time }))

        var (fixes, speedCorrected) = cleanFixes(raw, hasSpeed, ::reject)
        if (timed && !hasSpeed) {
            fixes = segment(fixes).flatMap { seg ->
                seg.mapIndexed { i, f -> f.copy(speedKmh = Math.round(gpsSpeedKmh(seg, i) ?: 0.0).toInt()) }
            }
        }

        val segments = segment(fixes)
        val counts = status.filterNotNull().groupingBy { it }.eachCount()
        val rejected = RejectReason.entries.filter { it in counts }.associateWith { counts.getValue(it) }
        val track = Track(fixes, segments, rejected, samples, status.toList(), stats(segments), speedCorrected = speedCorrected)
        return RouteClean(route, track, before, assumedStepS)
    }

    /**
     * Steps 4-9: everything that judges fixes by time, position and speed alone, shared by
     * video extraction and by cleaning an exported route file. Without [hasSpeed] (a route
     * file with no speeds) the speed steps 7-8 are skipped. Returns the kept fixes and how
     * many speeds were corrected.
     */
    fun cleanFixes(input: List<Fix>, hasSpeed: Boolean, reject: (Fix, RejectReason) -> Unit): Pair<List<Fix>, Int> {
        var fixes = input
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

        // 5b. Misread islands: the same misread on several frames in a row (e.g. "0.38659"
        //     for "10.38659" twice) is reachable from itself, so step 5 keeps it.
        fixes = dropIslands(fixes) { reject(it, RejectReason.PositionJump) }

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

        // 7. Speed misreads: the position is fine, so the fix stays with a corrected speed.
        var speedCorrected = 0
        if (hasSpeed) {
            fixes = segment(fixes).flatMap { seg ->
                correctSpeeds(seg).also { out -> speedCorrected += out.indices.count { out[it].speedKmh != seg[it].speedKmh } }
            }
        }

        // 8. Small spikes, refereed by the (now trusted) printed speed: a misread digit can
        //    put a point only ~10-40 m off the road, under the step-6 limit, but driving
        //    through it is faster than the overlay says while skipping it matches.
        if (hasSpeed) repeat(2) {
            if (fixes.size < 3) return@repeat
            fixes = fixes.filterIndexed { i, f ->
                if (i == 0 || i == fixes.lastIndex) return@filterIndexed true
                (!isSpeedSpike(fixes[i - 1], f, fixes[i + 1])).also { if (!it) reject(f, RejectReason.Spike) }
            }
        }

        // 9. Sideways kinks: a misread last digit can shift a point ~5-20 m sideways, which
        //    barely changes the distance driven, so step 8 misses it; compare each point
        //    with a smooth curve through its neighbours instead.
        fixes = segment(fixes).flatMap { seg -> dropKinks(seg) { reject(it, RejectReason.Spike) } }

        return fixes to speedCorrected
    }

    /** Speeds compared with this many fixes on each side (Hampel filter window). */
    const val SPEED_WINDOW = 5
    const val SPEED_MAD_K = 3.0
    /** Below this deviation (km/h) a speed is never suspicious - normal speed changes. */
    const val SPEED_MIN_DEV_KMH = 10.0
    /** GPS speed is measured over the fixes within this many seconds on each side. */
    const val GPS_SPEED_HALF_WINDOW_S = 2L
    /** A suspicious speed this close to the GPS speed is real (hard braking etc.). */
    const val GPS_SPEED_TOLERANCE_KMH = 15.0

    /**
     * Hampel filter on the printed speed, refereed by the speed the cleaned positions
     * travel: a speed far from the median of its neighbours (more than SPEED_MAD_K scaled
     * MADs, at least SPEED_MIN_DEV_KMH) that also disagrees with the GPS speed is a misread
     * and is replaced by the GPS speed (or by the median when there is no GPS speed).
     * [seg] is one segment, sorted by time.
     */
    private fun correctSpeeds(seg: List<Fix>): List<Fix> {
        if (seg.size < 3) return seg
        val speeds = seg.map { it.speedKmh.toDouble() }
        return seg.mapIndexed { i, f ->
            val window = speeds.subList(maxOf(0, i - SPEED_WINDOW), minOf(seg.size, i + SPEED_WINDOW + 1))
            val median = median(window)
            val mad = median(window.map { abs(it - median) })
            val limit = maxOf(SPEED_MAD_K * 1.4826 * mad, SPEED_MIN_DEV_KMH)
            if (abs(speeds[i] - median) <= limit) return@mapIndexed f
            val gps = gpsSpeedKmh(seg, i)
            when {
                gps == null -> f.copy(speedKmh = Math.round(median).toInt())
                abs(speeds[i] - gps) <= maxOf(GPS_SPEED_TOLERANCE_KMH, gps * 0.25) -> f
                else -> f.copy(speedKmh = Math.round(gps).toInt())
            }
        }
    }

    /** Distance travelled along the fixes within ±GPS_SPEED_HALF_WINDOW_S of fix [i], km/h. */
    private fun gpsSpeedKmh(seg: List<Fix>, i: Int): Double? {
        val t = seg[i].time
        var a = i
        while (a > 0 && Duration.between(seg[a - 1].time, t).seconds <= GPS_SPEED_HALF_WINDOW_S) a--
        var b = i
        while (b < seg.lastIndex && Duration.between(t, seg[b + 1].time).seconds <= GPS_SPEED_HALF_WINDOW_S) b++
        val dt = Duration.between(seg[a].time, seg[b].time).seconds
        if (dt <= 0) return null
        val dist = (a until b).sumOf { Geo.distanceM(seg[it], seg[it + 1]) }
        return dist / dt * 3.6
    }

    private fun median(v: List<Double>): Double {
        val s = v.sorted()
        return if (s.size % 2 == 1) s[s.size / 2] else (s[s.size / 2 - 1] + s[s.size / 2]) / 2
    }

    /** The longest run of fixes (s) that can be dropped as a repeated misread. */
    const val MAX_ISLAND = 30

    /**
     * Splits the track into runs at every unreachable step; consecutive fixes inside a run
     * are reachable from each other. Smallest first, a run of at most MAX_ISLAND fixes is
     * dropped if it is shorter than the nearest kept run on each side, does not join both of
     * them, and those runs reach each other across it (at a track end: shorter than the one
     * run beside it and not joining it).
     */
    private fun dropIslands(fixes: List<Fix>, onReject: (Fix) -> Unit): List<Fix> {
        val runs = mutableListOf<MutableList<Fix>>()
        for (f in fixes) {
            val last = runs.lastOrNull()?.last()
            if (last != null && reachable(last, f)) runs.last().add(f) else runs.add(mutableListOf(f))
        }
        if (runs.size < 2) return fixes
        val kept = BooleanArray(runs.size) { true }
        for (i in runs.indices.sortedBy { runs[it].size }) {
            val run = runs[i]
            if (run.size > MAX_ISLAND) break
            val prev = (i - 1 downTo 0).firstOrNull { kept[it] }?.let { runs[it] }
            val next = (i + 1 until runs.size).firstOrNull { kept[it] }?.let { runs[it] }
            // Dropping a run between them can join runs that were split only by it.
            val joinsPrev = prev != null && reachable(prev.last(), run.first())
            val joinsNext = next != null && reachable(run.last(), next.first())
            val island = when {
                prev != null && next != null -> !(joinsPrev && joinsNext) &&
                    run.size < prev.size && run.size < next.size && reachable(prev.last(), next.first())
                prev != null -> !joinsPrev && run.size < prev.size
                next != null -> !joinsNext && run.size < next.size
                else -> false
            }
            if (island) {
                kept[i] = false
                run.forEach(onReject)
            }
        }
        return runs.filterIndexed { i, _ -> kept[i] }.flatten()
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

    /** Below this (m) an off-line point is GPS jitter / 5-decimal rounding, not a misread. */
    const val SPEED_SPIKE_MIN_M = 8.0

    private fun speedTolerance(kmh: Double) = maxOf(GPS_SPEED_TOLERANCE_KMH, kmh * 0.3)

    private fun isSpeedSpike(prev: Fix, f: Fix, next: Fix): Boolean {
        val span = Duration.between(prev.time, next.time).seconds
        if (span <= 0 || span > 10) return false
        val w = Duration.between(prev.time, f.time).seconds.toDouble() / span
        val deviation = Geo.distanceM(f.lat, f.lon, prev.lat + (next.lat - prev.lat) * w, prev.lon + (next.lon - prev.lon) * w)
        if (deviation <= SPEED_SPIKE_MIN_M) return false
        val v = f.speedKmh.toDouble()
        val through = (Geo.distanceM(prev, f) + Geo.distanceM(f, next)) / span * 3.6
        val skipping = Geo.distanceM(prev, next) / span * 3.6
        return through - v > speedTolerance(v) && abs(skipping - v) <= speedTolerance(v)
    }

    /** Neighbours used on each side for the curve fit, and how far apart (s) they may be. */
    const val KINK_NEIGHBOURS = 4
    const val KINK_WINDOW_S = 6.0
    const val KINK_MIN_POINTS = 6
    /** A kink is this far (m) off the curve, and KINK_RMS_K times further than the neighbours. */
    const val KINK_MIN_M = 6.0
    const val KINK_RMS_K = 5.0

    /** Distance (m) of fix [i] from a curve fitted through its neighbours, and how well they fit it. */
    private class KinkFit(val offM: Double, val rmsM: Double)

    /**
     * Fits x(t), y(t) as quadratics (least squares) through the neighbours of fix [i] -
     * a car's path over a few seconds - in metres around fix i. Null when the neighbours
     * are too few or too far apart in time to judge.
     */
    private fun kinkFit(seg: List<Fix>, i: Int): KinkFit? {
        val f = seg[i]
        val idx = (maxOf(0, i - KINK_NEIGHBOURS)..minOf(seg.lastIndex, i + KINK_NEIGHBOURS)).filter { j ->
            j != i && abs(Duration.between(f.time, seg[j].time).seconds.toDouble()) <= KINK_WINDOW_S
        }
        if (idx.size < KINK_MIN_POINTS) return null
        val my = 111_195.0
        val mx = my * cos(Math.toRadians(f.lat))
        val t = idx.map { Duration.between(f.time, seg[it].time).seconds.toDouble() }
        val y = idx.map { (seg[it].lat - f.lat) * my }
        val x = idx.map { (seg[it].lon - f.lon) * mx }
        val cy = quadraticFit(t, y) ?: return null
        val cx = quadraticFit(t, x) ?: return null
        fun at(c: DoubleArray, tt: Double) = c[0] + c[1] * tt + c[2] * tt * tt
        val rms = sqrt(t.indices.sumOf { k ->
            val dy = y[k] - at(cy, t[k])
            val dx = x[k] - at(cx, t[k])
            dy * dy + dx * dx
        } / t.size)
        return KinkFit(sqrt(cy[0] * cy[0] + cx[0] * cx[0]), rms)
    }

    /**
     * Distance (m) of fix [i] from where the fixes on one side of it, extended as a straight
     * line, put it - the nearer of the two sides. At a real corner the point continues the
     * road on one side; a kink continues neither. Null when neither side has enough fixes.
     */
    private fun sideOffM(seg: List<Fix>, i: Int): Double? {
        val f = seg[i]
        val my = 111_195.0
        val mx = my * cos(Math.toRadians(f.lat))
        fun side(range: IntProgression): Double? {
            val idx = range.filter { j -> abs(Duration.between(f.time, seg[j].time).seconds.toDouble()) <= KINK_WINDOW_S }
            if (idx.size < 3) return null
            val t = idx.map { Duration.between(f.time, seg[it].time).seconds.toDouble() }
            val ay = linearFit(t, idx.map { (seg[it].lat - f.lat) * my }) ?: return null
            val ax = linearFit(t, idx.map { (seg[it].lon - f.lon) * mx }) ?: return null
            return sqrt(ay * ay + ax * ax)
        }
        val before = side(i - 1 downTo maxOf(0, i - KINK_NEIGHBOURS))
        val after = side(i + 1..minOf(seg.lastIndex, i + KINK_NEIGHBOURS))
        return listOfNotNull(before, after).minOrNull()
    }

    /** Least-squares line a + b t, returns a (the value at t = 0). */
    private fun linearFit(t: List<Double>, v: List<Double>): Double? {
        val n = t.size
        val st = t.sum()
        val stt = t.sumOf { it * it }
        val d = n * stt - st * st
        if (abs(d) < 1e-9) return null
        val sv = v.sum()
        val stv = t.indices.sumOf { t[it] * v[it] }
        return (stt * sv - st * stv) / d
    }

    /** Least-squares a + b t + c t^2 via the 3x3 normal equations (Cramer's rule). */
    private fun quadraticFit(t: List<Double>, v: List<Double>): DoubleArray? {
        val s = DoubleArray(5) { p -> t.sumOf { Math.pow(it, p.toDouble()) } }
        val r = DoubleArray(3) { p -> t.indices.sumOf { Math.pow(t[it], p.toDouble()) * v[it] } }
        fun det(a: Array<DoubleArray>) =
            a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) -
                a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0]) +
                a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0])
        val m = Array(3) { row -> DoubleArray(3) { col -> s[row + col] } }
        val d = det(m)
        if (abs(d) < 1e-9) return null
        return DoubleArray(3) { k -> det(Array(3) { row -> DoubleArray(3) { col -> if (col == k) r[row] else m[row][col] } }) / d }
    }

    /**
     * Drops fixes far off the curve through neighbours that fit it tightly (a real corner
     * bends the neighbours too, so their fit is loose and nothing is judged there). Only the
     * worst point of a neighbourhood goes per pass - a kink also pulls its neighbours' fits -
     * then the fits are redone.
     */
    private fun dropKinks(seg: List<Fix>, onReject: (Fix) -> Unit): List<Fix> {
        var cur = seg
        repeat(5) {
            val fits = cur.indices.map { kinkFit(cur, it) }
            val out = cur.indices.filter { i ->
                val k = fits[i] ?: return@filter false
                k.offM > maxOf(KINK_MIN_M, KINK_RMS_K * k.rmsM) &&
                    (sideOffM(cur, i) ?: 0.0) > KINK_MIN_M &&
                    (maxOf(0, i - KINK_NEIGHBOURS)..minOf(cur.lastIndex, i + KINK_NEIGHBOURS)).all { j -> (fits[j]?.offM ?: 0.0) <= k.offM }
            }.toSet()
            if (out.isEmpty()) return cur
            out.forEach { onReject(cur[it]) }
            cur = cur.filterIndexed { i, _ -> i !in out }
        }
        return cur
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
