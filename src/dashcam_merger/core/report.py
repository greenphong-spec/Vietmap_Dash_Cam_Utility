"""Human-readable summary and report (Design Section 5.12) - FR-9."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from .models import MergePlan, OutputPart, RunReport
from .safe_io import WriteGuard

log = logging.getLogger("dashcam_merger.report")

REPORT_FILENAME = "merge_report.txt"

# Column widths (Design Section 5.12), separated by two spaces.
W_CAMERA = 10
W_PART = 5
W_FILE_MIN = 40
W_FILES = 6
W_DURATION = 10
W_SIZE = 10
W_VERIFY = 6

KIB = 1024
MIB = 1024**2
GIB = 1024**3


def format_duration(seconds: float) -> str:
    """H:MM:SS."""
    total = int(round(max(0.0, seconds)))
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def format_size(bytes_: int) -> str:
    """GB with 2 decimals, MB with 1, otherwise KB."""
    if bytes_ >= GIB:
        return f"{bytes_ / GIB:.2f} GB"
    if bytes_ >= MIB:
        return f"{bytes_ / MIB:.1f} MB"
    return f"{bytes_ / KIB:.1f} KB"


def _row(values: list[str], file_width: int) -> str:
    widths = [W_CAMERA, W_PART, file_width, W_FILES, W_DURATION, W_SIZE, W_VERIFY]
    return "  ".join(v.ljust(w) for v, w in zip(values, widths)).rstrip()


def build_report_text(report: RunReport) -> str:
    """The report table shown on screen and written to merged\\merge_report.txt."""
    plan = report.plan
    names = [
        (r.final_path.name if r.final_path else (r.part.output_path.name if r.part.output_path else "-"))
        for r in report.results
    ]
    file_width = max([W_FILE_MIN] + [len(n) for n in names]) if names else W_FILE_MIN

    lines = [
        _row(
            ["Camera", "Part", "Output file", "Files", "Duration", "Size", "Verify"],
            file_width,
        )
    ]
    for result, name in zip(report.results, names):
        part = result.part
        duration = result.verify.actual_duration if result.verify else part.expected_duration
        size = result.verify.actual_size if result.verify else 0
        lines.append(
            _row(
                [
                    str(part.camera),
                    str(part.part_index),
                    name,
                    str(len(part.clips)),
                    format_duration(duration),
                    format_size(size),
                    result.status,
                ],
                file_width,
            )
        )

    if plan.not_selected:
        lines.append(_not_selected_line(plan))
    lines.append(
        f"Unclassified: {len(plan.unclassified)}   "
        f"Skipped unreadable: {len(plan.unreadable)}   "
        f"Sources untouched: {'OK' if report.sources_untouched else 'CHANGED'}"
    )
    lines.append(f"Output folder: {plan.output_dir}")

    failures = [r for r in report.results if r.status in ("FAIL", "CANCELLED")]
    if failures:
        lines.append("")
        for result in failures:
            name = result.part.output_path.name if result.part.output_path else "-"
            reason = result.verify.reason if result.verify and result.verify.reason else result.status
            lines.append(f"  {name}: {reason}")

    if report.source_problems:
        lines.append("")
        lines.append("Source problems:")
        lines.extend(f"  {p}" for p in report.source_problems)

    if plan.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  {w}" for w in plan.warnings)

    if report.deleted_sources or report.delete_errors:
        lines.append("")
        lines.append(f"Source clips deleted: {len(report.deleted_sources)}")
        if report.delete_errors:
            lines.append(f"Source clips NOT deleted: {len(report.delete_errors)}")
            lines.extend(f"  {path}: {reason}" for path, reason in report.delete_errors)

    return "\n".join(lines) + "\n"


def part_label(part: OutputPart) -> str:
    """'trip 2/9', 'trip 2/9 part 1/2', 'part 1/2' or '' for a single file (FR-3.5)."""
    label = f"trip {part.trip_index}/{part.trip_count}" if part.trip_count > 1 else ""
    if part.trip_parts > 1:
        label = f"{label} part {part.trip_part}/{part.trip_parts}".strip()
    return label


def part_span(part: OutputPart) -> tuple[str, str]:
    """Recording start and end of a part; the end drops the date when it is the same day."""
    first, last = part.clips[0], part.clips[-1]
    if first.timestamp is None or last.timestamp is None:
        return "-", "-"
    stop = last.timestamp + timedelta(seconds=last.probe.duration if last.probe else 0.0)
    end_format = "%H:%M:%S" if stop.date() == first.timestamp.date() else "%Y-%m-%d %H:%M:%S"
    return first.timestamp.strftime("%Y-%m-%d %H:%M:%S"), stop.strftime(end_format)


def _part_lines(plan: MergePlan) -> list[str]:
    """One line per output file, so trips can be checked before merging."""
    lines: list[str] = []
    for part in plan.parts:
        start, end = part_span(part)
        label = part_label(part)
        lines.append(
            f"  {part.camera}{' ' + label if label else ''}: {start} - {end}, "
            f"{len(part.clips)} files, {format_duration(part.expected_duration)}, "
            f"{format_size(part.expected_size)}"
        )
    return lines


def _not_selected_line(plan: MergePlan) -> str:
    """'Not selected (not exported): 2 of 4 files - Front trip 1/2, Rear trip 1/2'."""
    total = len(plan.parts) + len(plan.not_selected)
    names = ", ".join(
        f"{p.camera}{' ' + part_label(p) if part_label(p) else ''}" for p in plan.not_selected
    )
    return f"Not selected (not exported): {len(plan.not_selected)} of {total} files - {names}"


def build_plan_summary(plan: MergePlan) -> str:
    """Confirmation text shown before anything is written (FR-2.4)."""
    lines: list[str] = []
    for group in plan.groups:
        total = sum(c.size_bytes for c in group.clips)
        lines.append(f"{group.camera}: {len(group.clips)} files, {format_size(total)}")
    lines.append(f"Unclassified: {len(plan.unclassified)}")
    lines.append(f"Unreadable: {len(plan.unreadable)}")
    lines.append(f"Parts to write: {len(plan.parts)}")
    lines.extend(_part_lines(plan))
    if plan.not_selected:
        lines.append(_not_selected_line(plan))
    lines.append(f"Output folder: {plan.output_dir}")
    if plan.warnings:
        lines.append("Warnings:")
        lines.extend(f"  {w}" for w in plan.warnings)
    return "\n".join(lines) + "\n"


def write_report(guard: WriteGuard, output_dir: Path, text: str) -> Path:
    """Save the report next to the merged files (FR-9.2)."""
    path = output_dir / REPORT_FILENAME
    guard.write_text(path, text)
    log.info("Report written to %s", path)
    return path
