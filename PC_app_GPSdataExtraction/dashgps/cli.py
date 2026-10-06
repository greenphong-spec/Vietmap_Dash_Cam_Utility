"""Command line: extract a route from videos, test one frame, or clean a route file."""
from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from .core import profiles
from .core.exporters import ExportFormat
from .core.timezone import Zone

FORMAT_NAMES = {"kmz": ExportFormat.KMZ, "kml": ExportFormat.KML, "gpx": ExportFormat.GPX,
                "csv": ExportFormat.CSV, "ocrlog": ExportFormat.OCR_LOG}


def _formats(text: str):
    out = []
    for name in text.lower().split(","):
        name = name.strip()
        if name not in FORMAT_NAMES:
            raise argparse.ArgumentTypeError(f"unknown format '{name}' (choose from {', '.join(FORMAT_NAMES)})")
        out.append(FORMAT_NAMES[name])
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="DashGPS",
        description="Reads the date, time, speed and GPS position printed on dashcam video and saves "
                    "the route as KMZ / KML / GPX / CSV. Run without arguments for the window.",
    )
    p.add_argument("--tz", help="camera clock time zone, IANA name (e.g. Asia/Ho_Chi_Minh); default: this PC's zone")
    sub = p.add_subparsers(dest="command", required=True)

    ids = [x.id for x in profiles.ALL]
    e = sub.add_parser("extract", help="read the GPS route out of front-camera videos")
    e.add_argument("videos", nargs="+", help="video files or folders (original clips or merged files)")
    e.add_argument("-o", "--out", type=Path, help="save folder (default: folder of the first video)")
    e.add_argument("--profile", choices=ids, default=ids[0], help="camera overlay profile")
    e.add_argument("--interval", type=float, default=1.0, choices=[1.0, 2.0, 5.0, 10.0],
                   help="read one frame every N seconds (default 1)")
    e.add_argument("--formats", type=_formats, default=[ExportFormat.KMZ, ExportFormat.GPX],
                   help="comma list of kmz,kml,gpx,csv,ocrlog (default kmz,gpx)")
    e.add_argument("--separate", action="store_true", help="one file set per video instead of one combined route")
    e.add_argument("--ge-extras", action="store_true",
                   help="KMZ/KML: add Google Earth extras (moving marker, time slider, speed labels)")
    e.add_argument("--workers", type=int, help="OCR threads (default: by CPU count)")

    t = sub.add_parser("test-frame", help="OCR one frame and show what was read (check the camera profile)")
    t.add_argument("video", type=Path)
    t.add_argument("--at", type=float, default=10.0, help="position in seconds (default 10)")
    t.add_argument("--profile", choices=ids, default=ids[0])
    t.add_argument("--save", type=Path, help="also save the cropped overlay strip as PNG")

    c = sub.add_parser("clean", help="remove misread points from a KML / KMZ / GPX route file")
    c.add_argument("route", type=Path)
    c.add_argument("-o", "--out", type=Path, help="save folder (default: folder of the route file)")
    c.add_argument("--ge-extras", action="store_true", help="KMZ/KML: add Google Earth extras")
    return p


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    zone = Zone(args.tz)
    if args.command == "extract":
        return _extract(args, zone)
    if args.command == "test-frame":
        return _test_frame(args)
    return _clean(args, zone)


def _extract(args, zone: Zone) -> int:
    from . import extract

    videos = extract.find_videos(args.videos)
    missing = [v for v in videos if not v.is_file()]
    if not videos or missing:
        print(f"No such video: {missing[0] if missing else ' '.join(args.videos)}", file=sys.stderr)
        return 2
    profile = profiles.by_id(args.profile)
    print(f"{len(videos)} video(s), profile: {profile.name}, camera time zone: {zone}")
    if profile.note:
        print(profile.note)

    def progress(p):
        print(f"\r  video {p.video_index + 1}/{p.video_count} {p.video_name}: {int(p.pos_sec)} / {int(p.duration_sec)} s"
              f" · {p.frames_read} frames · {p.fraction * 100:5.1f}%   ", end="", flush=True)

    cancel = threading.Event()
    try:
        x = extract.extract(videos, profile, args.interval, cancel, progress, args.workers)
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    print()
    print(extract.summary(x, zone))
    if not x.track.fixes:
        print("No GPS data found - check the camera profile with: test-frame", file=sys.stderr)
        return 1
    folder = args.out or videos[0].parent
    saved = extract.export(x, folder, args.formats, zone, combine=not args.separate,
                           google_earth_extras=args.ge_extras)
    print(f"Saved {len(saved)} file(s) to {folder}:")
    for s in saved:
        print(f"  {s.path.name}")
    return 0


def _test_frame(args) -> int:
    from . import extract

    r = extract.test_frame(args.video, profiles.by_id(args.profile), args.at)
    if r is None:
        print("No frame could be decoded", file=sys.stderr)
        return 1
    print(f"Frame at {r.pos_sec:.1f} s")
    print(f"OCR:    {r.text or '(nothing)'}")
    print(f"Parsed: {r.parsed}")
    if args.save:
        from PIL import Image
        Image.fromarray(r.crop).save(args.save)
        print(f"Overlay strip saved to {args.save}")
    return 0


def _clean(args, zone: Zone) -> int:
    from . import extract

    c = extract.load_route(args.route, zone)
    print(extract.route_summary(c))
    if not c.track.fixes:
        print("Nothing left to save.", file=sys.stderr)
        return 1
    removed = len(c.route.points) - len(c.track.fixes)
    if removed == 0 and c.track.speed_corrected == 0:
        print("No noise found - the route is already clean; nothing saved.")
        return 0
    s = extract.save_route(c, args.out or args.route.parent, zone, args.ge_extras)
    print(f"Saved {s.path}")
    return 0
