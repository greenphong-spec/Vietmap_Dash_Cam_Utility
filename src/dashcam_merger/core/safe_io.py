"""The only module allowed to create, write or rename files (Design Section 5.3).

FR-8: nothing outside the run's allowed roots may ever be written or renamed.
There is deliberately no delete/unlink method here. Do not add one.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .winpath import win_long

log = logging.getLogger("dashcam_merger.safe_io")


class SafetyViolation(Exception):
    """Raised when a write would land outside the allowed roots (FR-8.1)."""


class WriteGuard:
    """Gatekeeper for every file the program creates (FR-8.1, FR-8.2)."""

    def __init__(self, allowed_roots: list[Path]) -> None:
        self._roots = [Path(r).resolve() for r in allowed_roots]

    @property
    def roots(self) -> list[Path]:
        return list(self._roots)

    def check(self, path: Path) -> Path:
        """Return the resolved path, or raise if it is outside every root."""
        p = Path(path).resolve()
        for root in self._roots:
            if p == root or root in p.parents:
                return p
        raise SafetyViolation(f"Refusing to write outside output folder: {p}")

    def mkdir(self, path: Path) -> None:
        p = self.check(path)
        os.makedirs(win_long(p), exist_ok=True)
        log.debug("mkdir %s", p)

    def write_text(self, path: Path, text: str) -> None:
        """Write UTF-8 without BOM and with \\n newlines (FR-5.3)."""
        p = self.check(path)
        with open(win_long(p), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        log.debug("wrote %s (%d chars)", p, len(text))

    def rename(self, src: Path, dst: Path) -> None:
        s = self.check(src)
        d = self.check(dst)
        os.rename(win_long(s), win_long(d))
        log.info("renamed %s -> %s", s.name, d.name)

    def unique_name(self, path: Path) -> Path:
        """First free name: path, then path_2, path_3, ... (FR-6.2)."""
        p = self.check(path)
        if not p.exists():
            return p
        n = 2
        while True:
            candidate = p.with_name(f"{p.stem}_{n}{p.suffix}")
            if not candidate.exists():
                return candidate
            n += 1
