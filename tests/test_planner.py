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
    plan = plan_for(clips, env, split_on_gap=False)
    assert len(plan.parts) == 1
    assert any("gap of" in w for w in plan.warnings)


# ---- trips (FR-3.5): split on by default, new trip after a break > 15 min
def trip_clips():
    # 0-2 | 19 min break | 22-23 | 14 min break | 38-39   (60 s clips)
    return [make_clip(0), make_clip(1), make_clip(2, minutes=22), make_clip(3, minutes=23),
            make_clip(4, minutes=38), make_clip(5, minutes=39)]


def test_trips_by_default_after_more_than_fifteen_minutes(env):
    plan = plan_for(trip_clips(), env)
    assert [len(p.clips) for p in plan.parts] == [2, 4]  # 19 min splits, 14 min does not
    assert [p.split_reason for p in plan.parts] == ["", "trip gap"]
    assert [(p.trip_index, p.trip_count) for p in plan.parts] == [(1, 2), (2, 2)]


def test_trip_gap_minutes_from_run_options(env):
    assert len(plan_for(trip_clips(), env, trip_gap_minutes=10).parts) == 3
    assert len(plan_for(trip_clips(), env, trip_gap_minutes=30).parts) == 1


def test_trip_gap_from_config(env):
    cfg = env[0]
    cfg.gap_split_seconds = 600
    assert len(plan_for(trip_clips(), env).parts) == 3


def test_trip_gap_below_gap_warning_still_splits(env):
    # A 3-min trip gap must work although gaps under 5 min are not warned about.
    clips = [make_clip(0), make_clip(1, minutes=5)]
    assert len(plan_for(clips, env, trip_gap_minutes=3).parts) == 2


def test_split_on_gap_off_keeps_one_file(env):
    assert len(plan_for(trip_clips(), env, split_on_gap=False).parts) == 1


def test_trip_gap_uses_probed_duration_not_start_times(env):
    # 5-min clips 5 min apart: 0 s break, never a trip even with a 1-min trip gap.
    clips = [make_clip(i, minutes=5 * i) for i in range(4)]
    for c in clips:
        c.probe = ProbeResult(300.0, 1000, SIG_A)
    assert len(plan_for(clips, env, trip_gap_minutes=1).parts) == 1


def test_trip_names(env):
    plan = plan_for(trip_clips(), env)
    assert plan.parts[0].output_path.name == "Front_20260831152516_to_20260831152616_trip1.mp4"
    assert plan.parts[1].output_path.name == "Front_20260831154716_to_20260831160416_trip2.mp4"


def test_codec_change_inside_a_trip_numbers_parts_per_trip(env):
    clips = trip_clips()
    clips[5].probe = ProbeResult(60.0, 1000, SIG_B)
    names = [p.output_path.name for p in plan_for(clips, env).parts]
    assert [n.rsplit("_", 2)[-2:] for n in names] == [
        ["20260831152616", "trip1.mp4"], ["trip2", "part1.mp4"], ["trip2", "part2.mp4"],
    ]


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


def test_trip_borders_are_not_warned_about_but_stops_inside_are(env):
    plan = plan_for(trip_clips(), env)  # 19 min -> trip border, 14 min -> stop in trip 2
    gaps = [w for w in plan.warnings if "gap of" in w]
    assert len(gaps) == 1 and "14.0 min" in gaps[0]
    assert not any("trip gap" in w for w in plan.warnings)



# ---- export selection (FR-3.6)
def front_and_rear_trips():
    return trip_clips() + [make_clip(i, camera="Rear", minutes=m)
                           for i, m in zip(range(10, 16), (0, 1, 22, 23, 38, 39))]


def test_select_trips_keeps_both_cameras_and_full_numbering(env):
    plan = plan_for(front_and_rear_trips(), env, trips={2})
    assert [(p.camera, p.trip_index) for p in plan.parts] == [("Front", 2), ("Rear", 2)]
    assert [p.output_path.name.endswith("_trip2.mp4") for p in plan.parts] == [True, True]
    assert [(p.camera, p.trip_index) for p in plan.not_selected] == [("Front", 1), ("Rear", 1)]
    assert len(plan.ffmpeg_commands) == 2


def test_missing_trip_number_is_warned(env):
    plan = plan_for(trip_clips(), env, trips={2, 7})
    assert len(plan.parts) == 1
    assert any("trip(s) 7 do not exist (trips 1-2)" in w for w in plan.warnings)


def test_select_parts_by_key(env):
    from dashcam_merger.core.planner import part_key

    everything = plan_for(front_and_rear_trips(), env)
    keep = {part_key(everything.parts[0]), part_key(everything.parts[3])}
    plan = plan_for(front_and_rear_trips(), env, selected_parts=keep)
    assert {part_key(p) for p in plan.parts} == keep
    assert len(plan.not_selected) == 2


def test_no_selection_exports_everything(env):
    plan = plan_for(front_and_rear_trips(), env)
    assert len(plan.parts) == 4 and plan.not_selected == []
