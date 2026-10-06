"""
Orchestrates test frame -> extraction -> cleaning -> export. PC counterpart of the
Android app's ExtractSession / RouteCleanSession, without UI state: the CLI and the GUI
both call these functions and get progress through a callback.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from . import frames
from .core import cleaner, routefiles
from .core.exporters import ExportFormat, Exporters
from .core.jvm import jfmt
from .core.models import OverlayProfile, Sample, Track
from .core.routefiles import RouteClean
from .core.timezone import Zone
from .ocr import OcrPool, default_workers

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".ts", ".avi", ".m4v")


@dataclass
class Progress:
    video_index: int
    video_count: int
    video_name: str
    pos_sec: float
    duration_sec: float
    frames_read: int
    fraction: float


@dataclass
class TestResult:
    crop: np.ndarray
    pos_sec: float
    text: str
    parsed: str


@dataclass
class Extraction:
    videos: List[Path]
    profile: OverlayProfile
    samples: List[Sample]
    track: Track
    seconds: float
    cancelled: bool = False

    @property
    def title(self) -> str:
        return title(self.track, self.videos)


@dataclass
class SavedFile:
    path: Path
    format: ExportFormat


def stem(p: Path) -> str:
    name = p.name
    return name.rsplit(".", 1)[0] if "." in name else name


def title(t: Track, videos: Sequence[Path]) -> str:
    if len(videos) == 1:
        return stem(videos[0])
    fmt = "%Y%m%d%H%M%S"
    start = t.stats.start.strftime(fmt) if t.stats.start else "start"
    end = t.stats.end.strftime(fmt) if t.stats.end else "end"
    return f"Track_{start}_to_{end}"


def format_reading(r) -> str:
    if r is None:
        return "not recognised"
    when = r.time.strftime("%Y-%m-%d %H:%M:%S") if r.time else f"time unreadable (…:{r.second_hint:02d})"
    return f"{when}   {r.speed_kmh} km/h   {jfmt(r.lat, 5)}, {jfmt(r.lon, 5)}"


def test_frame(video: Path, profile: OverlayProfile, at_sec: float = 10.0) -> Optional[TestResult]:
    """Decode one frame ~at_sec into the video, OCR it and show what was read."""
    from .ocr import Ocr
    for pos, crop in frames.sample(str(video), 1.0, profile.crop, start_sec=at_sec, max_frames=1):
        text = Ocr().read(crop)
        return TestResult(crop, pos, text, format_reading(profile.parse(text)))
    return None


def extract(
    videos: Sequence[Path],
    profile: OverlayProfile,
    interval_sec: float = 1.0,
    cancel: Optional[threading.Event] = None,
    on_progress: Callable[[Progress], None] = lambda p: None,
    workers: Optional[int] = None,
) -> Extraction:
    """Sample every video, OCR every sampled frame, then clean everything into one track."""
    cancel = cancel or threading.Event()
    started = time.monotonic()
    videos = [Path(v) for v in videos]
    durations = []
    for v in videos:
        try:
            durations.append(frames.info(str(v)).duration_sec)
        except Exception:
            durations.append(0.0)
    total = max(sum(durations), 1.0)
    collected: List[Sample] = []
    pool = OcrPool()
    n_workers = workers or default_workers()
    in_flight = n_workers * 3
    last_ui = [0.0]

    with ThreadPoolExecutor(n_workers, thread_name_prefix="ocr") as ex:
        for index, video in enumerate(videos):
            if cancel.is_set():
                break
            before = sum(durations[:index])
            pending: deque = deque()

            def drain_one():
                pos, fut = pending.popleft()
                collected.append(Sample(index, video.name, pos, fut.result()))

            def progress(pos: float):
                now = time.monotonic()
                if now - last_ui[0] > 0.25:
                    last_ui[0] = now
                    on_progress(Progress(index, len(videos), video.name, pos, durations[index],
                                         len(collected), min((before + pos) / total, 1.0)))

            for pos, crop in frames.sample(str(video), interval_sec, profile.crop, cancel, on_progress=progress):
                pending.append((pos, ex.submit(pool.read, crop)))
                if len(pending) >= in_flight:
                    drain_one()
            while pending:
                drain_one()

    track = cleaner.clean(collected, profile)
    return Extraction(videos, profile, collected, track, time.monotonic() - started, cancel.is_set())


def summary(x: Extraction, zone: Zone) -> str:
    t = x.track
    lines = Exporters(zone).summary(t, x.title)
    good = len(t.fixes)
    pct = 100.0 * good / t.samples if t.samples > 0 else 0.0
    lines += f"Frames with valid GPS: {good} of {t.samples} ({jfmt(pct, 0)}%)\n"
    if t.time_repaired > 0:
        lines += f"Time rebuilt from video clock: {t.time_repaired} (damaged time text)\n"
    if t.speed_corrected > 0:
        lines += f"Speed corrected from GPS: {t.speed_corrected} (misread speed text)\n"
    if t.rejected:
        lines += "Dropped: " + ", ".join(f"{v} {k.label}" for k, v in t.rejected.items()) + "\n"
    secs = int(x.seconds)
    lines += f"\nRead {len(x.samples)} frames in {secs // 60} min {secs % 60} s"
    if x.cancelled:
        lines += " (cancelled - partial result)"
    return lines


def unique_path(folder: Path, file_stem: str, ext: str) -> Path:
    """Never overwrites: an existing name gets _2, _3, ..."""
    taken = {p.name.lower() for p in folder.iterdir()} if folder.exists() else set()
    name = f"{file_stem}.{ext}"
    n = 2
    while name.lower() in taken:
        name = f"{file_stem}_{n}.{ext}"
        n += 1
    return folder / name


def _create(path: Path, write: Callable) -> None:
    """Creates a new file ('x' mode: fails rather than overwrite) and lets write fill it."""
    with open(path, "xb") as f:
        write(f)


def export(
    x: Extraction,
    folder: Path,
    formats: Iterable[ExportFormat],
    zone: Zone,
    combine: bool = True,
    google_earth_extras: bool = False,
) -> List[SavedFile]:
    """
    Write the chosen formats. combine = one file set for all videos; otherwise one set per
    video (each cleaned on its own).
    """
    folder.mkdir(parents=True, exist_ok=True)
    exporters = Exporters(zone)
    wanted = [f for f in ExportFormat if f in set(formats)]
    if combine or len(x.videos) == 1:
        jobs: List[Tuple[str, Track]] = [(x.title, x.track)]
    else:
        jobs = []
        for i, v in enumerate(x.videos):
            t = cleaner.clean([s for s in x.samples if s.video_index == i], x.profile)
            if t.fixes:
                jobs.append((stem(v), t))
    written = []
    for name, t in jobs:
        for fmt in wanted:
            path = unique_path(folder, name + fmt.suffix, fmt.ext)
            _create(path, lambda out, fmt=fmt, t=t, name=name: exporters.write(fmt, t, name, out, google_earth_extras))
            written.append(SavedFile(path, fmt))
    return written


# ---- Route-file cleaning -----------------------------------------------------------------


def load_route(path: Path, zone: Zone) -> RouteClean:
    """Read a KML / KMZ / GPX file and run the same checks as extraction (steps 4-9)."""
    route = routefiles.read(path.name, path.read_bytes(), zone)
    return cleaner.clean_route(route)


def route_summary(c: RouteClean) -> str:
    r, t = c.route, c.track
    removed = len(r.points) - len(t.fixes)
    s = f"{r.name}\n"
    s += f"{r.format.name}: {len(r.points)} points, {len(r.segments)} line(s), "
    s += "with times" if r.has_time else "no times"
    s += ", with speeds\n" if r.has_speed else ", no speeds\n"
    s += f"Removed: {removed} point(s)"
    if t.rejected:
        s += " - " + ", ".join(f"{v} {k.label}" for k, v in t.rejected.items())
    s += "\n"
    if t.speed_corrected > 0:
        s += f"Speeds corrected from GPS: {t.speed_corrected}\n"
    s += f"Distance: {jfmt(c.before.distance_m / 1000, 2)} km -> {jfmt(t.stats.distance_m / 1000, 2)} km\n"
    if r.has_speed:
        s += f"Max speed: {c.before.max_speed_kmh} -> {t.stats.max_speed_kmh} km/h\n"
    if r.has_time and not r.has_speed:
        s += "Speeds in the saved file are computed from the positions.\n"
    if c.assumed_step_s is not None:
        s += (f"No timestamps in this file: positions checked assuming ~{c.assumed_step_s} s between points; "
              "speed checks skipped. Clean the GPX export for the most thorough result.\n")
    return s


def save_route(c: RouteClean, folder: Path, zone: Zone, google_earth_extras: bool = False) -> SavedFile:
    """Saves <name>_clean.<same format>; the source file is never changed."""
    folder.mkdir(parents=True, exist_ok=True)
    fmt = c.route.format
    name = c.route.stem + "_clean"
    path = unique_path(folder, name, fmt.ext)
    _create(path, lambda out: Exporters(zone).write(fmt, c.track, name, out, google_earth_extras, timed=c.route.has_time))
    return SavedFile(path, fmt)


def find_videos(paths: Iterable[str]) -> List[Path]:
    """Files as given; folders expanded to their video files. Sorted by name like the Android picker."""
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out.extend(q for q in p.iterdir() if q.suffix.lower() in VIDEO_EXTENSIONS)
        else:
            out.append(p)
    return sorted(out, key=lambda q: q.name.lower())
