"""Write the Windows version resource for the exes and print the app version.

Used by build.bat: the version comes from one place, `dashcam_merger.__version__`,
and ends up in the exe's Properties > Details, the window title, `--version`
and the release zip name.

    python tools\\version_info.py            -> prints 1.1.0, writes build\\version_info.txt
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "version_info.txt"

TEMPLATE = """\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={tuple4},
    prodvers={tuple4},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('FileDescription', 'DashCam Merger'),
        StringStruct('ProductName', 'DashCam Merger'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('ProductVersion', '{version}'),
        StringStruct('InternalName', 'DashCamMerger'),
        StringStruct('OriginalFilename', 'DashCamMerger.exe'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def read_version() -> str:
    text = (ROOT / "src" / "dashcam_merger" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"(\d+\.\d+\.\d+)"', text, re.MULTILINE)
    if not match:
        sys.exit('__version__ = "X.Y.Z" not found in src/dashcam_merger/__init__.py')
    return match.group(1)


def main() -> int:
    version = read_version()
    tuple4 = tuple(int(n) for n in version.split(".")) + (0,)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(TEMPLATE.format(tuple4=tuple4, version=version), encoding="utf-8")
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
