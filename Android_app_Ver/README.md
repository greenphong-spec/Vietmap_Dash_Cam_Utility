# DashCam Merger — Android version

Android port of DashCam Merger (see `../DashCamMerger-Specification.md`), built for
the Samsung Galaxy Note 10 (SM-N970F, Android 12 / API 31); runs on Android 9+.

It joins each camera's clip sequence (Front / Rear / Interior) into one MP4 **without
re-encoding**, verifies the result, and writes `merge_report.txt` next to the outputs.
Output goes to a `merged/` subfolder of the input folder by default, or to any folder
you choose — e.g. merge from the SD card and save to the phone's `Movies/`. Source clips are never moved, renamed or
deleted.

Full rebuild reference: [`DashCamMerger-Android-Specification.md`](DashCamMerger-Android-Specification.md)
(browsable `DashCamMerger-Android-Specification.html`; regenerate with `python tools\gen_docs.py`
from the repo root after editing the `.md`).

## How it differs from the desktop version

| Desktop (Python) | Android |
|---|---|
| ffmpeg concat demuxer, `-c copy` | `MediaExtractor` → `MediaMuxer`: every compressed sample copied unchanged, only timestamps shifted. No ffmpeg binary, APK has no native code. |
| ffprobe | `MediaExtractor` track formats |
| `WriteGuard` (allow-listed roots) | `OutputFolder` — can only create files inside the output folder and only rename files it created in this run; no delete method |
| `--output-dir` | **Choose output folder** (optional; *Use default* returns to `<input>/merged`). An output folder inside the input tree is skipped by the scanner; picking the input folder itself means the default. The plan shows free space there and warns if the merge won't fit. |
| Folder path | Storage Access Framework folder picker (works for internal storage, SD card, USB-OTG) — no storage permissions requested |
| Input `.mp4 .mov .avi .ts .mkv` | `.mp4 .mov .ts .mkv .3gp` (Android cannot read `.avi`) |
| Output any codec ffmpeg can copy | Video H.264 / H.265 / MPEG-4 / H.263; audio AAC / AMR. Unsupported audio (e.g. PCM in `.mov`) is dropped with a warning, video still copied. |
| `--delete-source` (FR-12) | Not included — the Android app never deletes anything |
| `config.json`, custom camera profiles, re-encode, parallel jobs | Not included; built-in camera profiles + manual camera mapping screen |

Kept identical: camera profiles and 90 % detection threshold (FR-2), ordering by
timestamp → sequence → mtime with warnings (FR-3), gaps measured end-to-start from the
probed clip length — 1-, 3- and 5-minute clips all work, no length is assumed (FR-3.4a),
**trips**: one file per trip, on by default, a new trip after a break of more than
15 min (set on the Setup tab) (FR-3.5), split on codec change (FR-4.4), unreadable-file
skipping (FR-4.5), output naming `Front_<first>_to_<last>[_tripN][_partM].mp4` with
`_2`, `_3` on collisions (FR-6.2/6.3), `.partial.mp4` → final rename only after verification, failed
or cancelled outputs kept as `INCOMPLETE_DO_NOT_USE_*` (FR-6.5), duration within
max(1 s, 0.1 %) and size ≥ 90 % verification (FR-7), source-unchanged check (FR-7.6).

Extra option: **Split at 3.9 GB** — dashcam SD cards are usually FAT32, which cannot
hold a file over 4 GB.

## Using it

The screen has three tabs — **Setup** (folders and options), **Files** (the files to
export, full screen) and **Details** (plan text, report, errors). The Scan & plan /
Merge buttons and the progress bar stay at the bottom on every tab.

1. **Choose dashcam folder** — e.g. the SD card's `DCIM/Movie` (in the picker, open the
   SD card from the ☰ menu). Access is remembered.
2. Optionally **Choose output folder** — e.g. *Phone → Movies → DashMerged* to copy the
   merged result off the SD card. Leave unset to write to `<input>/merged`.
   **Split into trips** (on by default) writes one file per trip; a break of more than
   **15 min** (change it in the box next to it) starts a new trip.
3. **Scan & plan** — shows each camera, clip count, codec, the files that will be
   written, and any warnings. If the file naming isn't recognised you assign each group
   to Front/Rear/Interior/Skip.
   Each row shows the camera and trip (e.g. *Front trip 3/9*), its start and end time,
   clips, length and size. Every output file has a checkbox (all ticked by default): untick the ones you don't
   want, or use **Select all** / **Select none** or a camera's header box. Only the
   ticked files are merged; the report lists the skipped ones.
   **Order: By time / By camera** sorts the list (By time, the default, puts each trip's
   Front and Rear file side by side), ▲ / ▼ move a file, and files are merged in the order shown.
   **Keep Front/Rear pairs together** makes ticking a file also tick the other camera's
   file from the same time.
4. **Merge** (enabled when at least one file is ticked) — progress bar; the screen stays on while merging (large merges take a few
   minutes — keep the app in the foreground). **Cancel** keeps the partial file as
   `INCOMPLETE_DO_NOT_USE_*`.

## Build & install

Requires JDK 17 and the Android SDK (`local.properties` → `sdk.dir`).

```
gradlew.bat testDebugUnitTest      # JVM unit tests for the ported core (no device needed)
gradlew.bat assembleDebug          # app\build\outputs\apk\debug\app-debug.apk
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

Wireless debugging: `adb pair <ip>:<pair-port>` then `adb connect <ip>:<port>`.

## Layout

- `core/` — pure Kotlin, no Android imports, unit-tested: `Models`, `Cameras`,
  `Ordering`, `Planner`, `Report` (ports of the Python modules of the same names).
- `android/` — `SafStorage` (scanner + `OutputFolder` write guard), `MediaProbe`,
  `Merger` (stream copy + verify), `MergeSession` (pipeline orchestrator), `MainActivity`
  (Compose UI).
