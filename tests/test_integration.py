"""Integration tests IT-1 ... IT-10 and the safety grep (Design Sections 12, 13.3)."""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from dashcam_merger.cli import main as cli_main
from dashcam_merger.core.config import load_config
from dashcam_merger.core.models import RunOptions
from dashcam_merger.core.pipeline import AutoConfirmer, run
from dashcam_merger.core.progress import NullSink

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from make_clips import make_clips, make_timed_clips  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.integration


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def snapshot(folder: Path) -> dict[str, str]:
    return {p.name: sha256(p) for p in sorted(folder.glob("*.MP4"))}


def duration_of(ffprobe: Path, path: Path) -> float:
    out = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


@pytest.fixture()
def cfg(ffmpeg_path, ffprobe_path, tmp_path):
    config = load_config(tmp_path / "missing.json")
    config.ffmpeg_path = ffmpeg_path
    config.ffprobe_path = ffprobe_path
    return config


def run_cli(argv: list[str]) -> int:
    return cli_main(argv)


# ---------------------------------------------------------------- IT-1
def test_it1_six_front_six_rear_merge_cleanly(cfg, ffmpeg_path, ffprobe_path, tmp_path):
    clips_dir = tmp_path / "Movie"
    make_clips(clips_dir, ffmpeg_path, count=6, seconds=2)
    before = snapshot(clips_dir)

    code = run_cli(["--input", str(clips_dir), "--yes"])
    assert code == 0

    merged = clips_dir / "merged"
    outputs = sorted(merged.glob("*.mp4"))
    assert len(outputs) == 2
    assert {o.name.split("_")[0] for o in outputs} == {"Front", "Rear"}
    for output in outputs:
        assert duration_of(ffprobe_path, output) == pytest.approx(12.0, abs=1.0)
    assert (merged / "merge_report.txt").is_file()
    assert "PASS" in (merged / "merge_report.txt").read_text(encoding="utf-8")
    assert not list(merged.glob("*.partial.mp4"))
    assert snapshot(clips_dir) == before  # sources byte-for-byte unchanged


# ------------------------------------------------- clip length (FR-3.4a)
def _merge_timed(cfg, ffmpeg_path, ffprobe_path, folder, clips, split_on_gap=None):
    make_timed_clips(folder, ffmpeg_path, clips)
    opt = RunOptions(input_dir=folder, assume_yes=True, split_on_gap=split_on_gap)
    report = run(cfg, opt, AutoConfirmer(), NullSink(), threading.Event())
    gap_warnings = [w for w in report.plan.warnings if "gap of" in w]
    return report, gap_warnings


@pytest.mark.parametrize(
    "clips",
    [
        # 3-min cycle, names one clip length apart +- 1 s (Vietmap M1).
        [("20261017130506", 180), ("20261017130807", 180), ("20261017131106", 180)],
        # 5-min cycle: start-to-start 300-301 s must not be reported as a gap.
        [("20261017080000", 300), ("20261017080501", 300), ("20261017081001", 300)],
        # Setting changed from 1 to 3 min between two clips of one folder.
        [("20261013070000", 60), ("20261013070100", 60), ("20261013070200", 180)],
    ],
    ids=["3min", "5min", "1min-then-3min"],
)
def test_clip_lengths_merge_without_gap_warning(cfg, ffmpeg_path, ffprobe_path, tmp_path, clips):
    report, gaps = _merge_timed(cfg, ffmpeg_path, ffprobe_path, tmp_path / "Movie", clips)
    assert report.exit_code == 0
    assert [r.status for r in report.results] == ["PASS"]
    assert gaps == []
    expected = sum(seconds for _, seconds in clips)
    assert duration_of(ffprobe_path, report.results[0].final_path) == pytest.approx(expected, abs=1.0)


def test_short_last_clip_then_break_gives_one_gap_warning(cfg, ffmpeg_path, ffprobe_path, tmp_path):
    clips = [("20261014075736", 180), ("20261014080036", 8), ("20261014100036", 180)]
    report, gaps = _merge_timed(
        cfg, ffmpeg_path, ffprobe_path, tmp_path / "Movie", clips, split_on_gap=False
    )
    assert report.exit_code == 0
    assert len(report.results) == 1  # trips off: one file, the break is a warning
    assert len(gaps) == 1
    assert "gap of 119.9 min" in gaps[0]  # 2 h minus the 8 s clip


def test_short_last_clip_then_break_starts_a_trip(cfg, ffmpeg_path, ffprobe_path, tmp_path):
    clips = [("20261014075736", 180), ("20261014080036", 8), ("20261014100036", 180)]
    report, gaps = _merge_timed(cfg, ffmpeg_path, ffprobe_path, tmp_path / "Movie", clips)
    assert [len(r.part.clips) for r in report.results] == [2, 1]
    assert all(r.status == "PASS" for r in report.results)
    assert gaps == []  # the trip list shows the break


# ---------------------------------------------------------------- IT-2
def test_it2_resolution_change_splits_into_two_parts(cfg, ffmpeg_path, tmp_path):
    clips_dir = tmp_path / "Movie"
    make_clips(clips_dir, ffmpeg_path, camera_suffixes=("F",), count=6, seconds=2,
               different_res_index=2)

    opt = RunOptions(input_dir=clips_dir, assume_yes=True)
    report = run(cfg, opt, AutoConfirmer(), NullSink(), threading.Event())

    assert report.exit_code == 0
    front_parts = [r for r in report.results if r.part.camera == "Front"]
    assert len(front_parts) >= 2
    assert all(r.status == "PASS" for r in front_parts)
    assert any("codec change" in w for w in report.plan.warnings)
    names = [r.final_path.name for r in front_parts]
    assert any("_part1" in n for n in names) and any("_part2" in n for n in names)


def test_it2b_change_detected_at_any_position(cfg, ffmpeg_path, tmp_path):
    """Every clip is probed, so an odd clip is caught wherever it sits.

    FR-4.1's sampling default could not see clip 3 of 6; the amended planner
    probes the whole group concurrently (see planner._probe_everything).
    """
    clips_dir = tmp_path / "Movie"
    make_clips(clips_dir, ffmpeg_path, camera_suffixes=("F",), count=6, seconds=2,
               different_res_index=4)

    assert cfg.probe_sample_every == 20  # shipped default, no longer limits coverage
    opt = RunOptions(input_dir=clips_dir, assume_yes=True)
    report = run(cfg, opt, AutoConfirmer(), NullSink(), threading.Event())

    assert len(report.results) > 1
    assert any("codec change" in w for w in report.plan.warnings)
    assert all(r.status == "PASS" for r in report.results)


# ---------------------------------------------------------------- IT-3
def test_it3_truncated_clip_is_skipped_and_reported(cfg, ffmpeg_path, tmp_path):
    clips_dir = tmp_path / "Movie"
    make_clips(clips_dir, ffmpeg_path, camera_suffixes=("R",), count=6, seconds=2,
               truncate_index=1)

    opt = RunOptions(input_dir=clips_dir, assume_yes=True)
    report = run(cfg, opt, AutoConfirmer(), NullSink(), threading.Event())

    assert len(report.plan.unreadable) == 1
    assert report.exit_code == 0
    text = (clips_dir / "merged" / "merge_report.txt").read_text(encoding="utf-8")
    assert "Skipped unreadable: 1" in text
    merged_clip_counts = sum(len(r.part.clips) for r in report.results)
    assert merged_clip_counts == 5


# ---------------------------------------------------------------- IT-4
def test_it4_dry_run_writes_nothing(cfg, ffmpeg_path, tmp_path, capsys):
    clips_dir = tmp_path / "Movie"
    make_clips(clips_dir, ffmpeg_path, count=3, seconds=1)

    code = run_cli(["--input", str(clips_dir), "--dry-run"])
    assert code == 0
    assert not (clips_dir / "merged").exists()
    out = capsys.readouterr().out
    assert "Commands that would run:" in out
    assert "-c copy" in out and "concat" in out


# ---------------------------------------------------------------- IT-5
def test_it5_second_run_without_overwrite_adds_suffix(cfg, ffmpeg_path, tmp_path):
    clips_dir = tmp_path / "Movie"
    make_clips(clips_dir, ffmpeg_path, camera_suffixes=("F",), count=3, seconds=1)
    merged = clips_dir / "merged"

    assert run_cli(["--input", str(clips_dir), "--yes"]) == 0
    first = sorted(merged.glob("*.mp4"))
    assert len(first) == 1
    first_hash = sha256(first[0])

    assert run_cli(["--input", str(clips_dir), "--yes"]) == 0
    all_outputs = sorted(merged.glob("*.mp4"))
    assert len(all_outputs) == 2
    assert any(o.name.endswith("_2.mp4") for o in all_outputs)
    assert sha256(first[0]) == first_hash  # the first file was not touched


# ---------------------------------------------------------------- IT-6
def test_it6_cancel_keeps_incomplete_file(cfg, ffmpeg_path, tmp_path, monkeypatch):
    """FR-6.7: cancel kills ffmpeg, keeps the partial file under a loud name.

    A real stream copy of the fixture clips finishes in well under a second, so
    the writer is replaced by fixtures/slow_ffmpeg.py, which writes gradually
    and emits the same -progress lines. Everything else is the real pipeline.
    """
    clips_dir = tmp_path / "Movie"
    make_clips(clips_dir, ffmpeg_path, camera_suffixes=("F",), count=6, seconds=1)

    stub = Path(__file__).parent / "fixtures" / "slow_ffmpeg.py"

    def slow_command(_ffmpeg, part):
        return [sys.executable, str(stub), str(part.partial_path)]

    monkeypatch.setattr("dashcam_merger.core.pipeline.build_command", slow_command)

    cancel = threading.Event()

    class CancelOnFirstWrite:
        def emit(self, ev):
            if ev.kind == "progress" and ev.bytes_written > 0:
                cancel.set()

    opt = RunOptions(input_dir=clips_dir, assume_yes=True)
    report = run(cfg, opt, AutoConfirmer(), CancelOnFirstWrite(), cancel)

    merged = clips_dir / "merged"
    assert report.exit_code == 130
    assert not list(merged.glob("*.partial.mp4"))
    assert list(merged.glob("INCOMPLETE_DO_NOT_USE_*.mp4"))
    assert report.sources_untouched is True
    assert len(list(clips_dir.glob("*.MP4"))) == 6  # nothing deleted


# ---------------------------------------------------------------- IT-7
def test_it7_unknown_camera_map_tokens_leave_files_unclassified(cfg, ffmpeg_path, tmp_path):
    clips_dir = tmp_path / "Movie"
    make_clips(clips_dir, ffmpeg_path, count=3, seconds=1)

    opt = RunOptions(
        input_dir=clips_dir, assume_yes=True, camera_map={"F": "Front", "ZZZ": "Rear"}
    )
    report = run(cfg, opt, AutoConfirmer(), NullSink(), threading.Event())

    assert len(report.plan.unclassified) > 0
    assert report.exit_code == 0
    assert all(r.status == "PASS" for r in report.results)


# ---------------------------------------------------------------- IT-8
def test_it8_max_part_gb_produces_several_passing_parts(cfg, ffmpeg_path, tmp_path):
    clips_dir = tmp_path / "Movie"
    make_clips(clips_dir, ffmpeg_path, camera_suffixes=("F",), count=6, seconds=2)

    opt = RunOptions(input_dir=clips_dir, assume_yes=True, max_part_gb=0.00002)
    report = run(cfg, opt, AutoConfirmer(), NullSink(), threading.Event())

    assert len(report.results) > 1
    assert all(r.status == "PASS" for r in report.results)
    assert report.exit_code == 0


# ---------------------------------------------------------------- IT-9
def test_it9_quotes_and_unicode_in_filenames(cfg, ffmpeg_path, tmp_path):
    clips_dir = tmp_path / "Movie"
    clips_dir.mkdir()
    made = make_clips(clips_dir, ffmpeg_path, camera_suffixes=("F",), count=2, seconds=1)
    tricky = clips_dir / "xe o to 'test'_F.MP4"
    made[0].replace(tricky)

    opt = RunOptions(input_dir=clips_dir, assume_yes=True, camera_map={"F": "Front"})
    report = run(cfg, opt, AutoConfirmer(), NullSink(), threading.Event())

    assert report.exit_code == 0
    assert all(r.status == "PASS" for r in report.results)


def test_it9b_vietnamese_characters_in_filename(cfg, ffmpeg_path, tmp_path):
    clips_dir = tmp_path / "Movie"
    clips_dir.mkdir()
    made = make_clips(clips_dir, ffmpeg_path, camera_suffixes=("F",), count=2, seconds=1)
    made[0].replace(clips_dir / "xe ô tô 'test'_F.MP4")

    opt = RunOptions(input_dir=clips_dir, assume_yes=True, camera_map={"F": "Front"})
    report = run(cfg, opt, AutoConfirmer(), NullSink(), threading.Event())
    assert report.exit_code == 0


# ---------------------------------------------------------------- IT-10
def test_it10_output_equal_to_input_exits_two(cfg, ffmpeg_path, tmp_path, capsys):
    clips_dir = tmp_path / "Movie"
    make_clips(clips_dir, ffmpeg_path, camera_suffixes=("F",), count=2, seconds=1)

    code = run_cli(["--input", str(clips_dir), "--output", str(clips_dir), "--yes"])
    assert code == 2
    assert "not the input folder itself" in capsys.readouterr().err


# ---------------------------------------------------------------- FR-12 (source deletion)
def test_delete_source_removes_only_verified_clips(cfg, ffmpeg_path, tmp_path):
    clips_dir = tmp_path / "Movie"
    make_clips(clips_dir, ffmpeg_path, camera_suffixes=("F",), count=4, seconds=1)

    opt = RunOptions(input_dir=clips_dir, assume_yes=True, delete_source=True)
    report = run(cfg, opt, AutoConfirmer(), NullSink(), threading.Event())

    assert report.exit_code == 0
    assert all(r.status == "PASS" for r in report.results)
    assert len(list(clips_dir.glob("*.MP4"))) == 0  # sources gone
    assert len(report.deleted_sources) == 4
    assert report.delete_errors == []
    merged = clips_dir / "merged"
    assert list(merged.glob("*.mp4"))  # merged output itself is untouched
    assert "Source clips deleted: 4" in (merged / "merge_report.txt").read_text(encoding="utf-8")


def test_delete_source_not_confirmed_keeps_files(cfg, ffmpeg_path, tmp_path):
    clips_dir = tmp_path / "Movie"
    make_clips(clips_dir, ffmpeg_path, camera_suffixes=("F",), count=2, seconds=1)

    opt = RunOptions(input_dir=clips_dir, assume_yes=True, delete_source=True)
    report = run(
        cfg, opt, AutoConfirmer(confirm_delete_result=False), NullSink(), threading.Event()
    )

    assert report.exit_code == 0
    assert len(list(clips_dir.glob("*.MP4"))) == 2  # kept
    assert report.deleted_sources == []


def test_delete_source_without_flag_never_deletes(cfg, ffmpeg_path, tmp_path):
    clips_dir = tmp_path / "Movie"
    make_clips(clips_dir, ffmpeg_path, camera_suffixes=("F",), count=2, seconds=1)

    opt = RunOptions(input_dir=clips_dir, assume_yes=True)
    report = run(cfg, opt, AutoConfirmer(), NullSink(), threading.Event())

    assert len(list(clips_dir.glob("*.MP4"))) == 2
    assert report.deleted_sources == []


# ---------------------------------------------------------------- safety grep
@pytest.mark.parametrize("needle", ["unlink(", "rmtree(", "os.remove(", "send2trash"])
def test_safety_grep_only_logging_setup_and_cleanup_delete(needle):
    """Design Section 12: no delete anywhere except prune_old_logs and the
    opt-in, verification-gated cleanup.py (FR-12)."""
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if needle in line and not line.strip().startswith("#"):
                offenders.append(f"{path.name}:{number}")
    assert all(o.startswith(("logging_setup.py", "cleanup.py")) for o in offenders), offenders


def test_no_shell_true_anywhere():
    for path in (ROOT / "src").rglob("*.py"):
        assert "shell=True" not in path.read_text(encoding="utf-8"), path


# ------------------------------------------------------- trips (FR-3.5)
def test_trips_become_separate_files(cfg, ffmpeg_path, ffprobe_path, tmp_path):
    # Two drives of 10 s clips, 20 min apart (> 15 min default) with a 10 min stop
    # inside the second one (< 15 min, stays in the trip).
    clips = [("20261014075700", 10), ("20261014075710", 10),
             ("20261014081720", 10), ("20261014082730", 10)]
    folder = tmp_path / "Movie"
    report, _ = _merge_timed(cfg, ffmpeg_path, ffprobe_path, folder, clips)
    assert report.exit_code == 0
    names = sorted(r.final_path.name for r in report.results)
    assert names == [
        "Front_20261014075700_to_20261014075710_trip1.mp4",
        "Front_20261014081720_to_20261014082730_trip2.mp4",
    ]
    assert all(r.status == "PASS" for r in report.results)


def test_cli_trip_gap_minutes_overrides_config(cfg, ffmpeg_path, tmp_path, capsys):
    clips = [("20261014075700", 5), ("20261014080705", 5)]  # 10 min break
    folder = tmp_path / "Movie"
    make_timed_clips(folder, ffmpeg_path, clips)
    assert run_cli(["--input", str(folder), "--dry-run", "--trip-gap-minutes", "5"]) == 0
    out = capsys.readouterr().out
    assert "Front trip 1/2" in out and "Front trip 2/2" in out  # dry run shows the trips
    assert run_cli(["--input", str(folder), "--dry-run"]) == 0  # 15 min: one file
    assert "trip 1/" not in capsys.readouterr().out



# ------------------------------------------------ export selection (FR-3.6)
def test_cli_trips_exports_only_the_chosen_trip(cfg, ffmpeg_path, tmp_path):
    folder = tmp_path / "Movie"
    for suffix in ("F", "R"):
        make_timed_clips(folder, ffmpeg_path, [("20261014075700", 5), ("20261014085700", 5),
                                              ("20261014095700", 5)], suffix=suffix)
    before = snapshot(folder)
    assert run_cli(["--input", str(folder), "--yes", "--trips", "2"]) == 0
    names = sorted(p.name for p in (folder / "merged").glob("*.mp4"))
    assert names == [
        "Front_20261014085700_to_20261014085700_trip2.mp4",
        "Rear_20261014085700_to_20261014085700_trip2.mp4",
    ]
    report = (folder / "merged" / "merge_report.txt").read_text(encoding="utf-8")
    assert "Not selected (not exported): 4 of 6 files" in report
    assert snapshot(folder) == before
