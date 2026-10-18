# Dashcam tools

Tools for footage from multi-camera dashcams (Vietmap, Xforce, ...), grouped by product.
Each code project is self-contained: open or run it from its own folder.

| Folder | What it is | Run / test from that folder |
|---|---|---|
| `DashCamMerger/pc/` | **DashCam Merger** for Windows (GUI + CLI). Joins clip sequences per camera into one video, losslessly, with ffmpeg. Docs in `docs/`, user guide in `README.txt`. | `.venv\Scripts\activate`, `pytest -q`, `python src\main.py`, `build.bat` |
| `DashCamMerger/android/` | DashCam Merger for Android (Kotlin, Gradle). Kept in sync with the PC app. | Open in Android Studio, or `gradlew testDebugUnitTest` |
| `GPSExtractor/pc/` | **DashCam GPS Extractor** for Windows. Reads date, time, speed and GPS printed on the video (OCR) and exports GPX/KML/KMZ. | `.venv\Scripts\activate`, `pytest -q`, `python main.py`, `build.bat` |
| `GPSExtractor/android/` | GPS Extractor for Android. Its `core/` must give byte-identical exports to the PC version (`GPSExtractor/pc/tests/test_parity.py`). | Open in Android Studio, or `gradlew testDebugUnitTest` |
| `TripSorter/` | **Trip Sorter**, planning only (spec, plan, open points). No code yet. | n/a |
| `samples/` | Sample GPS exports (`gps-exports/`) and an overlay screenshot. `samples/video/` holds the large local test videos and is not in git. | n/a |

Not in git (generated or downloaded, rebuilt by each project's `build.bat`): `.venv/`, `build/`, `dist/`,
release `.zip`/`.sha256`, `DashCamMerger/pc/ffmpeg/` (drop in a gyan.dev essentials build) and
`DashCamMerger/pc/logs/`.

## Where things came from

| Now | Before |
|---|---|
| `DashCamMerger/pc/` | repository root (`src/`, `tests/`, `tools/`, `build.bat`, ...) |
| `DashCamMerger/pc/docs/` | `DashCamMerger-Specification.*`, `DashCamMerger-Design.*`, `merge-dashcam-camera-clips.md` at the root |
| `DashCamMerger/android/` | `Android_app_Ver/` |
| `GPSExtractor/pc/` | `PC_app_GPSdataExtraction/` |
| `GPSExtractor/android/` | `Android_app_GPSdataExtration/` |
| `TripSorter/` | `PC_app_TripSorter/` |
| `samples/gps-exports/`, `samples/video/` | `outGPS/`, `samplevideo/` |
