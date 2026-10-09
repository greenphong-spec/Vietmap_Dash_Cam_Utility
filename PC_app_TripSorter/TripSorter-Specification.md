# DashCam Trip Sorter – Software Specification

**Version:** 0.1 (draft – waiting for answers in [OPEN_POINTS.md](OPEN_POINTS.md))
**Date:** 2026-10-07
**Target platform:** Windows 10/11 PC, SD card in a card reader (or any folder)
**Source of requirements:** the owner's request ("separate the videos from the dashcam SD
card into one folder per trip"), and the trips the owner has already sorted by hand in
`D:\DashTrip\`
**Related:** [OPEN_POINTS.md](OPEN_POINTS.md) (questions), [PLAN.md](PLAN.md)
(implementation plan), `../DashCamMerger-Specification.md` (style and shared rules)

Requirements whose behaviour depends on an open answer are marked **→ OP-n**. Until that is
answered, the behaviour written here is the *(recommended)* option.

---

## 1. Purpose

A dashcam writes a flat stream of 1-minute clips to its SD card: hundreds of files per
day, Front and Rear mixed, with no notion of where one drive ends and the next begins. The
owner now sorts these by hand into trip folders on the PC, before merging them (DashCam
Merger) or extracting the route (GPS Extractor).

The Trip Sorter does this sorting:
1. reads the card;
2. **proposes** trips from the recording times;
3. lets the owner check and adjust them;
4. copies each trip into its own folder, verified.

The card is not changed unless the owner asks for it.

## 2. Background (measured on the owner's data)

| Item | Value | Source |
|---|---|---|
| Card layout | Clips in `\Vietmap_M1\Movie`; other folders unknown → OP-8 | Android merger spec, DT-4 |
| Clip names | `20260919174222_0018888F.MP4` (Front), `20260919174223_0018889R.MP4` (Rear): `<YYYYMMDDhhmmss>_<number><F/R>` | `D:\DashTrip` |
| Clip length | 60 s; the Rear clip starts ~1 s after its Front clip | `D:\DashTrip` |
| Clip size | Front ≈ 106 MB (2560×1440 HEVC); about 6–7 GB per hour of Front video | `D:\DashTrip` |
| Card read speed | ≈ 0.5 GB/min seen on the phone; PC readers are usually faster | Android merger spec |

The owner's three hand-sorted trips (breaks = time between the end of one Front clip and the
start of the next):

| Trip folder | Front clips | Span | Breaks > 30 s |
|---|---|---|---|
| `LeoCauRachMieu` | 79 | 19 Sep 17:42 – 19:00 | none |
| `PhanThietSaigon\Cam hanh trinh` | 46 | 31 Aug 15:25 – 16:10 | none |
| `TienGiang_SaiGon_12-13.Sep.2026` | 261 | 12 Sep 09:26 – 13 Sep 11:44 | 7 h 20 min, 32 min, 13 h 16 min, 41 min, 9 min |

**Consequence:** for this owner a trip is a *journey*, which can include long stops and a
night away. A fixed break rule alone cannot reproduce that, so trips are proposed by
breaks and then joined or split by the owner (FR-4) → **OP-1, OP-2**.

The sorted trip folders are inputs for later tools. They already contain
`merged\merge_report.txt` (DashCam Merger) and `trackmatch-manifest.csv`.

## 3. Goals and non-goals

### Goals
- G1 Sort a full card (up to 256 GB, ~5000 clips) into trip folders without manual file work.
- G2 Never lose a clip:
  - copy before anything else;
  - verify every copy;
  - never overwrite a file;
  - leave the card unchanged by default.
- G3 Show the proposed trips before copying, and let the owner join, split, rename or
  exclude them.
- G4 Run again on the same card (or after an interrupted copy) without making duplicates.
- G5 Produce trip folders that DashCam Merger and the GPS Extractor can use directly.

### Non-goals (v1)
- Editing, cutting or merging video. Merging is DashCam Merger's job; it can only be
  *started* from here (FR-9).
- Reading GPS from every frame. Only the start/end of a trip may be read, for place names (OP-15).
- Cloud upload, a phone version, formatting the card.

## 4. Users and usage modes

One user (the car owner), after a drive or every few weeks. The card is put into the PC's
card reader.

- **Window (GUI):** choose the card → see the proposed trips → adjust → Sort.
- **Command line:** `TripSorter-cli plan <card>` (dry run, prints the proposed trips) and
  `TripSorter-cli sort <card> --to D:\DashTrip [--yes]` → **OP-12**.

## 5. Technology choice

- Python 3.13, tkinter, same as DashCam Merger and the GPS Extractor; PyInstaller one-folder build.
- Reuses DashCam Merger's core (`src/dashcam_merger/core`):
  - scanner: `scan()`;
  - camera detection: `detect()`, whose built-in *Xforce* profile already matches Vietmap names;
  - name parsing: `parse_timestamp()`, `annotate()`;
  - duration probing: `probe_clip()`;
  - write-safety: the `WriteGuard` pattern.
- Bundled `ffprobe.exe` (as in DashCam Merger) for exact clip durations.
- Optional: the GPS Extractor (`PC_app_GPSdataExtraction`) for trip routes or place names (OP-11, OP-15).

## 6. Functional requirements

### FR-1 Source selection
- FR-1.1 The user SHALL be able to choose a removable drive (SD card) or any folder.
- FR-1.2 On a removable drive the tool SHALL find the camera's clip folders by itself
  (e.g. `\Vietmap_M1\Movie`), and show which folders it will read → **OP-8**.
- FR-1.3 The tool SHALL read the source only. It SHALL NOT create, rename or delete anything
  on the source, except under FR-7.

### FR-2 Scan and classify
- FR-2.1 The tool SHALL list the video files (`config.video_extensions`, case-insensitive)
  in the chosen folders.
- FR-2.2 Each clip SHALL get:
  - camera (Front / Rear / other), by the camera profiles of DashCam Merger;
  - start time and clip number from the file name;
  - size and duration.
  The duration comes from ffprobe; it is 60 s when probing is turned off.
- FR-2.3 A Rear clip SHALL be paired with the Front clip that starts within
  `config.pair_tolerance_seconds` (default 5) of it. Pairs always go to the same trip.
- FR-2.4 Event / locked clips, photos and parking clips SHALL be included or left out per
  OP-7. Default: normal clips only → **OP-7**.
- FR-2.5 Clips whose time cannot be read, or is impossible (before 2015, or more than a day in
  the future), SHALL be listed as "time unknown" and handled per **OP-13**.
- FR-2.6 Unreadable or zero-length files SHALL be listed and skipped, never copied silently.

### FR-3 Trip proposal
- FR-3.1 Clips SHALL be ordered by start time; ties by clip number.
- FR-3.2 Break = start of a clip − (start + duration) of the previous clip, counting Front
  and Rear together.
- FR-3.3 A new trip SHALL be proposed where the break exceeds `config.trip_break_minutes`
  (default 30) → **OP-1, OP-2**.
- FR-3.4 Trips shorter than `config.short_trip_minutes` (default 3) SHALL be marked
  "short" → **OP-14**.
- FR-3.5 If a proposed trip continues a trip already in the destination (starts within the
  break limit after it ends), the tool SHALL offer to join it → **OP-10**.
- FR-3.6 (only if OP-1 (d) is chosen) GPS rule: a trip ends at a break where the car is at a
  saved place ("home"). The position comes from the last/first frame, read with the GPS
  Extractor.

### FR-4 Review before copying
- FR-4.1 Before anything is copied, the tool SHALL show the proposed trips. For each trip it
  shows:
  - start and end;
  - duration;
  - Front/Rear clip counts;
  - size;
  - the breaks inside it;
  - the break before the next trip;
  - its suggested folder name;
  - a "short" or "continues existing trip" mark.
- FR-4.2 The user SHALL be able to:
  - **join** a trip with the next one;
  - **split** a trip at one of its breaks;
  - **rename** it;
  - **exclude** it (not copied).
- FR-4.3 The tool SHALL show the total size to copy and the free space at the destination,
  and SHALL refuse to start if it does not fit (with 1 GB margin).
- FR-4.4 With `--yes` (CLI) the proposal is used as is.

### FR-5 Destination and naming
- FR-5.1 Default destination root: `config.destination` (default `D:\DashTrip`) → **OP-4**.
- FR-5.2 Default folder name: `YYYY-MM-DD_hhmm-hhmm`. Over two or more days it is
  `YYYY-MM-DD_hhmm_to_YYYY-MM-DD_hhmm`. Editable in the review → **OP-5, OP-15**.
- FR-5.3 An existing folder name SHALL get `_2`, `_3`, … It is never merged into silently.
- FR-5.4 Inside a trip folder, clips keep their original names in one flat folder → **OP-6**.
  Event clips go in an `Event\` subfolder when included → **OP-7**.

### FR-6 Transfer
- FR-6.1 Clips SHALL be copied, never moved in one step. A copy is written as
  `<name>.partial`, then renamed to `<name>` after verification. A failed copy is left as
  `.partial` and reported; it is never deleted by the tool.
- FR-6.2 Verification: same size, and the same checksum (xxHash/SHA-256) of the source and of
  the copy read back.
- FR-6.3 A clip whose destination file already exists with the same name and size SHALL be
  skipped as "already copied" → **OP-9**. With a different size it is an error for that clip.
- FR-6.4 An interrupted run SHALL be resumable: rerunning gives the same trips, copies only
  what is missing, and finishes `.partial` files from scratch.
- FR-6.5 All writes SHALL go through a guard that allows only the destination root (the
  WriteGuard rule of DashCam Merger FR-8).
- FR-6.6 The original file times SHALL be kept on the copy.
- FR-6.7 Progress: per file and overall (bytes, files, estimated time left); cancellable.

### FR-7 Delete from card (opt-in) → OP-3
- FR-7.1 Off by default, and never remembered between runs.
- FR-7.2 Only clips whose copy passed FR-6.2 in **this** run, or which were verified as
  identical (checksum), may be deleted.
- FR-7.3 A second confirmation SHALL name the number of files and the GB to delete.
- FR-7.4 Deletion SHALL live in one module, the only one allowed to delete (as `cleanup.py`
  in DashCam Merger). A test SHALL check that no other module deletes.

### FR-8 Report → OP-16
- FR-8.1 Each trip folder SHALL get `trip_info.txt`:
  - start, end, duration;
  - clips per camera and size;
  - the breaks inside the trip;
  - the source card/folder;
  - the date of sorting.
- FR-8.2 The destination root SHALL get `trips.csv`, one row per trip, appended per run.
- FR-8.3 A run log SHALL be written to `logs\` (30-day rotation, as in DashCam Merger).

### FR-9 Follow-ups (optional) → OP-11
- FR-9.1 After sorting, the tool MAY start DashCam Merger on each new trip folder (CLI
  `--input <trip> --yes`).
- FR-9.2 After sorting, the tool MAY run the GPS Extractor on each trip's Front clips and save
  a KMZ (route + start + end) in the trip folder.

### FR-10 Configuration
- FR-10.1 `config.json` next to the exe:
  - `destination`
  - `trip_break_minutes`
  - `short_trip_minutes`
  - `pair_tolerance_seconds`
  - `include` (movie/event/photo)
  - `name_pattern`
  - `video_extensions`
  - `ffprobe_path`
  - `camera_profiles` (same format as DashCam Merger)
- FR-10.2 An invalid key or value SHALL stop with a message that names it.

### FR-11 Logging
- FR-11.1 Every decision (trip boundaries and why, skipped files, errors) SHALL be in the run
  log. The GUI shows the last lines.

## 7. GUI outline (tkinter)

```
+---------------------------------------------------------------------------+
| DashCam Trip Sorter                                                       |
| Source: [E:\ (SD card, Vietmap_M1)  v] [Folder...]    [Scan]              |
| Destination: D:\DashTrip  [Change...]    Free: 412 GB                     |
|---------------------------------------------------------------------------|
| Proposed trips (break > [30] min)                                         |
| [x] 2026-09-12 09:26 - 11:44  2h18  138F 138R  29 GB  2026-09-12_0926-1144|
|        break 7h20 ->                                    [Join with next]  |
| [x] 2026-09-12 19:05 - 20:05  1h00   58F  58R  12 GB  ...    [Split...]   |
| [ ] 2026-09-14 08:01 - 08:02  0h01    1F   1R  0.2 GB  short             |
|                                       name: [LeoCauRachMieu        ]      |
|---------------------------------------------------------------------------|
| To copy: 3 trips, 61 GB   [ ] Delete from card after verified copy        |
| [Sort]  [Cancel]       ######-------  12 / 61 GB  ~ 9 min left            |
+---------------------------------------------------------------------------+
```

## 8. Error handling

| Situation | Behaviour |
|---|---|
| Card removed during copy | Stop, keep `.partial` files, report the trips not finished; a rerun resumes (FR-6.4) |
| Destination full | Checked before starting (FR-4.3). If it fills anyway: stop as above |
| Copy verification fails | Retry once. If it fails again, the clip is "failed" and is never deleted from the card |
| Same name, different size at the destination | That clip is an error; the others continue |
| Unreadable clip on the card | Listed, skipped, never deleted |
| Camera clock wrong | FR-2.5 / OP-13 |

## 9. Performance targets
- Scan and propose trips for 5000 clips: under 30 s without ffprobe. With ffprobe on 8
  threads: under 2 min, or measure only the clips around breaks.
- Copy: limited by the card reader; verifying should add at most ~20% (the copy is checksummed
  while writing; the read-back check reads only the destination disk).

## 10. Testing and acceptance

| ID | Setup | Expected |
|---|---|---|
| IT-1 | Synthetic card: 3 bursts of F/R clips with breaks of 2 h and 5 min | 2 trips at a 30-min break; the 5-min break stays inside a trip |
| IT-2 | The file names of the owner's 3 sorted trips (386 Front clips + their Rear clips; names and times only) | Proposal with breaks > 30 min; joining as the owner did gives exactly the owner's 3 trips |
| IT-3 | Copy to a destination that already holds half of the clips | Only the missing half is copied; nothing is overwritten |
| IT-4 | Interrupt the copy halfway (kill the process) and rerun | Same trips; `.partial` redone; all files verified |
| IT-5 | Destination too small | Refused before copying, with the sizes shown |
| IT-6 | Delete from card on; one copy made to fail | Only verified clips deleted; the failed one stays |
| IT-7 | Clips with impossible time (2000-01-01) | Handled per OP-13, warning in the log |
| IT-8 | Safety grep | Only the FR-7 module contains a delete call |
| AT-1 | Real Vietmap M1 card, owner's choice of trips | Trip folders that DashCam Merger merges without warnings |

## 11. Build and release
- `build.bat`: venv, tests, PyInstaller GUI + CLI exes, bundled `ffprobe.exe`, zip + SHA-256.

## 12. Suggested project structure
```
PC_app_TripSorter/
  tripsorter/core/  models.py  scan.py  grouping.py  transfer.py  report.py  cleanup.py
  tripsorter/cli.py  tripsorter/gui.py  main.py
  tests/  config.json  build.bat  README.md
```

## 13. Future ideas (not in v1)
- Start automatically when the card is inserted (OP-12).
- Place names from GPS (OP-15).
- A KMZ overview of all trips (OP-16).
- Several cameras or cards in one run.
