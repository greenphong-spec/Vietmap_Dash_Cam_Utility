"""Tests for core/safe_io.py and core/winpath.py (Design Section 5.3, 5.9)."""

from __future__ import annotations

import os

import pytest

from dashcam_merger.core.safe_io import SafetyViolation, WriteGuard
from dashcam_merger.core.winpath import win_long


@pytest.fixture()
def dirs(tmp_path):
    inp = tmp_path / "input"
    out = tmp_path / "input" / "merged"
    tmp = tmp_path / "temp"
    for d in (inp, out, tmp):
        d.mkdir(parents=True, exist_ok=True)
    return inp, out, tmp


def test_write_into_input_folder_is_refused(dirs):
    inp, out, tmp = dirs
    guard = WriteGuard([out, tmp])
    with pytest.raises(SafetyViolation) as excinfo:
        guard.write_text(inp / "evil.txt", "no")
    assert "Refusing to write outside" in str(excinfo.value)


def test_write_into_output_subfolder_is_allowed(dirs):
    _, out, tmp = dirs
    guard = WriteGuard([out, tmp])
    guard.mkdir(out / "sub")
    guard.write_text(out / "sub" / "file.txt", "hello")
    assert (out / "sub" / "file.txt").read_text(encoding="utf-8") == "hello"


def test_write_text_has_no_bom_and_lf_newlines(dirs):
    _, out, tmp = dirs
    guard = WriteGuard([out, tmp])
    target = out / "list.txt"
    guard.write_text(target, "line1\nline2\n")
    raw = target.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw == b"line1\nline2\n"


def test_unique_name_appends_2_then_3(dirs):
    _, out, tmp = dirs
    guard = WriteGuard([out, tmp])
    target = out / "Front.mp4"
    assert guard.unique_name(target) == target
    target.write_bytes(b"x")
    second = guard.unique_name(target)
    assert second.name == "Front_2.mp4"
    second.write_bytes(b"x")
    assert guard.unique_name(target).name == "Front_3.mp4"


def test_rename_checks_both_sides(dirs):
    inp, out, tmp = dirs
    guard = WriteGuard([out, tmp])
    src = out / "a.partial.mp4"
    src.write_bytes(b"x")
    guard.rename(src, out / "a.mp4")
    assert (out / "a.mp4").is_file()
    outside = inp / "a.mp4"
    with pytest.raises(SafetyViolation):
        guard.rename(out / "a.mp4", outside)


def test_guard_has_no_delete_method():
    assert not hasattr(WriteGuard, "delete")
    assert not hasattr(WriteGuard, "unlink")
    assert not hasattr(WriteGuard, "remove")


def test_win_long_prefixes_on_windows(tmp_path):
    result = win_long(tmp_path)
    if os.name == "nt":
        assert result.startswith("\\\\?\\")
        assert win_long(tmp_path) == result  # idempotent shape
    else:
        assert result == str(tmp_path)
