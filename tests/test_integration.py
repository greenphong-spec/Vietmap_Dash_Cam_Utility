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
from make_clips import make_clips  # noqa: E402

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


# ---------------------------------------------------------- safety grep
@pytest.mark.parametrize("needle", ["unlink(", "rmtree(", "os.remove(", "send2trash"])
def test_safety_grep_only_logging_setup_deletes(needle):
    """Design Section 12: no delete anywhere except prune_old_logs."""
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if needle in line and not line.strip().startswith("#"):
                offenders.append(f"{path.name}:{number}")
    assert all(o.startswith("logging_setup.py") for o in offenders), offenders


def test_no_shell_true_anywhere():
    for path in (ROOT / "src").rglob("*.py"):
        assert "shell=True" not in path.read_text(encoding="utf-8"), path
