# Next steps – DashCam GPS Extractor (PC)

Status on 2026-10-06 (branch `pc-gps-extractor`, not pushed): everything below "Done"
is committed. 59 tests pass (with `DASHGPS_TEST_VIDEO` set). `build.bat` builds
`dist\DashGPS\` (209 MB) and the zip (100 MB).

## Done
- [x] `dashgps/core/` – Python port of the Android `core/`, byte-identical exports (`tests/test_parity.py`)
- [x] Keyframe sampler with seek, PyAV (`frames.py`); `frames_at` re-reads given frames
- [x] OCR: PP-OCRv6 on ONNX Runtime with space recovery, numpy + Pillow only (OpenCV removed)
- [x] Second look at rejected frames (`ocr.SECOND_LOOK`, `extract._second_look`): 93% -> 95% kept on a 78-min video
- [x] Parser: "7" accepted as a misread date separator, in BOTH apps (Kotlin + Python, same test)
- [x] CLI, window (remembers size/position), rear clips skipped when a folder is chosen
- [x] Full 78-min test video: `samples\video\Front_20260919174222_to_20260919190023.kmz` (route + Start + End)

## Not done
- [ ] Push the branch / open a PR (deliberately not done: needs the user's go-ahead).
- [ ] Android app: rebuild the APK so the phone gets the date-separator fix (code and tests are updated).
- [ ] Optional: the Android app could also take a second look at rejected frames (ML Kit on a scaled crop).
- [ ] Optional speed-up: skip the "VIETMAP" logo box (~37 ms/frame), or try onnxruntime-directml (GPU).
- [ ] Optional: drag-and-drop in the window (needs `tkinterdnd2`).
- [ ] Optional: a lighter route line for very long trips (e.g. Douglas-Peucker in KML only). This
      changes the exporter, so it has to go into Android too and the parity hashes have to be regenerated.

## Useful facts for the next session
- Models: `python tools/fetch_models.py` (RapidOCR 3.9.2 wheel, SHA-256 pinned).
- Real-video tests: `set DASHGPS_TEST_VIDEO=D:\DashTrip\LeoCauRachMieu\20260919174323_0018890F.MP4`.
- Parity hashes: regenerate from the Kotlin output (temporary test that dumps the exports, run with
  `gradlew --offline testDebugUnitTest`, delete it afterwards); never from the Python output.
- `E:\Front_…_part4.mp4` is damaged (no moov atom). D: reads at ~8 MB/s.
- Real GPS quirks that are NOT OCR errors: the camera's GPS can jump ~80 m right after it gets a
  fix, and sometimes prints the same position twice, then catches up.
