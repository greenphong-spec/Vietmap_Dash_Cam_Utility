"""Find the video files to merge (Design Section 5.4) - FR-1."""

from __future__ import annotations

import logging
from pathlib import Path

from .models import ClipInfo

log = logging.getLogger("dashcam_merger.scanner")

INCOMPLETE_PREFIX = "INCOMPLETE_DO_NOT_USE_"


def scan(
    input_dir: Path,
    extensions: list[str],
    recursive: bool,
    output_subfolder: str,
) -> list[ClipInfo]:
    """Return every source clip under input_dir, deterministically ordered.

    Skips the tool's own output folder (FR-1.3) and previously failed outputs
    (FR-1.4). An empty result is not an error here; the pipeline handles FR-1.5.
    """
    root = Path(input_dir).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Input folder not found: {root}")

    wanted = {e.lower() for e in extensions}
    skip_folder = output_subfolder.casefold()
    clips: list[ClipInfo] = []

    candidates = root.rglob("*") if recursive else root.glob("*")
    for path in candidates:
        try:
            if not path.is_file():
                continue
        except OSError:  # unreadable entry; ignore rather than abort the scan
            continue
        relative = path.relative_to(root)
        # FR-1.3: never re-merge our own outputs.
        if any(part.casefold() == skip_folder for part in relative.parts[:-1]):
            continue
        if path.name.startswith(INCOMPLETE_PREFIX):  # FR-1.4
            continue
        if path.suffix.lower() not in wanted:  # FR-1.2, case-insensitive
            continue
        try:
            st = path.stat()
        except OSError as e:
            log.warning("Cannot stat %s: %s", path, e)
            continue
        clips.append(
            ClipInfo(path=path.resolve(), size_bytes=st.st_size, mtime=st.st_mtime)
        )

    clips.sort(key=lambda c: str(c.path).lower())  # deterministic; real order later
    log.info("Scanned %d video files in %s", len(clips), root)
    return clips
