# Next steps – DashCam GPS Extractor (PC)

Status on 2026-10-06: the port is complete. 58 tests pass, and `build.bat` builds
`dist\DashGPS\` and the zip. The built exe was tested on real clips. Nothing is committed yet.

## Done
- [x] `dashgps/core/` – Python port of the Android `core/`, byte-identical exports (`tests/test_parity.py`)
- [x] All 33 Android unit tests ported (`test_core.py`, `test_routefiles.py`)
- [x] Keyframe sampler with seek, PyAV (`frames.py`)
- [x] OCR: PP-OCRv6 on ONNX Runtime, with space recovery (`ocr.py`); 98% kept on 610 real frames
- [x] CLI (`cli.py`): extract / test-frame / clean
- [x] Window (`gui.py`): videos, camera, test frame, result preview, save, clean a route file
- [x] `tools/fetch_models.py`, `requirements.txt`, `build.bat`, README, root CLAUDE.md note

## Not done
- [ ] **Commit.** Branch off `master` first. Files to commit: `PC_app_GPSdataExtraction/` (build output, models and `.venv` are gitignored) and the root `CLAUDE.md` change.
- [ ] **Smaller bundle** (320 MB, 141 MB zipped): OpenCV is 112 MB, and `ocr.py` only uses `resize`, `dilate`, `findContours`, `boundingRect`, `drawContours` and `mean`. Replace these with numpy/Pillow (plus a small connected-components routine), or exclude unused OpenCV DLLs in the PyInstaller spec. Rerun `test_ocr.py` and compare against the 610-frame result (597 kept).
- [ ] **Longer real-video check:** run a full trip (e.g. `D:\DashTrip\PhanThietSaigon\Cam hanh trinh`, all F clips) and a merged file, and check the route in Google Earth.
- [ ] **Optional speed-up:** the line box goes through recognition as one ~1150 px wide image (135 ms on this CPU). Options: skip the "VIETMAP" logo box (37 ms), or try onnxruntime-directml on a GPU.
- [ ] **Optional:** skip rear clips (`…R.MP4`) when a folder is chosen; at the moment they only cost time.
- [ ] **Optional:** GUI – drag-and-drop of videos/route files; remember the window size.
- [ ] **Keep in step with Android:** any change to the parser, cleaner or exporters must go into both apps; regenerate the hashes in `test_parity.py` from the Kotlin output.

## Useful facts for the next session
- Models: `python tools/fetch_models.py` (RapidOCR 3.9.2 wheel, SHA-256 pinned).
- Real-video test: set `DASHGPS_TEST_VIDEO=D:\DashTrip\LeoCauRachMieu\20260919174323_0018890F.MP4`, then run `pytest`.
- `E:\Front_…_part4.mp4` is damaged (no moov atom); don't use it for testing.
- Drive D: reads at only ~8 MB/s, which is why the sampler seeks instead of reading files sequentially.
