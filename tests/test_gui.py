"""Smoke tests for gui.py (Design Section 9). Skipped where Tk is unavailable."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

tkinter = pytest.importorskip("tkinter")

from dashcam_merger.core.config import load_config  # noqa: E402
from dashcam_merger.gui import App, CAMERA_CHOICES  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))


@pytest.fixture()
def app(tmp_path):
    try:
        instance = App(load_config(tmp_path / "missing.json"))
    except tkinter.TclError:
        pytest.skip("no display available for Tk")
    yield instance
    try:
        instance.root.destroy()
    except tkinter.TclError:
        pass


def test_window_title_and_geometry(app):
    from dashcam_merger import __version__

    assert app.root.title() == f"DashCam Merger {__version__}"
    app.root.update_idletasks()
    assert app.root.minsize() == (640, 480)


def test_buttons_start_disabled(app):
    assert str(app.merge_button["state"]) == "disabled"
    assert str(app.cancel_button["state"]) == "disabled"
    assert str(app.open_button["state"]) == "disabled"
    assert str(app.scan_button["state"]) == "normal"


def test_treeview_columns_match_spec(app):
    headings = [app.tree.heading(c)["text"] for c in app.tree["columns"]]
    assert headings == [
        "Camera",
        "Files",
        "Total size",
        "First clip",
        "Last clip",
        "Codec",
        "Order source",
    ]


def test_camera_choices_are_the_four_labels():
    assert CAMERA_CHOICES == ["Front", "Rear", "Interior", "Skip"]


def test_options_read_from_widgets(app, tmp_path):
    app.input_var.set(str(tmp_path))
    app.output_var.set(str(tmp_path / "out"))
    app.split_var.set(True)
    app.reencode_var.set(True)
    app.overwrite_var.set(True)
    app.max_gb_var.set("4")
    opt = app._options(dry_run=True)
    assert opt.input_dir == tmp_path
    assert opt.output_dir == tmp_path / "out"
    assert opt.split_on_gap is True and opt.reencode and opt.overwrite
    assert opt.max_part_gb == 4.0
    assert opt.assume_yes is True  # Scan + Merge click is the confirmation


def test_bad_max_gb_falls_back_to_zero(app, tmp_path):
    app.input_var.set(str(tmp_path))
    app.max_gb_var.set("not a number")
    assert app._options(dry_run=True).max_part_gb == 0.0


def test_log_pane_stays_readonly(app):
    app._log("hello")
    assert str(app.log_pane["state"]) == "disabled"
    assert "hello" in app.log_pane.get("1.0", "end")


def pump(app, seconds: float) -> None:
    """Run the Tk event loop so queued ProgressEvents reach the widgets."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.02)


def wait_for_worker(app, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while app.worker.is_alive() and time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.05)
    pump(app, 0.5)  # let the 100 ms poller drain the queue


@pytest.mark.integration
def test_scan_then_merge_on_fixture_folder(app, ffmpeg_path, ffprobe_path, tmp_path):
    from make_clips import make_clips

    clips_dir = tmp_path / "Movie"
    make_clips(clips_dir, ffmpeg_path, count=3, seconds=1)
    app.cfg.ffmpeg_path = ffmpeg_path
    app.cfg.ffprobe_path = ffprobe_path
    app.input_var.set(str(clips_dir))
    app.output_var.set(str(clips_dir / "merged"))

    app._scan()
    wait_for_worker(app, 60)
    assert len(app.tree.get_children()) == 2  # Front and Rear rows
    assert str(app.merge_button["state"]) == "normal"
    assert not (clips_dir / "merged").exists()  # scan is a dry run

    app._merge()
    wait_for_worker(app, 120)
    outputs = sorted((clips_dir / "merged").glob("*.mp4"))
    assert len(outputs) == 2
    assert str(app.open_button["state"]) == "normal"
    assert "PASS" in app.log_pane.get("1.0", "end")
