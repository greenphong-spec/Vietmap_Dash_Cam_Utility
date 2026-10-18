"""Opt-in, verification-gated deletion of source clips (FR-12).

This is a deliberate, narrow exception to FR-8 ("never delete"): every other
module is forbidden from deleting anything (see safe_io.py). This module may
permanently remove a *source* clip, and only when all of the following hold:
  - the user explicitly enabled --delete-source / the GUI checkbox,
  - the clip's output part passed verification (PartResult.status == "PASS"),
  - the user confirmed the action (pipeline.Confirmer.confirm_delete).
It refuses to delete anything inside the run's output or temp folders.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable

from .models import ClipInfo, PartResult

log = logging.getLogger("dashcam_merger.cleanup")


def sources_for_deletion(results: Iterable[PartResult]) -> list[ClipInfo]:
    """Clips belonging to a part that passed verification (FR-12.1)."""
    clips: list[ClipInfo] = []
    for result in results:
        if result.status == "PASS":
            clips.extend(result.part.clips)
    return clips


def delete_sources(
    clips: Iterable[ClipInfo], protected_roots: list[Path]
) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Permanently remove each clip's file.

    Returns (deleted paths, (path, error) failures). A path inside one of
    protected_roots is refused rather than deleted (FR-12.2).
    """
    protected = [Path(root).resolve() for root in protected_roots]
    deleted: list[Path] = []
    errors: list[tuple[Path, str]] = []
    for clip in clips:
        path = Path(clip.path).resolve()
        if any(path == root or root in path.parents for root in protected):
            errors.append((path, "refused: inside the run's output/temp folder"))
            continue
        try:
            os.remove(path)
        except OSError as e:
            errors.append((path, str(e)))
        else:
            deleted.append(path)
    return deleted, errors
