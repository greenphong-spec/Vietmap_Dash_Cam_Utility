"""Entry point (Design Section 10): arguments mean CLI, no arguments mean GUI."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, "") and __name__ == "__main__":
    # Running the file directly (python src\main.py) needs src\ on sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from dashcam_merger import cli


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:  # any argument -> CLI
        return cli.main(argv)
    from dashcam_merger import gui  # imported lazily so the CLI needs no tkinter

    return gui.main()


if __name__ == "__main__":
    sys.exit(main())
