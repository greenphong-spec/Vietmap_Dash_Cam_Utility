"""Output verification (Design Section 5.11) - FR-7."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .models import ClipInfo, OutputPart, VerifyResult
from .probe import ProbeError, probe_clip, probe_output
from .winpath import win_long

log = logging.getLogger("dashcam_merger.verify")

TOLERANCE_ABS = 1.0
TOLERANCE_REL = 0.001
SIZE_MIN_RATIO = 0.90


def ensure_all_probed(ffprobe: Path, part: OutputPart) -> None:
    """Probe anything still missing and recompute the expected duration (FR-7.2)."""
    for clip in part.clips:
        if clip.probe is None and not clip.unreadable:
            probe_clip(ffprobe, clip)
        if clip.unreadable:
            log.warning(
                "%s contributes 0 s to the expected duration (unreadable)", clip.path.name
            )
    part.expected_duration = sum(c.probe.duration for c in part.clips if c.probe is not None)


def verify_part(ffprobe: Path, part: OutputPart) -> VerifyResult:
    """Compare the written file against the sum of its sources (FR-7.3, FR-7.4)."""
    ensure_all_probed(ffprobe, part)
    expected = part.expected_duration
    expected_size = part.expected_size
    try:
        duration, size = probe_output(ffprobe, part.partial_path)
    except (ProbeError, OSError) as e:  # FR-7.4, e.g. "moov atom not found"
        return VerifyResult(False, 0.0, expected, 0, expected_size, f"ffprobe: {e}")

    tolerance = max(TOLERANCE_ABS, TOLERANCE_REL * expected)
    duration_ok = abs(duration - expected) <= tolerance
    # A re-encode legitimately changes the size, so only check it for stream copy.
    size_ok = True if part.reencode else size >= SIZE_MIN_RATIO * expected_size

    reasons: list[str] = []
    if not duration_ok:
        reasons.append(f"duration {duration:.2f}s vs expected {expected:.2f}s")
    if not size_ok:
        reasons.append(f"size {size} < 90% of {expected_size}")
    return VerifyResult(
        passed=not reasons,
        actual_duration=duration,
        expected_duration=expected,
        actual_size=size,
        expected_size=expected_size,
        reason="; ".join(reasons),
    )


def sources_untouched(clips: list[ClipInfo]) -> tuple[bool, list[str]]:
    """Confirm every source file is still there, same size, same mtime (FR-7.6)."""
    problems: list[str] = []
    for clip in clips:
        try:
            st = os.stat(win_long(clip.path))
        except OSError:
            problems.append(f"{clip.path}: changed or missing")
            continue
        if st.st_size != clip.size_bytes or abs(st.st_mtime - clip.mtime) >= 1:
            problems.append(f"{clip.path}: changed or missing")
    return (not problems), problems
