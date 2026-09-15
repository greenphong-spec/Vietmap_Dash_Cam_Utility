"""Tests for core/report.py (Design Section 5.12)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from dashcam_merger.core.models import (
    CameraGroup,
    ClipInfo,
    MergePlan,
    OutputPart,
    PartResult,
    RunReport,
    VerifyResult,
)
from dashcam_merger.core.report import (
    REPORT_FILENAME,
    build_plan_summary,
    build_report_text,
    format_duration,
    format_size,
    write_report,
)
from dashcam_merger.core.safe_io import WriteGuard

OUT = Path("D:/DCIM/Movie/merged")


def make_report(results, warnings=None, unclassified=0, unreadable=0, untouched=True):
    clips = [ClipInfo(path=Path("C:/x/a.mp4"), size_bytes=1, mtime=0.0)]
    plan = MergePlan(
        input_dir=Path("D:/DCIM/Movie"),
        output_dir=OUT,
        groups=[CameraGroup("Front", clips)],
        parts=[r.part for r in results],
        unclassified=[clips[0]] * unclassified,
        unreadable=[clips[0]] * unreadable,
        warnings=warnings or [],
    )
    return RunReport(
        run_id="20260831_152516",
        plan=plan,
        results=results,
        sources_untouched=untouched,
        started=datetime(2026, 8, 31, 15, 25, 16),
        finished=datetime(2026, 8, 31, 15, 48, 20),
    )


def make_result(camera, name, files, duration, size, status="PASS", reason=""):
    part = OutputPart(
        camera=camera,
        part_index=1,
        part_count=1,
        clips=[ClipInfo(Path("C:/x") / f"{i}.mp4", 1, 0.0) for i in range(files)],
        signature=None,
        reencode=False,
        output_path=OUT / name,
        expected_duration=duration,
        expected_size=size,
    )
    verify = VerifyResult(status == "PASS", duration, duration, size, size, reason)
    return PartResult(part, status, verify, OUT / name, 12.0, 0)


@pytest.mark.parametrize(
    "seconds, expected",
    [(8584, "2:23:04"), (0, "0:00:00"), (59, "0:00:59"), (3600, "1:00:00"), (86399, "23:59:59")],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    "size, expected",
    [
        (7_720_000_000, "7.19 GB"),
        (4_166_000_000, "3.88 GB"),
        (536_870_912, "512.0 MB"),
        (2048, "2.0 KB"),
    ],
)
def test_format_size(size, expected):
    assert format_size(size) == expected


def test_report_header_is_normative():
    report = make_report([make_result("Front", "Front_a.mp4", 143, 8584, 7_720_000_000)])
    header = build_report_text(report).splitlines()[0]
    assert header.split() == [
        "Camera",
        "Part",
        "Output",
        "file",
        "Files",
        "Duration",
        "Size",
        "Verify",
    ]
    assert header.startswith("Camera".ljust(10) + "  " + "Part".ljust(5) + "  ")


def test_report_rows_and_footer():
    results = [
        make_result(
            "Front", "Front_20260831152516_to_20260831171203.mp4", 143, 8584, 7_720_000_000
        ),
        make_result(
            "Rear", "Rear_20260831152516_to_20260831171203.mp4", 143, 8584, 4_166_000_000
        ),
    ]
    text = build_report_text(make_report(results))
    lines = text.splitlines()
    assert lines[1].startswith("Front")
    assert "143" in lines[1] and "2:23:04" in lines[1] and "7.19 GB" in lines[1]
    assert lines[1].rstrip().endswith("PASS")
    assert lines[2].startswith("Rear")
    assert (
        "Unclassified: 0   Skipped unreadable: 0   Sources untouched: OK" in text
    )
    assert f"Output folder: {OUT}" in text


def test_failure_lines_listed():
    results = [
        make_result("Front", "Front_a.mp4", 10, 600, 1000, status="FAIL", reason="ffprobe: moov")
    ]
    text = build_report_text(make_report(results))
    assert "FAIL" in text
    assert "  Front_a.mp4: ffprobe: moov" in text


def test_warnings_block():
    results = [make_result("Front", "Front_a.mp4", 10, 600, 1000)]
    text = build_report_text(make_report(results, warnings=["Front: gap of 12.0 min"]))
    assert "Warnings:" in text
    assert "  Front: gap of 12.0 min" in text


def test_no_warnings_block_when_none():
    text = build_report_text(make_report([make_result("Front", "F.mp4", 1, 60, 10)]))
    assert "Warnings:" not in text


def test_sources_changed_shown():
    report = make_report([make_result("Front", "F.mp4", 1, 60, 10)], untouched=False)
    report.source_problems = ["C:/x/a.mp4: changed or missing"]
    text = build_report_text(report)
    assert "Sources untouched: CHANGED" in text
    assert "Source problems:" in text


def test_plan_summary_lists_groups_and_counts():
    report = make_report([make_result("Front", "F.mp4", 1, 60, 10)], warnings=["w1"])
    summary = build_plan_summary(report.plan)
    assert "Front: 1 files," in summary
    assert "Unclassified: 0" in summary
    assert "Unreadable: 0" in summary
    assert "Parts to write: 1" in summary
    assert "  w1" in summary


def test_write_report_lands_in_output_folder(tmp_path):
    guard = WriteGuard([tmp_path])
    path = write_report(guard, tmp_path, "hello\n")
    assert path.name == REPORT_FILENAME
    assert path.read_text(encoding="utf-8") == "hello\n"
