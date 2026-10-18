"""ffprobe wrapper (Design Section 5.7) - FR-4, FR-7.1."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from .models import ClipInfo, ProbeResult, StreamSignature
from .winpath import win_long

log = logging.getLogger("dashcam_merger.probe")

# Hide the console window of every child process on Windows (Design Section 2).
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


class ProbeError(Exception):
    """ffprobe failed or returned something unusable."""


def build_probe_command(ffprobe: Path, file: Path) -> list[str]:
    """Normative ffprobe command (Design Section 11)."""
    return [
        str(ffprobe),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        win_long(file),
    ]


def ffprobe_json(ffprobe: Path, file: Path, timeout: int = 60) -> dict[str, Any]:
    """Run ffprobe and return its parsed JSON output."""
    cmd = build_probe_command(ffprobe, file)
    log.debug("ffprobe: %s", cmd)
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise ProbeError((proc.stderr or "").strip()[:300] or "ffprobe failed")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ProbeError((proc.stderr or "").strip()[:300] or f"invalid ffprobe output: {e}") from e
    if not isinstance(data, dict):
        raise ProbeError("invalid ffprobe output: not an object")
    return data


def signature_from_json(data: dict[str, Any]) -> StreamSignature:
    """Build the comparison signature from ffprobe JSON (FR-4.2)."""
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("no video stream")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    def as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    return StreamSignature(
        v_codec=str(video.get("codec_name", "")),
        width=as_int(video.get("width")),
        height=as_int(video.get("height")),
        r_frame_rate=str(video.get("r_frame_rate", "")),
        pix_fmt=str(video.get("pix_fmt", "")),
        a_codec=str(audio.get("codec_name", "")) if audio else "none",
        sample_rate=as_int(audio.get("sample_rate")) if audio else 0,
        channels=as_int(audio.get("channels")) if audio else 0,
    )


def _format_numbers(data: dict[str, Any]) -> tuple[float, int]:
    fmt = data.get("format") or {}
    try:
        duration = float(fmt["duration"])
    except (KeyError, TypeError, ValueError) as e:
        raise ProbeError("no duration in ffprobe output") from e
    try:
        size = int(fmt.get("size", 0))
    except (TypeError, ValueError):
        size = 0
    return duration, size


def probe_clip(ffprobe: Path, clip: ClipInfo) -> ProbeResult | None:
    """Probe one source clip; flag it unreadable instead of raising (FR-4.5)."""
    try:
        data = ffprobe_json(ffprobe, clip.path)
        duration, size = _format_numbers(data)
        result = ProbeResult(
            duration=duration,
            size_bytes=size or clip.size_bytes,
            signature=signature_from_json(data),
        )
    except (ProbeError, subprocess.TimeoutExpired, OSError) as e:
        clip.unreadable = True
        clip.unreadable_reason = str(e)[:300] or e.__class__.__name__
        log.warning("Unreadable clip %s: %s", clip.path.name, clip.unreadable_reason)
        return None
    clip.probe = result
    clip.unreadable = False
    clip.unreadable_reason = ""
    return result


def probe_output(ffprobe: Path, file: Path) -> tuple[float, int]:
    """Duration and size of a finished output; raises on any failure (FR-7.1)."""
    data = ffprobe_json(ffprobe, file)
    return _format_numbers(data)


def sample_indices(n: int, every: int) -> list[int]:
    """First, last and every N-th index (FR-4.1)."""
    if n <= 0:
        return []
    step = max(1, every)
    return sorted(set([0, n - 1] + list(range(0, n, step))))
