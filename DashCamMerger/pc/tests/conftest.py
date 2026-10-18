"""Shared pytest configuration: put the src folder on sys.path."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FFMPEG = ROOT / "ffmpeg" / "ffmpeg.exe"
FFPROBE = ROOT / "ffmpeg" / "ffprobe.exe"


def have_ffmpeg() -> bool:
    return FFMPEG.is_file() and FFPROBE.is_file()


@pytest.fixture(scope="session")
def ffmpeg_path() -> Path:
    if not have_ffmpeg():
        pytest.skip("ffmpeg\\ffmpeg.exe not present")
    return FFMPEG


@pytest.fixture(scope="session")
def ffprobe_path() -> Path:
    if not have_ffmpeg():
        pytest.skip("ffmpeg\\ffprobe.exe not present")
    return FFPROBE
