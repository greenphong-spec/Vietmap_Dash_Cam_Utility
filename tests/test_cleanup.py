"""Tests for core/cleanup.py (FR-12: opt-in, verification-gated source deletion)."""

from __future__ import annotations

import pytest

from dashcam_merger.core.cleanup import delete_sources, sources_for_deletion
from dashcam_merger.core.models import ClipInfo, OutputPart, PartResult


def _clip(path) -> ClipInfo:
    path.write_bytes(b"x")
    return ClipInfo(path=path, size_bytes=1, mtime=0.0)


def _result(status, clips) -> PartResult:
    part = OutputPart(
        camera="Front", part_index=1, part_count=1, clips=clips, signature=None, reencode=False
    )
    return PartResult(part=part, status=status, verify=None, final_path=None, elapsed_seconds=0.0, ffmpeg_exit_code=0)


def test_sources_for_deletion_only_includes_passed_parts(tmp_path):
    passed_clip = _clip(tmp_path / "pass.mp4")
    failed_clip = _clip(tmp_path / "fail.mp4")
    cancelled_clip = _clip(tmp_path / "cancelled.mp4")

    results = [
        _result("PASS", [passed_clip]),
        _result("FAIL", [failed_clip]),
        _result("CANCELLED", [cancelled_clip]),
    ]
    assert sources_for_deletion(results) == [passed_clip]


def test_delete_sources_removes_files(tmp_path):
    clip = _clip(tmp_path / "a.mp4")
    deleted, errors = delete_sources([clip], protected_roots=[tmp_path / "merged"])
    assert deleted == [clip.path.resolve()]
    assert errors == []
    assert not clip.path.exists()


def test_delete_sources_refuses_paths_inside_protected_roots(tmp_path):
    output_dir = tmp_path / "merged"
    output_dir.mkdir()
    clip = _clip(output_dir / "sneaky.mp4")
    deleted, errors = delete_sources([clip], protected_roots=[output_dir])
    assert deleted == []
    assert len(errors) == 1
    assert errors[0][0] == clip.path.resolve()
    assert "refused" in errors[0][1]
    assert clip.path.exists()  # never touched


def test_delete_sources_reports_missing_file_as_error(tmp_path):
    missing = tmp_path / "already_gone.mp4"
    clip = ClipInfo(path=missing, size_bytes=1, mtime=0.0)
    deleted, errors = delete_sources([clip], protected_roots=[tmp_path / "merged"])
    assert deleted == []
    assert len(errors) == 1
    assert errors[0][0] == missing.resolve()
