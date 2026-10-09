# DashCam Trip Sorter – Implementation plan

**Status:** P0 – waiting for the answers in [OPEN_POINTS.md](OPEN_POINTS.md). No code yet.
**Spec:** [TripSorter-Specification.md](TripSorter-Specification.md) v0.1

Each phase lists the open points it depends on. A phase can start once those are answered,
or once the *(recommended)* default is accepted.

## Phases

### P0 – Decide (owner + Claude)
- [ ] Owner answers Part A of OPEN_POINTS.md (OP-1 … OP-8)
- [ ] Owner pastes the card's folder list (OP-8) – or lends the card for one scan
- [ ] Program name (OP-17) and any extra wishes or "must never" rules (OP-18) go into the spec
- [ ] Copy the answers into the spec, mark it v1.0, remove the → OP marks that are settled
- [ ] Update this plan where the answers change the phases (e.g. OP-1 (d) adds GPS work to P1)

### P1 – Core: scan, classify, propose trips (blocked by OP-1, OP-2, OP-7, OP-8, OP-13)
- [ ] `tripsorter/core/models.py`: `Clip`, `Trip` (clips, start, end, breaks, size, name, flags)
- [ ] `scan.py`: card layout detection (FR-1.2), file listing, using `dashcam_merger.core.scanner.scan`,
      `cameras.detect` and `ordering.annotate`
- [ ] Pairing Front/Rear (FR-2.3); event/photo handling per OP-7
- [ ] `grouping.py`: breaks with ffprobe durations (`dashcam_merger.core.probe.probe_clip`,
      probe only the clips next to a break), trip proposal (FR-3), short trips (FR-3.4)
- [ ] Join / split / rename / exclude operations on a proposal (FR-4.2), as pure functions
- [ ] Tests:
  - synthetic names (IT-1);
  - **golden test on the owner's 3 real trips** (IT-2: their file names copied into
    `tests/resources/`, no video needed);
  - wrong clock (IT-7)

### P2 – Transfer (blocked by OP-3, OP-4, OP-6, OP-9)
- [ ] `transfer.py`: copy to `.partial` with a running checksum → read back → verify → rename;
      keep file times (FR-6)
- [ ] A guard allowing only the destination root (WriteGuard pattern; the merger's `rename`
      is same-volume only, so a guarded `copy` is added here, not in the merger)
- [ ] Skip already-copied files (OP-9), resume (FR-6.4), free-space check (FR-4.3)
- [ ] Naming (FR-5, OP-5) and `_2` for existing folder names
- [ ] Tests: IT-3, IT-4, IT-5 on temporary folders

### P3 – Command line (blocked by OP-12)
- [ ] `TripSorter-cli plan <source>` prints the proposal (dry run)
- [ ] `TripSorter-cli sort <source> --to <dest> [--break 30] [--yes]`
- [ ] Progress and summary output; exit codes

### P4 – Window (blocked by OP-12, OP-14)
- [ ] Source/destination pickers, removable-drive detection
- [ ] Trip list with join / split / rename / exclude, short-trip marks (FR-4, GUI outline in spec §7)
- [ ] Copy progress, cancel, final summary; settings remembered

### P5 – Report and follow-ups (blocked by OP-10, OP-11, OP-15, OP-16)
- [ ] `trip_info.txt`, `trips.csv`, run log (FR-8)
- [ ] Offer to join a trip that is already on the PC (FR-3.5, OP-10)
- [ ] Optional: run DashCam Merger / GPS Extractor per trip (FR-9)
- [ ] Optional: place names (OP-15)

### P6 – Delete from card, opt-in (blocked by OP-3)
- [ ] `cleanup.py`: the only module allowed to delete; verified clips only; double confirmation (FR-7)
- [ ] Safety-grep test (IT-8), modelled on `tests/test_integration.py` in DashCam Merger
- [ ] Test IT-6

### P7 – Build and acceptance
- [ ] `build.bat`, PyInstaller GUI + CLI, bundled `ffprobe.exe`, README
- [ ] AT-1 on the owner's real card, then sort a real card for the owner
- [ ] Root `CLAUDE.md`: short section on the new app

## Reuse map

| Need | Existing code | How |
|---|---|---|
| List video files | `src/dashcam_merger/core/scanner.py` `scan()` | import |
| Front/Rear from the name, timestamp, clip number | `core/cameras.py` `detect()` (built-in *Xforce* profile matches Vietmap), `core/ordering.py` `annotate()` / `parse_timestamp()` | import |
| Clip duration | `core/probe.py` `probe_clip()` + bundled `ffprobe.exe` | import |
| Break detection idea | `core/ordering.py` `detect_gaps()`, `core/planner.py` `gap_split_seconds` | same formula, own code (trips span cameras) |
| Write safety | `core/safe_io.py` `WriteGuard` (no delete; same-volume `rename` only) | same pattern; own guard with a `copy` method |
| Opt-in delete | `core/cleanup.py` + merger FR-12 | same pattern |
| Config / app folder | `core/config.py` `load_config()`, `app_dir()` | same pattern |
| Route / place names | `PC_app_GPSdataExtraction/dashgps/extract.py` | optional, P5 |

Import: run and build with `--paths ..\src` so `dashcam_merger.core` is importable, as the
merger's `build.bat` does with `--paths src`.

## Rough size
P1 1 session · P2 1 session · P3+P4 1–2 sessions · P5–P7 1 session.

## Next session – start here
1. Read OPEN_POINTS.md. If it is not answered yet, ask the owner the Part A questions.
2. Update the spec to v1.0 from the answers.
3. Start P1 with the golden test on the 3 real trips (names from `D:\DashTrip\…`).
