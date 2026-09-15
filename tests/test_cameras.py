"""Tests for core/cameras.py (Design Section 5.5)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest

from dashcam_merger.core.cameras import (
    BUILTIN_PROFILES,
    apply_manual_map,
    detect,
    extract_tokens,
    group_by_camera,
    parse_camera_map_arg,
)
from dashcam_merger.core.models import CameraProfile, ClipInfo

ROOT = Path("C:/DCIM/Movie")


def clip(relative: str) -> ClipInfo:
    return ClipInfo(path=ROOT / relative, size_bytes=1000, mtime=0.0)


def labels(clips):
    return [c.camera for c in clips]


@pytest.mark.parametrize(
    "name, expected",
    [
        ("20260831152516_0015340F.MP4", "Front"),
        ("20260831152516_0015340R.MP4", "Rear"),
        ("clip_R.mp4", "Rear"),
        ("clip_A.mp4", "Front"),
        ("clip_I.mp4", "Interior"),
        ("REAR_001.mp4", "Rear"),
        ("front_001.mp4", "Front"),
        ("CABIN_001.mp4", "Interior"),
        ("CAM2_001.mp4", "Rear"),
        ("CAM3_001.mp4", "Interior"),
    ],
)
def test_builtin_profiles_label_sample_names(name, expected):
    clips = [clip(name)]
    result = detect(clips, [])
    assert result.profile_name is not None
    assert clips[0].camera == expected


def test_70mai_pattern():
    clips = [clip("NO20260831-152516-000001B.MP4")]
    assert detect(clips, []).profile_name is not None
    assert clips[0].camera == "Rear"


def test_folder_profile():
    clips = [clip("Rear/001.mp4"), clip("Normal/001.mp4")]
    result = detect(clips, [])
    assert result.profile_name == "Folder"
    assert labels(clips) == ["Rear", "Front"]


def test_xforce_profile_also_fills_timestamp_and_sequence():
    clips = [clip("20260831152516_0015340F.MP4")]
    detect(clips, [])
    assert clips[0].timestamp == datetime(2026, 8, 31, 15, 25, 16)
    assert clips[0].sequence == 15340


def test_threshold_met_with_one_odd_file():
    clips = [clip(f"2026083115{i:02d}16_000000{i}F.MP4") for i in range(9)]
    clips.append(clip("random_note.mp4"))
    result = detect(clips, [])
    assert result.profile_name is not None
    assert result.matched_ratio == pytest.approx(0.9)
    assert clips[-1].camera == "Unclassified"


def test_threshold_not_met_returns_tokens():
    clips = [clip(f"2026083115{i:02d}16_000000{i}F.MP4") for i in range(8)]
    clips += [clip("random_note.mp4"), clip("another_note.mp4")]
    result = detect(clips, [])
    assert result.profile_name is None
    assert result.tokens  # populated for the manual mapping dialog
    assert all(c.camera == "Unclassified" for c in clips)


def test_user_profile_takes_precedence():
    user = CameraProfile(
        "MyBrand", "name", re.compile(r"_(?P<cam>CH[12])\.mp4$", re.I),
        {"CH1": "Interior", "CH2": "Rear"},
    )
    clips = [clip("rec_CH1.mp4"), clip("rec_CH2.mp4")]
    result = detect(clips, [user])
    assert result.profile_name == "MyBrand"
    assert labels(clips) == ["Interior", "Rear"]


def test_extract_tokens_uses_suffix_letters_in_one_folder():
    clips = [clip("rec001F.mp4"), clip("rec002R.mp4"), clip("rec003F.mp4")]
    tokens = extract_tokens(clips)
    assert set(tokens) == {"F", "R"}
    assert len(tokens["F"]) == 2


def test_extract_tokens_uses_folders_when_several():
    clips = [clip("A/001.mp4"), clip("B/001.mp4")]
    tokens = extract_tokens(clips)
    assert set(tokens) == {"folder:A", "folder:B"}


def test_extract_tokens_falls_back_to_all():
    clips = [clip("0001.mp4"), clip("0002.mp4")]
    assert set(extract_tokens(clips)) == {"ALL"}


def test_apply_manual_map_assigns_and_leaves_unknown_unclassified():
    clips = [clip("rec001F.mp4"), clip("rec002R.mp4"), clip("rec003X.mp4")]
    tokens = extract_tokens(clips)
    apply_manual_map(clips, tokens, {"F": "Front", "R": "Rear"})
    assert labels(clips) == ["Front", "Rear", "Unclassified"]


def test_parse_camera_map_arg_accepts_valid_forms():
    assert parse_camera_map_arg("F=Front,R=Rear") == {"F": "Front", "R": "Rear"}
    assert parse_camera_map_arg("folder:Normal=front") == {"FOLDER:NORMAL": "Front"}
    assert parse_camera_map_arg(" I = interior ") == {"I": "Interior"}
    assert parse_camera_map_arg("X=Skip") == {"X": "Skip"}


@pytest.mark.parametrize("bad", ["F", "F=Left", "=Front", "", "F=Front,R="])
def test_parse_camera_map_arg_rejects_invalid(bad):
    with pytest.raises(ValueError) as excinfo:
        parse_camera_map_arg(bad)
    assert "--camera-map" in str(excinfo.value)


def test_group_by_camera_fixed_order_and_unclassified():
    clips = [clip("a_R.mp4"), clip("b_I.mp4"), clip("c_F.mp4"), clip("d.mp4")]
    for c, label in zip(clips, ["Rear", "Interior", "Front", "Unclassified"]):
        c.camera = label
    groups, unclassified = group_by_camera(clips)
    assert [g.camera for g in groups] == ["Front", "Rear", "Interior"]
    assert len(unclassified) == 1


def test_group_by_camera_drops_skip():
    clips = [clip("a_F.mp4"), clip("b_R.mp4")]
    clips[1].camera = "Skip"
    clips[0].camera = "Front"
    groups, unclassified = group_by_camera(clips)
    assert [g.camera for g in groups] == ["Front"]
    assert unclassified == []


def test_builtin_profiles_all_have_cam_group():
    assert all("cam" in p.regex.groupindex for p in BUILTIN_PROFILES)
