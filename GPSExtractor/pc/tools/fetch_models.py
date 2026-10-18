"""
Puts the OCR models into dashgps/models/ (they are not kept in git, like ffmpeg in the
merger). They come from the RapidOCR wheel on PyPI - PaddleOCR PP-OCRv6 small, Apache-2.0 -
pinned by version and checked by SHA-256. Only the two .onnx files are used; RapidOCR
itself is not installed.

    python tools/fetch_models.py
"""
import hashlib
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

RAPIDOCR_VERSION = "3.9.2"
MODELS = {
    "PP-OCRv6_det_small.onnx": "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
    "PP-OCRv6_rec_small.onnx": "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
}
DEST = Path(__file__).resolve().parents[1] / "dashgps" / "models"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    todo = [n for n, h in MODELS.items() if not (DEST / n).is_file() or sha256((DEST / n).read_bytes()) != h]
    if not todo:
        print(f"Models already in {DEST}")
        return 0
    meta = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/rapidocr/{RAPIDOCR_VERSION}/json"))
    wheel = next(u for u in meta["urls"] if u["filename"].endswith(".whl"))
    print(f"Downloading {wheel['filename']} ({wheel['size'] // 2**20} MB)...")
    data = urllib.request.urlopen(wheel["url"]).read()
    if sha256(data) != wheel["digests"]["sha256"]:
        print("Wheel checksum mismatch", file=sys.stderr)
        return 1
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in todo:
            blob = z.read(f"rapidocr/models/{name}")
            if sha256(blob) != MODELS[name]:
                print(f"{name}: checksum mismatch", file=sys.stderr)
                return 1
            (DEST / name).write_bytes(blob)
            print(f"  {name}")
    print(f"Models saved to {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
