"""Long-path support on Windows (Design Section 5.9) - Spec Section 7."""

from __future__ import annotations

import os
from pathlib import Path

_PREFIX = "\\\\?\\"
_UNC_PREFIX = "\\\\?\\UNC\\"


def win_long(path: Path) -> str:
    r"""Return a path string usable beyond MAX_PATH.

    On Windows this prefixes the resolved path with \\?\ (or \\?\UNC\ for UNC
    paths). Elsewhere the plain string is returned.
    """
    if os.name != "nt":
        return str(path)
    s = str(path.resolve())
    if s.startswith(_PREFIX):
        return s
    if s.startswith("\\\\"):  # UNC share, e.g. \\server\share\file
        return _UNC_PREFIX + s[2:]
    return _PREFIX + s
