"""Turn scanned clips into a concrete merge plan (Design Section 5.8).

Covers FR-2.4, FR-3.3-3.5, FR-4.3-4.4, FR-6.3, FR-6.8.
"""

from __future__ import annotations

import logging
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor

from . import cameras, concat, ordering
from .models import (
    AppConfig,
    CameraGroup,
    ClipInfo,
    MergePlan,
    OutputPart,
    RunOptions,
    StreamSignature,
)
from .probe import probe_clip, sample_indices
from .progress import ProgressEvent, ProgressSink
from .safe_io import WriteGuard

log = logging.getLogger("dashcam_merger.planner")

GIB = 1024**3
PROBE_WORKERS = 8  # ffprobe is process-bound and read-only; see _probe_everything


def _effective(value, fallback):
    return fallback if value is None else value


def _timestamp_token(clip: ClipInfo) -> str:
    """FR-6.3: timestamp when known, otherwise the file stem."""
    if clip.timestamp is not None:
        return clip.timestamp.strftime("%Y%m%d%H%M%S")
    return clip.path.stem


def _probe_sampled(
    ffprobe: Path, group: CameraGroup, every: int, progress: ProgressSink
) -> list[ClipInfo]:
    """FR-4.1: probe the first, the last and every N-th clip of a group.

    Kept for callers that explicitly want sampling; the plan itself now probes
    every clip (see _probe_everything).
    """
    sampled: list[ClipInfo] = []
    for index in sample_indices(len(group.clips), every):
        clip = group.clips[index]
        if clip.probe is None and not clip.unreadable:
            probe_clip(ffprobe, clip)
        sampled.append(clip)
        progress.emit(
            ProgressEvent(kind="probe", message=f"Probed {clip.path.name}")
        )
    return sampled


def _probe_everything(ffprobe: Path, group: CameraGroup, progress: ProgressSink) -> None:
    """Probe every clip of a group concurrently.

    # DESIGN-GAP: FR-4.1's sampling (first, last, every N-th) cannot satisfy
    # FR-4.5 or the Spec section 10 acceptance tests - a corrupt or differently
    # encoded clip in an unsampled position is only discovered after ffmpeg has
    # already written a bad output. Probing is read-only and process-bound, so
    # it is done for every clip across a small thread pool: measured 58 ms per
    # probe, about 12 s for 1000 clips, inside the Spec section 8 30 s target.
    # config.probe_sample_every is retained for anyone wanting the old default.
    """
    pending = [c for c in group.clips if c.probe is None and not c.unreadable]
    if not pending:
        return
    workers = min(PROBE_WORKERS, len(pending))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for clip in pool.map(lambda c: (probe_clip(ffprobe, c), c)[1], pending):
            progress.emit(ProgressEvent(kind="probe", message=f"Probed {clip.path.name}"))


def part_key(part: OutputPart) -> str:
    """Stable id of an output part across Scan and Merge (FR-3.6): camera + first clip."""
    return f"{part.camera}|{part.clips[0].path.name}"


def _select(
    parts: list[OutputPart], opt: RunOptions, warnings: list[str]
) -> tuple[list[OutputPart], list[OutputPart]]:
    """Split parts into (exported, not selected) per opt.trips / opt.selected_parts."""
    if opt.trips is None and opt.selected_parts is None:
        return parts, []
    if opt.trips is not None:
        existing = {p.trip_index for p in parts}
        missing = sorted(opt.trips - existing)
        if missing:
            highest = max(existing) if existing else 0
            warnings.append(
                f"trip(s) {', '.join(map(str, missing))} do not exist (trips 1-{highest})"
            )
    chosen: list[OutputPart] = []
    skipped: list[OutputPart] = []
    for part in parts:
        ok = (opt.trips is None or part.trip_index in opt.trips) and (
            opt.selected_parts is None or part_key(part) in opt.selected_parts
        )
        (chosen if ok else skipped).append(part)
    log.info("Selection: %d of %d part(s) exported", len(chosen), len(parts))
    return chosen, skipped


def trip_gap_seconds(cfg: AppConfig, opt: RunOptions) -> float:
    """Break that starts a new trip (FR-3.5): the run's minutes, else config.json."""
    if opt.trip_gap_minutes is not None:
        return float(opt.trip_gap_minutes) * 60.0
    return float(cfg.gap_split_seconds)


def _split_group(
    group: CameraGroup, cfg: AppConfig, opt: RunOptions, fully_probed: bool
) -> list[tuple[list[ClipInfo], str]]:
    """Walk the ordered clips and start a new part whenever a rule fires."""
    split_on_gap = bool(_effective(opt.split_on_gap, cfg.split_on_gap))
    split_on_gap = split_on_gap and group.order_source == "timestamp"
    trip_gap = trip_gap_seconds(cfg, opt)
    max_part_gb = float(_effective(opt.max_part_gb, cfg.max_part_gb))
    max_bytes = max_part_gb * GIB if max_part_gb > 0 else 0.0

    parts: list[tuple[list[ClipInfo], str]] = []
    current: list[ClipInfo] = []
    current_bytes = 0
    current_sig: StreamSignature | None = None
    reason = ""

    previous: ClipInfo | None = None
    for clip in group.clips:
        new_reason = ""
        if current:
            gap = ordering.gap_seconds(previous, clip) if previous is not None else None
            sig = clip.probe.signature if clip.probe else None
            if (
                fully_probed
                and not opt.reencode
                and sig is not None
                and current_sig is not None
                and sig != current_sig
            ):
                new_reason = "codec change"  # FR-4.4
            elif split_on_gap and gap is not None and gap > trip_gap:
                new_reason = "trip gap"  # FR-3.5
            elif max_bytes and current_bytes + clip.size_bytes > max_bytes:
                new_reason = "max part size"  # FR-6.8
        if new_reason:
            parts.append((current, reason))
            current, current_bytes, current_sig, reason = [], 0, None, new_reason
        current.append(clip)
        previous = clip
        current_bytes += clip.size_bytes
        if current_sig is None and clip.probe is not None:
            current_sig = clip.probe.signature
    if current:
        parts.append((current, reason))
    return parts


def _number_trips(reasons: list[str]) -> list[tuple[int, int, int, int]]:
    """(trip, trips, part in trip, parts in trip) for each chunk; a trip gap starts a trip."""
    trip_of: list[int] = []
    trip = 0
    for i, reason in enumerate(reasons):
        if i == 0 or reason == "trip gap":
            trip += 1
        trip_of.append(trip)
    counts = {t: trip_of.count(t) for t in set(trip_of)}
    out: list[tuple[int, int, int, int]] = []
    seen: dict[int, int] = {}
    for t in trip_of:
        seen[t] = seen.get(t, 0) + 1
        out.append((t, trip, seen[t], counts[t]))
    return out

def build_plan(
    clips: list[ClipInfo],
    cfg: AppConfig,
    opt: RunOptions,
    ffprobe: Path,
    run_id: str,
    progress: ProgressSink,
    guard: WriteGuard | None = None,
    output_dir: Path | None = None,
    temp_dir: Path | None = None,
) -> MergePlan:
    """Build the full merge plan without writing anything.

    # DESIGN-GAP: Design 5.8 omits guard/output_dir/temp_dir from the signature
    # but step 7 needs all three (unique_name, output paths, list paths). They
    # are optional keyword arguments so the documented call form still works.
    """
    out_dir = output_dir or (opt.output_dir or opt.input_dir / cfg.output_subfolder)
    tmp_dir = temp_dir or Path("%TEMP%") / "DashCamMerger" / run_id
    write_guard = guard or WriteGuard([out_dir, tmp_dir])
    warnings: list[str] = []
    unreadable: list[ClipInfo] = []

    # 1. Group.
    groups, unclassified = cameras.group_by_camera(clips)

    # 2. Annotate and order.
    ordering.annotate(clips)
    for group in groups:
        ordering.order_group(group)
        if group.order_source == "mtime":  # FR-3.3
            warnings.append(
                f"{group.camera}: order based on file modification time, please spot-check"
            )
        elif group.order_source == "sequence":
            warnings.append(f"{group.camera}: order based on sequence number in filename")

    parts: list[OutputPart] = []
    for group in groups:
        # 3. Probe (FR-4.1, amended - see _probe_everything).
        _probe_everything(ffprobe, group, progress)
        fully_probed = True

        # 4. Signature check (FR-4.3 / FR-4.4) and unreadable files (FR-4.5).
        bad = [c for c in group.clips if c.unreadable]
        if bad:
            unreadable.extend(bad)
            group.clips = [c for c in group.clips if not c.unreadable]
            for clip in bad:
                warnings.append(
                    f"{group.camera}: skipped unreadable file {clip.path.name} "
                    f"({clip.unreadable_reason})"
                )
        if not group.clips:
            warnings.append(f"{group.camera}: no readable clips left, nothing to merge")
            continue

        # 5. Gaps (FR-3.4) and 6. split into parts. A gap that starts a new trip is
        # shown by the trip list itself, so only stops inside a trip are warned about.
        ordering.detect_gaps(group, cfg.gap_warning_seconds)
        chunks = _split_group(group, cfg, opt, fully_probed)
        trip_starts = {id(c[0]) for c, reason in chunks if reason == "trip gap"}
        for before, after, seconds in group.gaps:
            if id(after) not in trip_starts:
                warnings.append(
                    f"{group.camera}: gap of {seconds / 60:.1f} min between "
                    f"{before.path.name} and {after.path.name}"
                )
        trip_numbers = _number_trips([reason for _, reason in chunks])
        group_signatures = {c.probe.signature for c in group.clips if c.probe is not None}
        for index, (chunk_clips, reason) in enumerate(chunks, start=1):
            chunk_sigs = {c.probe.signature for c in chunk_clips if c.probe is not None}
            reencode = opt.reencode and len(group_signatures) > 1
            part = OutputPart(
                camera=group.camera,
                part_index=index,
                part_count=len(chunks),
                clips=chunk_clips,
                signature=None if reencode or len(chunk_sigs) != 1 else next(iter(chunk_sigs)),
                reencode=reencode,
                split_reason=reason,
                trip_index=trip_numbers[index - 1][0],
                trip_count=trip_numbers[index - 1][1],
                trip_part=trip_numbers[index - 1][2],
                trip_parts=trip_numbers[index - 1][3],
                expected_duration=sum(
                    c.probe.duration for c in chunk_clips if c.probe is not None
                ),
                expected_size=sum(c.size_bytes for c in chunk_clips),
            )
            parts.append(part)
            if reason and reason != "trip gap":
                warnings.append(
                    f"{group.camera} part {index}: new part started because of {reason}"
                )

    # 7. Name outputs (FR-6.3).
    for part in parts:
        first, last = part.clips[0], part.clips[-1]
        stem = f"{part.camera}_{_timestamp_token(first)}_to_{_timestamp_token(last)}"
        if part.trip_count > 1:
            stem = f"{stem}_trip{part.trip_index}"
        if part.trip_parts > 1:
            stem = f"{stem}_part{part.trip_part}"
        output_path = out_dir / f"{stem}.mp4"
        if output_path.exists() and not opt.overwrite:  # FR-6.2
            output_path = write_guard.unique_name(output_path)
        final_stem = output_path.stem
        part.output_path = output_path
        part.partial_path = out_dir / f"{final_stem}.partial.mp4"
        part.list_path = (out_dir if opt.keep_lists else tmp_dir) / f"{final_stem}.txt"

    # 8. Selection (FR-3.6): names above are from the whole card, so trip 3 stays _trip3.
    parts, not_selected = _select(parts, opt, warnings)

    plan = MergePlan(
        input_dir=opt.input_dir,
        output_dir=out_dir,
        groups=groups,
        parts=parts,
        unclassified=unclassified,
        unreadable=unreadable,
        warnings=warnings,
        not_selected=not_selected,
    )
    # 9. Commands (shown by --dry-run).
    plan.ffmpeg_commands = [concat.build_command(cfg.ffmpeg_path, p) for p in parts]
    log.info("Plan: %d group(s), %d part(s)", len(groups), len(parts))
    progress.emit(ProgressEvent(kind="plan", plan=plan))
    return plan
