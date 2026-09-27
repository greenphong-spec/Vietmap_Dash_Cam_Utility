# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

DashCam Merger: a Windows GUI + CLI tool that losslessly joins dashcam clip
sequences (per camera: Front/Rear/Interior) into single video files via the
ffmpeg concat demuxer, without re-encoding. It never moves or renames source
files, and only ever deletes them via the explicit, off-by-default,
verification-gated FR-12 opt-in (`--delete-source` / a GUI checkbox) —
never as a side effect of any other feature. Behavior is governed by two
documents in the repo root: `DashCamMerger-Specification.md` (requirements
FR-1…FR-12) and `DashCamMerger-Design.md` (implementation design).
`CHANGELOG.md` records where the shipped implementation deliberately
diverges from the design doc — check it before assuming the design doc is
current. The `.html` versions of both documents are a generated,
sidebar-navigable render of the `.md` (source of truth) — after editing
either `.md`, run `python tools\gen_docs.py` (needs `pip install markdown`)
to resync the `.html`; don't hand-edit the `.html` files.

## Commands

Run from the repo root; `src/` is the package root (`--paths src` / installed
as `dashcam_merger`).

```
python -m venv .venv && .venv\Scripts\activate
pip install pytest pyinstaller

pytest -m "not integration" -q          # unit tests, no ffmpeg needed
pytest -m integration -q                # requires ffmpeg\ffmpeg.exe + ffprobe.exe present
pytest tests/test_planner.py -q         # single file
pytest tests/test_planner.py::test_name -q   # single test

python src\main.py                      # no args -> GUI
python src\main.py --input D:\DCIM\Movie --dry-run   # any arg -> CLI

build.bat                               # full build: tests, then PyInstaller GUI+CLI, then zip
```

The `integration` marker (declared in `pytest.ini`) gates tests that shell
out to a real ffmpeg/ffprobe; they're skipped unless `ffmpeg\ffmpeg.exe`
exists, same as `build.bat` does.

`ffmpeg/` (bundled ffmpeg.exe/ffprobe.exe binaries) and `dist/`, `build/`,
and the release `.zip`/`.sha256` are gitignored — they're fetched/generated,
not sourced. To build or run integration tests locally, drop a gyan.dev
essentials build's `ffmpeg.exe`/`ffprobe.exe` into `ffmpeg/`.

## Architecture

`src/main.py` is the sole entry point: no argv → `dashcam_merger.gui`
(tkinter, imported lazily so the CLI has no tkinter dependency); any argv →
`dashcam_merger.cli`. Both call into `core.pipeline.run()`, the single
orchestrator, via a `Confirmer` protocol (`AutoConfirmer` for `--yes`/GUI
merge step, an interactive one for CLI prompts) and a `ProgressSink` for
progress events — this is how the same pipeline drives both a terminal and a
tkinter progress bar.

`pipeline.run()` executes in a fixed order, each step owned by one module in
`core/`:

1. `scanner.scan` — recursive walk, skips `merged/` and `INCOMPLETE_DO_NOT_USE_*`.
2. `cameras.detect` / `apply_manual_map` — matches built-in + `config.json`
   camera profiles (regex with a `(?P<cam>...)` group); falls back to the
   `Confirmer.request_camera_map` prompt when no profile matches.
3. `planner.build_plan` — orders clips (`ordering.py`: filename timestamp →
   sequence number → mtime, weakest-key warning), splits parts on gap/codec
   change/`max_part_gb`, and produces a `MergePlan` of `OutputPart`s.
4. Plan confirmation (skipped by `--dry-run`/`--yes`).
5. `concat.write_concat_list` + `run_ffmpeg` — one ffmpeg stream-copy
   (concat demuxer) invocation per part, killable via a `threading.Event`.
6. `verify.verify_part` — checks output duration/size against the summed
   source clips before the file is considered good.
7. `report.build_report_text` / `write_report` — screen report,
   `merged/merge_report.txt`, and the full log in `logs/`.

**Write safety (FR-8) is structural, not just convention**: `safe_io.WriteGuard`
is the *only* module permitted to create, write, or rename files, and every
write anywhere in the pipeline is routed through a `WriteGuard` instance
constructed with an explicit allow-list of roots (`output_dir`, `temp_dir`).
`WriteGuard.check()` resolves and rejects any path outside those roots.
There is deliberately no delete method on `WriteGuard` — a failed or
cancelled merge is kept as `INCOMPLETE_DO_NOT_USE_<name>` via `rename`, never
removed. `logging_setup.prune_old_logs` (30-day log rotation) and
`cleanup.py` (FR-12: opt-in, verification-gated source deletion, see below)
are the only two exceptions to "the program never deletes," and both live
outside `WriteGuard` on purpose. When touching any code path that writes a
file, route it through `WriteGuard` and do not add a delete method to it.

`cleanup.py` is the sole place allowed to delete a *source* clip, and only
when: `RunOptions.delete_source` is set (CLI `--delete-source` / GUI
checkbox, both default off), the clip's output part status is `"PASS"`
(`cleanup.sources_for_deletion`), and `Confirmer.confirm_delete()` returns
`True` — a confirmation kept separate from the FR-2.4 plan confirmation, so
GUI merges (which auto-confirm the plan) still show an explicit warning
dialog before deleting. `test_integration.py`'s safety-grep test allowlists
`cleanup.py` by name alongside `logging_setup.py`; don't add a delete call
anywhere else without also updating that test.

`models.py` holds the shared dataclasses (`ClipInfo`, `MergePlan`,
`OutputPart`, `PartResult`, `RunReport`, etc.) passed between every stage
above — read it first when tracing data through the pipeline.

`config.py` loads and validates `config.json` (extensions, ffmpeg/ffprobe
paths, gap thresholds, `camera_profiles`), raising errors that name the
offending key (FR-10). `config.app_dir()` is how the app locates itself
relative to the exe/script for `logs/`, bundled `ffmpeg/`, etc.

## Testing conventions

Tests mirror `core/` one-to-one (`test_<module>.py`). `test_integration.py`
covers the IT-1…IT-10 end-to-end scenarios from the spec, plus a safety grep
asserting no module other than `logging_setup` contains a delete call —
preserve that invariant (and that test) when adding new modules.
