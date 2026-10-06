import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RESOURCES = Path(__file__).resolve().parent / "resources"


def resource(name: str) -> bytes:
    return (RESOURCES / name).read_bytes()


os.environ.setdefault("PYTHONUTF8", "1")
