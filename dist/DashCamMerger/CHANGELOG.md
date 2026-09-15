# Changelog

All notable changes to DashCam Merger.

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
  Release builds must record the SHA-256 of the shipped zip here.
