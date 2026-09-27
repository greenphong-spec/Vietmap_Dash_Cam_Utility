"""Command line interface (Design Section 8) - FR-11."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path

from . import __version__
from .core import pipeline
from .core.cameras import parse_camera_map_arg
from .core.config import ConfigError, load_config
from .core.models import CameraLabel, ClipInfo, RunOptions
from .core.pipeline import (
    CancelledError,
    NoInputError,
    ToolMissingError,
    UsageError,
)
from .core.progress import CallbackSink, ProgressEvent, describe

log = logging.getLogger("dashcam_merger.cli")

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_NO_INPUT = 3
EXIT_CANCELLED = 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="DashCamMerger",
        description="Merge segmented multi-camera dashcam footage, losslessly.",
    )
    parser.add_argument("--input", required=True, type=Path, help="folder with the clips")
    parser.add_argument("--output", type=Path, help="output folder (default: <input>\\merged)")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--no-recursive", action="store_true", help="do not scan subfolders")
    parser.add_argument("--camera-map", help='e.g. "F=Front,R=Rear"')
    parser.add_argument("--split-on-gap", action="store_true", help="one file per trip")
    parser.add_argument("--max-part-gb", type=float, help="split outputs at N GB (0 = unlimited)")
    parser.add_argument("--reencode", action="store_true", help="re-encode mixed formats (slow)")
    parser.add_argument("--overwrite", action="store_true", help="replace existing outputs")
    parser.add_argument("--keep-lists", action="store_true", help="keep concat lists next to outputs")
    parser.add_argument("--parallel", type=int, help="run N ffmpeg processes at once")
    parser.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    parser.add_argument(
        "--delete-source",
        action="store_true",
        help="permanently delete source clips whose output passed verification "
        "(prompts for confirmation unless --yes is also given)",
    )
    parser.add_argument("--verbose", action="store_true", help="debug level logging")
    parser.add_argument("--config", type=Path, help="path to config.json")
    parser.add_argument(
        "--version", action="version", version=f"DashCamMerger {__version__}"
    )
    return parser


class CliConfirmer:
    """Terminal prompts for FR-2.3 and FR-2.4."""

    def __init__(self, assume_yes: bool) -> None:
        self.assume_yes = assume_yes

    @staticmethod
    def _interactive() -> bool:
        return sys.stdin is not None and sys.stdin.isatty()

    def confirm_plan(self, summary: str) -> bool:
        print(summary)
        if self.assume_yes:
            return True
        if not self._interactive():
            print("Not a terminal; use --yes to skip confirmation")
            return False
        return input("Proceed with merge? [y/N] ").strip().lower() in ("y", "yes")

    def confirm_delete(self, clips: list[ClipInfo]) -> bool:
        if self.assume_yes:
            return True
        if not self._interactive():
            print("Not a terminal; source clips were kept (use --yes to confirm deletion)")
            return False
        answer = input(
            f"Permanently delete {len(clips)} source clip file(s)? This cannot be undone. "
            "Type 'yes' to continue: "
        )
        return answer.strip().lower() == "yes"

    def request_camera_map(
        self, tokens: dict[str, list[ClipInfo]]
    ) -> dict[str, CameraLabel] | None:
        print("Camera detection was not conclusive. Sample files per group:")
        for token, clips in tokens.items():
            print(f"  {token}: {len(clips)} files")
            for clip in clips[:5]:
                print(f"      {clip.path.name}")
        if not self._interactive():
            print('Not a terminal; use --camera-map "F=Front,R=Rear" instead')
            return None
        mapping: dict[str, CameraLabel] = {}
        for token in tokens:
            answer = input(f"Camera for '{token}' [Front/Rear/Interior/Skip]: ").strip()
            label = {"front": "Front", "rear": "Rear", "interior": "Interior"}.get(
                answer.lower(), "Skip"
            )
            mapping[token.upper()] = label  # type: ignore[assignment]
        return mapping


def _make_printer():
    state = {"in_progress": False}

    def show(ev: ProgressEvent) -> None:
        if ev.kind == "progress":
            print("\r" + describe(ev).ljust(78), end="", flush=True)
            state["in_progress"] = True
            return
        if state["in_progress"]:
            print()
            state["in_progress"] = False
        if ev.kind == "dry_run" and ev.commands:
            print("Commands that would run:")
            for cmd in ev.commands:
                print("  " + " ".join(cmd))
        elif ev.kind in ("report", "log", "error", "part_start"):
            if ev.message:
                print(ev.message)
        elif ev.kind == "part_done" and ev.result is not None:
            name = ev.result.final_path.name if ev.result.final_path else "-"
            print(f"{ev.result.status}: {name}")

    return show


def options_from_args(args: argparse.Namespace) -> RunOptions:
    return RunOptions(
        input_dir=args.input,
        output_dir=args.output,
        recursive=not args.no_recursive,
        camera_map=parse_camera_map_arg(args.camera_map) if args.camera_map else None,
        split_on_gap=True if args.split_on_gap else None,
        max_part_gb=args.max_part_gb,
        reencode=args.reencode,
        overwrite=args.overwrite,
        keep_lists=args.keep_lists,
        parallel=args.parallel,
        dry_run=args.dry_run,
        assume_yes=args.yes,
        verbose=args.verbose,
        delete_source=args.delete_source,
    )


def _run_cancellable(cfg, opt: RunOptions, confirmer, cancel: threading.Event):
    """Run the pipeline on a worker thread so Ctrl+C can cancel it cleanly.

    Confirmation prompts still need stdin, so an interactive run without --yes
    stays on the main thread; there is nothing to cancel before it starts.
    """
    sink = CallbackSink(_make_printer())
    if not opt.assume_yes and not opt.dry_run:
        return pipeline.run(cfg, opt, confirmer, sink, cancel)

    box: dict[str, object] = {}

    def worker() -> None:
        try:
            box["report"] = pipeline.run(cfg, opt, confirmer, sink, cancel)
        except BaseException as e:  # re-raised on the calling thread below
            box["error"] = e

    thread = threading.Thread(target=worker, name="pipeline", daemon=False)
    thread.start()
    try:
        while thread.is_alive():
            thread.join(timeout=0.2)
    except KeyboardInterrupt:
        cancel.set()  # FR-6.7: ffmpeg is killed, the partial file is kept
        print("\nCancelling, please wait...", file=sys.stderr)
        thread.join()
        raise
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box["report"]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logging.getLogger("dashcam_merger").addHandler(stderr_handler)

    try:
        cfg = load_config(args.config)
        opt = options_from_args(args)
    except (ConfigError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE

    cancel = threading.Event()
    try:
        report = _run_cancellable(cfg, opt, CliConfirmer(args.yes), cancel)
    except (UsageError, ToolMissingError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE
    except NoInputError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_NO_INPUT
    except CancelledError as e:
        print(f"Cancelled: {e}", file=sys.stderr)
        return EXIT_CANCELLED
    except KeyboardInterrupt:
        cancel.set()
        print("\nCancelled by user", file=sys.stderr)
        return EXIT_CANCELLED
    return report.exit_code
