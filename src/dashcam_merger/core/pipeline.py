"""Run orchestrator (Design Section 6). The single entry point for CLI and GUI."""

from __future__ import annotations

import logging
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Protocol

from . import cameras, cleanup, planner, scanner
from .concat import build_command, run_ffmpeg, write_concat_list
from .logging_setup import prune_old_logs, setup_logging
from .models import (
    AppConfig,
    CameraLabel,
    ClipInfo,
    MergePlan,
    OutputPart,
    PartResult,
    RunOptions,
    RunReport,
    VerifyResult,
)
from .progress import ProgressEvent, ProgressSink
from .report import build_plan_summary, build_report_text, write_report
from .safe_io import WriteGuard
from .verify import sources_untouched, verify_part
from .config import app_dir

log = logging.getLogger("dashcam_merger.pipeline")

INCOMPLETE_PREFIX = "INCOMPLETE_DO_NOT_USE_"


class PipelineError(Exception):
    """Base class for every expected pipeline failure."""


class UsageError(PipelineError):
    """Bad input/output folders or conflicting options (exit 2)."""


class ToolMissingError(PipelineError):
    """ffmpeg or ffprobe is not where config says it is (exit 2)."""


class NoInputError(PipelineError):
    """No video files found (exit 3)."""


class CancelledError(PipelineError):
    """The user cancelled before or during the merge (exit 130)."""


class Confirmer(Protocol):
    def confirm_plan(self, summary: str) -> bool: ...  # FR-2.4

    def request_camera_map(
        self, tokens: dict[str, list[ClipInfo]]
    ) -> dict[str, CameraLabel] | None: ...  # FR-2.3

    def confirm_delete(self, clips: list[ClipInfo]) -> bool: ...  # FR-12.3


class AutoConfirmer:
    """Confirms everything; used with --yes and by the GUI merge step."""

    def __init__(
        self,
        camera_map: dict[str, CameraLabel] | None = None,
        confirm_delete_result: bool = True,
    ) -> None:
        self.camera_map = camera_map
        self.confirm_delete_result = confirm_delete_result

    def confirm_plan(self, summary: str) -> bool:
        return True

    def request_camera_map(self, tokens):
        return self.camera_map

    def confirm_delete(self, clips: list[ClipInfo]) -> bool:
        return self.confirm_delete_result


def _rename_incomplete(guard: WriteGuard, part: OutputPart) -> Path | None:
    """Keep a bad output, clearly marked, and never delete it (FR-6.7, FR-7.5)."""
    if part.partial_path is None or not part.partial_path.exists():
        return None
    assert part.output_path is not None
    destination = part.output_path.with_name(INCOMPLETE_PREFIX + part.output_path.name)
    destination = guard.unique_name(destination)
    guard.rename(part.partial_path, destination)
    return destination


def _execute_part(
    part: OutputPart,
    cfg: AppConfig,
    opt: RunOptions,
    guard: WriteGuard,
    progress: ProgressSink,
    cancel: threading.Event,
) -> PartResult:
    """Write, verify and finalise one output file. Touches only its own part."""
    if cancel.is_set():
        return PartResult(part, "SKIPPED", None, None, 0.0, None)

    assert part.list_path is not None and part.partial_path is not None
    assert part.output_path is not None
    progress.emit(
        ProgressEvent(
            kind="part_start",
            part=part,
            message=f"Merging {part.output_path.name} ({len(part.clips)} clips)",
        )
    )
    write_concat_list(guard, part.list_path, part.clips)  # FR-5
    cmd = build_command(cfg.ffmpeg_path, part)
    started = time.monotonic()
    rc = run_ffmpeg(cmd, part.expected_duration, cancel, progress, part)
    elapsed = time.monotonic() - started

    if rc == -1:  # FR-6.7
        return PartResult(part, "CANCELLED", None, _rename_incomplete(guard, part), elapsed, None)
    if rc != 0:
        failure = VerifyResult(
            False, 0.0, part.expected_duration, 0, part.expected_size, f"ffmpeg exit {rc}"
        )
        return PartResult(part, "FAIL", failure, _rename_incomplete(guard, part), elapsed, rc)

    result = verify_part(cfg.ffprobe_path, part)  # FR-7
    if result.passed:
        if opt.overwrite and part.output_path.exists():
            # Never delete: step the old output aside instead (FR-8.3).
            replaced = guard.unique_name(part.output_path.with_suffix(".replaced.mp4"))
            guard.rename(part.output_path, replaced)
        guard.rename(part.partial_path, part.output_path)  # FR-6.4
        return PartResult(part, "PASS", result, part.output_path, elapsed, rc)
    return PartResult(part, "FAIL", result, _rename_incomplete(guard, part), elapsed, rc)  # FR-7.5


def _resolve_dirs(cfg: AppConfig, opt: RunOptions, run_id: str) -> tuple[Path, Path]:
    input_dir = opt.input_dir.resolve()
    if not input_dir.is_dir():
        raise UsageError(f"Input folder not found: {input_dir}")
    output_dir = (opt.output_dir or input_dir / cfg.output_subfolder).resolve()
    # FR-8.1 must stay true even with --output.
    if output_dir == input_dir or output_dir in input_dir.parents:
        raise UsageError(
            "Output folder must be a subfolder or a separate folder, "
            "not the input folder itself"
        )
    temp_dir = Path(tempfile.gettempdir()) / "DashCamMerger" / run_id
    return output_dir, temp_dir


def run(
    cfg: AppConfig,
    opt: RunOptions,
    confirmer: Confirmer,
    progress: ProgressSink,
    cancel: threading.Event,
) -> RunReport:
    """Execute a full run and return its report (Design Section 6)."""
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    started = datetime.now()

    # 0. Preconditions.
    setup_logging(run_id, opt.verbose)
    prune_old_logs(app_dir() / "logs")
    for tool, path in (("ffmpeg", cfg.ffmpeg_path), ("ffprobe", cfg.ffprobe_path)):
        if not path.is_file():
            raise ToolMissingError(f"{tool} not found at {path}")
    output_dir, temp_dir = _resolve_dirs(cfg, opt, run_id)
    guard = WriteGuard([output_dir, temp_dir])
    log.info("Run %s: input=%s output=%s", run_id, opt.input_dir, output_dir)

    # 1. Scan (FR-1).
    clips = scanner.scan(opt.input_dir, cfg.video_extensions, opt.recursive, cfg.output_subfolder)
    if not clips:
        message = f"No video files found in {opt.input_dir}"
        progress.emit(ProgressEvent(kind="error", message=message))
        raise NoInputError(message)
    progress.emit(ProgressEvent(kind="log", message=f"Found {len(clips)} video files"))

    # 2. Detect cameras (FR-2).
    if opt.camera_map:
        cameras.apply_manual_map(clips, cameras.extract_tokens(clips), opt.camera_map)
    else:
        detection = cameras.detect(clips, cfg.camera_profiles)
        if detection.profile_name is None:
            mapping = confirmer.request_camera_map(detection.tokens)
            if mapping is None:
                raise CancelledError("Camera mapping cancelled")
            cameras.apply_manual_map(clips, detection.tokens, mapping)
        else:
            progress.emit(
                ProgressEvent(
                    kind="log",
                    message=f"Camera profile '{detection.profile_name}' matched "
                    f"{detection.matched_ratio * 100:.0f}% of files",
                )
            )

    # 3. Plan.
    plan = planner.build_plan(
        clips, cfg, opt, cfg.ffprobe_path, run_id, progress,
        guard=guard, output_dir=output_dir, temp_dir=temp_dir,
    )

    # 4. Confirm (FR-2.4). The summary is always shown; --yes skips only the wait.
    summary = build_plan_summary(plan)
    if not opt.dry_run:
        if opt.assume_yes:
            progress.emit(ProgressEvent(kind="log", message=summary))
        elif not confirmer.confirm_plan(summary):
            raise CancelledError("Cancelled at the confirmation prompt")

    # 5. Dry run writes nothing at all.
    if opt.dry_run:
        progress.emit(ProgressEvent(kind="dry_run", plan=plan, commands=plan.ffmpeg_commands))
        return RunReport(run_id, plan, [], True, started, datetime.now())

    if not plan.parts:
        progress.emit(ProgressEvent(kind="log", message="Nothing to merge"))
        results: list[PartResult] = []
    else:
        # 6. Prepare.
        guard.mkdir(output_dir)
        guard.mkdir(temp_dir)

        # 7. Execute parts (FR-6.5).
        workers = max(1, opt.parallel if opt.parallel is not None else cfg.parallel_jobs)
        results = []
        if workers == 1:
            for part in plan.parts:
                result = _execute_part(part, cfg, opt, guard, progress, cancel)
                results.append(result)
                progress.emit(ProgressEvent(kind="part_done", part=part, result=result))
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(_execute_part, part, cfg, opt, guard, progress, cancel)
                    for part in plan.parts
                ]
                for future in futures:
                    result = future.result()
                    results.append(result)
                    progress.emit(
                        ProgressEvent(kind="part_done", part=result.part, result=result)
                    )

    report = _finish(plan, results, clips, guard, progress, run_id, started)

    # 8. Optional source deletion (FR-12), gated on verification and confirmation.
    if opt.delete_source and results:
        deletable = cleanup.sources_for_deletion(results)
        if not deletable:
            progress.emit(
                ProgressEvent(kind="log", message="No verified clips eligible for deletion")
            )
        elif confirmer.confirm_delete(deletable):
            deleted, errors = cleanup.delete_sources(deletable, [output_dir, temp_dir])
            report.deleted_sources = deleted
            report.delete_errors = errors
            for path in deleted:
                log.info("Deleted source clip: %s", path)
            for path, err in errors:
                log.error("Could not delete source clip %s: %s", path, err)
            summary = f"Deleted {len(deleted)} source clip(s)"
            if errors:
                summary += f", {len(errors)} could not be deleted"
            progress.emit(ProgressEvent(kind="log", message=summary))
            try:
                write_report(guard, plan.output_dir, build_report_text(report))
            except OSError as e:
                log.error("Could not update merge_report.txt: %s", e)
        else:
            progress.emit(
                ProgressEvent(kind="log", message="Source deletion not confirmed; original clips kept")
            )

    return report


def _finish(
    plan: MergePlan,
    results: list[PartResult],
    clips: list[ClipInfo],
    guard: WriteGuard,
    progress: ProgressSink,
    run_id: str,
    started: datetime,
) -> RunReport:
    """Steps 8-10: source check, report, no temp cleanup (the program never deletes)."""
    ok, problems = sources_untouched(clips)  # FR-7.6
    for problem in problems:
        log.error("Source problem: %s", problem)

    report = RunReport(
        run_id=run_id,
        plan=plan,
        results=results,
        sources_untouched=ok,
        started=started,
        finished=datetime.now(),
        source_problems=problems,
    )
    text = build_report_text(report)
    try:
        write_report(guard, plan.output_dir, text)  # FR-9.2
    except OSError as e:  # a full disk must not hide the report from the user
        log.error("Could not write merge_report.txt: %s", e)
    progress.emit(ProgressEvent(kind="report", message=text))
    return report
