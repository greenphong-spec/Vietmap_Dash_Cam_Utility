"""Event channel between the core and the CLI/GUI (Design Section 7)."""

from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import Callable, Literal, Protocol

from .models import MergePlan, OutputPart, PartResult

EventKind = Literal[
    "log",
    "probe",
    "plan",
    "dry_run",
    "part_start",
    "progress",
    "part_done",
    "report",
    "error",
]


@dataclass(slots=True)
class ProgressEvent:
    kind: EventKind
    message: str = ""
    part: OutputPart | None = None
    plan: MergePlan | None = None
    result: PartResult | None = None
    done_seconds: float = 0.0
    total_seconds: float = 0.0
    bytes_written: int = 0
    elapsed: float = 0.0
    commands: list[list[str]] | None = None


class ProgressSink(Protocol):
    def emit(self, ev: ProgressEvent) -> None: ...


class QueueSink:
    """Used by the GUI: events are drained on the Tk main thread."""

    def __init__(self, q: "queue.Queue[ProgressEvent]") -> None:
        self._queue = q

    def emit(self, ev: ProgressEvent) -> None:
        self._queue.put(ev)


class CallbackSink:
    """Used by the CLI: the callback runs on the calling thread."""

    def __init__(self, fn: Callable[[ProgressEvent], None]) -> None:
        self._fn = fn

    def emit(self, ev: ProgressEvent) -> None:
        self._fn(ev)


class NullSink:
    """Discards everything; handy for tests and non-interactive runs."""

    def emit(self, ev: ProgressEvent) -> None:  # noqa: D102 - trivial
        return None


def percent_of(ev: ProgressEvent) -> float:
    if ev.total_seconds <= 0:
        return 0.0
    # ffmpeg's first -progress packet can carry a small negative out_time_ms.
    return min(100.0, max(0.0, 100.0 * ev.done_seconds / ev.total_seconds))


def mb_per_s(ev: ProgressEvent) -> float:
    if ev.elapsed < 0.5:
        return 0.0
    return ev.bytes_written / 1_048_576 / ev.elapsed


def eta_seconds(ev: ProgressEvent) -> float | None:
    if ev.done_seconds < 1.0 or ev.total_seconds <= 0:
        return None
    return (ev.total_seconds - ev.done_seconds) * ev.elapsed / ev.done_seconds


def _hms(seconds: float) -> str:
    total = int(max(0.0, seconds))
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def describe(ev: ProgressEvent) -> str:
    """One-line human text for an event; shared by the CLI and the GUI."""
    if ev.kind != "progress" or ev.part is None:
        return ev.message
    part = ev.part
    eta = eta_seconds(ev)
    eta_text = _hms(eta) if eta is not None else "--:--:--"
    return (
        f"{part.camera} part {part.part_index}/{part.part_count}: "
        f"{percent_of(ev):5.1f}%  {mb_per_s(ev):6.1f} MB/s  "
        f"elapsed {_hms(ev.elapsed)}  ETA {eta_text}"
    )
