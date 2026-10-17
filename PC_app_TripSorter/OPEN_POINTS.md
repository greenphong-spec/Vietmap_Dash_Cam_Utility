# DashCam Trip Sorter – Open points

**For:** the owner of the dashcam, before coding starts
**Spec:** [TripSorter-Specification.md](TripSorter-Specification.md) (v0.1 draft) · **Plan:** [PLAN.md](PLAN.md)

## How to answer

- Tick one box per question: change `- [ ]` to `- [x]`. Where several answers can apply, it says so.
- Or write your own answer after **Other:**.
- Add anything else after **Answer / notes:**. A few words is enough.
- *(recommended)* marks the option the spec uses until you answer.
- Part A has to be answered before coding starts. Part B can keep the recommended default
  and be changed later.

When you have answered, ask Claude to "update the spec from OPEN_POINTS.md". The answers
are then copied into the spec (v1.0), and Part A of the plan is ticked off.

---

## Part A – must answer before coding

### OP-1 What is a "trip"?

**Why it matters:** this decides every folder the program makes. Your own sorting shows
that a long break does not always end a trip. `TienGiang_SaiGon_12-13.Sep.2026` is one
trip, yet inside it the camera stopped for:

| Break | From → to |
|---|---|
| 7 h 20 min | 12 Sep 11:44 → 19:05 |
| 32 min | 12 Sep 19:19 → 19:52 |
| 13 h 16 min (overnight) | 12 Sep 20:05 → 13 Sep 09:22 |
| 41 min | 13 Sep 09:26 → 10:09 |
| 9 min | 13 Sep 11:25 → 11:35 |

`LeoCauRachMieu` (78 min) and `PhanThietSaigon` (45 min) have no break over 30 s.

- [ ] (a) Fully automatic: a new trip starts after a break longer than N (OP-2). The
  12–13 Sep journey would become 4–6 trips.
- [ ] (b) One trip per calendar day (a break past midnight always splits).
- [ ] (c) *(recommended)* The program **proposes** trips by break length (OP-2). In a review
  screen you then **join** neighbouring trips (e.g. the 12–13 Sep pieces into one) or
  **split** one, before anything is copied.
- [ ] (d) GPS-based: a trip ends when the car is back at "home" or another saved place. This
  needs the GPS read from the video first (slower, uses the GPS Extractor).
- [ ] (e) Combination – describe:

**Other:** ______________________________

**Answer / notes:** ______________________________

### OP-2 How long a break starts a new trip?

**Why it matters:** this is used by OP-1 (a) and (c). A traffic jam or a short stop for fuel
usually keeps recording, or stops for only a few minutes. A coffee or lunch stop is 20–60
minutes.

- [ ] 10 minutes
- [ ] 30 minutes *(recommended – the same as DashCam Merger's "trip gap")*
- [ ] 1 hour
- [ ] 2 hours

**Other:** ______ minutes

**Answer / notes:** ______________________________

### OP-3 Copy or move the clips off the card?

**Why it matters:** the card is the only copy until the clips are on the PC.

- [ ] (a) Copy only – the card is never changed. You format it in the camera yourself.
- [ ] (b) *(recommended)* Copy and verify each file (size + checksum). Then, only if you tick
  "delete from card" and confirm a second time, delete the verified clips from the card.
- [ ] (c) Move (copy + verify + delete straight away, no extra question).

**Other:** ______________________________

**Answer / notes:** ______________________________

### OP-4 Where should the trip folders go?

**Why it matters:** this becomes the default destination. It can still be changed each time.

- [ ] `D:\DashTrip` (where your sorted trips are now)

**Other (folder):** ______________________________

**Answer / notes:** ______________________________

### OP-5 How should a trip folder be named?

**Why it matters:** your current folders have hand-written place names
(`TienGiang_SaiGon_12-13.Sep.2026`, `LeoCauRachMieu`).

- [ ] (a) Start date and time: `2026-09-19_1742`
- [ ] (b) Start and end: `2026-09-19_1742-1900` (`2026-09-12_0926_to_2026-09-13_1144` over 2 days)
- [ ] (c) *(recommended)* (b) as the suggested name, which you can rename in the review screen
  (e.g. to `LeoCauRachMieu`).
- [ ] (d) Date + place names from GPS, automatically: `2026-09-19_MyTho_BenTre` (see OP-15)
- [ ] (e) Your style: `<places>_<dd-dd.Mon.yyyy>` like `TienGiang_SaiGon_12-13.Sep.2026`,
  with the places typed by you.

**Other (pattern):** ______________________________

**Answer / notes:** ______________________________

### OP-6 How should the files be arranged inside a trip folder?

**Why it matters:** DashCam Merger and the GPS Extractor work with both layouts. Today your
trips are flat: Front and Rear clips mixed in one folder.

- [ ] (a) *(recommended)* Flat, as now: all clips in the trip folder, original names.
- [ ] (b) `Front\` and `Rear\` subfolders.
- [ ] (c) Front only – Rear clips are not copied.

**Other:** ______________________________

**Answer / notes:** ______________________________

### OP-7 Which files from the card should be sorted? *(several can be ticked)*

**Why it matters:** cameras keep normal clips, "event"/locked clips (after a bump or the SOS
button) and photos in separate folders.

- [x] Normal recordings (`Movie`)
- [ ] Event / locked / emergency clips
- [ ] Photos
- [ ] Parking-mode recordings (if your camera makes them)

If event clips are sorted, put them:
- [ ] *(recommended)* in the trip they belong to by time, in an `Event\` subfolder
- [ ] in the trip folder with the normal clips
- [ ] in one separate `Events\` folder, outside the trips

**Other:** ______________________________

**Answer / notes:** ______________________________

### OP-8 What is on your SD card?

**Why it matters:** the program finds the clip folders by itself, but it should be tested on
the real layout. What is known so far: clips are in `\Vietmap_M1\Movie`, named
`20260919174222_0018888F.MP4` (Front) and `…_0018889R.MP4` (Rear). Clip length is the
camera's loop-recording setting: 1-min clips in September, 3-min clips since the card was
formatted on 13 Oct; 5 min is also possible (see OP-19).

Seen on the card (`E:\`, 17 Oct 2026): `Vietmap_M1\Movie\` (clips only), map data
`citiesen.bin`, `districtsen.bin`, `edogen.bin`, `roadsen.bin`, `format.dat`, `LOST.DIR\`,
`System Volume Information\`. No event / photo / parking folder yet - they may appear once
the camera records an event (OP-7).

Please list the folders on the card (or paste the output of `dir /s /b X:\ | more` with X =
card drive, first ~30 lines are enough):

```
(paste here)
```

Camera model(s) you will use with this program:

- [x] Vietmap SpeedMap M1

**Other cameras:** ______________________________

---

## Part B – can keep the default for now

### OP-9 The same card is read again (clips already copied before)

- [ ] *(recommended)* Skip a clip if a file with the same name and size is already in the
  destination.
- [ ] Skip only if the checksum is also the same (slower: every file is read twice).
- [ ] Ask each time.

**Answer / notes:** ______________________________

### OP-10 A new card continues a trip that is already on the PC

Example: a long drive over two card dumps.

- [ ] (a) Never join: always make new trip folders.
- [ ] (b) *(recommended)* Propose joining in the review screen when the first new clip
  starts within the OP-2 break after the existing trip ends.
- [ ] (c) Always join automatically when that is the case.

**Answer / notes:** ______________________________

### OP-11 What should happen after sorting? *(several can be ticked)*

- [ ] *(recommended)* Nothing – just the trip folders and a report.
- [ ] Merge each trip's clips into one video per camera (DashCam Merger).
- [ ] Extract each trip's GPS route to a KMZ for Google Earth (GPS Extractor).
- [ ] Ask each time.

**Answer / notes:** ______________________________

### OP-12 How do you want to use it?

- [ ] *(recommended)* A window, plus a command line for scripts.
- [ ] Window only.
- [ ] Command line only.

Start automatically when the dashcam card is put in the PC?
- [ ] No *(recommended)* – you open the program yourself.
- [ ] Yes – open the program and show the proposed trips.
- [ ] Yes – and sort without asking, using the rules above.

**Answer / notes:** ______________________________

### OP-13 The camera clock is wrong

Example: after a battery reset the camera writes 2000-01-01 in names and on the video.

- [ ] *(recommended)* Order by the clip number in the name (`_0018888`), show a warning, and
  let you rename the trips.
- [ ] Use the file's modified time from the card instead.
- [ ] Put such clips in an `Unknown_time\` folder.

**Answer / notes:** ______________________________

### OP-14 Very short trips

Example: 1–2 clips while moving the car in the car park.

- [ ] *(recommended)* Keep them as trips, but list them as "short" in the review screen so
  they can be joined or excluded.
- [ ] Put trips shorter than ___ minutes in one `Short\` folder.
- [ ] Skip them (leave on the card).

**Answer / notes:** ______________________________

### OP-15 Place names in folder names or the report

The places come from the GPS printed on the front video (the GPS Extractor reads it). Only
the start and end of each trip need to be read, so this is fast.

- [ ] *(recommended)* No place names in v1.
- [ ] Offline: nearest province/district from a list bundled with the program (no internet).
- [ ] Online: OpenStreetMap lookup (needs internet; only start/end coordinates are sent).

**Answer / notes:** ______________________________

### OP-16 Report *(several can be ticked)*

- [ ] *(recommended)* `trip_info.txt` in each trip folder: start, end, duration, clips, size,
  breaks, source card.
- [ ] One `trips.csv` index of all trips in the destination folder (for Excel).
- [ ] A KMZ with all trips' routes (needs the GPS Extractor; slow).

**Answer / notes:** ______________________________

### OP-19 Clip length when the program does not measure every clip

**Why it matters:** the camera records 1-, 3- or 5-minute clips (your setting; 3 min now).
Measuring every clip with ffprobe is exact but slower on a card reader. Without measuring,
the program needs to know the clip length to find the breaks. The last clip before you
switch the engine off is always shorter, so that one is measured in any case (FR-2.2a).

- [ ] *(recommended)* Automatic: worked out from the clip start times (every 3 min → 3-min
  clips); a card with mixed lengths is handled.
- [ ] Always measure every clip (slower, never wrong).
- [ ] Fixed in `config.json`: ___ min (1 / 3 / 5).

**Answer / notes:** ______________________________

### OP-17 Program name

- [ ] *(recommended)* DashCam Trip Sorter (`TripSorter.exe`)

**Other:** ______________________________

### OP-18 Anything else?

What annoys you today when sorting by hand? Anything the program must never do?

______________________________

______________________________
