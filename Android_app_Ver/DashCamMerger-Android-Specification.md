# DashCam Merger for Android — Specification (as built)

| | |
|---|---|
| Document version | 1.4 — 2026-10-03 (1.1: choose which files to export, AR-10.5–AR-10.13; 1.2: export order and Front/Rear pairing, AR-10.14–AR-10.17; 1.3: tabbed layout, AR-12; 1.4: output names use `yyyyMMdd_HHmmss`, AR-6.3) |
| App version | `1.4-android` (versionCode 5), package `com.phong.dashcammerger` |
| Status | Built, installed and verified on a Samsung Galaxy Note 10 (SM-N970F, Android 12) |
| Source location | `Android_app_Ver/` in the DashCam Merger repository (commit `51ae98e`, later updates follow) |
| Parent documents | `../DashCamMerger-Specification.md` (desktop FR-1…FR-12), `../DashCamMerger-Design.md` |

This document describes **what was actually built**, in enough detail to rebuild the app
from scratch without the source code. It is written as a rebuild reference: every rule,
regular expression, threshold, file name and user-visible text that affects behaviour is
listed. Requirement IDs `AR-x` (Android Requirement) are new; where a requirement ports a
desktop requirement the desktop ID is given as `(FR-x)`.

---

## 1. Purpose and scope

### 1.1 Problem

Dashcams (Vietmap, Xforce, 70mai, …) record 1-minute clips per camera, e.g.
`20260924070830_0001446F.MP4` (Front) and `20260924070830_0001447R.MP4` (Rear). Watching a
trip means opening dozens of files. The desktop tool (Windows, Python + ffmpeg) joins them;
this app does the same **on the phone**, directly from the dashcam's SD card (or a card
reader on USB-OTG), without a PC.

### 1.2 What the app does

1. The user picks the dashcam folder (input) and optionally a different output folder
   (typical use: input on the SD card, output in the phone's `Movies/`).
2. The app finds all clips, works out which camera each clip belongs to, orders them in
   time, and shows a **plan**: which output files will be written, from how many clips,
   total duration and size, and warnings (gaps, unreadable files, space).
3. On **Merge**, each camera's clips are joined into one MP4 per trip part **losslessly**
   (compressed samples are copied, never decoded or re-encoded).
4. Each output is verified (duration and size) before it gets its final name, and a
   `merge_report.txt` is written next to the outputs.

### 1.3 Non-goals (deliberately not built)

- No re-encoding, trimming, overlays, speed-up, or format conversion.
- No deletion of source clips (desktop FR-12 `--delete-source` is **not** ported). The app
  never deletes any file.
- No `config.json`, no user-defined camera profiles, no CLI, no parallel merge jobs.
- No background/foreground service: merging runs while the app is open (screen kept on).
- No network access, no analytics, no storage permissions.

---

## 2. Platform and build environment

### 2.1 Target device and OS

| Item | Value |
|---|---|
| Reference device | Samsung Galaxy Note 10, SM-N970F, `arm64-v8a`, Android 12 (API 31) |
| `minSdk` | 28 (Android 9) |
| `targetSdk` / `compileSdk` | 36 |
| Native code | None (APK contains no `.so`; pure Kotlin/JVM) |
| Permissions in manifest | **None**. Folder access is granted per folder through the system picker (Storage Access Framework, persisted URI permissions). |

### 2.2 Toolchain (versions that are known to build)

| Component | Version |
|---|---|
| JDK | Temurin 17 (`jdk-17.0.20.8-hotspot`) |
| Gradle (wrapper) | 8.14.5 (`gradle-8.14.5-bin.zip`) |
| Android Gradle Plugin | 8.13.2 |
| Kotlin (+ Compose compiler plugin) | 2.4.10 |
| Compose BOM | 2026.06.01 |
| `androidx.core:core-ktx` | 1.18.0 |
| `androidx.lifecycle:lifecycle-runtime-ktx` | 2.10.0 |
| `androidx.activity:activity-compose` | 1.12.0 |
| Compose artifacts | `ui`, `ui-graphics`, `material3` (versions from BOM) |
| Unit tests | `junit:junit:4.13.2` |
| Android SDK | platform `android-36`, build-tools 35.0.0 |

Newer AndroidX versions (core-ktx 1.19, activity-compose 1.13, lifecycle 2.11) require
compileSdk 37 / AGP 9.1 and must **not** be used with the setup above.

Java/Kotlin target: `JavaVersion.VERSION_17`, `jvmTarget = JVM_17`. Minification off in
both debug and release. `gradle.properties`: `org.gradle.jvmargs=-Xmx2048m
-Dfile.encoding=UTF-8`, `org.gradle.parallel=true`, `org.gradle.caching=true`,
`android.useAndroidX=true`, `android.nonTransitiveRClass=true`,
`kotlin.code.style=official`. `settings.gradle.kts` uses the standard `google()` (restricted
to `com.android.*`, `com.google.*`, `androidx.*` groups), `mavenCentral()`,
`gradlePluginPortal()` repositories, `RepositoriesMode.FAIL_ON_PROJECT_REPOS`, root project
name `DashCamMerger`, one module `:app`.

### 2.3 Build, test, install

```
gradlew.bat testDebugUnitTest       # 9 JVM unit tests, no device needed
gradlew.bat assembleDebug           # app\build\outputs\apk\debug\app-debug.apk (~29 MB)
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

Wireless debugging: *Settings → Developer options → Wireless debugging*, then
`adb pair <ip>:<pairing-port>` and `adb connect <ip>:<port>`.

`local.properties` (not committed) holds `sdk.dir=C\:\\Android\\Sdk`.

---

## 3. Functional requirements

### 3.1 Folder selection and access

- **AR-1.1** The user selects the **input folder** with the system folder picker
  (`ACTION_OPEN_DOCUMENT_TREE`). Any provider the picker offers may be used; the supported
  and tested one is external storage (phone storage, SD card, USB-OTG card reader).
- **AR-1.2** On selection the app takes a **persistable** read+write URI permission and
  stores the tree URI in preferences, so the choice survives restarts.
- **AR-1.3** The user may select a separate **output folder** the same way (read+write,
  persisted). A **Use default** button clears it.
- **AR-1.4** Output location rule:
  - no output folder chosen → `<input folder>/merged` (created if missing; an existing
    child named `merged` in any letter case is reused);
  - output folder chosen **and** it is the same tree as the input (same tree document ID)
    → treated as "not chosen" (default `merged/` subfolder);
  - otherwise → files are written **directly into** the chosen folder (no subfolder).
- **AR-1.5** Folders are shown as `Phone: /<path>` for the primary volume and
  `SD card/USB (<volume-id>): /<path>` for other volumes, derived from the tree document
  ID `<volume>:<path>` (e.g. `1721-8D53:Vietmap_M1/Movie`).
- **AR-1.6** Changing input or output folder resets the session to Idle (discarding any
  shown plan).

### 3.2 Scanning (FR-1)

- **AR-2.1** The input tree is walked **recursively** (breadth-first) with
  `DocumentsContract` child queries (`DOCUMENT_ID, DISPLAY_NAME, MIME_TYPE, SIZE,
  LAST_MODIFIED`).
- **AR-2.2** A file is a clip when its extension (case-insensitive) is one of
  `mp4, mov, ts, mkv, 3gp`. (`.avi` from the desktop list is excluded: Android's
  `MediaExtractor` cannot read it.)
- **AR-2.3** Skipped: any sub-folder named `merged` (case-insensitive) (FR-1.3); the output
  folder itself if it lies inside the input tree (matched by document ID) (FR-1.3); any
  file whose name starts with `INCOMPLETE_DO_NOT_USE_` (FR-1.4).
- **AR-2.4** Each clip records: document URI, file name, parent folder name, size in bytes,
  last-modified time (ms). Scan results are sorted by lower-cased URI for determinism.
- **AR-2.5** No clips found → state *Error*, status `No video files found`, text
  `No mp4/mov/ts/mkv/3gp files in this folder or its subfolders.` (FR-1.5)
- **AR-2.6** The output folder is opened (and `merged/` created if needed) at the start of
  the scan, before any clip is read.

### 3.3 Camera detection (FR-2)

- **AR-3.1** Built-in profiles are tried **in this order**; the first profile that labels
  **≥ 90 %** of all clips wins (FR-2.2). Matching is case-insensitive; "name" profiles
  match the file name, "parent" profiles match the containing folder name. The token
  captured in group `cam` is upper-cased and looked up in the profile's map; a clip counts
  as matched only if the token is in the map.

| # | Profile | Scope | Token → camera |
|---|---|---|---|
| 1 | Xforce | name | F→Front, R→Rear |
| 2 | SuffixLetter | name | F, A→Front; R, B→Rear; I→Interior |
| 3 | Word | name | FRONT→Front; REAR, BACK→Rear; INSIDE, INTERIOR, CABIN→Interior |
| 4 | CamN | name | 1→Front, 2→Rear, 3→Interior |
| 5 | 70mai | name | F→Front, B→Rear |
| 6 | Folder | parent | FRONT, F, NORMAL→Front; REAR, R, BACK, B→Rear; INTERIOR, INSIDE, CABIN, I→Interior |

Regular expressions (Java syntax, compiled with `Pattern.CASE_INSENSITIVE`, applied with
`find()`), exactly as used:

```
1 Xforce        ^(?<ts>\d{14})_(?<seq>\d+)(?<cam>[FR])\.(?:mp4|mov)$
2 SuffixLetter  _(?<cam>[FRABI])\.[A-Za-z0-9]+$
3 Word          (?<cam>FRONT|REAR|BACK|INSIDE|INTERIOR|CABIN)
4 CamN          CAM(?<cam>[1-3])
5 70mai         ^(?:NO|EV|PA)?\d{4}[-_]?\d{4}[-_]\d{6}[-_]\d+(?<cam>[FB])\.[A-Za-z0-9]+$
6 Folder        ^(?<cam>front|f|normal|rear|r|back|b|interior|inside|cabin|i)$
```

- **AR-3.2** The Xforce profile also sets the clip's timestamp from group `ts`
  (`yyyyMMddHHmmss`) and its sequence number from group `seq`. Vietmap M1 files
  (`20260924070830_0001446F.MP4`) match this profile.
- **AR-3.3** Before each profile is tried, all labels are reset to *Unclassified*.
- **AR-3.4** **Manual mapping** (FR-2.3): if no profile reaches 90 %, clips are grouped by a
  token and the user assigns each token to Front / Rear / Interior / Skip:
  - if the clips live in more than one folder, token = `folder:<parent folder name>`;
  - otherwise token = the letters immediately before the extension (regex
    `([A-Za-z]+)\.[A-Za-z0-9]+$`, upper-cased), or `ALL` if there are none.
  - Tokens are listed sorted. Tokens the user leaves unassigned become *Unclassified*;
    *Skip* clips are ignored silently.
- **AR-3.5** Output groups are built in the order Front, Rear, Interior; groups with no
  clips are omitted. Unclassified clips are counted in the plan and report but not merged.

### 3.4 Ordering and gaps (FR-3)

- **AR-4.1** For every clip the file stem (name without extension) is parsed:
  - **Timestamp**, first valid match of, in order:
    `TS14 = (?<!\d)(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?!\d)` then
    `TS_SEP = (?<!\d)(\d{4})[-_]?(\d{2})[-_]?(\d{2})[-_T ](\d{2})[-_:.]?(\d{2})[-_:.]?(\d{2})(?!\d)`.
    Matches that are not a valid date/time (e.g. month 13) are skipped and the next match
    is tried. Result is a local date-time (no time zone).
  - **Sequence** = the **last** run of 3+ digits `(?<!\d)(\d{3,})(?!\d)` that does not
    overlap the timestamp match.
  - Values already set by the camera profile are kept.
- **AR-4.2** Each camera group is sorted by the strongest key that **all** its clips have
  (FR-3.1/3.2):
  1. all have a timestamp → by (timestamp, sequence or 0, lower-case name);
  2. else all have a sequence → by (sequence, lower-case name), warning
     `<Camera>: order based on sequence number in filename`;
  3. else → by (mtime, lower-case name), warning
     `<Camera>: order based on file modification time, please spot-check` (FR-3.3).
- **AR-4.3** Gaps (FR-3.4), only for timestamp-ordered groups: for consecutive clips A, B:
  `gap = (B.timestamp − A.timestamp in whole seconds) − A.duration`, where A.duration is
  the probed duration, or 60 s if unknown. A gap **> 300 s** produces the warning
  `<Camera>: gap of <minutes, 1 decimal> min between <A name> and <B name>`.

### 3.5 Probing (FR-4)

- **AR-5.1** **Every** clip of every camera group is probed (not sampled), with a pool of
  **4 worker threads**. Progress is shown as `Reading <Camera> clips… <done> / <total>`.
- **AR-5.2** Probing opens the clip with `MediaExtractor.setDataSource(context, uri)` and
  selects tracks:
  - video = first track whose MIME starts with `video/`;
  - audio = first `audio/` track **if** its MIME is muxable (`audio/mp4a-latm` (AAC),
    `audio/3gpp` (AMR-NB), `audio/amr-wb`); if the first audio track is not muxable it is
    recorded as *dropped* and no other audio track is considered.
- **AR-5.3** A clip is **unreadable** (with this reason) when: opening throws (reason =
  exception message); `no video track`; video MIME not in {`video/avc`, `video/hevc`,
  `video/mp4v-es`, `video/3gpp`} → `video codec <mime> cannot be stream-copied to MP4`;
  duration ≤ 0 → `no duration (file may be truncated)`.
- **AR-5.4** Duration = max(video `KEY_DURATION`, audio `KEY_DURATION`) in microseconds.
- **AR-5.5** **Stream signature** (two strings; clips can only share an output if both are
  equal):
  - video: `<mime> <width>x<height> rot<rotation> csd:<h0>/<h1>/<h2>`
  - audio: `<mime> <sampleRate>Hz <channels>ch csd:<…>` or `none`

  where `h0…h2` are hex `Arrays.hashCode` values of the `csd-0`, `csd-1`, `csd-2` codec
  config buffers that exist. Including the codec config (SPS/PPS) is required because
  `MediaMuxer` takes the track format from the **first** clip only.
- **AR-5.6** Description for the plan: `H.264|H.265|MPEG-4|H.263 <w>x<h>` plus
  ` + AAC|AMR|AMR-WB` when audio is copied.
- **AR-5.7** Unreadable clips are removed from their group and listed in warnings as
  `<Camera>: skipped unreadable file <name> (<reason>)` (FR-4.5). If a group has dropped
  audio: `<Camera>: audio not copied (<mime> cannot be stored in MP4), video only`. If a
  group ends up empty: `<Camera>: no readable clips left, nothing to merge`.

### 3.6 Planning, splitting and naming (FR-3.5, FR-4.4, FR-6)

- **AR-6.1** Walking each ordered group, a **new output part** starts before clip C when
  (checked in this priority order):
  1. C's signature differs from the current part's signature → reason `codec change`
     (FR-4.4);
  2. option *trip gap* is on and the gap before C is **> 1800 s** → `trip gap` (FR-3.5);
  3. option *3.9 GB* is on and current part bytes + C's size > 3.9 × 1024³ bytes →
     `max part size` (FR-6.8).

  Each split adds the warning `<Camera> part <n>: new part started because of <reason>`.
- **AR-6.2** Expected duration of a part = sum of its clips' probed durations; expected
  size = sum of its clips' sizes.
- **AR-6.3** Output name (FR-6.3):
  `<Camera>_<first>_to_<last>.mp4`, with `_part<n>` appended to the stem when the camera
  has more than one part. `<first>`/`<last>` are the first/last clip's timestamp as
  `yyyyMMdd_HHmmss` (date and time separated by `_`, since v1.4), or the clip's file stem if it has no timestamp.
  Example: `Front_20260924_070830_to_20260924_073130_part1.mp4`.
- **AR-6.4** Collisions (FR-6.2): if the name exists in the output folder, or was already
  given to another part in this plan, try `<stem>_2.mp4`, `<stem>_3.mp4`, … until free.
  Existing files are never overwritten.
- **AR-6.5** Each part is first written as `<final stem>.partial.mp4`.

### 3.7 Merging (lossless stream copy)

- **AR-7.1** Parts are processed sequentially. For each part:
  1. create `<stem>.partial.mp4` (MIME `video/mp4`) in the output folder;
  2. open it `"rw"` and create `MediaMuxer(fd, MUXER_OUTPUT_MPEG_4)`;
  3. for each clip in order, open a `MediaExtractor`, select the same tracks as in
     probing, and copy samples (below);
  4. `muxer.stop()`; then verify (§3.8).
- **AR-7.2** Muxer set-up happens on the **first clip** of the part: `addTrack` with the
  first clip's video format, then its audio format if audio is copied;
  `setOrientationHint(KEY_ROTATION)` if the video format carries a rotation; `start()`.
- **AR-7.3** **Timestamp algorithm** (per clip):
  - `base` = the extractor's first sample time after selecting the tracks (clamped ≥ 0),
    i.e. the earliest sample of the clip;
  - `offset` = running output time, starting at 0 for the part;
  - each sample is written with `pts_out = offset + (pts_in − base)`, unchanged bytes,
    `BUFFER_FLAG_KEY_FRAME` iff the extractor reports `SAMPLE_FLAG_SYNC`;
  - audio only: if `pts_out ≤` the previous audio `pts_out`, use previous + 1 µs (keeps
    audio strictly increasing across clip joins);
  - per track remember the last sample-to-sample delta; the clip's end =
    max over samples of `(pts_in − base + delta_of_that_track)`;
  - after the clip: `offset += clip end`.

  Samples are read in the file's own interleaving order (both tracks selected on one
  extractor), so the output interleaving mirrors the source.
- **AR-7.4** Sample buffer: one direct `ByteBuffer` of **16 MiB** (enough for a 4K
  keyframe).
- **AR-7.5** Cancellation is checked before each clip and before each sample. On cancel or
  any exception the muxer is released and the partial file is **kept** and renamed to
  `INCOMPLETE_DO_NOT_USE_<final stem>.mp4` (`_2`, `_3`… if taken) (FR-6.5). If that
  rename fails the file keeps its `.partial.mp4` name.
- **AR-7.6** Progress = bytes of samples copied / total expected bytes of all parts, UI
  updated at most every 200 ms. Status line: `Part <i>/<n>: <output name>`.

### 3.8 Verification (FR-7)

- **AR-8.1** After `stop()`, the partial file is re-opened with `MediaExtractor`; its
  duration is the max `KEY_DURATION` across its tracks.
- **AR-8.2** **PASS** requires both:
  - `|actual − expected| ≤ max(1.0 s, 0.1 % × expected)`; and
  - `actual size ≥ 90 %` of the summed source sizes.
- **AR-8.3** PASS → rename `.partial.mp4` → final name (FR-6.4). FAIL → rename to
  `INCOMPLETE_DO_NOT_USE_…` with reason(s): `output cannot be read back`,
  `duration <a>s vs expected <e>s`, `size <n> < 90% of <m>` (joined with `; `).
- **AR-8.4** After all parts: every source clip is re-queried; a clip that is missing or
  whose size changed is reported as `<name>: changed or missing` and the report shows
  `Sources untouched: CHANGED` (FR-7.6). (Android checks size only, not mtime.)

### 3.9 Report (FR-9)

- **AR-9.1** `merge_report.txt` (UTF-8, `\n`) is written into the output folder after
  every merge run (replacing an earlier report there). Format:

```
Front part 1: Front_20260924_070830_to_20260924_073130_part1.mp4
    24 files, 0:23:07, 2.33 GB  ->  PASS
Rear part 1: Rear_20260924_070830_to_20260924_073130_part1.mp4
    24 files, 0:23:07, 1.60 GB  ->  PASS
Unclassified: 0   Skipped unreadable: 0   Sources untouched: OK
Output folder: Phone: /Movies/DashMerged

Warnings:
  Front: gap of 494.6 min between 20260924073130_0001492F.MP4 and 20260924154610_0001494F.MP4
  Front part 2: new part started because of trip gap
```

  For non-PASS parts the status is followed by ` (<reason>)` and the file name is the
  `INCOMPLETE_…` name. A `Source problems:` section appears when AR-8.4 found any.
  When the user unticked parts (AR-10.12), the per-part lines are followed by
  `Not selected (skipped by user): <m>` and one line per skipped output name, indented
  4 spaces.
- **AR-9.2** Formats: duration `H:MM:SS` (rounded seconds); size `x.xx GB` (≥ 1 GiB),
  `x.x MB` (≥ 1 MiB) else `x.x KB`, 1024-based, `.` decimal separator.
- **AR-9.3** The same text is shown on screen when the run finishes.

### 3.10 Plan screen (FR-2.4)

- **AR-10.1** Plan text:

```
Front: 3 files, 10.6 MB  [H.264 1280x720 + AAC]
Rear: 3 files, 10.6 MB  [H.264 1280x720 + AAC]
Unclassified: 0
Unreadable: 0
Parts to write: 2
  Front_20260927_120000_to_20260927_120200.mp4  (3 clips, 0:00:30, 10.6 MB)
  Rear_20260927_120000_to_20260927_120200.mp4  (3 clips, 0:00:30, 10.6 MB)
Output folder: Phone: /Movies/DashMerged
Warnings:
  …
Free space there: 128.28 GB
```

- **AR-10.2** Free space: for the external-storage provider
  (`com.android.externalstorage.documents`) the volume is the part of the output folder's
  document ID before `:`; free bytes = `StatFs(<path>).availableBytes` with path =
  `Environment.getExternalStorageDirectory()` for `primary`, else `/storage/<volume>`. If
  unknown, the line is omitted. (The provider's roots table is not usable: it needs a
  system permission.)
- **AR-10.3** If the summed expected size of the **selected** parts (AR-10.9) > free
  space, the selection card shows (in the error colour)
  `NOT ENOUGH SPACE: needs about <x>, only <y> free in the output folder. Free up space or
  choose another output folder.` The Merge button is still offered (the user decides).
- **AR-10.4** A plan with zero parts → state *Error*, status `Nothing to merge`.

#### Choosing which files to export (v1.1)

- **AR-10.5 Selection list.** In state *Planned*, above the plan text, a card *Files to
  export* lists one row per planned output part: checkbox, output file name,
  `<n> clips, H:MM:SS, <size>`, and ▲ / ▼ buttons (AR-10.17). The rows are one flat list
  in export order (AR-10.14). Above them is one tri-state box per camera present, shown as
  `<Camera> (<ticked>/<n>)`, in `CAMERA_ORDER`.
- **AR-10.6 Default.** Right after a successful plan every part is ticked, so tapping
  Merge immediately behaves exactly like v1.0.
- **AR-10.7 Bulk controls.** Buttons **Select all** (disabled when all are ticked) and
  **Select none** (disabled when none are ticked). Each camera header has a tri-state
  checkbox (on / off / some); tapping it ticks all of that camera's parts, or unticks
  them all when they were all ticked.
- **AR-10.8 Summary and Merge gating.** A line `Selected: <k> of <n> file(s), <size>`
  (size = sum of the selected parts' expected sizes) updates live. **Merge** is enabled
  only when k ≥ 1. Status text in *Planned*: `Plan ready - choose files, then tap Merge`.
- **AR-10.9 Free space on the selection.** AR-10.3 is evaluated on the selected parts
  and updates as the selection changes. The plan text keeps the `Free space there:` line.
- **AR-10.10 Merge only the selection.** Merge processes only the ticked parts, in plan
  order. Progress, the `Part i/k` labels and the Done/Cancelled/Finished status counts use
  k = number of ticked parts. The source-unchanged check (FR-7.6) covers the clips of
  the ticked parts.
- **AR-10.11 Stable names.** Output names are the ones computed at plan time (AR-6.3,
  AR-6.4) and are not recomputed when parts are unticked. For example, `_part2` may be
  written without `_part1`. Unticked parts write nothing.
- **AR-10.12 Report.** See AR-9.1: skipped parts are listed under
  `Not selected (skipped by user):`.
- **AR-10.13 Lifetime.** The selection is held in `MergeSession`, so it survives a
  screen rotation. Every new plan resets it to "all ticked". It is cleared by
  `Scan & plan`, a folder change and `reset()`. Edits are ignored outside *Planned*.

#### Export order and Front/Rear pairing (v1.2)

- **AR-10.14 Export order.** `MergeSession.order` is a permutation of the plan's part
  indices. The list shows parts in this order, and Merge writes the ticked ones in this
  order (the report lists results in the same order, then the skipped ones). It is
  rebuilt from the current sort mode on every new plan.
- **AR-10.15 Sort mode.** Chips **Order: By camera | By time** (preference
  `order_by_time`, default *By camera*). Choosing one re-sorts the list immediately and
  drops manual moves.
  - *By camera* = plan order: Front parts, then Rear, then Interior, each in time order.
  - *By time* = by part start (first clip's timestamp); same start → `CAMERA_ORDER`, so
    the Front and Rear file of one trip sit together (Front 1, Rear 1, Front 2, Rear 2).
    Parts without a timestamp go last, by part number then camera.
- **AR-10.16 Keep Front/Rear pairs together.** Switch *Keep Front/Rear pairs together
  (same time)* (preference `link_pairs`, default off). Two parts of **different** cameras
  are partners when their time ranges overlap: range = [first clip timestamp, last clip
  timestamp + last clip duration (60 s if unknown)). When neither part has a timestamp,
  parts with the same part number are partners; a part with a timestamp is never paired
  with one without. With the switch on, ticking or unticking a row applies the same
  state to its partners. *Select all*, *Select none* and the camera boxes are not
  affected by the switch.
- **AR-10.17 Manual order.** ▲ swaps a row with the one above and ▼ with the one below
  (disabled on the first and last row respectively).

### 3.11 Write safety (FR-8)

- **AR-11.1** One class, `OutputFolder`, is the **only** code that creates, writes or
  renames files. It has **no delete method**; nothing in the app deletes files.
- **AR-11.2** It creates files only as direct children of the output folder; names
  containing `/` are refused; creating a name that already exists is refused; if the
  provider silently changes the name (e.g. adds ` (1)`), it is reported as an error.
- **AR-11.3** It renames only documents it created during this run (tracked set of URIs).
  The one exception is `merge_report.txt`, which may be an existing file it opens with
  mode `"wt"` (truncate) to replace.
- **AR-11.4** Source clips are only ever opened for reading (`MediaExtractor`, queries).

### 3.12 Options (persisted)

| Switch label | Default | Effect |
|---|---|---|
| `Start a new file after a trip gap (> 30 min)` | off | AR-6.1 rule 2 |
| `Split at 3.9 GB (FAT32 SD card / USB stick)` | off | AR-6.1 rule 3, 3.9 GiB limit (FAT32 cannot hold files ≥ 4 GiB) |

Fixed values: output subfolder `merged`, gap warning 300 s, gap split 1800 s, match
threshold 90 %, default clip length 60 s.

---

## 4. User interface

Single activity, Jetpack Compose, Material 3; light/dark colour scheme follows the system;
edge-to-edge with safe-drawing padding; 16 dp outer padding, 12 dp vertical spacing. The
screen keeps itself on while scanning or merging.

### 4.1 Layout (top to bottom, v1.3 tabbed)

1. Title **DashCam Merger** (titleLarge, bold). Outer padding 16 dp horizontal, 8 dp
   vertical; 8 dp spacing.
2. **AR-12.1** Tab row **Setup | Files | Details**. The selected tab's content fills all
   space between the tab row and the bottom bar (item 4), so each can be used full screen.
   The Files tab label is `Files <ticked>/<n>` (one line) once a plan exists. The selected tab
   survives rotation (`rememberSaveable`).
3. Tab content:
   - **Setup**: the line *"Joins dashcam clips per camera into one video, without
     re-encoding. Source files are never changed or deleted."* and a **Card** with
     label *Input (dashcam clips)*; folder text (AR-1.5) or *No folder chosen*; button
     *Choose dashcam folder* / *Change input folder*; label *Output (merged videos)*;
     folder text or *Same as input, in a "merged" subfolder*; buttons *Choose output
     folder* and (only when one is set) *Use default*; the two option switches (§3.12).
     Scrolls vertically. In state *NeedCameraMap* the tab shows the **camera-mapping**
     list (§4.2) instead.
   - **Files**: the **Files to export** card (AR-10.5–10.17), scrolling vertically; before
     any plan: *No plan yet - tap Scan & plan to list the files to export.* While merging
     and after the run the list stays visible but all its controls are disabled.
   - **Details**: the plan text (AR-10.1), the merge report (AR-9.1) or the error text, in
     monospace 11 sp (line height 15 sp), scrolling vertically and horizontally; before
     anything exists: *The plan, the merge report and any errors appear here.*
4. **AR-12.2** Bottom bar, always visible whichever tab is open: status line (bodyMedium,
   medium weight); while busy a linear progress bar; button row **Scan & plan** (enabled
   when an input folder is set and not busy) plus **Merge <k> file(s)** (state *Planned*,
   enabled when k ≥ 1) or **Cancel** (shown while *Scanning*/*Merging*, enabled only while
   *Merging*).

**AR-12.3** The app switches tab when the state changes: *NeedCameraMap* → Setup,
*Planned* → Files, *Done* / *Error* → Details. The user can switch freely afterwards.

All folder buttons and switches are disabled while busy.

### 4.2 Camera-mapping list (AR-3.4)

One card per token: `<token>  (<n> files, e.g. <first file name>)` and four filter chips
*Front*, *Rear*, *Interior*, *Skip* (single choice per token). Button **Apply and plan**
(enabled once at least one token is assigned) runs planning.

### 4.3 State machine

| State | Entered by | Status text | Buttons |
|---|---|---|---|
| Idle | start, folder change | — | Scan & plan |
| Scanning | Scan & plan / Apply and plan | `Scanning folder…`, `Found <n> clips (<profile> naming). Reading streams…`, `Reading <Camera> clips… d / t` | Scan & plan (disabled), Cancel (disabled) |
| NeedCameraMap | no profile ≥ 90 % | `Camera naming not recognised - please assign each group` | Scan & plan, mapping list |
| Planned | plan with ≥ 1 part | `Plan ready - choose files, then tap Merge` | Scan & plan, **Merge <k> file(s)** (enabled when ≥ 1 file ticked), Select all / Select none, Order chips, pair switch, checkboxes, ▲ / ▼ |
| Merging | Merge | `Part i/k: <name>` (k = ticked parts) / `Cancelling…` | Cancel |
| Done | run finished | `Done - all <n> part(s) verified` / `Finished with problems - <p> of <n> part(s) passed` / `Cancelled - <p> of <n> part(s) finished` | Scan & plan |
| Error | exception, no clips, nothing to merge | `Error` / `No video files found` / `Nothing to merge` | Scan & plan |

A plan is used for one merge only; after *Done* the user rescans (existing outputs then
get `_2` names, AR-6.4).

### 4.4 Preferences

`SharedPreferences` file `dashcam_merger`: `tree_uri` (input), `output_tree_uri`
(output), `split_on_gap` (bool), `split_fat32` (bool), `order_by_time` (bool, AR-10.15),
`link_pairs` (bool, AR-10.16).

---

## 5. Architecture

### 5.1 Source layout

```
Android_app_Ver/
  settings.gradle.kts, build.gradle.kts, gradle.properties, gradle/libs.versions.toml,
  gradle/wrapper/*, gradlew(.bat), README.md, this specification (.md + .html)
  app/build.gradle.kts
  app/src/main/AndroidManifest.xml
  app/src/main/res/values/{strings.xml, themes.xml}
  app/src/main/res/drawable/{ic_launcher_background.xml, ic_launcher_foreground.xml}
  app/src/main/res/mipmap-anydpi-v26/ic_launcher.xml
  app/src/main/java/com/phong/dashcammerger/
    core/      (pure Kotlin, no android.* imports, JVM unit-tested)
      Models.kt    data classes and enums
      Cameras.kt   profiles, detect, manual map, grouping
      Ordering.kt  timestamp/sequence parsing, sort, gaps
      Planner.kt   probe orchestration, split, naming, uniqueName
      Report.kt    plan summary, report text, formatters
    android/
      SafStorage.kt   SafScanner (scan, describe, stat) + OutputFolder (write guard)
      MediaProbe.kt   track selection, probe, output duration
      Merger.kt       stream-copy + verify + keep-incomplete
      MergeSession.kt orchestrator / state holder
      MainActivity.kt Compose UI
  app/src/test/java/com/phong/dashcammerger/core/CoreTest.kt
```

### 5.2 Data model (`core/Models.kt`)

| Type | Fields |
|---|---|
| `enum Camera` | Front, Rear, Interior, Skip, Unclassified (`CAMERA_ORDER` = Front, Rear, Interior) |
| `StreamSignature` (data) | `video: String`, `audio: String` |
| `ProbeInfo` | `durationUs: Long`, `signature`, `description: String`, `audioDropped: String?` |
| `ClipInfo` | `uri: String`, `name`, `parentName`, `sizeBytes`, `mtimeMs`; mutable `camera`, `cameraToken`, `timestamp: LocalDateTime?`, `sequence: Long?`, `probe: ProbeInfo?`, `unreadableReason: String?`; derived `unreadable`, `stem` |
| `enum OrderSource` | Timestamp, Sequence, Mtime |
| `Gap` | `before`, `after`, `seconds: Double` |
| `CameraGroup` | `camera`, `clips: MutableList`, `orderSource`, `gaps` |
| `OutputPart` | `camera`, `partIndex`, `partCount`, `clips`, `splitReason`, `expectedDurationUs`, `expectedSize`, mutable `outputName`; derived `partialName` |
| `PlanOptions` (data) | `outputSubfolder="merged"`, `gapWarningSeconds=300`, `gapSplitSeconds=1800`, `splitOnGap=false`, `maxPartGb=0.0` |
| `MergePlan` | `groups`, `parts`, `unclassified`, `unreadable`, `warnings` |
| `enum PartStatus` | PASS, FAIL, CANCELLED |
| `PartResult` | `part`, `status`, `finalName?`, `actualDurationUs`, `actualSize`, `reason`, `elapsedMs` |
| `RunReport` | `plan`, `results`, `outputFolder`, `sourcesUntouched`, `sourceProblems`, `skipped: List<OutputPart> = []` (unticked parts, AR-10.12) |
| const | `INCOMPLETE_PREFIX = "INCOMPLETE_DO_NOT_USE_"` |

`core` receives Android-specific operations as function parameters, so it can be tested
on the JVM: `Planner.buildPlan(clips, options, probe: (ClipInfo)->Unit,
nameExists: (String)->Boolean, onProgress: (String, Int, Int)->Unit)`.

### 5.3 Pipeline (`MergeSession`)

Mirrors desktop `pipeline.run()`:

1. **Scan** (background thread): open output folder → scan input (skip output folder) →
   `Cameras.detect` → if no profile: *NeedCameraMap* and stop.
2. **Plan**: `Planner.buildPlan` (group → annotate → order → probe all → drop unreadable →
   gaps → split → name) → free-space check → plan text → *Planned*.
3. **Confirm & select**: the user reads the plan, ticks the files to export (state
   `planParts`, `selected` = indices into `plan.parts`, `order`, `orderByTime`,
   `freeBytes`; edited via `toggle(i, linked)`, `setCamera`, `selectAll`, `selectNone`,
   `arrange(byTime)`, `move(pos, delta)`; pure helpers in `core/Arrange.kt`) and taps
   **Merge** (the explicit FR-2.4 step).
4. **Merge** (background thread): for each **ticked** part, in `order` `Merger.mergePart` (write partial → verify
   → rename) → source check → write report → *Done*.

`MergeSession` is a process-level singleton holding Compose `mutableStateOf` fields, so a
configuration change does not lose a running merge (the activity additionally declares
`configChanges="orientation|screenSize|screenLayout|keyboardHidden"`). Worker threads post
all UI-state changes to the main looper. Cancellation is an `AtomicBoolean`.

### 5.4 Manifest

One `activity` `.android.MainActivity`, `exported="true"`, MAIN/LAUNCHER intent filter;
`application` with `allowBackup="false"`, label `DashCam Merger`, icon
`@mipmap/ic_launcher`, theme `Theme.DashCamMerger` (parent
`android:Theme.Material.NoActionBar`). No `uses-permission` entries.

### 5.5 Launcher icon

Adaptive icon: background solid `#1F3A5F`; foreground vector (108×108 viewport) — white
rectangle `M28,40h52v28h-52z`, three navy "frames" `M32,46h12v16h-12z`,
`M48,46h12v16h-12z`, `M64,46h12v16h-12z` (`#1F3A5F`), and an amber bar
`M30,72h48v4h-48z` (`#F2B233`): three film frames joined into one strip.

---

## 6. Differences from the desktop version (summary)

| Topic | Desktop | Android |
|---|---|---|
| Join engine | ffmpeg concat demuxer `-c copy` | `MediaExtractor` → `MediaMuxer` sample copy |
| Probe | ffprobe | `MediaExtractor` formats |
| Output container | MP4 | MP4 |
| Input extensions | mp4 mov avi ts mkv | mp4 mov ts mkv 3gp |
| Codecs | anything ffmpeg can copy | video H.264/H.265/MPEG-4/H.263; audio AAC/AMR (other audio dropped with warning) |
| Signature | codec parameters | codec parameters **plus codec-config hash and rotation** |
| Output location | `--output-dir` or `<input>\merged` | output folder picker or `<input>/merged` |
| Write guard | `WriteGuard` allow-listed roots | `OutputFolder`: one folder, own files only |
| Source deletion (FR-12) | opt-in | not available |
| Re-encode fallback | yes (`--reencode`) | no |
| Logs folder | `logs/` 30-day rotation | none (report only) |
| Free-space check | — | plan warning |
| FAT32 split | `max_part_gb` config | 3.9 GB switch |
| Source-unchanged check | size + mtime | size |

---

## 7. Error handling and edge cases

| Situation | Behaviour |
|---|---|
| Folder permission revoked / card removed | Exception in scan or merge → *Error* with the exception message; files already finished stay. |
| Clip cannot be opened / truncated | Listed as unreadable, skipped, rest merged (AR-5.7). |
| Unsupported audio (e.g. PCM `.mov`) | Video-only output, warning. |
| Codec/resolution change mid-trip | New part (`codec change`). |
| Output name exists | `_2`, `_3`… |
| Leftover `<name>.partial.mp4` from a crashed run | `createFile` refuses to overwrite → *Error* for the run; rename or remove that file manually, then rescan. |
| Output larger than 4 GiB on FAT32 target | Write fails → part FAIL, kept as `INCOMPLETE_…`; use the 3.9 GB switch. |
| Not enough space | Plan warning; if merged anyway, the write fails → `INCOMPLETE_…`. |
| App closed / killed mid-merge | Partial file stays as `….partial.mp4` (no final name, never mistaken for a good file). |
| Cancel | Current part → `INCOMPLETE_…`; remaining parts not started; report written. |

---

## 8. Known limitations

- Merging runs in the foreground only; leaving the app for long may let Android stop it.
- Throughput is bounded by the SD card / USB reader (observed ≈ 0.5 GB per minute for
  SD → phone on the Note 10: 9.4 GB in about 15 minutes).
- B-frame streams are copied in decode order with their original timestamps; typical
  dashcam streams (no B-frames / fixed GOP) are the tested case.
- The audio track may start a few milliseconds offset at clip joins (per-clip rebase uses
  the earliest sample of the clip); not audible in tests.
- Only the external-storage provider reports free space.

---

## 9. Verification

### 9.1 Unit tests (`CoreTest`, JVM, all passing)

| Test | Checks |
|---|---|
| `xforceProfileDetectsCamerasAndTimestamps` | Xforce profile wins; F/R labels; timestamp and sequence from the name |
| `folderProfileUsesParentName` | Folder profile; `Front`/`rear`/`Inside` folders |
| `unknownNamingFallsBackToTokens` | `VID0001.mp4` → no profile, token `ALL`; manual map applies |
| `timestampAndSequenceParsing` | separated timestamp form; invalid month rejected; sequence excludes timestamp digits |
| `ordersByTimestampAndDetectsGaps` | sort order; 12:01→13:00 with 60 s clip = 58 min gap |
| `planSplitsOnCodecChangeAndNamesOutputs` | codec change split; `_part1`/`_part2` names; collision → `_2`; expected duration |
| `unreadableClipsAreSkippedWithWarning` | unreadable clip removed, one part remains |
| `maxPartSizeSplits` | 4 × 1 GiB with 2.5 GB limit → parts of 2 + 2 |
| `formatting` | `1:01:05`, `1.50 GB` |
| `reportListsSkippedParts` | report lists `Not selected (skipped by user): 1` and the unticked part's name; no such line when nothing was skipped |
| `arrangeOrdersByCameraOrTimeAndMoves` | 2 trips × Front/Rear: by camera F1 F2 R1 R2; by time F1 R1 F2 R2; ▲/▼ swaps and ignores moves past either end |
| `arrangePairsSameTimePartsAcrossCameras` | F1↔R1, F2↔R2 by overlapping time; without timestamps paired by part number |
| `selectionSummaryAndSpaceWarning` | `Selected: 1 of 2 file(s), 0.3 KB`; space warning only when needed > known free space |

### 9.2 Device acceptance tests (Note 10, 2026-09-27)

| ID | Procedure | Expected | Result |
|---|---|---|---|
| DT-1 | 6 synthetic clips (ffmpeg `testsrc2` 1280×720 30 fps H.264 + AAC, 10 s, Xforce names, 3 F + 3 R) in `Phone:/DashTest`; output `Phone:/Movies/DashMerged`; Scan & plan | Plan: 2 parts, 3 clips each, 0:00:30, H.264 1280x720 + AAC, free space shown | Pass |
| DT-2 | Merge DT-1 | 2 outputs PASS, report written, sources unchanged | Pass; ffprobe: 900 video frames (= 3 × 300), 1296 AAC frames, 30.1 s; ffmpeg full decode: 0 errors |
| DT-3 | Rescan DT-1 with outputs present | Names get `_2` | Pass |
| DT-4 | Real Vietmap M1 card (`SD card/USB (1721-8D53): /Vietmap_M1/Movie`, 1920×1080 H.264 High + AAC, 1-min clips), trip-gap switch on, output to phone | 4 parts (Front/Rear × 2 trips, split at a 494.6 min gap), all PASS | Pass (sizes 1.6–2.9 GB) |
| DT-5 | Pull `Rear_20260924070830_to_20260924073130_part1.mp4` (1.7 GB, 24 clips) to PC; ffmpeg full decode; frames at 58/60/62/600/1200 s | No decode errors, clean image across clip joins | Pass: 41 596 frames, 1386.5 s, 0 errors |

| DT-6 | DT-1 clips; Scan & plan → both rows ticked, `Selected: 2 of 2`; Select none → Merge disabled; tick only Rear → Merge | Only the Rear file written, PASS; report shows `Not selected (skipped by user): 1` + Front name; sources unchanged | Not yet run (v1.1) |
| DT-7 | A plan with ≥ 2 parts per camera (DT-4 card with trip gap, or 3.9 GB split); toggle a camera header; untick `_part1`, rotate the screen, Merge | Header toggles all of that camera's parts; selection survives rotation; only `_part2` written, with its planned name | Not yet run (v1.1) |

| DT-8 | DT-4-style plan (2 trips × Front/Rear); *By time*; pair switch on; untick Front part 2; ▼ on the first row; Merge | List F1 R1 F2 R2; unticking F2 also unticks R2; merge writes R1 then F1; report lists F2/R2 as not selected | Partly run 2026-10-03 on a Vietmap M1 card (14 parts, 7 F + 7 R): By time gave F1 R1 F2 R2 F3 R3…; unticking Front part 2 gave Front 6/7, Rear 6/7, 12/14 — Pass. Merge not run |

| DT-9 | Open app; Scan & plan; Merge; rotate during each step | Starts on Setup; plan → Files tab full screen with bottom bar visible; Done → Details shows the report; tab kept on rotation | Partly run 2026-10-03: Setup start and auto-switch to Files with bottom bar visible — Pass. Done → Details and rotation not run |

Note: the synthetic test clips are colour bars (red/green/yellow/blue/magenta/cyan) by
design; colourful output from those clips is not a defect.

### 9.3 Re-running the acceptance test

```
ffmpeg -f lavfi -i testsrc2=size=1280x720:rate=30:duration=10 ^
       -f lavfi -i sine=frequency=440:duration=10 ^
       -c:v libx264 -preset veryfast -g 30 -c:a aac -shortest 20260927120000_0001F.mp4
(repeat for 120100_0002F, 120200_0003F and the R variants)
adb push <folder>\. /sdcard/DashTest/
```

Then run DT-1…DT-3 and check the outputs with
`ffprobe -count_frames -show_entries stream=codec_name,nb_read_frames,duration` and
`ffmpeg -v error -i <out> -f null -` (must print nothing).

---

## 10. Rebuild checklist

1. Create an Android project with the toolchain in §2.2 (Compose, Material 3, single
   module `:app`, package `com.phong.dashcammerger`, min 28 / target 36).
2. Implement `core/` from §3.3–3.6, §3.9–3.10 and §5.2 in pure Kotlin; port the unit tests
   in §9.1 first and make them pass.
3. Implement `SafScanner` and `OutputFolder` (§3.1–3.2, §3.11, AR-10.2) with
   `DocumentsContract` only (no `DocumentFile` dependency needed).
4. Implement `MediaProbe` (§3.5) and `Merger` (§3.7–3.8) exactly as specified —
   especially the signature including codec config, the timestamp rebase, and the
   partial → verify → rename / keep-incomplete flow.
5. Implement `MergeSession` (§5.3) and the UI (§4), including the export selection
   (AR-10.5–AR-10.17).
6. Manifest and icon per §5.4–5.5; no permissions.
7. Run §9.1, then the device tests §9.2.
