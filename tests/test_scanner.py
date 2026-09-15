"""Tests for core/scanner.py (Design Section 5.4)."""

from __future__ import annotations

import pytest

from dashcam_merger.core.scanner import scan

EXTS = [".mp4", ".mov", ".avi", ".ts", ".mkv"]


@pytest.fixture()
def tree(tmp_path):
    (tmp_path / "sub" / "deeper").mkdir(parents=True)
    (tmp_path / "merged").mkdir()
    (tmp_path / "top.MP4").write_bytes(b"\0" * 10)
    (tmp_path / "sub" / "mid.mp4").write_bytes(b"\0" * 20)
    (tmp_path / "sub" / "deeper" / "deep.MOV").write_bytes(b"\0" * 30)
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")
    (tmp_path / "merged" / "Front_old.mp4").write_bytes(b"\0" * 40)
    (tmp_path / "INCOMPLETE_DO_NOT_USE_Front.mp4").write_bytes(b"\0" * 50)
    return tmp_path


def names(clips):
    return sorted(c.path.name for c in clips)


def test_recursive_finds_nested_files(tree):
    clips = scan(tree, EXTS, recursive=True, output_subfolder="merged")
    assert names(clips) == ["deep.MOV", "mid.mp4", "top.MP4"]


def test_merged_folder_skipped(tree):
    clips = scan(tree, EXTS, recursive=True, output_subfolder="merged")
    assert all("merged" not in c.path.parts for c in clips)


def test_incomplete_prefix_skipped(tree):
    clips = scan(tree, EXTS, recursive=True, output_subfolder="merged")
    assert all(not c.path.name.startswith("INCOMPLETE_") for c in clips)


def test_extension_match_is_case_insensitive(tree):
    clips = scan(tree, EXTS, recursive=True, output_subfolder="merged")
    assert "top.MP4" in names(clips) and "deep.MOV" in names(clips)


def test_non_video_extension_ignored(tree):
    clips = scan(tree, EXTS, recursive=True, output_subfolder="merged")
    assert all(c.path.suffix.lower() != ".txt" for c in clips)


def test_no_recursive_sees_only_top_level(tree):
    clips = scan(tree, EXTS, recursive=False, output_subfolder="merged")
    assert names(clips) == ["top.MP4"]


def test_sizes_and_mtime_recorded(tree):
    clips = scan(tree, EXTS, recursive=True, output_subfolder="merged")
    by_name = {c.path.name: c for c in clips}
    assert by_name["top.MP4"].size_bytes == 10
    assert by_name["top.MP4"].mtime > 0


def test_result_is_deterministically_sorted(tree):
    first = [str(c.path) for c in scan(tree, EXTS, True, "merged")]
    second = [str(c.path) for c in scan(tree, EXTS, True, "merged")]
    assert first == second == sorted(first, key=str.lower)


def test_empty_folder_returns_empty_list(tmp_path):
    assert scan(tmp_path, EXTS, recursive=True, output_subfolder="merged") == []


def test_missing_folder_raises(tmp_path):
    with pytest.raises(NotADirectoryError):
        scan(tmp_path / "nope", EXTS, recursive=True, output_subfolder="merged")


def test_output_subfolder_match_is_case_insensitive(tmp_path):
    (tmp_path / "MERGED").mkdir()
    (tmp_path / "MERGED" / "old.mp4").write_bytes(b"\0")
    (tmp_path / "keep.mp4").write_bytes(b"\0")
    clips = scan(tmp_path, EXTS, recursive=True, output_subfolder="merged")
    assert names(clips) == ["keep.mp4"]
