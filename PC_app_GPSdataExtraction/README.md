# DashCam GPS Extractor (Windows PC)

PC version of `../Android_app_GPSdataExtration`. Reads the date, time, speed and GPS
position that a dashcam **prints on the video** and saves the route as **KMZ / KML**
(Google Earth, My Maps…), **GPX** (any GPS/map app) and **CSV**, plus a per-frame OCR log.
It works on original clips and on files merged with DashCam Merger. Everything runs offline.

`DashGPS.exe` opens the window; `DashGPS-cli.exe` (or `python main.py <command>`) is the
command line.

## Same results as the phone

The parsing, cleaning and file writing are a line-for-line Python port of the Android
app's `core/` package (`dashgps/core/`), including its JVM number behaviour (half-up
rounding, `%.6f` formatting, left-to-right sums). Three groups of tests check it:

- **All 33 Android unit tests**, ported as they are (`test_core.py`, `test_routefiles.py`),
  including the replay of a real 23-min video's OCR log: 1308 of 1387 frames kept, the
  same as on the phone.
- **Byte-for-byte parity** (`test_parity.py`): KML, KML with Google Earth extras, GPX, CSV,
  OCR log and summary for the replay and for the real noisy GPX/KMZ routes have the same
  SHA-256 hashes as the files the Kotlin code writes.
- **Real overlay frames** (`test_ocr.py`) and an end-to-end run on a real clip
  (`test_video.py`, set `DASHGPS_TEST_VIDEO`).

Only the two Android-specific parts are replaced:

| Android | PC | Why |
|---|---|---|
| `MediaExtractor` seek to each keyframe + `MediaCodec` | PyAV (FFmpeg) seek to each keyframe, decode only that packet, copy only the luma of the overlay strip | Same sampling: one keyframe per interval, each used once. Seeking reads only the keyframes, so slow disks and SD cards are not read end to end (a 106 MB clip on an 8 MB/s drive: 3 s instead of 13 s). |
| ML Kit text recognition | PaddleOCR **PP-OCRv6** small models on ONNX Runtime, in a small pipeline (`dashgps/ocr.py`) | ML Kit only runs on Android. See below. |

### OCR

The models come from the RapidOCR wheel (Apache-2.0); `tools/fetch_models.py` downloads
the pinned version and checks SHA-256 hashes. Instead of a general OCR wrapper, a short
pipeline (numpy + Pillow, no OpenCV) runs them for one strip of text: detection at 1024 px wide, boxes back onto the
full-resolution strip, recognition at 48 px height with few distinct input shapes, then
CTC decoding. On the test PC (6-core Xeon E5-1650 v2, 2013, no AVX2) that is 0.09 s per
frame with 6 threads, about **5–6 minutes per hour of video**. A newer CPU is faster.

One fix matters for quality. The recogniser sometimes drops the double space between
overlay fields ("19/09/2026  18:02:21" → "19/09/202618:02:21"), and the parser (shared
with the phone) needs that space. ML Kit keeps it. The decoder therefore puts a space back
wherever the model still gives the space class some probability in the gap, even when
another class won. In 600 real frames every such gap was a real word boundary.

On 610 frames from 10 real Vietmap M1 front clips: **597 kept (98%)**. 9 dropped frames
were clip-boundary duplicates, which is expected. 4 were real misreads (lost colon,
digit or hemisphere letter), and the cleaner drops those exactly as on the phone.

## Supported cameras

| Profile | Overlay read |
|---|---|
| Vietmap SpeedMap M1 – front | `13/09/2026 11:31:13 39KM/H N10.76736 E106.63187`, bottom-left strip. Front video only (the rear camera shows no GPS). |

Adding a camera means adding an `OverlayProfile` in `dashgps/core/profiles.py` (and in
the Android app's `OverlayProfiles.kt`, to keep the two in step). Use **Test frame** to check
the crop and what OCR reads.

## Using it

**Window:** choose video(s) or a folder → **Test frame** (shows the cropped strip, the OCR
text and the parsed values) → **Extract GPS** → check the route outline and summary →
choose a save folder and formats → **Save files** → **Open in Google Earth / map**.
Files are never overwritten: `_2`, `_3`… are added. **Clean a route file** removes misread
points from a KML/KMZ/GPX saved earlier and writes `<name>_clean`.

**Command line:**

```
DashGPS-cli extract D:\DCIM\Movie\*F.MP4 -o D:\Routes --formats kmz,gpx,csv
DashGPS-cli extract Front_merged.mp4 --interval 2 --ge-extras
DashGPS-cli test-frame Front.mp4 --at 30 --save strip.png
DashGPS-cli clean D:\Routes\trip.gpx
DashGPS-cli --tz Asia/Ho_Chi_Minh extract ...      # camera clock zone (default: this PC's)
```

Folders are expanded to the video files inside them; in a folder with both front and rear
clips (`…F.MP4` / `…R.MP4`, `Front_…` / `Rear_…`) the rear ones are skipped, since they have
no GPS text. Files named one by one are always used.

Times: the camera prints local time. KML/GPX need UTC, so times are converted with the
PC's time zone, or with `--tz` / the **Camera clock time zone** field (the Android app
uses the phone's zone).

## Develop / build

```
pip install -r requirements.txt pytest
python tools\fetch_models.py        # OCR models -> dashgps\models\ (not in git)
python -m pytest -q                 # 58 tests; set DASHGPS_TEST_VIDEO=<front clip> for test_video.py
python main.py                      # window; any argument -> command line
build.bat                           # venv, models, tests, PyInstaller -> dist\DashGPS\ + zip
```

## Layout

- `dashgps/core/` – pure Python, platform-independent, ported from the Android `core/`:
  `models`, `profiles` (camera profiles + parser), `cleaner` (steps 1–9), `exporters`
  (KML/KMZ/GPX/CSV/OCR log), `routefiles` (KML/KMZ/GPX reader), `timezone`, `jvm`
  (JVM rounding/formatting helpers).
- `dashgps/frames.py` – keyframe sampler (PyAV); `dashgps/ocr.py` – OCR pipeline;
  `dashgps/extract.py` – orchestration (extract, export, clean route), shared by
- `dashgps/cli.py` and `dashgps/gui.py` (tkinter).
