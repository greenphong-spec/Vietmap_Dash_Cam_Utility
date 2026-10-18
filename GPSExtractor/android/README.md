# DashCam GPS Extractor (Android)

Reads the date, time, speed and GPS position that a dashcam **prints on the video** and
saves the route as **KMZ / KML** (Google Earth, Google My Maps, …), **GPX** (any GPS/map
app, Strava, OsmAnd…) and **CSV**. Works on original clips and on files merged with
DashCam Merger. Everything runs on the phone, offline.

Built for the Samsung Galaxy Note 10 (Android 12); runs on Android 9+ (ARM phones).

## Supported cameras

| Profile | Overlay read | Notes |
|---|---|---|
| Vietmap SpeedMap M1 – front | `13/09/2026 11:31:13 39KM/H N10.76736 E106.63187` in the bottom-left strip | Front video only (the rear camera shows no GPS). 2560×1440 HEVC, one keyframe per second. |

Adding a camera = adding an `OverlayProfile` in `core/OverlayProfiles.kt`: the crop region
(fractions of the frame) and a parser for its text. Use **Test frame** in the app to check
the crop and what OCR reads.

## How it works

1. **Frames** — `MediaExtractor` seeks to one **keyframe** per sample interval (default
   1 s) and only those samples go to the hardware decoder, so a 30 fps video is read ~30×
   faster than decoding every frame. Only the luma plane of the overlay strip is copied.
2. **OCR** — Google ML Kit text recognition, Latin model bundled in the APK (offline,
   no Play services needed). Up to 4 frames are recognised in parallel with decoding.
3. **Parsing** — tolerant of OCR confusions (O→0, l→1, S→5, missing spaces, text split over
   lines).
4. **Cleaning** — a reading is kept only if it agrees with its neighbours:
   the overlay clock must advance with the video clock (misread digits in the time are
   dropped; a real recording gap inside a merged video is kept as a new segment),
   one point per second, no position jump faster than 250 km/h, (0,0) = no GPS fix.
5. **Export** — times converted from the camera's local time (phone time zone) to UTC.
   KML/KMZ contain the route line, a timed track for Google Earth's time slider (hidden by
   default), start/end pins and a hidden folder of speed labels every 10 s. GPX 1.1 carries
   speed (m/s) in the Garmin TrackPointExtension. New segment after a gap > 2 min.

## Using it

1. **Choose video(s)** — front-camera clips or merged files (several allowed).
2. Camera profile and sampling interval; **Test frame** shows the cropped strip, the OCR
   text and the parsed values for a frame at 10 s.
3. **Extract GPS** — progress per video; result shows the route outline, distance, time,
   max/average speed and how many frames were dropped and why.
4. **Choose save folder**, tick formats (KMZ + GPX by default), **Save files**, then
   **Open in Google Earth / map** or **Share**. Files never overwrite: `_2`, `_3`… are added.

## Build

```
gradlew.bat testDebugUnitTest   # 12 JVM unit tests (parser, cleaner, KML/KMZ/GPX/CSV)
gradlew.bat assembleDebug       # app\build\outputs\apk\debug\app-debug.apk (~50 MB, ML Kit model)
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

Same toolchain as `../../DashCamMerger/android` (AGP 8.13.2, Kotlin 2.4.10, Compose BOM 2026.06.01,
JDK 17) plus `com.google.mlkit:text-recognition:16.0.1`.

## Layout

- `core/` — pure Kotlin, unit-tested: `Models`, `OverlayProfiles` (camera profiles +
  parser), `TrackCleaner` (+ `Geo`), `Exporters` (KML/KMZ/GPX/CSV).
- `android/` — `FrameSampler` (keyframe decode + luma crop), `Ocr` (ML Kit),
  `Storage` (video info, export folder - create-only), `ExtractSession` (orchestrator),
  `MainActivity` (Compose UI with route preview).
