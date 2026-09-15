"""Synthetic dashcam clips for the integration tests (Design Section 13.2)."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def make_clips(
    dst: Path,
    ffmpeg: Path,
    camera_suffixes: tuple[str, ...] = ("F", "R"),
    count: int = 6,
    seconds: int = 2,
    size: str = "640x360",
    start: str = "20260831152516",
    different_res_index: int | None = None,
    truncate_index: int | None = None,
) -> list[Path]:
    """Create Xforce-style clips: <ts>_<seq:07d><suffix>.MP4, 60 s apart."""
    dst.mkdir(parents=True, exist_ok=True)
    begin = datetime.strptime(start, "%Y%m%d%H%M%S")
    created: list[Path] = []

    for suffix in camera_suffixes:
        for index in range(count):
            stamp = (begin + timedelta(minutes=index)).strftime("%Y%m%d%H%M%S")
            target = dst / f"{stamp}_{index + 1:07d}{suffix}.MP4"
            resolution = "320x180" if index == different_res_index else size
            cmd = [
                str(ffmpeg),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc=size={resolution}:rate=30",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440",
                "-t",
                str(seconds),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(target),
            ]
            subprocess.run(
                cmd, check=True, capture_output=True, creationflags=CREATE_NO_WINDOW
            )
            if index == truncate_index:
                data = target.read_bytes()[:2000]
                target.write_bytes(data)
            created.append(target)
    return created
