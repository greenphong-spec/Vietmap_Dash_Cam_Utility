"""Concat lists and the ffmpeg runner (Design Section 5.10) - FR-5, FR-6."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

from .models import ClipInfo, OutputPart
from .progress import ProgressEvent, ProgressSink
from .safe_io import WriteGuard

log = logging.getLogger("dashcam_merger.concat")

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
STDERR_TAIL_LINES = 50
POLL_SECONDS = 0.25


def escape_concat_path(p: Path) -> str:
    """One concat-demuxer line with an absolute, single-quoted path (FR-5.2)."""
    s = p.resolve().as_posix()
    s = s.replace("'", "'\\''")
    return f"file '{s}'"


def write_concat_list(guard: WriteGuard, list_path: Path, clips: list[ClipInfo]) -> None:
    """Write the concat list as UTF-8 without BOM (FR-5.1, FR-5.3)."""
    body = "\n".join(escape_concat_path(c.path) for c in clips) + "\n"
    guard.write_text(list_path, body)
    log.info("Concat list %s lists %d clips", list_path.name, len(clips))


def build_command(ffmpeg: Path, part: OutputPart) -> list[str]:
    """Normative ffmpeg command (FR-6.1, FR-4.4)."""
    codec_args = (
        ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac"]
        if part.reencode
        else ["-c", "copy"]
    )
    return [
        str(ffmpeg),
        "-y",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(part.list_path),
        *codec_args,
        "-movflags",
        "+faststart",
        str(part.partial_path),
    ]


def parse_progress_line(line: str) -> tuple[str, str] | None:
    """Split one ffmpeg -progress 'key=value' line."""
    line = line.strip()
    if not line or "=" not in line:
        return None
    key, _, value = line.partition("=")
    return key.strip(), value.strip()


def _pump(stream, sink: "queue.Queue[str | None]") -> None:
    try:
        for line in stream:
            sink.put(line)
    finally:
        sink.put(None)


def run_ffmpeg(
    cmd: list[str],
    expected_duration: float,
    cancel: threading.Event,
    progress: ProgressSink,
    part: OutputPart,
) -> int:
    """Run ffmpeg, stream progress events, honour cancellation (FR-6.6, FR-6.7).

    Returns the ffmpeg exit code, or -1 when cancelled.
    """
    log.info("Running: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    stdout_q: "queue.Queue[str | None]" = queue.Queue()
    stderr_tail: list[str] = []

    def drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_tail.append(line.rstrip())
            del stderr_tail[:-STDERR_TAIL_LINES]

    threading.Thread(target=_pump, args=(proc.stdout, stdout_q), daemon=True).start()
    err_thread = threading.Thread(target=drain_stderr, daemon=True)
    err_thread.start()

    t0 = time.monotonic()
    done_seconds = 0.0
    bytes_written = 0
    finished = False
    cancelled = False

    while not finished:
        if cancel.is_set():
            cancelled = True
            break
        try:
            line = stdout_q.get(timeout=POLL_SECONDS)
        except queue.Empty:
            continue
        if line is None:
            break
        pair = parse_progress_line(line)
        if pair is None:
            continue
        key, value = pair
        if key == "out_time_ms":  # microseconds despite the name
            try:
                done_seconds = int(value) / 1_000_000
            except ValueError:
                pass
        elif key == "total_size":
            try:
                bytes_written = int(value)
            except ValueError:
                pass
        elif key == "progress":
            progress.emit(
                ProgressEvent(
                    kind="progress",
                    part=part,
                    done_seconds=done_seconds,
                    total_seconds=expected_duration,
                    bytes_written=bytes_written,
                    elapsed=time.monotonic() - t0,
                )
            )
            if value == "end":
                finished = True

    if cancelled:
        proc.kill()
        proc.wait()
        err_thread.join(timeout=2)
        log.warning("Cancelled: %s", part.output_path.name if part.output_path else "part")
        return -1

    rc = proc.wait()
    err_thread.join(timeout=5)
    tail = "\n".join(stderr_tail[-10:])
    log.info("ffmpeg exit %d for %s", rc, part.partial_path.name if part.partial_path else "?")
    if tail:
        log.log(logging.ERROR if rc != 0 else logging.DEBUG, "ffmpeg stderr tail:\n%s", tail)
    return rc


def ffmpeg_version(ffmpeg: Path) -> str:
    """First line of `ffmpeg -version`, for the About dialog."""
    try:
        proc = subprocess.run(
            [str(ffmpeg), "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"ffmpeg not available ({e})"
    return (proc.stdout or "").splitlines()[0] if proc.stdout else "unknown ffmpeg version"
