"""Tests for core/planner.py (Design Section 5.8). No ffmpeg needed."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from dashcam_merger.core.config import load_config
from dashcam_merger.core.models import ClipInfo, ProbeResult, RunOptions, StreamSignature
from dashcam_merger.core.planner import build_plan
from dashcam_merger.core.progress import NullSink
from dashcam_merger.core.safe_io import WriteGuard

SIG_A = StreamSignature("h264", 1920, 1080, "30/1", "yuv420p", "aac", 48000, 2)
SIG_B = StreamSignature("h264", 1280, 720, "30/1", "yuv420p", "aac", 48000, 2)
START = datetime(2026, 8, 31, 15, 25, 16)
NEVER_PROBE = Path("C:/nonexistent/ffprobe.exe")


def make_clip(index: int, camera="Front", sig=SIG_A, size=1000, minutes=None, root=None):
    stamp = START + timedelta(minutes=index if minutes is None else minutes)
    name = f"{stamp.strftime('%Y%m%d%H%M%S')}_{index:07d}{'F' if camera == 'Front' else 'R'}.MP4"
    clip = ClipInfo(
        path=(root or Path("C:/DCIM/Movie")) / name, size_bytes=size, mtime=float(index)
    )
    clip.camera = camera
    clip.timestamp = stamp
    clip.sequence = index
    clip.probe = ProbeResult(duration=60.0, size_bytes=size, signature=sig)
    return clip


@pytest.fixture()
def env(tmp_path):
    cfg = load_config(tmp_path / "missing.json")
    out = tmp_path / "merged"
    tmp = tmp_path / "temp"
    out.mkdir()
    tmp.mkdir()
    guard = WriteGuard([out, tmp])
    opt = RunOptions(input_dir=tmp_path, output_dir=out)
    return cfg, opt, guard, out, tmp


def plan_for(clips, env, **opt_kwargs):
    cfg, opt, guard, out, tmp = env
    for key, value in opt_kwargs.items():
        setattr(opt, key, value)
    return build_plan(
        clips, cfg, opt, NEVER_PROBE, "20260831_152516", NullSink(),
        guard=guard, output_dir=out, temp_dir=tmp,
    )


def test_uniform_signature_gives_one_part(env):
    clips = [make_clip(i) for i in range(5)]
    plan = plan_for(clips, env)
    assert len(plan.parts) == 1
    assert plan.parts[0].part_count == 1
    assert plan.parts[0].split_reason == ""
    assert plan.parts[0].signature == SIG_A


def test_two_cameras_give_two_parts_in_fixed_order(env):
    clips = [make_clip(i) for i in range(3)] + [make_clip(i, camera="Rear") for i in range(3)]
    plan = plan_for(clips, env)
    assert [p.camera for p in plan.parts] == ["Front", "Rear"]


def test_signature_change_splits_with_reason(env):
    clips = [make_clip(0), make_clip(1), make_clip(2, sig=SIG_B), make_clip(3, sig=SIG_B)]
    clips[0].probe = None  # force the "sample mismatch -> probe all" path
    clips[0].probe = ProbeResult(60.0, 1000, SIG_A)
    plan = plan_for(clips, env)
    assert len(plan.parts) == 2
    assert plan.parts[1].split_reason == "codec change"
    assert [p.part_index for p in plan.parts] == [1, 2]
    assert all(p.part_count == 2 for p in plan.parts)
    assert any("codec change" in w for w in plan.warnings)


def test_reencode_keeps_one_part(env):
    clips = [make_clip(0), make_clip(1, sig=SIG_B)]
    plan = plan_for(clips, env, reencode=True)
    assert len(plan.parts) == 1
    assert plan.parts[0].reencode is True
    assert plan.parts[0].signature is None


def test_max_part_gb_splits_by_size(env):
    # Design 5.8 step 6 rule: split when current_bytes + clip > limit and the
    # part is not empty. 3 x 0.6 GB under a 1 GB limit therefore yields three
    # parts of one clip each (the "two parts" line in the Design test list is
    # inconsistent with the rule it documents; the rule is normative).
    big = int(0.6 * 1024**3)
    plan = plan_for([make_clip(i, size=big) for i in range(3)], env, max_part_gb=1.0)
    assert len(plan.parts) == 3
    assert plan.parts[1].split_reason == "max part size"
    assert all(len(p.clips) == 1 for p in plan.parts)


def test_max_part_gb_packs_clips_that_fit(env):
    half = int(0.4 * 1024**3)
    plan = plan_for([make_clip(i, size=half) for i in range(4)], env, max_part_gb=1.0)
    assert [len(p.clips) for p in plan.parts] == [2, 2]


def test_max_part_gb_zero_means_unlimited(env):
    big = int(3 * 1024**3)
    plan = plan_for([make_clip(i, size=big) for i in range(4)], env, max_part_gb=0.0)
    assert len(plan.parts) == 1


def test_split_on_gap_splits_at_forty_minute_gap(env):
    clips = [make_clip(0), make_clip(1), make_clip(2, minutes=42), make_clip(3, minutes=43)]
    plan = plan_for(clips, env, split_on_gap=True)
    assert len(plan.parts) == 2
    assert plan.parts[1].split_reason == "trip gap"


def test_gap_warning_without_split(env):
    clips = [make_clip(0), make_clip(1, minutes=20)]
    plan = plan_for(clips, env)
    assert len(plan.parts) == 1
    assert any("gap of" in w for w in plan.warnings)


def test_output_name_follows_fr_6_3(env):
    clips = [make_clip(0), make_clip(1), make_clip(2)]
    plan = plan_for(clips, env)
    assert plan.parts[0].output_path.name == "Front_20260831152516_to_20260831152716.mp4"
    assert plan.parts[0].partial_path.name == "Front_20260831152516_to_20260831152716.partial.mp4"


def test_part_suffix_when_several_parts(env):
    clips = [make_clip(0), make_clip(1, sig=SIG_B)]
    plan = plan_for(clips, env)
    assert plan.parts[0].output_path.name.endswith("_part1.mp4")
    assert plan.parts[1].output_path.name.endswith("_part2.mp4")


def test_existing_output_gets_underscore_two(env):
    _, _, _, out, _ = env
    clips = [make_clip(0), make_clip(1)]
    first = plan_for(clips, env)
    first.parts[0].output_path.write_bytes(b"x")
    again = plan_for([make_clip(0), make_clip(1)], env)
    assert again.parts[0].output_path.name.endswith("_2.mp4")


def test_expected_totals(env):
    clips = [make_clip(i, size=1234) for i in range(4)]
    part = plan_for(clips, env).parts[0]
    assert part.expected_duration == pytest.approx(240.0)
    assert part.expected_size == 4936


def test_list_path_in_temp_by_default_and_output_with_keep_lists(env):
    _, _, _, out, tmp = env
    clips = [make_clip(0)]
    assert plan_for(clips, env).parts[0].list_path.parent == tmp
    assert plan_for([make_clip(0)], env, keep_lists=True).parts[0].list_path.parent == out


def test_unclassified_carried_into_plan(env):
    clips = [make_clip(0), make_clip(1)]
    stray = ClipInfo(path=Path("C:/DCIM/Movie/x.mp4"), size_bytes=5, mtime=0.0)
    plan = plan_for(clips + [stray], env)
    assert len(plan.unclassified) == 1


def test_mtime_ordering_warning(env):
    # Names carry neither a timestamp nor a 3+ digit sequence.
    clips = []
    for index, name in enumerate(["rec_a.mp4", "rec_b.mp4"]):
        clip = ClipInfo(path=Path("C:/DCIM/Movie") / name, size_bytes=10, mtime=float(index))
        clip.camera = "Front"
        clip.probe = ProbeResult(60.0, 10, SIG_A)
        clips.append(clip)
    plan = plan_for(clips, env)
    assert any("modification time" in w for w in plan.warnings)
    assert plan.parts[0].output_path.name == "Front_rec_a_to_rec_b.mp4"


def test_ffmpeg_commands_built_for_dry_run(env):
    clips = [make_clip(0)]
    plan = plan_for(clips, env)
    assert len(plan.ffmpeg_commands) == 1
    assert "-c" in plan.ffmpeg_commands[0] and "copy" in plan.ffmpeg_commands[0]


def test_name_falls_back_to_stem_without_timestamp(env):
    clips = [make_clip(0), make_clip(1)]
    for c in clips:
        c.timestamp = None
    plan = plan_for(clips, env)
    assert plan.parts[0].output_path.name.startswith("Front_2026")  # stem still carries digits
