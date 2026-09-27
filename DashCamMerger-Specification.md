# DashCam Merger – Software Specification

**Version:** 1.0 draft
**Date:** 2026-09-14
**Target platform:** Windows 10 / 11 (64-bit), fully offline
**Source of requirements:** `merge-dashcam-camera-clips.md` (Claude skill) in this folder

---

## 1. Purpose

DashCam Merger is a standalone Windows program that takes a folder of short dashcam
video segments (typically 1-minute clips) recorded by a multi-channel dashcam
(Xforce, Vietmap, 70mai, and similar) and merges the clips of each camera
(front / rear / interior) into one continuous video per camera, in correct
chronological order, without re-encoding and without touching the original files.

It must run with no internet connection and no AI assistant. Everything the Claude
skill does by reasoning is replaced by fixed rules, configuration, and user prompts.

## 2. Goals and non-goals

### Goals
- One-click (or one-command) merge of a dashcam folder into `merged\` outputs.
- Lossless, fast merging via ffmpeg stream copy (`-c copy`).
- Correct camera grouping and time ordering, verified before writing.
- Every output verified with ffprobe; the user is never told "success" without proof.
- Source clips are never deleted, moved, renamed, or modified.
- Works on a plain Windows PC with no Python, no ffmpeg pre-installed (both bundled).

### Non-goals (v1)
- No video editing, trimming, overlays, GPS parsing, or speed/plate detection.
- No re-encoding pipeline (only a warning and an optional advanced re-encode flag).
- No cloud upload (Descript, Drive, etc.).
- No side-by-side / picture-in-picture combining of front + rear into one frame.

## 3. Users and usage modes

| Mode | Who | How |
|------|-----|-----|
| GUI | Owner of the dashcam, non-technical | Double-click `DashCamMerger.exe`, pick folder, click **Merge** |
| CLI | Power user, batch scripts, Task Scheduler | `DashCamMerger.exe --input "D:\DCIM\Movie" --output "D:\merged"` |

Both modes call the same core library so behaviour is identical.

## 4. Technology choice

| Item | Choice | Reason |
|------|--------|--------|
| Language | Python 3.12 | Fast to write, easy to maintain, good subprocess handling |
| GUI | Tkinter (standard library) | Zero extra dependencies, small build |
| Video tools | ffmpeg.exe + ffprobe.exe (static builds, e.g. gyan.dev or BtbN) | Industry standard, lossless concat |
| Packaging | PyInstaller, one-folder build | No Python install needed on target PC |
| Config | `config.json` beside the exe | Editable in Notepad, no registry |
| Logs | `logs\merge_YYYYMMDD_HHMMSS.log` | Plain text, easy to share when asking for help |

Alternative accepted if Python is unwanted: a single PowerShell 5.1 script
`Merge-DashCam.ps1` with the same rules and a bundled `ffmpeg\` folder. The
specification below applies to either implementation.

Distribution layout:

```
DashCamMerger\
  DashCamMerger.exe
  config.json
  ffmpeg\ffmpeg.exe
  ffmpeg\ffprobe.exe
  logs\
  README.txt
```

Total size target: under 150 MB. No installer required; a zip is enough.

## 5. Functional requirements

Each requirement has an ID for testing. "SHALL" = mandatory.

### FR-1 Input selection
- FR-1.1 The user SHALL be able to choose an input folder (GUI folder picker or `--input`).
- FR-1.2 The tool SHALL scan the folder recursively (depth unlimited by default,
  `--no-recursive` to disable) for video files with extensions in
  `config.video_extensions` (default: `.mp4 .mov .avi .ts .mkv`, case-insensitive).
- FR-1.3 Files inside a folder named `merged` (the tool's own output) SHALL be skipped
  to avoid merging previous outputs.
- FR-1.4 Files whose name starts with `INCOMPLETE_DO_NOT_USE_` SHALL be skipped.
- FR-1.5 If zero video files are found, the tool SHALL report this clearly and stop.

### FR-2 Camera detection
The Claude skill inspects filenames and asks the user when unsure. The offline tool
uses a rule table plus a confirmation dialog.

- FR-2.1 The tool SHALL ship with built-in camera detection profiles, tried in order:

| Profile | Regex on filename (case-insensitive) | Camera label |
|---------|---------------------------------------|--------------|
| Xforce | `^(\d{14})_(\d+)([FR])\.(mp4\|mov)$` | group 3: `F`→Front, `R`→Rear |
| Suffix underscore | `_(F\|R\|A\|B\|I)\.` before extension | F/A→Front, R/B→Rear, I→Interior |
| Word | `(FRONT\|REAR\|BACK\|INSIDE\|INTERIOR\|CABIN)` | as named |
| CamN | `CAM([1-3])` | CAM1→Front, CAM2→Rear, CAM3→Interior |
| Folder | parent folder name matches any of the above | same mapping |
| 70mai | `(\d{4}_\d{4}_\d{6}_)(\d+)([FB])?` or folders `Normal\` `Rear\` | F→Front, B→Rear |

- FR-2.2 The tool SHALL apply the first profile that matches at least 90 % of the
  files. Files that do not match are listed as "Unclassified".
- FR-2.3 If no profile reaches 90 %, or the user passes `--camera-map`, the tool SHALL
  fall back to **manual mapping**:
  - GUI: dialog showing 5 sample filenames per detected suffix/folder, with a dropdown
    (Front / Rear / Interior / Skip) for each.
  - CLI: `--camera-map "F=Front,R=Rear"` or `--camera-map "folder:Normal=Front,folder:Rear=Rear"`.
- FR-2.4 Before merging, the tool SHALL always show a summary
  ("Front: 143 files, 7.2 GB · Rear: 143 files, 3.9 GB · Unclassified: 0") and wait for
  confirmation (GUI button; CLI prompt unless `--yes`).
- FR-2.5 Users SHALL be able to add custom profiles in `config.json` without code changes:

```json
"camera_profiles": [
  {"name": "MyBrand", "regex": "_(CH[12])\\.mp4$", "map": {"CH1": "Front", "CH2": "Rear"}}
]
```

### FR-3 Chronological ordering
- FR-3.1 For each camera group, the tool SHALL derive a sort key from the filename in
  this priority: (a) 14-digit timestamp `YYYYMMDDHHMMSS`; (b) `YYYY_MMDD_HHMMSS` or
  `YYYY-MM-DD_HH-MM-SS` style; (c) numeric sequence number; (d) file modified time.
- FR-3.2 Sorting SHALL never rely on OS directory listing order.
- FR-3.3 If the key used is (d) file mtime, the tool SHALL display a warning
  ("Order based on file modification time, please spot-check") and write it to the log.
- FR-3.4 The tool SHALL detect gaps: if two consecutive timestamps differ by more than
  `config.gap_warning_seconds` (default 300), it SHALL note "gap of N minutes between
  clip X and clip Y" in the summary. Gaps do not block the merge.
- FR-3.5 Optional `--split-on-gap` (default off): start a new output part whenever a
  gap larger than `gap_split_seconds` (default 1800) is detected, so separate trips
  become separate files (`Front_trip1.mp4`, `Front_trip2.mp4`).

### FR-4 Codec consistency check
- FR-4.1 Before merging a group, the tool SHALL run ffprobe on the first file, the
  last file, and every N-th file (`config.probe_sample_every`, default 20).
- FR-4.2 Compared fields: video `codec_name`, `width`, `height`, `r_frame_rate`,
  `pix_fmt`; audio `codec_name`, `sample_rate`, `channels`, and presence/absence of audio.
- FR-4.3 If all sampled files match, stream copy is used.
- FR-4.4 If they differ, the tool SHALL probe every file in the group, split the group
  into runs of identical parameters, and:
  - default: merge each run separately as its own part (`Front_part1.mp4` …) with
    stream copy, and explain why in the summary and log;
  - with `--reencode`: re-encode the whole group to one file using
    `-c:v libx264 -crf 18 -preset medium -c:a aac` (slow, not lossless; GUI shows an
    explicit warning and estimated time).
- FR-4.5 A file ffprobe cannot read SHALL be excluded, reported as "corrupt/unreadable",
  and never abort the whole job.

### FR-5 Concat list generation
- FR-5.1 One list file per output SHALL be written to `%TEMP%\DashCamMerger\` (or the
  output folder when `--keep-lists`), using the ffmpeg concat demuxer format.
- FR-5.2 Paths SHALL be **absolute**, forward-slash form, single-quoted, with any
  single quote in the name escaped as `'\''`.
- FR-5.3 The list file SHALL be UTF-8 without BOM.

### FR-6 Merge execution
- FR-6.1 Command used for lossless merge:

```
ffmpeg -y -hide_banner -f concat -safe 0 -i "<list.txt>" -c copy -movflags +faststart "<output.mp4>"
```

- FR-6.2 Outputs SHALL be written to `<input>\merged\` by default, or `--output`.
  The folder is created if missing. Existing outputs are never overwritten silently:
  the tool appends `_2`, `_3`… unless `--overwrite` is given.
- FR-6.3 Output file naming: `<Camera>_<firstTimestamp>_to_<lastTimestamp>.mp4`,
  e.g. `Front_20260831152516_to_20260831171203.mp4`. Parts add `_partN`.
- FR-6.4 The tool SHALL write to a temporary name `<name>.partial.mp4` and rename to
  the final name only after verification (FR-7) passes. This guarantees a finished
  `.mp4` in `merged\` is always a good file.
- FR-6.5 Groups SHALL be processed sequentially by default; `--parallel N` allows N
  simultaneous ffmpeg processes (default 1; disk-bound work rarely benefits).
- FR-6.6 Progress: the tool SHALL parse ffmpeg `-progress pipe:1` output and show
  percent complete, elapsed time, MB/s, and estimated remaining time per group.
- FR-6.7 Cancel: the user can cancel at any time. On cancel the tool kills ffmpeg,
  renames any `.partial.mp4` to `INCOMPLETE_DO_NOT_USE_<name>.mp4`, and never deletes it.
- FR-6.8 There is NO time cap. The 120-second limit and 2.5–3 GB chunking from the
  Claude skill apply only to the sandboxed remote shell and SHALL NOT be implemented.
  A single 50 GB merge in one ffmpeg call is fine locally. An optional
  `--max-part-gb N` remains available for users who want smaller files (e.g. FAT32 USB
  sticks, where 4 GB is the limit) – default: unlimited.

### FR-7 Verification
- FR-7.1 After each ffmpeg run the tool SHALL run
  `ffprobe -v error -show_entries format=duration,size -of json` on the output.
- FR-7.2 The expected duration is the sum of every source clip's ffprobe duration
  (probed during FR-4; if sampling was used, remaining files are probed here).
- FR-7.3 PASS if `|actual − expected| ≤ max(1.0 s, 0.1 % of expected)`.
- FR-7.4 FAIL if ffprobe errors (e.g. "moov atom not found"), duration mismatch beyond
  tolerance, or output size is under 90 % of the summed input size.
- FR-7.5 On FAIL the output is renamed `INCOMPLETE_DO_NOT_USE_<name>.mp4`, the reason is
  logged, and the job continues with the next group. The final summary lists failures.
- FR-7.6 After all groups, the tool SHALL re-check that every source file still exists
  with unchanged size and report "Sources untouched: OK".

### FR-8 Safety rules (absolute, except the opt-in deletion in FR-12)
- FR-8.1 The program SHALL contain no code path that moves, renames, or writes to any
  file inside the input folder except inside the `merged\` subfolder it creates. The one
  exception is deleting a source clip, which is only ever possible through the explicit,
  off-by-default mechanism in FR-12 - never as a side effect of any other feature.
- FR-8.2 The only files the tool may rename are ones it created itself in this run.
- FR-8.3 `--overwrite` applies only to previous outputs in `merged\`, never to sources.

### FR-9 Reporting
- FR-9.1 At the end, the tool SHALL show and log a report table:

```
Camera   Part  Output file                                   Files  Duration   Size     Verify
Front    1     Front_20260831152516_to_20260831171203.mp4    143    2:23:04    7.19 GB  PASS
Rear     1     Rear_20260831152516_to_20260831171203.mp4     143    2:23:04    3.88 GB  PASS
Unclassified: 0   Skipped unreadable: 0   Sources untouched: OK
Output folder: D:\DCIM\Movie\merged
```

- FR-9.2 The same report SHALL be saved as `merged\merge_report.txt` and the full log
  (all ffmpeg/ffprobe commands and their stderr) as `logs\merge_<timestamp>.log`.
- FR-9.3 GUI: an **Open output folder** button opens `merged\` in Explorer.

### FR-10 Configuration (`config.json`)

```json
{
  "video_extensions": [".mp4", ".mov", ".avi", ".ts", ".mkv"],
  "output_subfolder": "merged",
  "probe_sample_every": 20,
  "gap_warning_seconds": 300,
  "gap_split_seconds": 1800,
  "split_on_gap": false,
  "max_part_gb": 0,
  "parallel_jobs": 1,
  "ffmpeg_path": "ffmpeg\\ffmpeg.exe",
  "ffprobe_path": "ffmpeg\\ffprobe.exe",
  "camera_profiles": []
}
```

Missing keys use defaults; an invalid file produces a clear error naming the line.

### FR-11 CLI reference

```
DashCamMerger.exe --input <folder> [--output <folder>] [--yes] [--no-recursive]
                  [--camera-map "F=Front,R=Rear"] [--split-on-gap] [--max-part-gb N]
                  [--reencode] [--overwrite] [--keep-lists] [--parallel N]
                  [--dry-run] [--delete-source] [--verbose] [--version]
```

- `--dry-run` performs scan, grouping, ordering and probing, prints the plan and the
  exact ffmpeg commands, and writes nothing.
- Exit codes: 0 = all groups PASS; 1 = at least one group FAIL; 2 = usage/config error;
  3 = no input files; 130 = cancelled by user.

### FR-12 Opt-in source deletion (off by default)
- FR-12.1 `--delete-source` (CLI) / "Delete original clips after successful merge" (GUI)
  SHALL be off unless explicitly set. When on, a source clip is eligible for deletion
  only if the output part it belongs to has status PASS (FR-7). Clips belonging to a
  FAILED, CANCELLED, or SKIPPED part SHALL never be deleted.
- FR-12.2 Deletion SHALL require a confirmation naming the exact number of files, taken
  separately from the plan confirmation in FR-2.4: on the CLI, a typed "yes" prompt
  unless `--yes` is also given (which counts as confirmation); in the GUI, a warning
  dialog every time, regardless of other auto-confirm behaviour.
- FR-12.3 Deletion SHALL refuse to remove any path inside the run's output or temp
  folders, even if such a path were ever passed to it.
- FR-12.4 The report (screen, and `merge_report.txt`) SHALL state how many source clips
  were deleted, and name any that could not be deleted and why.
- FR-12.5 This is the only path in the program that deletes a file the user did not
  create in this run (compare FR-8.1); it lives in its own module, kept separate from
  the write/rename gatekeeper (Design Section 5.3).

## 6. GUI specification (Tkinter)

Single window, about 720×520 px, resizable.

1. **Input folder** – entry + **Browse…** button.
2. **Output folder** – entry, defaults to `<input>\merged`, + **Browse…**.
3. **Scan** button → fills a table: Camera | Files | Total size | First clip | Last clip |
   Codec | Order source (timestamp / mtime). Unclassified row if any.
4. Options checkboxes: *Split on trip gaps*, *Re-encode if formats differ (slow)*,
   *Overwrite existing outputs*, *Delete original clips after successful merge
   (cannot be undone)* (FR-12, off by default); spinbox *Max part size (GB,
   0 = unlimited)*.
5. **Merge** button (enabled after a successful scan) and **Cancel** button.
6. Progress area: one progress bar per camera group with percent, MB/s, ETA;
   a scrolling log pane below.
7. Result panel with the report table (FR-9) and **Open output folder**.
8. Menu: File → Open config.json, Open logs folder, Exit; Help → About (shows ffmpeg
   version and build date).

When camera detection is ambiguous (FR-2.3) a modal dialog appears during **Scan**.

## 7. Error handling

| Situation | Behaviour |
|-----------|-----------|
| ffmpeg/ffprobe missing | Stop with message naming the expected path; exit 2 |
| Input folder not readable | Stop; exit 2 |
| Output drive full during merge | ffmpeg fails → output renamed INCOMPLETE_…, error logged, continue with next group; exit 1 |
| Filename contains `'` or non-ASCII | Handled by escaping (FR-5.2) and UTF-8; tested |
| Very long paths (>260 chars) | Use `\\?\` prefix on Windows for all file operations |
| Mixed audio/no-audio clips | Treated as codec mismatch (FR-4.4) |
| User closes window mid-merge | Same as Cancel (FR-6.7) |

## 8. Performance targets

- Stream copy throughput is limited by disk; target ≥ 100 MB/s on SSD-to-SSD,
  ≥ 30 MB/s from an SD card reader. The tool never adds more than 5 % overhead.
- Scan + probe of 1 000 clips with sampling completes in under 30 s on HDD.
- Memory under 200 MB regardless of footage size (streaming, no clips loaded in RAM).

## 9. Logging

- One log file per run in `logs\`. Level INFO by default, DEBUG with `--verbose`.
- Every external command is logged verbatim with its exit code and stderr tail.
- Logs never contain anything but paths, sizes, durations and tool output.
- Logs older than 30 days are pruned at startup (only in the tool's own `logs\` folder).

## 10. Testing and acceptance

### Unit tests (pytest, run by the developer)
- Camera profile matching for each brand pattern, including near-misses.
- Sort-key extraction for all timestamp styles; stable ordering with identical keys.
- Concat list escaping (`'`, spaces, Unicode, UNC paths).
- Duration tolerance logic (FR-7.3).
- Config loading with missing/invalid keys.

### Integration tests (need ffmpeg)
- Generate 6 synthetic 2-second clips per camera with ffmpeg `testsrc`, run the tool,
  assert two outputs of 12 s each, PASS, sources unchanged (hash compare).
- Inject one clip with different resolution → expect two parts and a warning.
- Inject one truncated file → expect it skipped and reported, job still PASS for the rest.
- Cancel mid-run → expect `INCOMPLETE_DO_NOT_USE_` file and no deletion.

### Acceptance criteria (user-visible)
1. Merging a real Xforce folder produces one Front and one Rear file whose durations
   equal the sums of the clips, playable in VLC and Windows Media Player, seekable.
2. The original folder is byte-for-byte unchanged apart from the new `merged\` folder,
   unless the user explicitly enabled and confirmed FR-12 source deletion.
3. The whole procedure needs no internet and no admin rights.
4. A non-technical user can complete it from README.txt in under 5 minutes.

## 11. Build and release procedure

1. `python -m venv .venv && .venv\Scripts\activate && pip install pyinstaller pytest`
2. Download a static Windows ffmpeg build; copy `ffmpeg.exe`, `ffprobe.exe` into `ffmpeg\`.
3. `pytest`
4. `pyinstaller --noconfirm --onedir --windowed --name DashCamMerger --add-data "ffmpeg;ffmpeg" --add-data "config.json;." src\main.py`
5. Zip `dist\DashCamMerger\` as `DashCamMerger_v1.0_win64.zip`.
6. Record ffmpeg version and SHA-256 of the zip in `CHANGELOG.md`.

## 12. Suggested project structure

```
src\
  main.py            entry point: decides GUI vs CLI
  cli.py             argparse, exit codes
  gui.py             Tkinter window
  core\
    scanner.py       FR-1
    cameras.py       FR-2 profiles + manual mapping
    ordering.py      FR-3
    probe.py         FR-4 ffprobe wrapper + caching
    concat.py        FR-5, FR-6 list writing + ffmpeg runner + progress
    verify.py        FR-7
    report.py        FR-9
    config.py        FR-10
    cleanup.py       FR-12 opt-in source deletion
tests\
ffmpeg\
config.json
README.txt
```

## 13. Future ideas (not in v1)
- Side-by-side Front+Rear composite export.
- Extract embedded GPS/G-sensor data to GPX.
- Auto-detect and merge only clips from a chosen date range.
- Portable "right-click a folder → Merge dashcam clips" Explorer context menu entry.
