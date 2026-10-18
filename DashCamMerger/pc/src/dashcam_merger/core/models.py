"""Data model (Design Section 4). All dataclasses, no behaviour."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

CameraLabel = Literal["Front", "Rear", "Interior", "Unclassified", "Skip"]
OrderSource = Literal["timestamp", "sequence", "mtime"]

# Exactly three real camera labels (Design Section 2).
CAMERA_LABELS: tuple[str, ...] = ("Front", "Rear", "Interior")
ASSIGNABLE_LABELS: tuple[str, ...] = ("Front", "Rear", "Interior", "Skip")


@dataclass(slots=True)
class StreamSignature:  # FR-4.2
    v_codec: str
    width: int
    height: int
    r_frame_rate: str  # exactly as ffprobe reports, e.g. "30000/1001"
    pix_fmt: str
    a_codec: str  # "none" if no audio stream
    sample_rate: int  # 0 if no audio
    channels: int  # 0 if no audio

    def __hash__(self) -> int:
        return hash(
            (
                self.v_codec,
                self.width,
                self.height,
                self.r_frame_rate,
                self.pix_fmt,
                self.a_codec,
                self.sample_rate,
                self.channels,
            )
        )


@dataclass(slots=True)
class ProbeResult:
    duration: float  # seconds, from format.duration
    size_bytes: int  # from format.size
    signature: StreamSignature


@dataclass(slots=True)
class ClipInfo:
    path: Path  # absolute, resolved
    size_bytes: int
    mtime: float  # os.stat().st_mtime
    camera: CameraLabel = "Unclassified"
    camera_token: str = ""  # raw token the profile matched, e.g. "F" or "Rear"
    timestamp: datetime | None = None  # parsed from filename (FR-3.1 a/b)
    sequence: int | None = None  # parsed from filename (FR-3.1 c)
    probe: ProbeResult | None = None  # filled by probe.py when needed
    unreadable: bool = False  # FR-4.5
    unreadable_reason: str = ""


@dataclass(slots=True)
class CameraGroup:
    camera: CameraLabel
    clips: list[ClipInfo]  # sorted chronologically after ordering.py
    order_source: OrderSource = "timestamp"
    # (before, after, seconds)
    gaps: list[tuple[ClipInfo, ClipInfo, float]] = field(default_factory=list)


@dataclass(slots=True)
class OutputPart:
    camera: CameraLabel
    part_index: int  # 1-based
    part_count: int  # total parts for this camera
    clips: list[ClipInfo]
    signature: StreamSignature | None
    reencode: bool  # True only with --reencode and mixed signatures
    list_path: Path | None = None  # concat list file (FR-5)
    output_path: Path | None = None  # final name
    partial_path: Path | None = None  # <name>.partial.mp4
    expected_duration: float = 0.0
    expected_size: int = 0
    split_reason: str = ""  # "", "codec change", "trip gap", "max part size"
    trip_index: int = 1  # 1-based trip of this camera (FR-3.5)
    trip_count: int = 1  # trips for this camera
    trip_part: int = 1  # 1-based part within the trip (codec change / max part size)
    trip_parts: int = 1  # parts in this trip


@dataclass(slots=True)
class MergePlan:
    input_dir: Path
    output_dir: Path
    groups: list[CameraGroup]
    parts: list[OutputPart]
    unclassified: list[ClipInfo]
    unreadable: list[ClipInfo]
    warnings: list[str]  # human readable (FR-2.4, FR-3.3, FR-3.4)
    ffmpeg_commands: list[list[str]] = field(default_factory=list)  # for --dry-run
    not_selected: list[OutputPart] = field(default_factory=list)  # FR-3.6, not exported


@dataclass(slots=True)
class VerifyResult:  # FR-7
    passed: bool
    actual_duration: float
    expected_duration: float
    actual_size: int
    expected_size: int
    reason: str  # "" when passed


@dataclass(slots=True)
class PartResult:
    part: OutputPart
    status: Literal["PASS", "FAIL", "CANCELLED", "SKIPPED"]
    verify: VerifyResult | None
    final_path: Path | None  # where the file ended up (may be INCOMPLETE_ name)
    elapsed_seconds: float
    ffmpeg_exit_code: int | None


@dataclass(slots=True)
class RunReport:
    run_id: str
    plan: MergePlan
    results: list[PartResult]
    sources_untouched: bool
    started: datetime
    finished: datetime
    source_problems: list[str] = field(default_factory=list)
    deleted_sources: list[Path] = field(default_factory=list)  # FR-12
    delete_errors: list[tuple[Path, str]] = field(default_factory=list)  # FR-12

    @property
    def exit_code(self) -> int:
        """130 if any CANCELLED, else 1 if any FAIL, else 0 (Design Section 8.3)."""
        if any(r.status == "CANCELLED" for r in self.results):
            return 130
        if any(r.status == "FAIL" for r in self.results):
            return 1
        return 0


@dataclass(slots=True)
class CameraProfile:
    name: str
    scope: Literal["name", "parent"]  # match filename or parent folder name
    regex: re.Pattern  # must contain a named group "cam"
    map: dict[str, CameraLabel]  # keys upper-cased at load time


@dataclass(slots=True)
class AppConfig:  # FR-10
    video_extensions: list[str]
    output_subfolder: str
    probe_sample_every: int
    gap_warning_seconds: int
    gap_split_seconds: int
    split_on_gap: bool
    max_part_gb: float
    parallel_jobs: int
    ffmpeg_path: Path  # absolute after load
    ffprobe_path: Path  # absolute after load
    camera_profiles: list[CameraProfile]  # user profiles only


@dataclass(slots=True)
class RunOptions:  # everything the CLI/GUI can set per run
    input_dir: Path
    output_dir: Path | None = None  # None -> input_dir / config.output_subfolder
    recursive: bool = True
    camera_map: dict[str, CameraLabel] | None = None  # FR-2.3
    split_on_gap: bool | None = None  # None -> config value
    trip_gap_minutes: float | None = None  # None -> config gap_split_seconds (FR-3.5)
    # FR-3.6 export selection; None = everything. Both given -> a part must match both.
    trips: set[int] | None = None  # trip numbers (CLI --trips), all cameras
    selected_parts: set[str] | None = None  # planner.part_key() values (GUI checkboxes)
    max_part_gb: float | None = None
    reencode: bool = False
    overwrite: bool = False
    keep_lists: bool = False
    parallel: int | None = None
    dry_run: bool = False
    assume_yes: bool = False
    verbose: bool = False
    delete_source: bool = False  # FR-12: permanently delete verified source clips
