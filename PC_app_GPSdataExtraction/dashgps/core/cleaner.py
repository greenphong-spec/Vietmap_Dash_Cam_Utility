"""
Turns raw OCR samples into a clean track. Port of core/TrackCleaner.kt - same steps, same
constants, same order of operations, so a video gives the same track on PC and phone.

OCR occasionally misreads a digit, so every reading is cross-checked against its
neighbours rather than trusted on its own:

 1. parse with the camera profile (unreadable text is dropped);
 2. range checks; (0,0) means the camera had no GPS fix;
 3. time check: the overlay clock must advance together with the video clock, so
    (overlay time - position in video) is nearly constant. A reading whose offset is not
    shared by at least 2 of its 6 nearest neighbours is a misread. (A real recording gap
    inside a merged video shifts the offset for a whole run of frames, so it passes.)
 3b. time repair: a reading whose date/time text was damaged (e.g. "07:1:00" for
    "07:11:00") but whose position is readable gets its time rebuilt from the video
    clock - position in video + the offset its accepted neighbours agree on - and is kept
    only if the rebuilt seconds equal the seconds that were readable;
 4. one fix per overlay second;
 5. position check: a fix that cannot be reached from either time-neighbour at
    <= 250 km/h is a misread;
 5b. island check: a short run of fixes that is unreachable from the track on both
    sides, while that track reaches across it, is a misread repeated on several frames;
 6. spike check: a fix > 40 m off the line between its neighbours, and further off than
    the neighbours are apart, is a misread of the last digits;
 7. speed check: a speed far from its neighbours' median that also disagrees with the
    speed the positions travel is a misread digit; the fix is kept with the GPS speed;
 8. small spike check: a fix > 8 m off the line between its neighbours that makes the
    route faster than the printed speed, while skipping it matches, is a misread;
 9. kink check: a fix > 6 m off a smooth curve through its neighbours, where those
    neighbours fit the curve tightly, and > 6 m off the straight road leading in and the
    one leading out (so a sharp corner stays), is a misread last digit shifting it sideways.

Every sample's outcome is kept (Track.sample_status) for the OCR log.
"""
from __future__ import annotations

import bisect
import math
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Tuple

from .jvm import epoch_seconds, from_epoch_seconds, jfmt, jround, ksum, ldt_to_string, seconds_between
from .models import Fix, OverlayProfile, RejectReason, Sample, Track, TrackStats

EARTH_RADIUS_M = 6_371_008.8


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) * math.sin(dp / 2) + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) * math.sin(dl / 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(max(h, 0.0), 1.0)))


def fix_distance_m(a: Fix, b: Fix) -> float:
    return distance_m(a.lat, a.lon, b.lat, b.lon)


# Offsets closer than this count as "the same clock" (frame vs. overlay tick phase).
TIME_TOLERANCE_S = 2.0
TIME_NEIGHBOURS = 3
TIME_MIN_SUPPORT = 2

# 250 km/h plus a little GPS jitter.
MAX_SPEED_MPS = 70.0
JITTER_M = 30.0

# A gap longer than this starts a new track segment (no line drawn across it).
SEGMENT_GAP_SECONDS = 120

# Assumed travel per point (m/s) to space out a route file that has no timestamps.
UNTIMED_SPEED_MPS = 15.0

# Speeds compared with this many fixes on each side (Hampel filter window).
SPEED_WINDOW = 5
SPEED_MAD_K = 3.0
# Below this deviation (km/h) a speed is never suspicious - normal speed changes.
SPEED_MIN_DEV_KMH = 10.0
# GPS speed is measured over the fixes within this many seconds on each side.
GPS_SPEED_HALF_WINDOW_S = 2
# A suspicious speed this close to the GPS speed is real (hard braking etc.).
GPS_SPEED_TOLERANCE_KMH = 15.0

# The longest run of fixes (s) that can be dropped as a repeated misread.
MAX_ISLAND = 30

# How far (s of video) the neighbours used for a time repair may be.
REPAIR_WINDOW_S = 15.0

# A spike deviates this far (m) from where its neighbours put it ...
SPIKE_MIN_M = 40.0
# Below this (m) an off-line point is GPS jitter / 5-decimal rounding, not a misread.
SPEED_SPIKE_MIN_M = 8.0

# Neighbours used on each side for the curve fit, and how far apart (s) they may be.
KINK_NEIGHBOURS = 4
KINK_WINDOW_S = 6.0
KINK_MIN_POINTS = 6
# A kink is this far (m) off the curve, and KINK_RMS_K times further than the neighbours.
KINK_MIN_M = 6.0
KINK_RMS_K = 5.0

_M_PER_DEG = 111_195.0

Reject = Callable[[Fix, RejectReason], None]


def _rejected_counts(status) -> dict:
    counts = {}
    for r in status:
        if r is not None:
            counts[r] = counts.get(r, 0) + 1
    return {r: counts[r] for r in RejectReason if r in counts}


def clean(samples: List[Sample], profile: OverlayProfile) -> Track:
    status: List[Optional[RejectReason]] = [None] * len(samples)

    def reject(f: Fix, r: RejectReason) -> None:
        status[f.sample_index] = r

    # 1-2. Parse and range-check. Readings without a usable time wait for step 3b.
    partial = []
    fixes: List[Fix] = []
    for i, s in enumerate(samples):
        r = profile.parse(s.text)
        if r is None:
            reason = RejectReason.Unreadable
        elif r.lat == 0.0 and r.lon == 0.0:
            reason = RejectReason.NoGpsFix
        elif abs(r.lat) > 90 or abs(r.lon) > 180 or not 0 <= r.speed_kmh <= 300:
            reason = RejectReason.OutOfRange
        else:
            reason = None
        if reason is not None:
            status[i] = reason
        elif r.time is None:
            partial.append((i, r))
        else:
            fixes.append(Fix(i, s.video_index, s.video_name, s.pos_sec, r.time, r.lat, r.lon, r.speed_kmh, s.text))

    # 3. Time consistency, per video.
    out = []
    for per_video in _group_by(fixes, lambda f: f.video_index).values():
        srt = sorted(per_video, key=lambda f: f.pos_sec)
        if len(srt) < 4:
            out.extend(srt)
            continue
        offsets = [epoch_seconds(f.time) - f.pos_sec for f in srt]
        for i, f in enumerate(srt):
            lo = max(0, i - TIME_NEIGHBOURS)
            hi = min(len(srt) - 1, i + TIME_NEIGHBOURS)
            support = sum(1 for j in range(lo, hi + 1) if j != i and abs(offsets[j] - offsets[i]) <= TIME_TOLERANCE_S)
            if support >= TIME_MIN_SUPPORT:
                out.append(f)
            else:
                reject(f, RejectReason.TimeMismatch)
    fixes = out

    # 3b. Rebuild damaged times from the video clock.
    by_video = {k: sorted(v, key=lambda f: f.pos_sec) for k, v in _group_by(fixes, lambda f: f.video_index).items()}
    for i, r in partial:
        s = samples[i]
        time = repair_time(by_video.get(s.video_index, []), s.pos_sec, r.second_hint)
        if time is None:
            status[i] = RejectReason.Unreadable
        else:
            fixes = fixes + [Fix(i, s.video_index, s.video_name, s.pos_sec, time, r.lat, r.lon, r.speed_kmh, s.text)]

    fixes, speed_corrected = clean_fixes(fixes, True, reject)

    segments = segment(fixes)
    partial_idx = {i for i, _ in partial}
    return Track(
        fixes, segments, _rejected_counts(status), list(samples), list(status), stats(segments),
        time_repaired=sum(1 for f in fixes if f.sample_index in partial_idx),
        speed_corrected=speed_corrected,
    )


def clean_route(route) -> "RouteClean":
    """
    Cleans a route read from a KML / KMZ / GPX file with steps 4-9. Without timestamps
    (the plain KML/KMZ export) points get evenly spaced times - one step per point, sized
    so the typical step is driven at ~54 km/h - and only position checks apply; without
    speeds the speed checks are skipped and the speed is filled in from the positions.
    """
    from .routefiles import RouteClean

    points = route.points
    status: List[Optional[RejectReason]] = [None] * len(points)

    def reject(f: Fix, r: RejectReason) -> None:
        status[f.sample_index] = r

    timed = route.has_time
    has_speed = route.has_speed
    assumed_step_s = None
    if timed:
        times = [p.time for p in points]
    else:
        steps = sorted(
            d for seg in route.segments for a, b in zip(seg, seg[1:])
            if (d := distance_m(a.lat, a.lon, b.lat, b.lon)) > 0.5
        )
        median = steps[len(steps) // 2] if steps else 0.0
        step = max(1, math.ceil(median / UNTIMED_SPEED_MPS))
        assumed_step_s = step
        t = datetime(2000, 1, 1, 0, 0)
        times = []
        for si, seg in enumerate(route.segments):
            if si > 0:
                t = t + timedelta(seconds=SEGMENT_GAP_SECONDS + 3600)  # keep the file's line breaks
            for _ in seg:
                times.append(t)
                t = t + timedelta(seconds=step)

    samples = []
    for i, p in enumerate(points):
        text = f"{jfmt(p.lat, 6)},{jfmt(p.lon, 6)}"
        if p.time is not None:
            text += "," + ldt_to_string(p.time)
        if p.speed_kmh is not None:
            text += f",{jfmt(p.speed_kmh, 1)} km/h"
        samples.append(Sample(0, route.name, float(i), text))
    raw = []
    for i, p in enumerate(points):
        if p.lat == 0.0 and p.lon == 0.0:
            reason = RejectReason.NoGpsFix
        elif abs(p.lat) > 90 or abs(p.lon) > 180 or not 0.0 <= (p.speed_kmh if p.speed_kmh is not None else 0.0) <= 300.0:
            reason = RejectReason.OutOfRange
        else:
            reason = None
        if reason is not None:
            status[i] = reason
        else:
            raw.append(Fix(i, 0, route.name, float(i), times[i], p.lat, p.lon,
                           jround(p.speed_kmh if p.speed_kmh is not None else 0.0), samples[i].text))
    before = stats(segment(sorted(raw, key=lambda f: f.time)))

    fixes, speed_corrected = clean_fixes(raw, has_speed, reject)
    if timed and not has_speed:
        fixes = [
            f.copy(speed_kmh=jround(g if (g := _gps_speed_kmh(seg, i)) is not None else 0.0))
            for seg in segment(fixes) for i, f in enumerate(seg)
        ]

    segments = segment(fixes)
    track = Track(fixes, segments, _rejected_counts(status), samples, list(status), stats(segments),
                  speed_corrected=speed_corrected)
    return RouteClean(route, track, before, assumed_step_s)


def clean_fixes(fixes: List[Fix], has_speed: bool, reject: Reject) -> Tuple[List[Fix], int]:
    """
    Steps 4-9: everything that judges fixes by time, position and speed alone, shared by
    video extraction and by cleaning an exported route file. Without has_speed (a route
    file with no speeds) the speed steps 7-8 are skipped. Returns the kept fixes and how
    many speeds were corrected.
    """
    # 4. One fix per overlay second (keep the earliest frame).
    seen = set()
    out = []
    for f in sorted(fixes, key=lambda f: (f.time, f.video_index, f.pos_sec)):
        if f.time in seen:
            reject(f, RejectReason.Duplicate)
        else:
            seen.add(f.time)
            out.append(f)
    fixes = out

    # 5. Position jumps; two passes so a misread next to another misread is caught too.
    for _ in range(2):
        if len(fixes) < 2:
            continue
        cur = fixes
        fixes = []
        for i, f in enumerate(cur):
            prev = cur[i - 1] if i > 0 else None
            nxt = cur[i + 1] if i + 1 < len(cur) else None
            if (prev is not None and _reachable(prev, f)) or (nxt is not None and _reachable(f, nxt)):
                fixes.append(f)
            else:
                reject(f, RejectReason.PositionJump)

    # 5b. Misread islands: the same misread on several frames in a row (e.g. "0.38659"
    #     for "10.38659" twice) is reachable from itself, so step 5 keeps it.
    fixes = _drop_islands(fixes, lambda f: reject(f, RejectReason.PositionJump))

    # 6. Sideways spikes: a misread last digits moves a point ~10-200 m off the road,
    #    which is still "reachable", but the neighbours on both sides agree with each
    #    other and not with it.
    fixes = _filter_triples(fixes, _is_spike, reject)

    # 7. Speed misreads: the position is fine, so the fix stays with a corrected speed.
    speed_corrected = 0
    if has_speed:
        out = []
        for seg in segment(fixes):
            corrected = _correct_speeds(seg)
            speed_corrected += sum(1 for a, b in zip(corrected, seg) if a.speed_kmh != b.speed_kmh)
            out.extend(corrected)
        fixes = out

    # 8. Small spikes, refereed by the (now trusted) printed speed: a misread digit can
    #    put a point only ~10-40 m off the road, under the step-6 limit, but driving
    #    through it is faster than the overlay says while skipping it matches.
    if has_speed:
        fixes = _filter_triples(fixes, _is_speed_spike, reject)

    # 9. Sideways kinks: a misread last digit can shift a point ~5-20 m sideways, which
    #    barely changes the distance driven, so step 8 misses it; compare each point
    #    with a smooth curve through its neighbours instead.
    fixes = [f for seg in segment(fixes) for f in _drop_kinks(seg, lambda f: reject(f, RejectReason.Spike))]

    return fixes, speed_corrected


def _filter_triples(fixes: List[Fix], is_bad, reject: Reject) -> List[Fix]:
    """Two passes dropping inner fixes for which is_bad(prev, f, next) (neighbours from the pass input)."""
    for _ in range(2):
        if len(fixes) < 3:
            continue
        cur = fixes
        fixes = []
        for i, f in enumerate(cur):
            if 0 < i < len(cur) - 1 and is_bad(cur[i - 1], f, cur[i + 1]):
                reject(f, RejectReason.Spike)
            else:
                fixes.append(f)
    return fixes


def _correct_speeds(seg: List[Fix]) -> List[Fix]:
    """
    Hampel filter on the printed speed, refereed by the speed the cleaned positions
    travel: a speed far from the median of its neighbours (more than SPEED_MAD_K scaled
    MADs, at least SPEED_MIN_DEV_KMH) that also disagrees with the GPS speed is a misread
    and is replaced by the GPS speed (or by the median when there is no GPS speed).
    seg is one segment, sorted by time.
    """
    if len(seg) < 3:
        return seg
    speeds = [float(f.speed_kmh) for f in seg]
    out = []
    for i, f in enumerate(seg):
        window = speeds[max(0, i - SPEED_WINDOW):min(len(seg), i + SPEED_WINDOW + 1)]
        med = _median(window)
        mad = _median([abs(v - med) for v in window])
        limit = max(SPEED_MAD_K * 1.4826 * mad, SPEED_MIN_DEV_KMH)
        if abs(speeds[i] - med) <= limit:
            out.append(f)
            continue
        gps = _gps_speed_kmh(seg, i)
        if gps is None:
            out.append(f.copy(speed_kmh=jround(med)))
        elif abs(speeds[i] - gps) <= max(GPS_SPEED_TOLERANCE_KMH, gps * 0.25):
            out.append(f)
        else:
            out.append(f.copy(speed_kmh=jround(gps)))
    return out


def _gps_speed_kmh(seg: List[Fix], i: int) -> Optional[float]:
    """Distance travelled along the fixes within +-GPS_SPEED_HALF_WINDOW_S of fix i, km/h."""
    t = seg[i].time
    a = i
    while a > 0 and seconds_between(seg[a - 1].time, t) <= GPS_SPEED_HALF_WINDOW_S:
        a -= 1
    b = i
    while b < len(seg) - 1 and seconds_between(t, seg[b + 1].time) <= GPS_SPEED_HALF_WINDOW_S:
        b += 1
    dt = seconds_between(seg[a].time, seg[b].time)
    if dt <= 0:
        return None
    dist = ksum(fix_distance_m(seg[k], seg[k + 1]) for k in range(a, b))
    return dist / dt * 3.6


def _median(v: List[float]) -> float:
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2


def _drop_islands(fixes: List[Fix], on_reject) -> List[Fix]:
    """
    Splits the track into runs at every unreachable step; consecutive fixes inside a run
    are reachable from each other. Smallest first, a run of at most MAX_ISLAND fixes is
    dropped if it is shorter than the nearest kept run on each side, does not join both of
    them, and those runs reach each other across it (at a track end: shorter than the one
    run beside it and not joining it).
    """
    runs: List[List[Fix]] = []
    for f in fixes:
        if runs and _reachable(runs[-1][-1], f):
            runs[-1].append(f)
        else:
            runs.append([f])
    if len(runs) < 2:
        return fixes
    kept = [True] * len(runs)
    for i in sorted(range(len(runs)), key=lambda k: len(runs[k])):
        run = runs[i]
        if len(run) > MAX_ISLAND:
            break
        prev = next((runs[k] for k in range(i - 1, -1, -1) if kept[k]), None)
        nxt = next((runs[k] for k in range(i + 1, len(runs)) if kept[k]), None)
        # Dropping a run between them can join runs that were split only by it.
        joins_prev = prev is not None and _reachable(prev[-1], run[0])
        joins_next = nxt is not None and _reachable(run[-1], nxt[0])
        if prev is not None and nxt is not None:
            island = (not (joins_prev and joins_next) and len(run) < len(prev) and len(run) < len(nxt)
                      and _reachable(prev[-1], nxt[0]))
        elif prev is not None:
            island = not joins_prev and len(run) < len(prev)
        elif nxt is not None:
            island = not joins_next and len(run) < len(nxt)
        else:
            island = False
        if island:
            kept[i] = False
            for f in run:
                on_reject(f)
    return [f for k, run in enumerate(runs) if kept[k] for f in run]


def repair_time(srt: List[Fix], pos_sec: float, second_hint: Optional[int]) -> Optional[datetime]:
    """
    Time for a frame at pos_sec from the accepted fixes of the same video around it: they
    must agree (within TIME_TOLERANCE_S) on overlay-minus-video offset. Of the whole
    seconds nearest to that estimate, the one whose seconds equal second_hint wins.
    """
    if second_hint is None:
        return None
    idx = bisect.bisect_left([f.pos_sec for f in srt], pos_sec)
    near = [srt[k] for k in range(max(0, idx - TIME_NEIGHBOURS), min(len(srt), idx + TIME_NEIGHBOURS))
            if abs(srt[k].pos_sec - pos_sec) <= REPAIR_WINDOW_S]
    if len(near) < TIME_MIN_SUPPORT:
        return None
    offsets = sorted(epoch_seconds(f.time) - f.pos_sec for f in near)
    if offsets[-1] - offsets[0] > TIME_TOLERANCE_S:
        return None
    estimate = jround(offsets[len(offsets) // 2] + pos_sec)
    for d in (0, -1, 1):
        t = from_epoch_seconds(estimate + d)
        if t.second == second_hint:
            return t
    return None


def _is_spike(prev: Fix, f: Fix, nxt: Fix) -> bool:
    """Deviates SPIKE_MIN_M from where its neighbours put it, and further than they are apart."""
    span = seconds_between(prev.time, nxt.time)
    if span <= 0 or span > 10:
        return False  # too far apart to judge
    w = seconds_between(prev.time, f.time) / span
    exp_lat = prev.lat + (nxt.lat - prev.lat) * w
    exp_lon = prev.lon + (nxt.lon - prev.lon) * w
    deviation = distance_m(f.lat, f.lon, exp_lat, exp_lon)
    return deviation > SPIKE_MIN_M and deviation > fix_distance_m(prev, nxt)


def _speed_tolerance(kmh: float) -> float:
    return max(GPS_SPEED_TOLERANCE_KMH, kmh * 0.3)


def _is_speed_spike(prev: Fix, f: Fix, nxt: Fix) -> bool:
    span = seconds_between(prev.time, nxt.time)
    if span <= 0 or span > 10:
        return False
    w = seconds_between(prev.time, f.time) / span
    deviation = distance_m(f.lat, f.lon, prev.lat + (nxt.lat - prev.lat) * w, prev.lon + (nxt.lon - prev.lon) * w)
    if deviation <= SPEED_SPIKE_MIN_M:
        return False
    v = float(f.speed_kmh)
    through = (fix_distance_m(prev, f) + fix_distance_m(f, nxt)) / span * 3.6
    skipping = fix_distance_m(prev, nxt) / span * 3.6
    return through - v > _speed_tolerance(v) and abs(skipping - v) <= _speed_tolerance(v)


def _kink_fit(seg: List[Fix], i: int) -> Optional[Tuple[float, float]]:
    """
    Fits x(t), y(t) as quadratics (least squares) through the neighbours of fix i - a
    car's path over a few seconds - in metres around fix i. Returns (distance of fix i
    from the curve, rms of the neighbours), or None when the neighbours are too few or
    too far apart in time to judge.
    """
    f = seg[i]
    idx = [j for j in range(max(0, i - KINK_NEIGHBOURS), min(len(seg) - 1, i + KINK_NEIGHBOURS) + 1)
           if j != i and abs(float(seconds_between(f.time, seg[j].time))) <= KINK_WINDOW_S]
    if len(idx) < KINK_MIN_POINTS:
        return None
    my = _M_PER_DEG
    mx = my * math.cos(math.radians(f.lat))
    t = [float(seconds_between(f.time, seg[j].time)) for j in idx]
    y = [(seg[j].lat - f.lat) * my for j in idx]
    x = [(seg[j].lon - f.lon) * mx for j in idx]
    cy = _quadratic_fit(t, y)
    if cy is None:
        return None
    cx = _quadratic_fit(t, x)
    if cx is None:
        return None

    def at(c, tt):
        return c[0] + c[1] * tt + c[2] * tt * tt

    rms = math.sqrt(ksum(
        (y[k] - at(cy, t[k])) * (y[k] - at(cy, t[k])) + (x[k] - at(cx, t[k])) * (x[k] - at(cx, t[k]))
        for k in range(len(t))
    ) / len(t))
    return math.sqrt(cy[0] * cy[0] + cx[0] * cx[0]), rms


def _side_off_m(seg: List[Fix], i: int) -> Optional[float]:
    """
    Distance (m) of fix i from where the fixes on one side of it, extended as a straight
    line, put it - the nearer of the two sides. At a real corner the point continues the
    road on one side; a kink continues neither. None when neither side has enough fixes.
    """
    f = seg[i]
    my = _M_PER_DEG
    mx = my * math.cos(math.radians(f.lat))

    def side(rng) -> Optional[float]:
        idx = [j for j in rng if abs(float(seconds_between(f.time, seg[j].time))) <= KINK_WINDOW_S]
        if len(idx) < 3:
            return None
        t = [float(seconds_between(f.time, seg[j].time)) for j in idx]
        ay = _linear_fit(t, [(seg[j].lat - f.lat) * my for j in idx])
        if ay is None:
            return None
        ax = _linear_fit(t, [(seg[j].lon - f.lon) * mx for j in idx])
        if ax is None:
            return None
        return math.sqrt(ay * ay + ax * ax)

    before = side(range(i - 1, max(0, i - KINK_NEIGHBOURS) - 1, -1))
    after = side(range(i + 1, min(len(seg) - 1, i + KINK_NEIGHBOURS) + 1))
    found = [v for v in (before, after) if v is not None]
    return min(found) if found else None


def _linear_fit(t: List[float], v: List[float]) -> Optional[float]:
    """Least-squares line a + b t, returns a (the value at t = 0)."""
    n = len(t)
    st = ksum(t)
    stt = ksum(x * x for x in t)
    d = n * stt - st * st
    if abs(d) < 1e-9:
        return None
    sv = ksum(v)
    stv = ksum(t[k] * v[k] for k in range(n))
    return (stt * sv - st * stv) / d


def _det3(a) -> float:
    return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
            - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
            + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))


def _quadratic_fit(t: List[float], v: List[float]):
    """Least-squares a + b t + c t^2 via the 3x3 normal equations (Cramer's rule)."""
    s = [ksum(math.pow(x, p) for x in t) for p in range(5)]
    r = [ksum(math.pow(t[k], p) * v[k] for k in range(len(t))) for p in range(3)]
    m = [[s[row + col] for col in range(3)] for row in range(3)]
    d = _det3(m)
    if abs(d) < 1e-9:
        return None
    return [
        _det3([[r[row] if col == k else m[row][col] for col in range(3)] for row in range(3)]) / d
        for k in range(3)
    ]


def _drop_kinks(seg: List[Fix], on_reject) -> List[Fix]:
    """
    Drops fixes far off the curve through neighbours that fit it tightly (a real corner
    bends the neighbours too, so their fit is loose and nothing is judged there). Only the
    worst point of a neighbourhood goes per pass - a kink also pulls its neighbours' fits -
    then the fits are redone.
    """
    cur = seg
    for _ in range(5):
        fits = [_kink_fit(cur, i) for i in range(len(cur))]
        out = set()
        for i, k in enumerate(fits):
            if k is None:
                continue
            off, rms = k
            if not off > max(KINK_MIN_M, KINK_RMS_K * rms):
                continue
            side = _side_off_m(cur, i)
            if not (side if side is not None else 0.0) > KINK_MIN_M:
                continue
            lo, hi = max(0, i - KINK_NEIGHBOURS), min(len(cur) - 1, i + KINK_NEIGHBOURS)
            if all((fits[j][0] if fits[j] is not None else 0.0) <= off for j in range(lo, hi + 1)):
                out.add(i)
        if not out:
            return cur
        for i in sorted(out):
            on_reject(cur[i])
        cur = [f for i, f in enumerate(cur) if i not in out]
    return cur


def _reachable(a: Fix, b: Fix) -> bool:
    dt = max(abs(seconds_between(a.time, b.time)), 1)
    return fix_distance_m(a, b) <= MAX_SPEED_MPS * dt + JITTER_M


def segment(fixes: List[Fix]) -> List[List[Fix]]:
    out: List[List[Fix]] = []
    for f in fixes:
        if not out or seconds_between(out[-1][-1].time, f.time) > SEGMENT_GAP_SECONDS:
            out.append([f])
        else:
            out[-1].append(f)
    return out


def stats(segments: List[List[Fix]]) -> TrackStats:
    all_fixes = [f for seg in segments for f in seg]
    return TrackStats(
        points=len(all_fixes),
        segments=len(segments),
        distance_m=ksum(ksum(fix_distance_m(a, b) for a, b in zip(seg, seg[1:])) for seg in segments),
        start=all_fixes[0].time if all_fixes else None,
        end=all_fixes[-1].time if all_fixes else None,
        moving_seconds=sum(seconds_between(seg[0].time, seg[-1].time) for seg in segments),
        max_speed_kmh=max((f.speed_kmh for f in all_fixes), default=0),
    )


def _group_by(items, key) -> dict:
    out: dict = {}
    for it in items:
        out.setdefault(key(it), []).append(it)
    return out
