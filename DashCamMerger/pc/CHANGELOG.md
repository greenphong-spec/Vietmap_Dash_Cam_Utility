# Changelog

All notable changes to DashCam Merger.

## Unreleased

### Added
- **Choose which trips to export** (Spec FR-3.6). GUI: an *Export* checkbox per row of the
  plan table (click a row to tick/untick, *Select all* / *Select none*, "Export N of M
  files"); rows are sorted by trip so Front and Rear of a trip sit together. CLI:
  `--trips 1,3,5-7`. File names keep the whole folder's trip numbers; the plan and
  `merge_report.txt` list what was not exported.

### Changed
- **Trips** (Spec FR-3.5, rev. 2026-10-17): clips are grouped into trips and each trip
  becomes its own file, **on by default**. A break of more than **15 minutes** starts a new
  trip (was: off, 30 min). Set it in the GUI (*Split into trips: new trip after a break of
  more than N min*, remembered in `config.json`), with `--trip-gap-minutes N` /
  `--no-split-on-gap` on the command line, or in `config.json` (`split_on_gap`,
  `gap_split_seconds`). The trip gap no longer depends on `gap_warning_seconds`.
- Output names: `_tripN` per trip; `_partM` now counts parts within a trip.
- The GUI table shows one row per output file (Camera, Trip, Files, Duration, Size,
  Start, End, Codec) with a scrollbar; the trip settings moved to the second options row.
- The plan summary (CLI confirmation, `--dry-run`, GUI log) lists every trip with start,
  end, files, duration and size. `--dry-run` now prints this summary (it printed only the
  ffmpeg commands). Trip borders are no longer repeated as gap warnings.
- Gap detection no longer assumes a 60 s clip (Spec FR-3.4a, rev. 2026-10-17). Dashcams
  record 1-, 3- or 5-minute clips and the last clip of a drive is shorter, so the gap is
  always the next clip's start minus the previous clip's start + *probed* duration; a pair
  without a probed duration is skipped instead of guessed. Removed
  `ordering.DEFAULT_CLIP_SECONDS`. Verified on a Vietmap M1 card with 3-min clips (228
  clips): exactly the 8 real breaks per camera reported, none inside a drive.

### Added
- Tests for 3-min and 5-min clip cycles, a 1-min → 3-min setting change, and a short last
  clip before a break (`test_ordering.py`, `test_integration.py`,
  `fixtures/make_clips.make_timed_clips`).
- `--delete-source` (CLI) and "Delete original clips after successful merge"
  (GUI) (FR-12): an explicit, off-by-default opt-in to permanently delete
  source clips once their merged output has passed verification. Requires a
  separate confirmation (a typed "yes" on the CLI without `--yes`, a warning
  dialog in the GUI) naming the exact file count. Clips belonging to a
  FAILED or CANCELLED part are never touched. Deletion outcome is logged and
  added to `merge_report.txt`.
- New `core/cleanup.py`: the sole, narrowly-scoped exception to FR-8's
  "never delete" rule, kept deliberately separate from `safe_io.WriteGuard`.
  The safety-grep test (`test_integration.py`) now allowlists this module by
  name alongside `logging_setup.py`.

## 1.0.0 — 2026-09-14

First version. Implements `DashCamMerger-Specification.md` v1.0 and
`DashCamMerger-Design.md` v1.0 (FR-1 … FR-11, Spec §6–§11).

### Added
- Recursive scan of a dashcam folder, skipping `merged\` and
  `INCOMPLETE_DO_NOT_USE_*` files (FR-1).
- Camera detection with six built-in profiles (Xforce, suffix letter, words,
  CAM numbers, 70mai, folder names), user profiles from `config.json`, and a
  manual mapping dialog / `--camera-map` when detection is inconclusive (FR-2).
- Chronological ordering from filename timestamps, then sequence numbers, then
  file modification time, with a warning when the weakest key is used; gap
  detection and optional split per trip (FR-3).
- Codec consistency check with automatic split into parts on format changes,
  optional `--reencode`, and exclusion of unreadable clips (FR-4).
- Lossless stream-copy merge through the ffmpeg concat demuxer, with absolute
  single-quoted paths in UTF-8 concat lists (FR-5, FR-6).
- Live progress with percent, MB/s and ETA; cancellation at any time keeps the
  partial file as `INCOMPLETE_DO_NOT_USE_*` (FR-6.6, FR-6.7).
- Verification of every output against the summed source durations and sizes
  before the file is given its final name (FR-7).
- `WriteGuard`: the only module that writes, and only inside the output and
  temp folders. No delete path exists anywhere except log pruning (FR-8).
- Report table on screen, in `merged\merge_report.txt`, and a full run log in
  `logs\` (FR-9).
- `config.json` with validation that names the offending key or line (FR-10).
- Tkinter GUI (scan table, per-part progress bars, log pane, camera dialog) and
  a full CLI with the documented exit codes (Spec §6, FR-11).
- 180 tests: unit tests per module plus integration tests IT-1 … IT-10 and a
  safety grep asserting no module but `logging_setup` contains a delete.

### Changed from the design document
- **Probing (FR-4.1).** The design samples the first, last and every 20th clip.
  That cannot satisfy FR-4.5 or the Spec §10 acceptance tests: a corrupt or
  differently encoded clip in an unsampled position is only discovered after
  ffmpeg has already written a bad output. Every clip is now probed, across a
  pool of 8 threads. Measured 58 ms per probe, so ~12 s for 1000 clips, inside
  the Spec §8 target of 30 s. `probe_sample_every` remains in `config.json`.
- `planner.build_plan` takes three extra optional arguments (`guard`,
  `output_dir`, `temp_dir`) that design step 7 needs but the signature omitted.
- `ordering.parse_timestamp` keeps its documented signature; the match span the
  design also requires is returned by a new `parse_timestamp_span`.
- The CLI runs the pipeline on a worker thread so Ctrl+C can set the cancel
  event and let ffmpeg be killed cleanly, as design §8.2 describes.
- The design's own example "3 × 0.6 GB clips with `max_part_gb=1` → two parts"
  contradicts the splitting rule it documents; the rule was implemented and
  yields three parts.

### Bundled
- ffmpeg / ffprobe 6.1.1 (gyan.dev essentials build).

### Release artefact
- `DashCamMerger_v1.0_win64.zip` - 70.8 MB
  SHA-256 `D88DC2C036CB825EDD357ABFB753936BB6CDE30B49B62D923367042EF68AC6FA`
- Unpacked folder is 190 MB, above the Spec section 4 target of 150 MB: the two
  static ffmpeg binaries are 79 MB each. The zip is well inside it. To hit 150 MB
  unpacked, ship a smaller ffmpeg build (a shared/LGPL build is roughly a third
  of the size) and drop the two binaries into an `ffmpeg\` folder beside the exe.
