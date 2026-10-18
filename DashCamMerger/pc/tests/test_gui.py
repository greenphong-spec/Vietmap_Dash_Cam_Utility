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
        "Export",
        "Camera",
        "Trip",
        "Files",
        "Duration",
        "Size",
        "Start",
        "End",
        "Codec",
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
    assert opt.trip_gap_minutes == 15.0
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
    assert len(app.tree.get_children()) == 2  # one trip each for Front and Rear
    assert str(app.merge_button["state"]) == "normal"
    assert not (clips_dir / "merged").exists()  # scan is a dry run

    app._merge()
    wait_for_worker(app, 120)
    outputs = sorted((clips_dir / "merged").glob("*.mp4"))
    assert len(outputs) == 2
    assert str(app.open_button["state"]) == "normal"
    assert "PASS" in app.log_pane.get("1.0", "end")


# ---- trips (FR-3.5, FR-10.3)
def test_trip_controls_start_from_config(tmp_path):
    cfg = load_config(tmp_path / "missing.json")
    cfg.split_on_gap = False
    cfg.gap_split_seconds = 1200
    try:
        instance = App(cfg)
    except tkinter.TclError:
        pytest.skip("no display available for Tk")
    try:
        assert instance.split_var.get() is False
        assert instance.trip_gap_var.get() == "20"
    finally:
        instance.root.destroy()


def test_split_off_is_passed_on(app, tmp_path):
    app.input_var.set(str(tmp_path))
    app.split_var.set(False)
    assert app._options(dry_run=True).split_on_gap is False


def test_trip_gap_box_value_is_used(app, tmp_path):
    app.input_var.set(str(tmp_path))
    app.trip_gap_var.set("7.5")
    assert app._options(dry_run=True).trip_gap_minutes == 7.5


@pytest.mark.parametrize("text", ["", "abc", "0", "-3"])
def test_bad_trip_gap_falls_back_to_config(app, tmp_path, text):
    app.input_var.set(str(tmp_path))
    app.trip_gap_var.set(text)
    assert app._options(dry_run=True).trip_gap_minutes == 15.0
    assert app.trip_gap_var.get() == "15"


def test_trip_settings_saved_to_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"output_subfolder": "out", "gap_split_seconds": 900}', encoding="utf-8")
    try:
        instance = App(load_config(path), path)
    except tkinter.TclError:
        pytest.skip("no display available for Tk")
    try:
        instance.trip_gap_var.set("20")
        instance.split_var.set(False)
        instance._save_trip_settings()
    finally:
        instance.root.destroy()
    saved = load_config(path)
    assert saved.gap_split_seconds == 1200 and saved.split_on_gap is False
    assert saved.output_subfolder == "out"  # other settings kept


def test_trip_settings_not_saved_without_config_path(app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app.trip_gap_var.set("20")
    app._save_trip_settings()
    assert not list(tmp_path.iterdir())


# ---- export selection (FR-3.6)
def fake_plan(tmp_path):
    from datetime import datetime, timedelta

    from dashcam_merger.core.models import (
        ClipInfo, MergePlan, OutputPart, ProbeResult, StreamSignature,
    )

    sig = StreamSignature("hevc", 2560, 1440, "30/1", "yuv420p", "none", 0, 0)

    parts = []
    for camera, suffix in (("Front", "F"), ("Rear", "R")):
        for trip in (1, 2, 3):
            stamp = datetime(2026, 10, 14, 7, 0) + timedelta(hours=trip)
            clip = ClipInfo(path=tmp_path / f"{stamp:%Y%m%d%H%M%S}_000000{trip}{suffix}.MP4",
                            size_bytes=1000, mtime=0.0)
            clip.timestamp = stamp
            clip.probe = ProbeResult(180.0, 1000, sig)
            parts.append(OutputPart(camera, trip, 3, [clip], None, False, trip_index=trip,
                                    trip_count=3, expected_duration=180.0, expected_size=1000))
    return MergePlan(tmp_path, tmp_path / "merged", [], parts, [], [], [])


def rows(app):
    return [app.tree.item(r, "values") for r in app.tree.get_children()]


def test_scan_table_sorted_by_trip_all_ticked(app, tmp_path):
    app._fill_tree(fake_plan(tmp_path))
    assert [(v[0], v[1], v[2]) for v in rows(app)] == [
        ("\u2611", "Front", "trip 1/3"), ("\u2611", "Rear", "trip 1/3"),
        ("\u2611", "Front", "trip 2/3"), ("\u2611", "Rear", "trip 2/3"),
        ("\u2611", "Front", "trip 3/3"), ("\u2611", "Rear", "trip 3/3"),
    ]
    assert app.selection_var.get() == "Export 6 of 6 files"


def test_toggle_rows_and_merge_options(app, tmp_path):
    app.input_var.set(str(tmp_path))
    app._fill_tree(fake_plan(tmp_path))
    ids = app.tree.get_children()
    app._toggle(ids[0])  # Front trip 1 off
    app._toggle(ids[1])  # Rear trip 1 off
    assert rows(app)[0][0] == "\u2610" and rows(app)[2][0] == "\u2611"
    assert app.selection_var.get() == "Export 4 of 6 files"
    opt = app._options(dry_run=False)
    assert len(opt.selected_parts) == 4
    assert not any("_0000001" in key for key in opt.selected_parts)
    assert app._options(dry_run=True).selected_parts is None  # a scan plans everything
    app._toggle(ids[0])
    assert app.selection_var.get() == "Export 5 of 6 files"


def test_select_none_disables_merge_and_select_all_restores(app, tmp_path):
    app._fill_tree(fake_plan(tmp_path))
    app._select_all(False)
    assert app.export_keys == set()
    assert str(app.merge_button["state"]) == "disabled"
    assert {v[0] for v in rows(app)} == {"\u2610"}
    app._select_all(True)
    assert len(app.export_keys) == 6
    assert str(app.merge_button["state"]) == "normal"


def test_merge_plan_does_not_replace_the_scan_table(app, tmp_path):
    from dashcam_merger.core.progress import ProgressEvent

    plan = fake_plan(tmp_path)
    app._fill_tree(plan)
    app._toggle(app.tree.get_children()[0])
    app._dry_run = False  # a merge is running; its plan holds only the ticked rows
    plan.parts = plan.parts[1:2]
    app._handle(ProgressEvent(kind="plan", plan=plan))
    assert len(app.tree.get_children()) == 6
    assert len(app.export_keys) == 5
