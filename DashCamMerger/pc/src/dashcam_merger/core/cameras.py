"""Camera detection and grouping (Design Section 5.5) - FR-2."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .models import CAMERA_LABELS, CameraGroup, CameraLabel, CameraProfile, ClipInfo
from .ordering import parse_sequence, parse_timestamp_14

log = logging.getLogger("dashcam_merger.cameras")

# FR-2.1: built-in profiles, tried in this order.
BUILTIN_PROFILES: list[CameraProfile] = [
    CameraProfile(
        "Xforce",
        "name",
        re.compile(r"^(?P<ts>\d{14})_(?P<seq>\d+)(?P<cam>[FR])\.(?:mp4|mov)$", re.I),
        {"F": "Front", "R": "Rear"},
    ),
    CameraProfile(
        "SuffixLetter",
        "name",
        re.compile(r"_(?P<cam>[FRABI])\.[A-Za-z0-9]+$", re.I),
        {"F": "Front", "A": "Front", "R": "Rear", "B": "Rear", "I": "Interior"},
    ),
    CameraProfile(
        "Word",
        "name",
        re.compile(r"(?P<cam>FRONT|REAR|BACK|INSIDE|INTERIOR|CABIN)", re.I),
        {
            "FRONT": "Front",
            "REAR": "Rear",
            "BACK": "Rear",
            "INSIDE": "Interior",
            "INTERIOR": "Interior",
            "CABIN": "Interior",
        },
    ),
    CameraProfile(
        "CamN",
        "name",
        re.compile(r"CAM(?P<cam>[1-3])", re.I),
        {"1": "Front", "2": "Rear", "3": "Interior"},
    ),
    CameraProfile(
        "70mai",
        "name",
        re.compile(
            r"^(?:NO|EV|PA)?\d{4}[-_]?\d{4}[-_]\d{6}[-_]\d+(?P<cam>[FB])\.[A-Za-z0-9]+$",
            re.I,
        ),
        {"F": "Front", "B": "Rear"},
    ),
    CameraProfile(
        "Folder",
        "parent",
        re.compile(
            r"^(?P<cam>front|f|normal|rear|r|back|b|interior|inside|cabin|i)$", re.I
        ),
        {
            "FRONT": "Front",
            "F": "Front",
            "NORMAL": "Front",
            "REAR": "Rear",
            "R": "Rear",
            "BACK": "Rear",
            "B": "Rear",
            "INTERIOR": "Interior",
            "INSIDE": "Interior",
            "CABIN": "Interior",
            "I": "Interior",
        },
    ),
]

MATCH_THRESHOLD = 0.90

TOKEN_TAIL = re.compile(r"([A-Za-z]+)\.[A-Za-z0-9]+$")
_LABEL_ALIASES: dict[str, CameraLabel] = {
    "FRONT": "Front",
    "REAR": "Rear",
    "INTERIOR": "Interior",
    "SKIP": "Skip",
}


@dataclass(slots=True)
class DetectionResult:
    profile_name: str | None  # None when no profile reached the threshold
    matched_ratio: float
    tokens: dict[str, list[ClipInfo]] = field(default_factory=dict)


def _reset(clips: list[ClipInfo]) -> None:
    for clip in clips:
        clip.camera = "Unclassified"
        clip.camera_token = ""


def apply_profile(clips: list[ClipInfo], profile: CameraProfile) -> int:
    """Label every clip this profile recognises; return how many matched."""
    matched = 0
    has_ts = "ts" in profile.regex.groupindex
    has_seq = "seq" in profile.regex.groupindex
    for clip in clips:
        subject = clip.path.name if profile.scope == "name" else clip.path.parent.name
        m = profile.regex.search(subject)
        if not m:
            continue
        token = (m.group("cam") or "").upper()
        label = profile.map.get(token)
        if label is None:
            continue
        clip.camera = label
        clip.camera_token = token
        matched += 1
        # Reuse what the profile already parsed instead of a second pass.
        if has_ts and m.group("ts"):
            clip.timestamp = parse_timestamp_14(m.group("ts"))
        if has_seq and m.group("seq"):
            clip.sequence = parse_sequence(m.group("seq"), None) or int(m.group("seq"))
    return matched


def extract_tokens(clips: list[ClipInfo]) -> dict[str, list[ClipInfo]]:
    """Group clips by a guessed token for the manual mapping dialog (FR-2.3)."""
    tokens: dict[str, list[ClipInfo]] = {}
    parents = {clip.path.parent for clip in clips}
    use_folder = len(parents) > 1
    for clip in clips:
        if use_folder:
            token = "folder:" + clip.path.parent.name
        else:
            m = TOKEN_TAIL.search(clip.path.name)
            token = m.group(1).upper() if m else "ALL"
        tokens.setdefault(token, []).append(clip)
    return {key: tokens[key] for key in sorted(tokens)}


def detect(clips: list[ClipInfo], user_profiles: list[CameraProfile]) -> DetectionResult:
    """Pick the first profile matching >= 90% of the clips (FR-2.2)."""
    if not clips:
        return DetectionResult(None, 0.0, {})
    best_ratio = 0.0
    for profile in list(user_profiles) + BUILTIN_PROFILES:
        _reset(clips)
        ratio = apply_profile(clips, profile) / len(clips)
        best_ratio = max(best_ratio, ratio)
        if ratio >= MATCH_THRESHOLD:
            log.info("Camera profile '%s' matched %.0f%% of files", profile.name, ratio * 100)
            return DetectionResult(profile.name, ratio, {})
    _reset(clips)
    log.info("No camera profile reached %.0f%% (best %.0f%%)", MATCH_THRESHOLD * 100, best_ratio * 100)
    return DetectionResult(None, best_ratio, extract_tokens(clips))


def apply_manual_map(
    clips: list[ClipInfo],
    tokens: dict[str, list[ClipInfo]],
    mapping: dict[str, CameraLabel],
) -> None:
    """Assign labels chosen by the user or by --camera-map (FR-2.3)."""
    upper = {str(k).upper(): v for k, v in mapping.items()}
    for token, token_clips in tokens.items():
        label = upper.get(token.upper(), "Unclassified")
        for clip in token_clips:
            clip.camera = label
            clip.camera_token = token


def parse_camera_map_arg(text: str) -> dict[str, CameraLabel]:
    """Parse --camera-map "F=Front,R=Rear,folder:Normal=Front"."""
    mapping: dict[str, CameraLabel] = {}
    for entry in text.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise ValueError(f"--camera-map: invalid entry '{entry}'")
        key, _, value = entry.partition("=")
        key = key.strip().upper()
        label = _LABEL_ALIASES.get(value.strip().upper())
        if not key or label is None:
            raise ValueError(f"--camera-map: invalid entry '{entry}'")
        mapping[key] = label
    if not mapping:
        raise ValueError(f"--camera-map: invalid entry '{text}'")
    return mapping


def group_by_camera(clips: list[ClipInfo]) -> tuple[list[CameraGroup], list[ClipInfo]]:
    """Build Front/Rear/Interior groups plus the Unclassified leftovers."""
    groups: list[CameraGroup] = []
    for label in CAMERA_LABELS:
        members = [c for c in clips if c.camera == label]
        if members:
            groups.append(CameraGroup(camera=label, clips=members))
    skipped = [c for c in clips if c.camera == "Skip"]
    if skipped:
        log.info("Skipping %d files the user marked 'Skip'", len(skipped))
    unclassified = [c for c in clips if c.camera == "Unclassified"]
    return groups, unclassified
