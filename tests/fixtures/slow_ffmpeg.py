"""A stand-in for ffmpeg that writes slowly and speaks the -progress protocol.

Used by the cancellation test: a real stream copy of short fixture clips
finishes in a fraction of a second, which leaves no window in which to cancel.
This writes the output file gradually and emits the same key=value lines on
stdout that ffmpeg's `-progress pipe:1` produces.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

CHUNK = b"\0" * 65536


def main() -> int:
    output = Path(sys.argv[-1])  # ffmpeg's last argument is the output file
    output.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(output, "wb") as fh:
        for step in range(1, 200):
            fh.write(CHUNK)
            fh.flush()
            written += len(CHUNK)
            print(f"out_time_ms={step * 1_000_000}", flush=True)
            print(f"total_size={written}", flush=True)
            print("progress=continue", flush=True)
            time.sleep(0.05)
    print("progress=end", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
