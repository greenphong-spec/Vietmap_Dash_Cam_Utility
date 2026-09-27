# DashCam Merger – Detailed Software Design

**Version:** 1.0
**Date:** 2026-09-14
**Implements:** `DashCamMerger-Specification.md` v1.0 (all FR-1 … FR-12, sections 6–11)
**Audience:** the developer or AI model that writes the code. Follow this document literally.

---

## 0. How to use this document

1. Implement the modules in the order given in **Section 14 (Implementation plan)**. Do not skip ahead.
2. After each step, run the tests listed for that step. Do not start the next step until they pass.
3. Every function signature, data class field, regex, command line, file name, and message text in this
   document is **normative**. Copy them exactly. Do not rename, reorder, or "improve" them.
4. When something is not covered here, choose the simplest solution that keeps every rule in
   **Section 2 (Fixed decisions)** and **Section 12 (Safety rules)** true, and add a comment
   `# DESIGN-GAP: <what you decided>` at that place in the code.
5. Never add third-party Python packages. Only the standard library, plus `pytest` and `pyinstaller`
   as developer-only tools.
6. Requirement IDs (`FR-x.y`) are quoted next to the code that satisfies them. Keep them as comments
   in the code (`# FR-6.4`) so the traceability matrix in Section 16 stays true.

---

## 1. System overview

```
+-------------------+        +------------------------------------------------------------+
|  Presentation     |        |  Core library  (package: dashcam_merger.core)              |
|                   |        |                                                            |
|  cli.py  -------->|        |  config -> scanner -> cameras -> ordering -> probe          |
|  gui.py  -------->| calls  |          -> planner -> concat (ffmpeg) -> verify -> report  |
|                   |        |                                                            |
|  main.py (entry)  |        |  safe_io  (only module allowed to write/rename files)       |
+-------------------+        |  progress (event objects sent to CLI/GUI through a queue)  |
                             +------------------------------------------------------------+
                                       |                       |
                                 ffmpeg\ffprobe.exe      ffmpeg\ffmpeg.exe
```

- The core never prints and never opens a window. It reports through `ProgressEvent` objects
  pushed to a `queue.Queue` and through the log.
- The CLI and GUI are thin. All decisions are made in the core so both modes behave identically (Spec §3).

---

## 2. Fixed decisions

| Topic | Decision |
|-------|----------|
| Python | 3.12 (3.13 acceptable). Type hints everywhere. `from __future__ import annotations` in each file. |
| Dependencies | Standard library only: `pathlib, re, json, subprocess, threading, queue, dataclasses, datetime, logging, argparse, tkinter, hashlib, shutil, os, sys, time, tempfile`. |
| Package name | `dashcam_merger` (folder `src\dashcam_merger`). |
| Encoding | All text files read/written as UTF-8. Concat lists: UTF-8 **without BOM** (FR-5.3). |
| Paths | `pathlib.Path` everywhere. Convert to string only when calling subprocess. Use `win_long(path)` (Section 5.9) for every OS file operation. |
| Subprocess | `subprocess.Popen` / `subprocess.run` with `creationflags=subprocess.CREATE_NO_WINDOW` on Windows, `text=True, encoding="utf-8", errors="replace"`. Never `shell=True`. |
| ffmpeg location | `config.ffmpeg_path` and `config.ffprobe_path`, relative to the **application directory** (Section 5.1 `app_dir()`), not to the current working directory. |
| Output naming | `<Camera>_<firstTs>_to_<lastTs>[_partN].mp4` (FR-6.3). If timestamps are unavailable, use `<Camera>_<firstFileStem>_to_<lastFileStem>`. |
| Temp files | `%TEMP%\DashCamMerger\<runId>\` for concat lists; `<output>\<name>.partial.mp4` during writing (FR-6.4). |
| Run id | `runId = datetime.now().strftime("%Y%m%d_%H%M%S")`. Used in log file name and temp folder. |
| Threads | GUI: the core runs in one `threading.Thread`; cancellation via `threading.Event`. CLI: same core function, called directly on the main thread. |
| Camera labels | Exactly three strings: `"Front"`, `"Rear"`, `"Interior"`. Anything else is `"Unclassified"` or `"Skip"`. |
| Version string | `__version__ = "1.0.0"` in `dashcam_merger/__init__.py`. |

---

## 3. Project layout (normative)

```
DashCamMerger\
  src\
    main.py                          entry point (Section 10)
    dashcam_merger\
      __init__.py                    __version__
      cli.py                         Section 8
      gui.py                         Section 9
      core\
        __init__.py
        models.py                    Section 4
        config.py                    Section 5.1
        logging_setup.py             Section 5.2
        safe_io.py                   Section 5.3
        scanner.py                   Section 5.4
        cameras.py                   Section 5.5
        ordering.py                  Section 5.6
        probe.py                     Section 5.7
        planner.py                   Section 5.8
        winpath.py                   Section 5.9
        concat.py                    Section 5.10
        verify.py                    Section 5.11
        report.py                    Section 5.12
        pipeline.py                  Section 6 (orchestrates everything)
        progress.py                  Section 7
  tests\
    conftest.py
    test_config.py
    test_scanner.py
    test_cameras.py
    test_ordering.py
    test_probe.py
    test_planner.py
    test_concat.py
    test_verify.py
    test_report.py
    test_cli.py
    test_integration.py
    fixtures\make_clips.py           generates synthetic clips with ffmpeg (Section 13.2)
  ffmpeg\
    ffmpeg.exe
    ffprobe.exe
  config.json
  README.txt
  CHANGELOG.md
  build.bat                          Section 15
  DashCamMerger.spec                 PyInstaller spec (generated by build.bat on first run)
```

---

## 4. Data model (`core/models.py`)

All classes are `@dataclass`. Use `slots=True` where possible. Use `field(default_factory=...)` for mutable defaults.

```python
CameraLabel = Literal["Front", "Rear", "Interior", "Unclassified", "Skip"]
OrderSource = Literal["timestamp", "sequence", "mtime"]

@dataclass(slots=True)
class ClipInfo:
    path: Path                     # absolute, resolved
    size_bytes: int
    mtime: float                   # os.stat().st_mtime
    camera: CameraLabel = "Unclassified"
    camera_token: str = ""         # raw token the profile matched, e.g. "F" or "Rear"
    timestamp: datetime | None = None   # parsed from filename (FR-3.1 a/b)
    sequence: int | None = None    # parsed from filename (FR-3.1 c)
    probe: ProbeResult | None = None    # filled by probe.py when needed
    unreadable: bool = False       # FR-4.5
    unreadable_reason: str = ""

@dataclass(slots=True)
class StreamSignature:             # FR-4.2
    v_codec: str
    width: int
    height: int
    r_frame_rate: str              # exactly as ffprobe reports, e.g. "30000/1001"
    pix_fmt: str
    a_codec: str                   # "none" if no audio stream
    sample_rate: int               # 0 if no audio
    channels: int                  # 0 if no audio

@dataclass(slots=True)
class ProbeResult:
    duration: float                # seconds, from format.duration
    size_bytes: int                # from format.size
    signature: StreamSignature

@dataclass(slots=True)
class CameraGroup:
    camera: CameraLabel
    clips: list[ClipInfo]          # sorted chronologically after ordering.py
    order_source: OrderSource = "timestamp"
    gaps: list[tuple[ClipInfo, ClipInfo, float]] = field(default_factory=list)  # (before, after, seconds)

@dataclass(slots=True)
class OutputPart:
    camera: CameraLabel
    part_index: int                # 1-based
    part_count: int                # total parts for this camera
    clips: list[ClipInfo]
    signature: StreamSignature | None
    reencode: bool                 # True only with --reencode and mixed signatures
    list_path: Path | None = None  # concat list file (FR-5)
    output_path: Path | None = None        # final name
    partial_path: Path | None = None       # <name>.partial.mp4
    expected_duration: float = 0.0
    expected_size: int = 0
    split_reason: str = ""         # "", "codec change", "trip gap", "max part size"

@dataclass(slots=True)
class MergePlan:
    input_dir: Path
    output_dir: Path
    groups: list[CameraGroup]
    parts: list[OutputPart]
    unclassified: list[ClipInfo]
    unreadable: list[ClipInfo]
    warnings: list[str]            # human readable, shown in summary (FR-2.4, FR-3.3, FR-3.4)
    ffmpeg_commands: list[list[str]]   # for --dry-run

@dataclass(slots=True)
class VerifyResult:                # FR-7
    passed: bool
    actual_duration: float
    expected_duration: float
    actual_size: int
    expected_size: int
    reason: str                    # "" when passed

@dataclass(slots=True)
class PartResult:
    part: OutputPart
    status: Literal["PASS", "FAIL", "CANCELLED", "SKIPPED"]
    verify: VerifyResult | None
    final_path: Path | None        # where the file ended up (may be INCOMPLETE_ name)
    elapsed_seconds: float
    ffmpeg_exit_code: int | None

@dataclass(slots=True)
class RunReport:
    run_id: str
    plan: MergePlan
    results: list[PartResult]
    sources_untouched: bool
    started: datetime
    finished: datetime

    @property
    def exit_code(self) -> int: ...   # Section 8.3

@dataclass(slots=True)
class CameraProfile:
    name: str
    scope: Literal["name", "parent"]      # match filename or parent folder name
    regex: re.Pattern                     # must contain a named group "cam"
    map: dict[str, CameraLabel]           # keys upper-cased at load time

@dataclass(slots=True)
class AppConfig:                          # FR-10
    video_extensions: list[str]
    output_subfolder: str
    probe_sample_every: int
    gap_warning_seconds: int
    gap_split_seconds: int
    split_on_gap: bool
    max_part_gb: float
    parallel_jobs: int
    ffmpeg_path: Path                     # absolute after load
    ffprobe_path: Path                    # absolute after load
    camera_profiles: list[CameraProfile]  # user profiles only; built-ins are in cameras.py

@dataclass(slots=True)
class RunOptions:                         # everything the CLI/GUI can set per run
    input_dir: Path
    output_dir: Path | None = None        # None -> input_dir / config.output_subfolder
    recursive: bool = True
    camera_map: dict[str, CameraLabel] | None = None    # FR-2.3 (CLI form parsed)
    split_on_gap: bool | None = None      # None -> config value
    max_part_gb: float | None = None
    reencode: bool = False
    overwrite: bool = False
    keep_lists: bool = False
    parallel: int | None = None
    dry_run: bool = False
    assume_yes: bool = False
    verbose: bool = False
```

---

## 5. Module designs

Each module lists: purpose, public functions (signature + contract), algorithm, errors, tests.

### 5.1 `core/config.py` — FR-10

```python
DEFAULTS: dict = {
  "video_extensions": [".mp4", ".mov", ".avi", ".ts", ".mkv"],
  "output_subfolder": "merged",
  "probe_sample_every": 20,
  "gap_warning_seconds": 300,
  "gap_split_seconds": 1800,
  "split_on_gap": False,
  "max_part_gb": 0,
  "parallel_jobs": 1,
  "ffmpeg_path": "ffmpeg\\ffmpeg.exe",
  "ffprobe_path": "ffmpeg\\ffprobe.exe",
  "camera_profiles": []
}

class ConfigError(Exception): ...

def app_dir() -> Path
def load_config(path: Path | None = None) -> AppConfig
def profile_from_dict(d: dict) -> CameraProfile
```

- `app_dir()`: if `getattr(sys, "frozen", False)` return `Path(sys.executable).parent`,
  else return the repository root (`Path(__file__).resolve().parents[3]`). Every relative path in
  config is resolved against this.
- `load_config()`: default path is `app_dir() / "config.json"`. If the file is missing, use `DEFAULTS`
  and log INFO `"config.json not found, using defaults"`. If present, parse JSON; on
  `json.JSONDecodeError` raise `ConfigError(f"config.json line {e.lineno}: {e.msg}")` (Spec FR-10
  "names the line"). Unknown keys: log WARNING and ignore. Missing keys: use default.
- Validation (raise `ConfigError` with the key name in the message):
  - `video_extensions`: non-empty list of strings starting with `.`; lower-case them.
  - `probe_sample_every` ≥ 1; `gap_warning_seconds` ≥ 0; `gap_split_seconds` ≥ 0;
    `max_part_gb` ≥ 0; `parallel_jobs` in 1..8.
  - `ffmpeg_path`, `ffprobe_path`: resolve against `app_dir()`; do **not** check existence here
    (pipeline does, Section 6 step 0, so the error message can carry the expected path).
  - `camera_profiles`: each item needs `name` (str), `regex` (str, compiled with `re.IGNORECASE`,
    must contain group `cam`, else `ConfigError`), `map` (dict str→one of Front/Rear/Interior/Skip),
    optional `scope` in {"name","parent"} default "name". Keys of `map` are upper-cased.

Tests (`test_config.py`): defaults when missing; invalid JSON reports line number; unknown key
ignored; `parallel_jobs: 9` → ConfigError mentions `parallel_jobs`; custom profile without `cam`
group → ConfigError; relative ffmpeg path becomes absolute under `app_dir()`.

### 5.2 `core/logging_setup.py` — Spec §9

```python
def setup_logging(run_id: str, verbose: bool, log_dir: Path | None = None) -> Path
def prune_old_logs(log_dir: Path, max_age_days: int = 30) -> int
```

- `log_dir` default `app_dir() / "logs"`; create it. File name `merge_<run_id>.log`.
- Root logger `dashcam_merger`. File handler level DEBUG if `verbose` else INFO. Format:
  `%(asctime)s %(levelname)-7s %(name)s: %(message)s`.
- Also attach a `logging.handlers.QueueHandler`-like adapter? **No.** The GUI log pane is fed by
  `ProgressEvent` (Section 7), not by the logging module. Keep logging file-only. CLI adds a
  `StreamHandler` to stderr at WARNING level (so ffmpeg problems are visible) in `cli.py`.
- `prune_old_logs`: delete only files matching `merge_*.log` inside `log_dir` older than
  `max_age_days`. Return count. This is the **only** delete in the whole program and it must
  be guarded: `assert log_dir.name == "logs"`.

### 5.3 `core/safe_io.py` — FR-8 (Section 12)

The single place where files are created, renamed or removed. All other modules call these.

```python
class SafetyViolation(Exception): ...

class WriteGuard:
    def __init__(self, allowed_roots: list[Path]) -> None
    def check(self, path: Path) -> Path          # returns resolved path or raises SafetyViolation
    def mkdir(self, path: Path) -> None
    def write_text(self, path: Path, text: str) -> None   # utf-8, newline="\n", no BOM
    def rename(self, src: Path, dst: Path) -> None
    def unique_name(self, path: Path) -> Path    # FR-6.2 appends _2, _3 ...
```

- `allowed_roots` for a run are exactly: `[output_dir, temp_dir]` (temp = `%TEMP%\DashCamMerger\<runId>`).
  The log folder is handled by `logging_setup` and not by `WriteGuard`.
- `check()`: `p = path.resolve()`; pass if `p == root or root in p.parents` for some root; else raise
  `SafetyViolation(f"Refusing to write outside output folder: {p}")`.
- Every method calls `check()` on every path argument first. `rename` checks both `src` and `dst`.
- `unique_name(p)`: if `p` does not exist return `p`; else try `p.with_stem(f"{p.stem}_{n}")` for
  n = 2, 3, … until free.
- There is **no** `delete`/`unlink` method. Do not add one.

Tests (`test_safe_io` inside `test_planner.py` or own file): writing to input folder raises;
writing to `output_dir/sub/file` allowed; `unique_name` yields `_2` then `_3`.

#### 5.3.1 `core/cleanup.py` — FR-12 (opt-in exception to "never delete")

A second, deliberately separate module — not routed through `WriteGuard` — that may
permanently delete a *source* clip, and only that:

```python
def sources_for_deletion(results: list[PartResult]) -> list[ClipInfo]
    # clips of every result with status == "PASS"; FAIL/CANCELLED/SKIPPED are never included

def delete_sources(clips: list[ClipInfo], protected_roots: list[Path]
                   ) -> tuple[list[Path], list[tuple[Path, str]]]
    # os.remove() each clip not under a protected_root; returns (deleted, (path, error))
```

`pipeline.run()` calls this only when `RunOptions.delete_source` is set, after `_finish()`
has already produced the report, and only after `Confirmer.confirm_delete(clips)` returns
`True` — a confirmation kept separate from the FR-2.4 plan confirmation (Section 6, step 11)
so that an auto-confirmed merge (`--yes` for the plan) does not silently also authorize
deletion; the GUI always shows its own warning dialog for this regardless of
`assume_yes` (Section 9). `protected_roots` is always `[output_dir, temp_dir]`, so a bug
elsewhere that mislabels a merged output as a "source" cannot delete it. The safety-grep
test (13.3) allowlists `cleanup.py` by name alongside `logging_setup.py`.

### 5.4 `core/scanner.py` — FR-1

```python
def scan(input_dir: Path, extensions: list[str], recursive: bool,
         output_subfolder: str) -> list[ClipInfo]
```

Algorithm:
1. `input_dir = input_dir.resolve()`. If not a directory → raise `NotADirectoryError`.
2. Iterate `input_dir.rglob("*")` if recursive else `input_dir.glob("*")`. Use `os.scandir`
   recursion if `rglob` proves slow; behaviour identical.
3. Skip if any path part (relative to `input_dir`) equals `output_subfolder` case-insensitively (FR-1.3).
4. Skip if `name.startswith("INCOMPLETE_DO_NOT_USE_")` (FR-1.4).
5. Keep only regular files with `suffix.lower() in extensions`.
6. Build `ClipInfo(path=p.resolve(), size_bytes=st.st_size, mtime=st.st_mtime)`.
7. Return list sorted by `str(path).lower()` (deterministic; real ordering happens later).
8. Log INFO: `"Scanned {n} video files in {input_dir}"`. Empty list is **not** an error here; the
   pipeline handles FR-1.5.

Tests: nested folders found; `merged\` skipped; `INCOMPLETE_` skipped; extension case-insensitive;
`--no-recursive` sees only top level.

### 5.5 `core/cameras.py` — FR-2

#### 5.5.1 Built-in profiles (normative, in this order)

```python
BUILTIN_PROFILES: list[CameraProfile] = [
  CameraProfile("Xforce", "name",
      re.compile(r"^(?P<ts>\d{14})_(?P<seq>\d+)(?P<cam>[FR])\.(?:mp4|mov)$", re.I),
      {"F": "Front", "R": "Rear"}),
  CameraProfile("SuffixLetter", "name",
      re.compile(r"_(?P<cam>[FRABI])\.[A-Za-z0-9]+$", re.I),
      {"F": "Front", "A": "Front", "R": "Rear", "B": "Rear", "I": "Interior"}),
  CameraProfile("Word", "name",
      re.compile(r"(?P<cam>FRONT|REAR|BACK|INSIDE|INTERIOR|CABIN)", re.I),
      {"FRONT": "Front", "REAR": "Rear", "BACK": "Rear", "INSIDE": "Interior",
       "INTERIOR": "Interior", "CABIN": "Interior"}),
  CameraProfile("CamN", "name",
      re.compile(r"CAM(?P<cam>[1-3])", re.I),
      {"1": "Front", "2": "Rear", "3": "Interior"}),
  CameraProfile("70mai", "name",
      re.compile(r"^(?:NO|EV|PA)?\d{4}[-_]?\d{4}[-_]\d{6}[-_]\d+(?P<cam>[FB])\.[A-Za-z0-9]+$", re.I),
      {"F": "Front", "B": "Rear"}),
  CameraProfile("Folder", "parent",
      re.compile(r"^(?P<cam>front|f|normal|rear|r|back|b|interior|inside|cabin|i)$", re.I),
      {"FRONT": "Front", "F": "Front", "NORMAL": "Front", "REAR": "Rear", "R": "Rear",
       "BACK": "Rear", "B": "Rear", "INTERIOR": "Interior", "INSIDE": "Interior",
       "CABIN": "Interior", "I": "Interior"}),
]
MATCH_THRESHOLD = 0.90
```

User profiles from `config.camera_profiles` are tried **before** the built-ins.

#### 5.5.2 Public functions

```python
@dataclass(slots=True)
class DetectionResult:
    profile_name: str | None            # None when no profile reached threshold
    matched_ratio: float
    tokens: dict[str, list[ClipInfo]]   # token -> files (for manual mapping dialog)

def detect(clips: list[ClipInfo], user_profiles: list[CameraProfile]) -> DetectionResult
def apply_profile(clips: list[ClipInfo], profile: CameraProfile) -> int      # returns matched count
def extract_tokens(clips: list[ClipInfo]) -> dict[str, list[ClipInfo]]
def apply_manual_map(clips: list[ClipInfo], tokens: dict[str, list[ClipInfo]],
                     mapping: dict[str, CameraLabel]) -> None
def parse_camera_map_arg(text: str) -> dict[str, CameraLabel]                 # CLI --camera-map
def group_by_camera(clips: list[ClipInfo]) -> tuple[list[CameraGroup], list[ClipInfo]]
```

- `apply_profile`: for each clip, subject = `clip.path.name` if scope == "name" else
  `clip.path.parent.name`. `m = profile.regex.search(subject)`. If `m` and `m["cam"].upper()` in
  `profile.map`: set `clip.camera = map[...]`, `clip.camera_token = m["cam"].upper()`, count += 1.
  Also, if the regex has groups `ts` / `seq`, store them in `clip.timestamp` / `clip.sequence`
  (parse with `ordering.parse_timestamp_14`) — this saves a second pass for Xforce.
- `detect`: for each profile in `user_profiles + BUILTIN_PROFILES`: reset all clips to
  Unclassified, run `apply_profile`, `ratio = matched / len(clips)`. First profile with
  `ratio >= MATCH_THRESHOLD` wins: leave its assignments in place, return
  `DetectionResult(profile.name, ratio, {})`. If none wins: reset all clips to Unclassified and return
  `DetectionResult(None, best_ratio, extract_tokens(clips))`.
- `extract_tokens` (used for the manual dialog, FR-2.3): if the clips have more than one distinct
  parent folder → token = `"folder:" + parent.name`. Otherwise token = the alphabetic run
  immediately before the extension, regex `([A-Za-z]+)\.[A-Za-z0-9]+$` on the name, upper-cased;
  if no match token = `"ALL"`. Return dict token → clips, tokens sorted alphabetically.
- `apply_manual_map`: for each token in `tokens`, label = `mapping.get(token.upper(), "Unclassified")`;
  assign to every clip of that token. `"Skip"` clips are set to camera `"Skip"` and are excluded
  from groups.
- `parse_camera_map_arg("F=Front,R=Rear,folder:Normal=Front")`: split on `,`, then on the first `=`;
  key upper-cased; value must be one of Front/Rear/Interior/Skip (case-insensitive, normalised);
  else raise `ValueError(f"--camera-map: invalid entry '{entry}'")`.
- `group_by_camera`: returns groups for Front, Rear, Interior in that fixed order (only those with
  ≥1 clip) and the list of Unclassified clips. `Skip` clips are dropped silently but logged.

Tests: each built-in profile against 3 sample names (Xforce `20260831152516_0015340F.MP4`,
suffix `clip_R.mp4`, word `REAR_001.mp4`, CamN `CAM2_001.mp4`, 70mai `NO20260831-152516-000001B.mp4`,
folder `Rear\001.mp4`); threshold: 9 matching + 1 odd file → profile chosen with odd file
Unclassified; 8/10 → `profile_name is None` and tokens populated; user profile takes precedence;
`parse_camera_map_arg` accepts and rejects properly.

### 5.6 `core/ordering.py` — FR-3

```python
TS14 = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?!\d)")
TS_SEP = re.compile(r"(?<!\d)(\d{4})[-_]?(\d{2})[-_]?(\d{2})[-_T ](\d{2})[-_:.]?(\d{2})[-_:.]?(\d{2})(?!\d)")
SEQ = re.compile(r"(?<!\d)(\d{3,})(?!\d)")

def parse_timestamp_14(text: str) -> datetime | None
def parse_timestamp(name: str) -> datetime | None
def parse_sequence(name: str, timestamp_span: tuple[int, int] | None) -> int | None
def annotate(clips: list[ClipInfo]) -> None
def order_group(group: CameraGroup) -> None
def detect_gaps(group: CameraGroup, warn_seconds: int) -> None
```

- `parse_timestamp`: try `TS14` then `TS_SEP` on the file **stem**. Validate with
  `datetime(y, m, d, H, M, S)` inside `try/except ValueError` → invalid dates return None and the
  next match of the same regex is tried (`finditer`). Return the first valid one. Record the
  span of the match for `parse_sequence`.
- `parse_sequence`: find all `SEQ` matches on the stem that do **not** overlap the timestamp span;
  return `int` of the **last** one; None if no match.
- `annotate`: for each clip with `timestamp is None`, call `parse_timestamp`; then `parse_sequence`.
  (Clips that got these from the camera profile keep them.)
- `order_group` (FR-3.1, FR-3.2):
  1. `if all(c.timestamp for c in clips)`: `order_source = "timestamp"`, key `(c.timestamp, c.sequence or 0, c.path.name.lower())`.
  2. `elif all(c.sequence is not None for c in clips)`: `order_source = "sequence"`, key `(c.sequence, c.path.name.lower())`.
  3. `else`: `order_source = "mtime"`, key `(c.mtime, c.path.name.lower())` (FR-3.3 warning is added by the planner).
  Sort in place with `list.sort(key=...)` (stable).
- `detect_gaps` (FR-3.4): only when `order_source == "timestamp"`. For consecutive clips a, b:
  `gap = (b.timestamp - a.timestamp).total_seconds() - a_duration` where `a_duration` is
  `a.probe.duration` if probed else `60.0` (typical clip). If `gap > warn_seconds` append
  `(a, b, gap)` to `group.gaps`. Note: the planner calls `detect_gaps` **after** probing so real
  durations are used whenever sampled.

Tests: 14-digit and separated timestamps; invalid date `20261399...` ignored; sequence picks the
last ≥3-digit run outside the timestamp; group order by timestamp regardless of list order; mtime
fallback selected when one clip lacks timestamp; gap of 10 minutes detected with 60-second clips.

### 5.7 `core/probe.py` — FR-4, FR-7.1

```python
class ProbeError(Exception): ...

def ffprobe_json(ffprobe: Path, file: Path, timeout: int = 60) -> dict
def probe_clip(ffprobe: Path, clip: ClipInfo) -> ProbeResult      # sets clip.probe or clip.unreadable
def probe_output(ffprobe: Path, file: Path) -> tuple[float, int]    # (duration, size) FR-7.1
def signature_from_json(data: dict) -> StreamSignature
def sample_indices(n: int, every: int) -> list[int]
```

- Command (normative):
  `[ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(file)]`
- `ffprobe_json`: run with `subprocess.run(..., capture_output=True, timeout=timeout)`. If exit
  code ≠ 0 or stdout is not JSON → raise `ProbeError(stderr.strip()[:300] or "ffprobe failed")`.
- `signature_from_json`: first stream with `codec_type == "video"` → v fields (`width`, `height`
  as int, `r_frame_rate`, `pix_fmt`, default `""` when missing). First `audio` stream → a fields;
  if none: `("none", 0, 0)`. No video stream → `ProbeError("no video stream")`.
- `probe_clip`: on success set `clip.probe`; on `ProbeError` or `subprocess.TimeoutExpired` set
  `clip.unreadable = True`, `clip.unreadable_reason = str(e)`, log WARNING, and return None (FR-4.5).
- `probe_output`: same command, returns `(float(format.duration), int(format.size))`; raises
  `ProbeError` on any failure (e.g. "moov atom not found" text comes back in stderr).
- `sample_indices(n, every)`: `sorted(set([0, n-1] + list(range(0, n, every))))` for n ≥ 1;
  `[]` for n = 0.

Tests: parse a saved ffprobe JSON fixture (video+audio, video only); `sample_indices(50, 20)` →
`[0, 20, 40, 49]`; `sample_indices(1, 20)` → `[0]`; a text file as input marks clip unreadable
(integration, needs ffprobe).

### 5.8 `core/planner.py` — FR-2.4, FR-3.3–3.5, FR-4.3–4.4, FR-6.3, FR-6.8

```python
def build_plan(clips: list[ClipInfo], cfg: AppConfig, opt: RunOptions,
               ffprobe: Path, run_id: str, progress: ProgressSink) -> MergePlan
```

Steps (in order):

1. **Group**: `groups, unclassified = cameras.group_by_camera(clips)`.
2. **Annotate & order**: `ordering.annotate(all clips)`; for each group `ordering.order_group(g)`;
   if `g.order_source == "mtime"` add warning
   `f"{g.camera}: order based on file modification time, please spot-check"` (FR-3.3);
   if `"sequence"` add `f"{g.camera}: order based on sequence number in filename"`.
3. **Sample probe** (FR-4.1): for each group, for `i in sample_indices(len(clips), cfg.probe_sample_every)`
   call `probe_clip`. Emit `ProgressEvent(kind="probe", ...)` per file.
4. **Signature check** (FR-4.3/4.4): `sigs = {c.probe.signature for c in sampled if c.probe}`.
   If `len(sigs) > 1` **or** any sampled clip is unreadable: probe **every** clip in the group
   (unreadable ones stay flagged and are removed from the group into `plan.unreadable`).
5. **Gaps**: `ordering.detect_gaps(g, cfg.gap_warning_seconds)`; for each gap add warning
   `f"{g.camera}: gap of {gap/60:.1f} min between {a.name} and {b.name}"` (FR-3.4).
6. **Split into parts** — produce `list[list[ClipInfo]]` per group by walking clips in order and
   starting a new part when any rule fires; record `split_reason` on the new part:
   - `codec change`: when the group was fully probed, `reencode` is False, and
     `clip.probe.signature != current_part_signature`. (With `opt.reencode` True never split for this reason; the single part gets `reencode=True` and `signature=None`.)
   - `trip gap`: `split_on_gap` effective value is True and the gap before this clip exceeds `cfg.gap_split_seconds`. (FR-3.5)
   - `max part size`: `max_part_gb > 0` and `current_part_bytes + clip.size_bytes > max_part_gb * 1024**3` and the current part is not empty. (FR-6.8)
7. **Name outputs** (FR-6.3): `first = part.clips[0]`, `last = part.clips[-1]`;
   `ts(c) = c.timestamp.strftime("%Y%m%d%H%M%S") if c.timestamp else c.path.stem`;
   `name = f"{camera}_{ts(first)}_to_{ts(last)}"`; if `part_count > 1` append `f"_part{part_index}"`;
   extension `.mp4`. `output_path = output_dir / name.mp4`. If it exists and not `opt.overwrite`:
   `output_path = guard.unique_name(output_path)` (FR-6.2). `partial_path = output_dir / f"{final_stem}.partial.mp4"`.
   `list_path = temp_dir / f"{final_stem}.txt"` (or `output_dir / ...` when `opt.keep_lists`).
8. **Expected totals**: `expected_duration = sum(c.probe.duration for c in clips if c.probe)`
   plus, for un-probed clips, they are probed lazily in verify (FR-7.2). Set
   `expected_size = sum(c.size_bytes)`.
9. **Commands**: `plan.ffmpeg_commands = [concat.build_command(...) for each part]` (for dry-run display).
10. Return the plan. Also emit `ProgressEvent(kind="plan", plan=plan)` so the CLI/GUI can show the
    summary table (FR-2.4).

Tests (`test_planner.py`, pure unit with fake probe results injected on `ClipInfo.probe`):
uniform signature → one part; signature change in the middle → two parts with reason
`codec change`; `reencode=True` → one part with `reencode=True`; `max_part_gb=1` with 3×0.6 GB
clips → two parts; names follow FR-6.3 pattern; existing output → `_2` suffix; `split_on_gap` splits
at a 40-minute gap.

### 5.9 `core/winpath.py` — Spec §7 (long paths)

```python
def win_long(path: Path) -> str
```

- On Windows (`os.name == "nt"`): `s = str(path.resolve())`; if `s.startswith("\\\\?\\")` return
  as is; if UNC (`s.startswith("\\\\")`) return `"\\\\?\\UNC\\" + s[2:]`; else return `"\\\\?\\" + s`.
- Elsewhere return `str(path)`.
- Used for: `open()`, `os.rename`, `os.stat`, `os.makedirs`, and as the argument passed to
  ffprobe/ffmpeg (ffmpeg accepts `\\?\` paths on Windows). Inside concat list files use
  `path.resolve().as_posix()` **without** the prefix (ffmpeg's concat demuxer handles normal absolute paths; the `\\?\` form breaks its quoting).

### 5.10 `core/concat.py` — FR-5, FR-6

```python
def write_concat_list(guard: WriteGuard, list_path: Path, clips: list[ClipInfo]) -> None
def escape_concat_path(p: Path) -> str
def build_command(ffmpeg: Path, part: OutputPart) -> list[str]
def run_ffmpeg(cmd: list[str], expected_duration: float, cancel: threading.Event,
               progress: ProgressSink, part: OutputPart) -> int   # returns exit code, -1 if cancelled
```

- `escape_concat_path`: `s = p.resolve().as_posix()`; `s = s.replace("'", "'\\''")`;
  return `f"file '{s}'"` (FR-5.2).
- `write_concat_list`: lines joined by `"\n"` plus trailing newline, written through
  `guard.write_text` (UTF-8, no BOM, FR-5.3).
- `build_command` (normative):
  - Stream copy (FR-6.1):
    `[ffmpeg, "-y", "-hide_banner", "-nostats", "-loglevel", "error", "-progress", "pipe:1",
      "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", "-movflags", "+faststart",
      str(partial_path)]`
  - Re-encode (FR-4.4, only when `part.reencode`):
    replace `"-c", "copy"` by `"-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac"`.
- `run_ffmpeg`:
  1. `Popen(cmd, stdout=PIPE, stderr=PIPE, text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)`.
  2. Read stderr in a helper thread into a list (keep last 50 lines).
  3. Read stdout line by line. ffmpeg `-progress` emits `key=value` lines; on `out_time_ms=N`
     (microseconds despite the name) compute `done = N / 1_000_000`; on `total_size=N` store bytes;
     on `progress=continue` or `progress=end` emit
     `ProgressEvent(kind="progress", part=part, done_seconds=done, total_seconds=expected_duration, bytes_written=size, elapsed=time.monotonic()-t0)` (FR-6.6). The CLI/GUI derive percent, MB/s and ETA from these fields.
  4. Between reads check `cancel.is_set()` (poll every 0.25 s using `select`-free approach: the
     stdout reader thread pushes lines into a queue; main loop does `queue.get(timeout=0.25)`).
     On cancel: `proc.kill()`, `proc.wait()`, return `-1` (FR-6.7).
  5. On normal end: `rc = proc.wait()`. Log the full command, `rc`, and the stderr tail. Return `rc`.

Tests: `escape_concat_path` with `it's.mp4` → `file '/C:/x/it'\''s.mp4'`; command list matches the
normative form exactly (assert equality with a literal list); progress parser turns
`out_time_ms=90000000` into 90.0 s (unit test on a small `parse_progress_line` helper — add it);
cancel kills a running ffmpeg within 2 s (integration).

### 5.11 `core/verify.py` — FR-7

```python
TOLERANCE_ABS = 1.0
TOLERANCE_REL = 0.001
SIZE_MIN_RATIO = 0.90

def ensure_all_probed(ffprobe: Path, part: OutputPart) -> None     # FR-7.2 lazy probe
def verify_part(ffprobe: Path, part: OutputPart) -> VerifyResult
def sources_untouched(clips: list[ClipInfo]) -> tuple[bool, list[str]]   # FR-7.6
```

- `ensure_all_probed`: probe any clip in `part.clips` with `probe is None`; recompute
  `part.expected_duration` as the sum over clips with probe results (unreadable ones contribute 0 and
  are logged — they were in the concat list, so ffmpeg most likely failed anyway).
- `verify_part`:
  1. `ensure_all_probed`.
  2. `try: dur, size = probe_output(ffprobe, part.partial_path) except ProbeError as e: return VerifyResult(False, 0, exp, 0, part.expected_size, f"ffprobe: {e}")` (FR-7.4 moov atom case).
  3. `tol = max(TOLERANCE_ABS, TOLERANCE_REL * exp)`; `dur_ok = abs(dur - exp) <= tol` (FR-7.3).
  4. `size_ok = size >= SIZE_MIN_RATIO * part.expected_size` (FR-7.4). For re-encoded parts skip the size check (`size_ok = True`) because size legitimately changes.
  5. `reason` = `""` if both ok, else `f"duration {dur:.2f}s vs expected {exp:.2f}s"` and/or `f"size {size} < 90% of {expected}"` joined by `"; "`.
- `sources_untouched`: for each clip `os.stat(win_long(path))`; compare `st_size == clip.size_bytes`
  and `abs(st_mtime - clip.mtime) < 1`; missing file or mismatch → collect message
  `f"{path}: changed or missing"`. Return `(len(problems) == 0, problems)`.

Tests: tolerance boundaries (exp 100 → tol 1.0; exp 5000 → tol 5.0); size 89 % → FAIL with
reason containing "size"; ProbeError → FAIL with reason starting "ffprobe:"; `sources_untouched`
detects size change via a temp file.

### 5.12 `core/report.py` — FR-9

```python
def format_duration(seconds: float) -> str          # "H:MM:SS"
def format_size(bytes_: int) -> str                 # "7.19 GB", "512.0 MB"
def build_report_text(report: RunReport) -> str
def write_report(guard: WriteGuard, output_dir: Path, text: str) -> Path   # merged\merge_report.txt
def build_plan_summary(plan: MergePlan) -> str      # FR-2.4 confirmation text
```

Normative table layout (`build_report_text`), columns padded with spaces, header exactly:

```
Camera     Part  Output file                                           Files  Duration   Size       Verify
Front      1     Front_20260831152516_to_20260831171203.mp4            143    2:23:04    7.19 GB    PASS
Rear       1     Rear_20260831152516_to_20260831171203.mp4             143    2:23:04    3.88 GB    PASS
Unclassified: 0   Skipped unreadable: 0   Sources untouched: OK
Output folder: D:\DCIM\Movie\merged
```

- Column widths: Camera 10, Part 5, Output file = longest name (min 40), Files 6, Duration 10,
  Size 10, Verify 6. Separate columns by two spaces.
- After the table, one line per FAIL/CANCELLED part: `f"  {name}: {reason}"`.
- Then `Warnings:` followed by each `plan.warnings` line prefixed by two spaces (omit block if none).
- `build_plan_summary`: one line per group `f"{camera}: {n} files, {size}"` then
  `f"Unclassified: {n}"`, `f"Unreadable: {n}"`, then `Parts to write: {len(parts)}` and the warnings.
- `format_size`: GB if ≥ 1 GiB (2 decimals), else MB (1 decimal), else KB.

Tests: table header string equality; `format_duration(8584)` → `2:23:04`;
`format_size(7_720_000_000)` → `7.19 GB`.

---

## 6. `core/pipeline.py` — the run orchestrator

```python
class Confirmer(Protocol):
    def confirm_plan(self, summary: str) -> bool: ...                     # FR-2.4
    def request_camera_map(self, tokens: dict[str, list[ClipInfo]]) -> dict[str, CameraLabel] | None: ...  # FR-2.3
    def confirm_delete(self, clips: list[ClipInfo]) -> bool: ...          # FR-12.2

def run(cfg: AppConfig, opt: RunOptions, confirmer: Confirmer,
        progress: ProgressSink, cancel: threading.Event) -> RunReport
```

Exact sequence (each step logs INFO at start):

0. **Preconditions**: `run_id`; `setup_logging`; `prune_old_logs`; check `cfg.ffmpeg_path.is_file()`
   and `cfg.ffprobe_path.is_file()`, else raise `ToolMissingError(f"ffmpeg not found at {path}")`
   (Spec §7 → exit 2). Check `opt.input_dir.is_dir()` else `UsageError`.
   `output_dir = opt.output_dir or input_dir / cfg.output_subfolder`. `temp_dir = Path(tempfile.gettempdir()) / "DashCamMerger" / run_id`.
   `guard = WriteGuard([output_dir, temp_dir])`.
   **Guard rule**: `output_dir` must not be `input_dir` itself and must not be a parent of `input_dir`; raise `UsageError("Output folder must be a subfolder or a separate folder, not the input folder itself")`. (Keeps FR-8.1 true even with `--output`.)
1. **Scan** (5.4). If empty → emit `ProgressEvent(kind="error", message="No video files found in <dir>")` and raise `NoInputError` (exit 3, FR-1.5).
2. **Detect cameras** (5.5). If `opt.camera_map` given: `apply_manual_map(clips, extract_tokens(clips), opt.camera_map)`; else `detect()`; if `profile_name is None`: `mapping = confirmer.request_camera_map(tokens)`; if `None` → `CancelledError`; else `apply_manual_map`.
3. **Plan** (5.8). Emit `kind="plan"`.
4. **Confirm**: `if not opt.dry_run and not opt.assume_yes: if not confirmer.confirm_plan(build_plan_summary(plan)): raise CancelledError`.
5. **Dry run**: if `opt.dry_run`: emit `kind="dry_run"` with commands; return a `RunReport` with empty results (exit 0). Nothing is written, not even the temp dir.
6. **Prepare**: `guard.mkdir(output_dir)`; `guard.mkdir(temp_dir)`.
7. **Execute parts** sequentially (FR-6.5; with `parallel > 1` use `concurrent.futures.ThreadPoolExecutor(max_workers=parallel)` running the same `execute_part` function — the function must be thread-safe: it touches only its own `part`):

   ```
   execute_part(part):
       if cancel.is_set(): return PartResult(part, "SKIPPED", None, None, 0, None)
       write_concat_list(guard, part.list_path, part.clips)                # FR-5
       cmd = build_command(cfg.ffmpeg_path, part)
       t0 = monotonic(); rc = run_ffmpeg(cmd, part.expected_duration, cancel, progress, part)
       if rc == -1:                                                         # FR-6.7
           final = rename_incomplete(part)   -> INCOMPLETE_DO_NOT_USE_<name>.mp4 (if partial exists)
           return PartResult(part, "CANCELLED", None, final, elapsed, None)
       if rc != 0:
           final = rename_incomplete(part)
           return PartResult(part, "FAIL", VerifyResult(False, 0, exp, 0, expsize, f"ffmpeg exit {rc}"), final, elapsed, rc)
       v = verify_part(cfg.ffprobe_path, part)                              # FR-7
       if v.passed:
           if opt.overwrite and part.output_path.exists(): guard.rename(part.output_path, guard.unique_name(part.output_path.with_suffix(".replaced.mp4")))  # never delete, FR-8
           guard.rename(part.partial_path, part.output_path)                # FR-6.4
           return PartResult(part, "PASS", v, part.output_path, elapsed, rc)
       final = rename_incomplete(part)                                      # FR-7.5
       return PartResult(part, "FAIL", v, final, elapsed, rc)
   ```

   `rename_incomplete(part)`: `dst = output_dir / ("INCOMPLETE_DO_NOT_USE_" + part.output_path.name)`; `dst = guard.unique_name(dst)`; `guard.rename(part.partial_path, dst)`; return `dst`. If the partial file does not exist, return `None`.
   Emit `ProgressEvent(kind="part_done", result=...)` after each part.
8. **Sources check** (FR-7.6): `ok, problems = sources_untouched(all clips)`; log each problem at ERROR.
9. **Report** (FR-9): `text = build_report_text(report)`; `write_report(guard, output_dir, text)`; emit `kind="report"` with text. Return `RunReport`.
10. **Temp cleanup**: none. Temp lists stay in `%TEMP%` (the OS cleans it; the program never deletes). With `--keep-lists` they were written to `output_dir` instead.
11. **Optional source deletion** (FR-12), only if `opt.delete_source` and there were results:
    `deletable = cleanup.sources_for_deletion(results)`; if empty, log and skip. Otherwise
    `if confirmer.confirm_delete(deletable):` call `cleanup.delete_sources(deletable, [output_dir, temp_dir])`,
    store `(deleted, errors)` on the already-built `report`, log each outcome, emit a summary
    `kind="log"` event, and rewrite `merge_report.txt` (step 9's `build_report_text` now
    includes the deletion section) so the file on disk matches what was actually deleted.
    If not confirmed, emit a "kept" log event and leave every source file alone.

Exceptions defined in `pipeline.py`: `UsageError`, `ToolMissingError`, `NoInputError`, `CancelledError`
(all subclass `PipelineError(Exception)`).

---

## 7. `core/progress.py` — event channel

```python
@dataclass(slots=True)
class ProgressEvent:
    kind: Literal["log", "probe", "plan", "dry_run", "part_start", "progress", "part_done", "report", "error"]
    message: str = ""
    part: OutputPart | None = None
    plan: MergePlan | None = None
    result: PartResult | None = None
    done_seconds: float = 0.0
    total_seconds: float = 0.0
    bytes_written: int = 0
    elapsed: float = 0.0
    commands: list[list[str]] | None = None

class ProgressSink(Protocol):
    def emit(self, ev: ProgressEvent) -> None: ...

class QueueSink:                    # used by GUI
    def __init__(self, q: queue.Queue[ProgressEvent]) -> None
    def emit(self, ev) -> None: q.put(ev)

class CallbackSink:                 # used by CLI
    def __init__(self, fn: Callable[[ProgressEvent], None]) -> None
```

Derived display values (implemented once in `progress.py` as `def describe(ev) -> str`, used by both
CLI and GUI):
- `percent = min(100, 100 * done_seconds / total_seconds)` (0 if total unknown)
- `mb_per_s = bytes_written / 1_048_576 / elapsed` (0 if elapsed < 0.5)
- `eta_s = (total_seconds - done_seconds) * elapsed / done_seconds` (blank if done < 1 s)
- Text: `f"{part.camera} part {i}/{n}: {percent:5.1f}%  {mb_per_s:6.1f} MB/s  elapsed {H:MM:SS}  ETA {H:MM:SS}"`

---

## 8. `cli.py` — FR-11

### 8.1 Arguments (argparse, exact flag names)

```
--input PATH          required
--output PATH
--yes                 store_true  -> assume_yes
--no-recursive        store_true  -> recursive=False
--camera-map TEXT     parsed by cameras.parse_camera_map_arg
--split-on-gap        store_true
--max-part-gb FLOAT
--reencode            store_true
--overwrite           store_true
--keep-lists          store_true
--parallel INT
--dry-run             store_true
--delete-source       store_true  -> RunOptions.delete_source (FR-12, default False)
--verbose             store_true
--version             action="version", "DashCamMerger %(version)s"
--config PATH         optional path to config.json (not in spec; harmless addition, document in README)
```

### 8.2 Behaviour

- `CliConfirmer.confirm_plan(summary)`: print summary, then `input("Proceed with merge? [y/N] ")`; return `answer.strip().lower() in ("y", "yes")`. When stdin is not a TTY and `--yes` is absent, print `"Not a terminal; use --yes to skip confirmation"` and return False.
- `CliConfirmer.request_camera_map(tokens)`: print each token with up to 5 sample names, then for each token `input(f"Camera for '{token}' [Front/Rear/Interior/Skip]: ")`; empty answer = Skip. Non-TTY → print hint to use `--camera-map` and return None.
- `CliConfirmer.confirm_delete(clips)` (FR-12.2): if `--yes` was given, return `True` immediately —
  no separate prompt. Otherwise `input(f"Permanently delete {n} source clip file(s)? ... Type 'yes' to continue: ")`;
  only the literal `"yes"` (case-insensitive) confirms. Non-TTY without `--yes` → print a hint and return `False`
  (files are kept).
- Progress: `CallbackSink` printing `describe(ev)` on one line with `\r` for `progress` events, and full lines for other kinds. Print the report text at the end.
- `Ctrl+C`: catch `KeyboardInterrupt` in `cli.main`, set `cancel`, wait for the pipeline to return, exit 130.

### 8.3 Exit codes (normative)

| Code | When |
|------|------|
| 0 | every part PASS (or dry-run completed) |
| 1 | at least one part FAIL |
| 2 | `UsageError`, `ConfigError`, `ToolMissingError`, argparse error |
| 3 | `NoInputError` |
| 130 | `CancelledError` or KeyboardInterrupt (any part CANCELLED) |

`RunReport.exit_code`: 130 if any CANCELLED, else 1 if any FAIL, else 0.

---

## 9. `gui.py` — Spec §6

### 9.1 Layout (Tkinter, `ttk` widgets, grid geometry)

```
Row 0: [Label "Input folder"]  [Entry input_var (width 70)] [Button "Browse…"]
Row 1: [Label "Output folder"] [Entry output_var]           [Button "Browse…"]
Row 2: [Button "Scan"]  [Checkbutton "Split on trip gaps"] [Checkbutton "Re-encode if formats differ (slow)"]
       [Checkbutton "Overwrite existing outputs"] [Checkbutton "Delete original clips after successful merge
       (cannot be undone)" - FR-12, default unchecked] [Label "Max part size (GB, 0 = unlimited)"] [Spinbox 0..64 step 1]
Row 3: Treeview columns: Camera | Files | Total size | First clip | Last clip | Codec | Order source   (height 6)
Row 4: [Button "Merge" (disabled until scan OK)] [Button "Cancel" (disabled until merging)] [Button "Open output folder" (disabled until done)]
Row 5: Frame "progress_frame": one row per part -> Label(name) + ttk.Progressbar(length 400) + Label(status text)
Row 6: ScrolledText log pane (height 10, state disabled, appended programmatically)
Menu:  File -> Open config.json | Open logs folder | Exit ;  Help -> About
```

Window title `"DashCam Merger 1.0.0"`, `geometry("760x560")`, `minsize(640, 480)`.

### 9.2 Behaviour

- **Browse…**: `filedialog.askdirectory()`. Setting input auto-fills output with `<input>\merged` if output is empty or was auto-filled.
- **Scan**: runs `pipeline.run` with `dry_run=True` in a worker thread (so probing does not freeze the UI). The `GuiConfirmer.request_camera_map` shows the modal dialog (Section 9.3) — it must be invoked on the Tk main thread: the worker puts a request on the queue and waits on a `threading.Event` for the answer. On `kind="plan"` fill the Treeview: per group: camera, file count, `format_size(sum)`, first/last clip names, codec = `f"{sig.v_codec} {w}x{h}"` from the first probed clip (or "mixed"), order source. Enable **Merge**. Store the chosen camera mapping so the real merge does not ask again.
- **Merge**: new worker thread running `pipeline.run` with `dry_run=False`, `assume_yes=True` (the Scan table plus the Merge click *is* the confirmation required by FR-2.4), and the stored `camera_map`. Disable Scan/Merge, enable Cancel.
- **Queue polling**: `root.after(100, poll)` drains the `queue.Queue`; `progress` → update the part's Progressbar (`value=percent`) and status label with `describe(ev)`; `log` → append to log pane; `part_done` → set label `PASS`/`FAIL`/`CANCELLED`; `report` → show report in a read-only Toplevel or in the log pane and enable **Open output folder** (`os.startfile(output_dir)`); `error` → `messagebox.showerror`.
- **Delete original clips after successful merge** checkbox (FR-12) → `RunOptions.delete_source`,
  forced `False` whenever `dry_run=True` (a Scan never deletes anything). When set,
  `GuiConfirmer.confirm_delete(clips)` shows a `messagebox.askyesno` warning dialog naming the
  exact file count, on the Tk main thread (same request/`threading.Event` bridge as
  `request_camera_map`) — this fires every time, independent of the `assume_yes=True` used for
  the merge-plan confirmation.
- **Cancel**: `cancel.set()`; button text becomes "Cancelling…" until the worker finishes.
- **Window close during merge** (`WM_DELETE_WINDOW`): same as Cancel, then wait for the worker (join with timeout 10 s), then destroy (Spec §7).
- **About**: run `ffmpeg -version` (first line) and show it with `__version__`.

### 9.3 Camera mapping dialog (FR-2.3)

Modal `Toplevel`, title "Assign cameras". One row per token: `Label(token)`, `Label("\n".join(first 5 names))`,
`ttk.Combobox(values=["Front", "Rear", "Interior", "Skip"], state="readonly")` default `"Skip"`.
Buttons **OK** / **Cancel**. OK returns `{token: value}`; Cancel returns `None` (pipeline raises `CancelledError`, GUI shows "Scan cancelled").

---

## 10. `main.py`

```python
def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:                      # any argument -> CLI
        return cli.main(argv)
    return gui.main()             # no arguments -> GUI, returns 0
if __name__ == "__main__":
    sys.exit(main())
```

Build note: the exe is built `--windowed`; on Windows a windowed exe has no console, so for CLI use
the README tells users to run `DashCamMerger.exe --input ... | more` from a terminal **or** build a second
console exe `DashCamMerger-cli.exe` (same code, `--console`). Section 15 builds both.

---

## 11. External commands (normative reference)

| Purpose | Command |
|---------|---------|
| Probe clip / output | `ffprobe -v error -print_format json -show_format -show_streams <file>` |
| Merge (copy) | `ffmpeg -y -hide_banner -nostats -loglevel error -progress pipe:1 -f concat -safe 0 -i <list> -c copy -movflags +faststart <out.partial.mp4>` |
| Merge (re-encode) | same, with `-c:v libx264 -crf 18 -preset medium -c:a aac` instead of `-c copy` |
| Version (About) | `ffmpeg -version` |

ffprobe JSON fields used: `format.duration`, `format.size`, `streams[].codec_type`, `codec_name`, `width`, `height`, `r_frame_rate`, `pix_fmt`, `sample_rate`, `channels`.

ffmpeg `-progress` keys used: `out_time_ms` (microseconds), `total_size`, `progress` (`continue`/`end`).

---

## 12. Safety rules → enforcement

| Rule | Enforcement |
|------|-------------|
| FR-8.1 never write in input folder | `WriteGuard` allowed roots = `[output_dir, temp_dir]`; output cannot equal or contain input (Section 6 step 0). `grep` the code base for `unlink`, `rmtree`, `remove(` — only `logging_setup.prune_old_logs` and (opt-in, FR-12) `cleanup.py` may contain a delete. |
| FR-8.2 only rename own files | `rename` is called only on `partial_path` and on a previous output when `--overwrite`; both are under `output_dir`. |
| FR-8.3 overwrite never touches sources | `--overwrite` renames the old output to `*.replaced.mp4` and never deletes. |
| FR-6.4 finished `.mp4` is always good | Only `verify_part` PASS leads to the rename from `.partial.mp4`. |
| FR-7.6 | `sources_untouched` runs at the end of every non-dry run and is printed in the report. |
| FR-12 source deletion stays opt-in and verification-gated | `RunOptions.delete_source` defaults `False`; `cleanup.sources_for_deletion` only returns clips of a `PASS` result; `Confirmer.confirm_delete` is required before any `os.remove`; `delete_sources` itself refuses paths under `[output_dir, temp_dir]`. |

Unit test `test_safety_grep` (in `test_integration.py`): scan all `src\**\*.py` for the strings
`unlink(`, `rmtree(`, `os.remove(`, `send2trash`; assert every hit is in `logging_setup.py` or
`cleanup.py`.

---

## 13. Test plan

### 13.1 Unit tests (no ffmpeg needed)

Listed per module in Section 5. Use `tmp_path` fixtures; create fake clips with `Path.write_bytes(b"\0" * n)`.
Fake probe results are injected by setting `clip.probe = ProbeResult(60.0, size, StreamSignature(...))`.
Run: `pytest -m "not integration"`.

### 13.2 Fixture generator `tests\fixtures\make_clips.py`

```python
def make_clips(dst: Path, ffmpeg: Path, camera_suffixes=("F", "R"), count=6, seconds=2,
               size="640x360", start="20260831152516", different_res_index: int | None = None,
               truncate_index: int | None = None) -> list[Path]
```
- Produces `f"{ts}_{seq:07d}{suffix}.MP4"` (Xforce style), timestamps 60 s apart, with
  `ffmpeg -y -f lavfi -i testsrc=size=<size>:rate=30 -f lavfi -i sine=frequency=440 -t <seconds> -c:v libx264 -pix_fmt yuv420p -c:a aac <file>`.
- `different_res_index`: that clip gets `size="320x180"`.
- `truncate_index`: that clip is cut to its first 2 000 bytes after creation (unreadable).

### 13.3 Integration tests (`@pytest.mark.integration`, require `ffmpeg\` present)

| ID | Scenario | Expected |
|----|----------|----------|
| IT-1 | 6 F + 6 R clips, 2 s each, CLI `--yes` | 2 outputs, duration 12 ± 1 s, both PASS, exit 0, `merge_report.txt` exists, all 12 sources same SHA-256 as before |
| IT-2 | clip 3 of Front has different resolution | Front → 2 parts (`_part1`, `_part2`), warning contains `codec change`, exit 0 |
| IT-3 | clip 2 of Rear truncated | Rear merged from 5 clips, report shows `Skipped unreadable: 1`, exit 0 |
| IT-4 | `--dry-run` | no `merged\` folder created, commands printed, exit 0 |
| IT-5 | run twice without `--overwrite` | second run produces `_2` files, first files unchanged |
| IT-6 | cancel after 0.5 s (set `cancel` event programmatically with 60 clips of 2 s) | file `INCOMPLETE_DO_NOT_USE_*.mp4` present, no `.partial.mp4`, exit 130, sources untouched |
| IT-7 | `--camera-map` with unknown token names | Unclassified count > 0, still exit 0 for other groups |
| IT-8 | `--max-part-gb 0.001` (≈1 MB) with 6 clips | multiple parts, every part PASS |
| IT-9 | filename with `'` and Vietnamese characters (`xe ô tô 'test'_F.MP4`) | PASS |
| IT-10 | `--output` equal to `--input` | exit 2, message about output folder |

### 13.4 Acceptance (manual, Spec §10)

Run the GUI on a real Xforce folder; confirm two files in `merged\`, playable and seekable in VLC and
Windows Media Player; input folder unchanged apart from `merged\`; no internet; no admin.

---

## 14. Implementation plan (do these in order)

| Step | Deliverable | Done when |
|------|-------------|-----------|
| 0 | Layout from Section 3, `__version__`, empty modules, `conftest.py` that adds `src` to `sys.path`, `pytest` runs 0 tests green | `pytest` exits 0 |
| 1 | `models.py`, `config.py`, `logging_setup.py` | `test_config.py` green |
| 2 | `safe_io.py`, `winpath.py` | safe_io tests green |
| 3 | `scanner.py` | `test_scanner.py` green |
| 4 | `ordering.py` | `test_ordering.py` green |
| 5 | `cameras.py` | `test_cameras.py` green |
| 6 | `probe.py` (+ JSON fixtures in `tests\fixtures\`) | `test_probe.py` green |
| 7 | `progress.py`, `planner.py` | `test_planner.py` green |
| 8 | `concat.py`, `verify.py`, `report.py` | their tests green |
| 9 | `pipeline.py`, `cli.py`, `main.py` | `test_cli.py` green; `make_clips.py` works; IT-1 … IT-10 green |
| 10 | `gui.py` | manual checklist in Section 9 completed; Scan → Merge → PASS on fixture folder |
| 11 | `build.bat`, README.txt, CHANGELOG.md | `dist\DashCamMerger\DashCamMerger.exe` runs on a clean Windows 10 VM without Python |

Rules for the implementer:
- Write the test file for a step **before** the module, from the "Tests" lines in Section 5.
- Do not modify a finished module to make a later step easier; extend it with a new function instead and note `# DESIGN-GAP`.
- Keep every function under ~60 lines; split otherwise.
- No global mutable state except `BUILTIN_PROFILES`, `DEFAULTS` and regex constants.

---

## 15. Build (`build.bat`)

```bat
@echo off
setlocal
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install --quiet pyinstaller pytest
pytest -m "not integration" || exit /b 1
if exist ffmpeg\ffmpeg.exe pytest -m integration || exit /b 1
pyinstaller --noconfirm --onedir --windowed --name DashCamMerger ^
  --add-data "ffmpeg;ffmpeg" --add-data "config.json;." --paths src src\main.py
pyinstaller --noconfirm --onedir --console --name DashCamMerger-cli ^
  --paths src src\main.py
copy /y dist\DashCamMerger-cli\DashCamMerger-cli.exe dist\DashCamMerger\
copy /y README.txt dist\DashCamMerger\
if not exist dist\DashCamMerger\logs mkdir dist\DashCamMerger\logs
powershell -Command "Compress-Archive -Force dist\DashCamMerger\* DashCamMerger_v1.0_win64.zip"
powershell -Command "(Get-FileHash DashCamMerger_v1.0_win64.zip -Algorithm SHA256).Hash" > DashCamMerger_v1.0_win64.sha256
endlocal
```

`app_dir()` in the frozen build is `dist\DashCamMerger\`, so `ffmpeg\` and `config.json` sit beside the
exe exactly as Spec §4 shows. Both exes share the folder; the CLI exe uses the same `ffmpeg\`.

README.txt must contain: what the tool does, the 4-step GUI procedure, the CLI one-liner, where
outputs and logs go, the "never deletes your clips" promise, and the ffmpeg version bundled.

---

## 16. Traceability matrix

| Requirement | Design location |
|-------------|-----------------|
| FR-1.1 | cli `--input`, gui Browse |
| FR-1.2 | scanner.scan, config.video_extensions |
| FR-1.3 | scanner step 3 |
| FR-1.4 | scanner step 4 |
| FR-1.5 | pipeline step 1, exit 3 |
| FR-2.1 | cameras.BUILTIN_PROFILES |
| FR-2.2 | cameras.detect, MATCH_THRESHOLD |
| FR-2.3 | cameras.extract_tokens / apply_manual_map / parse_camera_map_arg, gui 9.3, cli 8.2 |
| FR-2.4 | report.build_plan_summary, pipeline step 4, gui Scan table |
| FR-2.5 | config.profile_from_dict, cameras.detect (user profiles first) |
| FR-3.1 | ordering.parse_timestamp / parse_sequence / order_group |
| FR-3.2 | ordering.order_group (explicit key sort) |
| FR-3.3 | planner step 2 warning |
| FR-3.4 | ordering.detect_gaps, planner step 5 |
| FR-3.5 | planner step 6 `trip gap` |
| FR-4.1 | probe.sample_indices, planner step 3 |
| FR-4.2 | models.StreamSignature, probe.signature_from_json |
| FR-4.3 | planner step 6 (single part, stream copy) |
| FR-4.4 | planner step 4 & 6 `codec change`; concat.build_command re-encode branch |
| FR-4.5 | probe.probe_clip unreadable flag, planner step 4 |
| FR-5.1 | planner step 7 list_path, concat.write_concat_list |
| FR-5.2 | concat.escape_concat_path |
| FR-5.3 | safe_io.write_text (utf-8, no BOM) |
| FR-6.1 | concat.build_command |
| FR-6.2 | pipeline step 0 output_dir, safe_io.unique_name, planner step 7 |
| FR-6.3 | planner step 7 |
| FR-6.4 | pipeline execute_part rename after PASS |
| FR-6.5 | pipeline step 7 executor |
| FR-6.6 | concat.run_ffmpeg progress events, progress.describe |
| FR-6.7 | concat.run_ffmpeg cancel, pipeline rename_incomplete |
| FR-6.8 | planner `max part size` only; no time cap anywhere |
| FR-7.1 | probe.probe_output |
| FR-7.2 | verify.ensure_all_probed |
| FR-7.3 | verify.verify_part tolerance |
| FR-7.4 | verify.verify_part size / ProbeError |
| FR-7.5 | pipeline execute_part FAIL branch |
| FR-7.6 | verify.sources_untouched, pipeline step 8 |
| FR-8.1–8.3 | safe_io.WriteGuard, Section 12 |
| FR-9.1 | report.build_report_text |
| FR-9.2 | report.write_report, logging_setup |
| FR-9.3 | gui Open output folder |
| FR-10 | config.py |
| FR-11 | cli.py, exit codes 8.3 |
| FR-12 | cleanup.py Section 5.3.1, pipeline step 11, cli.py `--delete-source`, gui.py delete checkbox |
| Spec §6 GUI | gui.py Section 9 |
| Spec §7 errors | pipeline step 0, winpath, planner step 4, concat/verify FAIL paths |
| Spec §8 performance | stream copy only; probing sampled; no file content read by Python |
| Spec §9 logging | logging_setup |
| Spec §10 tests | Section 13 |
| Spec §11 build | Section 15 |
