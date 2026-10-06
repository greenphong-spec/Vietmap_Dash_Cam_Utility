"""
Tkinter window: the Android app's screen on the PC. Same sections - 1. Videos,
2. Camera, test frame, 3. Result with route preview, 4. Save, and "Clean a route file".
Heavy work runs on a worker thread; it talks to the window through a queue.
"""
from __future__ import annotations

import json
import math
import os
import queue
import threading
import traceback
from pathlib import Path
from tkinter import BooleanVar, Canvas, StringVar, Tk, filedialog, messagebox
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .core import profiles
from .core.exporters import ExportFormat
from .core.timezone import Zone

TITLE = f"DashCam GPS Extractor {__version__}"
INTERVALS = ["1 s", "2 s", "5 s", "10 s"]
SETTINGS = Path(os.environ.get("APPDATA", Path.home())) / "DashGPS" / "settings.json"
VIDEO_TYPES = [("Videos", "*.mp4 *.MP4 *.mov *.mkv *.ts *.avi *.m4v"), ("All files", "*.*")]
ROUTE_TYPES = [("Route files", "*.kmz *.kml *.gpx"), ("All files", "*.*")]


def _load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_settings(d: dict) -> None:
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except OSError:
        pass


def _size(n: int) -> str:
    return f"{n / 1024 ** 3:.2f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.1f} MB"


def _open(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except OSError as e:
        messagebox.showerror(TITLE, f"No app can open {path.name}.\n{e}\n\nInstall Google Earth Pro to open KMZ/KML files.")


class App:
    def __init__(self, root: Tk):
        self.root = root
        self.q: "queue.Queue" = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.s = _load_settings()
        self.zone = Zone(self.s.get("tz") or None)
        self.videos: list[Path] = []
        self.extraction = None
        self.route_clean = None
        self.saved: list = []
        self.route_saved = None
        self._test_image = None

        root.title(TITLE)
        root.geometry(self.s.get("geometry", "980x860"))
        root.minsize(760, 600)
        root.protocol("WM_DELETE_WINDOW", self._close)

        outer = ttk.Frame(root)
        outer.pack(fill="both", expand=True)
        canvas = Canvas(outer, highlightthickness=0)
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas, padding=12)
        win = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        root.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        body.columnconfigure(0, weight=1)
        self.body = body
        self._rows = 0

        ttk.Label(body, text="DashCam GPS Extractor", font=("Segoe UI", 15, "bold")).grid(row=self._row(), sticky="w")
        ttk.Label(body, text="Reads the date, time, speed and GPS position printed on dashcam video and saves "
                             "the route for Google Earth or any map app.").grid(row=self._row(), sticky="w", pady=(0, 8))

        self._build_videos()
        self._build_camera()
        self.status = StringVar()
        ttk.Label(body, textvariable=self.status, font=("Segoe UI", 10, "bold"), wraplength=940,
                  justify="left").grid(row=self._row(), sticky="w", pady=(6, 2))
        self.progress = ttk.Progressbar(body, maximum=1000)
        self.progress.grid(row=self._row(), sticky="ew")
        self._build_test()
        self._build_result()
        self._build_save()
        self._build_clean()
        self._refresh()
        root.after(100, self._poll)

    # ---- layout ------------------------------------------------------------------------

    def _row(self) -> int:
        """Explicit rows: sections hidden with grid_remove() must keep their place."""
        self._rows += 1
        return self._rows - 1

    def _section(self, title: str) -> ttk.LabelFrame:
        f = ttk.LabelFrame(self.body, text=title, padding=10)
        f.grid(row=self._row(), sticky="ew", pady=6)
        f.columnconfigure(0, weight=1)
        return f

    def _build_videos(self):
        f = self._section("1. Videos")
        row = ttk.Frame(f)
        row.grid(sticky="w")
        self.btn_videos = ttk.Button(row, text="Choose video(s)…", command=self._choose_videos)
        self.btn_videos.pack(side="left")
        self.btn_folder = ttk.Button(row, text="Add a folder…", command=self._choose_folder)
        self.btn_folder.pack(side="left", padx=6)
        self.videos_label = StringVar(value="Pick one or more front-camera videos (original clips or merged files).")
        ttk.Label(f, textvariable=self.videos_label, wraplength=900, justify="left").grid(sticky="w", pady=(6, 0))

    def _build_camera(self):
        f = self._section("2. Camera")
        self.profile = StringVar(value=profiles.by_id(self.s.get("profile")).name)
        ttk.Combobox(f, textvariable=self.profile, values=[p.name for p in profiles.ALL], state="readonly",
                     width=50).grid(sticky="w")
        self.profile_note = StringVar()
        ttk.Label(f, textvariable=self.profile_note).grid(sticky="w")
        row = ttk.Frame(f)
        row.grid(sticky="w", pady=(6, 0))
        ttk.Label(row, text="Read one frame every:").pack(side="left")
        self.interval = StringVar(value=self.s.get("interval", "1 s"))
        for v in INTERVALS:
            ttk.Radiobutton(row, text=v, value=v, variable=self.interval).pack(side="left", padx=4)
        row = ttk.Frame(f)
        row.grid(sticky="w", pady=(6, 0))
        ttk.Label(row, text="Camera clock time zone:").pack(side="left")
        self.tz = StringVar(value=self.s.get("tz", ""))
        ttk.Entry(row, textvariable=self.tz, width=24).pack(side="left", padx=4)
        self.tz_label = StringVar()
        ttk.Label(row, textvariable=self.tz_label).pack(side="left")
        row = ttk.Frame(f)
        row.grid(sticky="w", pady=(8, 0))
        self.btn_test = ttk.Button(row, text="Test frame", command=self._test_frame)
        self.btn_test.pack(side="left")
        self.btn_extract = ttk.Button(row, text="Extract GPS", command=self._extract)
        self.btn_extract.pack(side="left", padx=6)
        self.btn_cancel = ttk.Button(row, text="Cancel", command=self._cancel)
        self.btn_cancel.pack(side="left")

    def _build_test(self):
        self.test_frame = f = self._section("Test frame")
        self.test_img = ttk.Label(f)
        self.test_img.grid(sticky="w")
        self.test_text = StringVar()
        ttk.Label(f, textvariable=self.test_text, font=("Consolas", 9), wraplength=900, justify="left").grid(sticky="w")
        f.grid_remove()

    def _build_result(self):
        self.result_frame = f = self._section("3. Result")
        self.preview = Canvas(f, height=260, background="#fafafa", highlightthickness=1, highlightbackground="#ccc")
        self.preview.grid(sticky="ew")
        self.preview.bind("<Configure>", lambda e: self._draw_preview())
        self.summary = ScrolledText(f, height=10, font=("Consolas", 9), wrap="word")
        self.summary.grid(sticky="ew", pady=(6, 0))
        f.grid_remove()

    def _build_save(self):
        self.save_frame = f = self._section("4. Save")
        self.out_dir = StringVar(value=self.s.get("out_dir", ""))
        row = ttk.Frame(f)
        row.grid(sticky="ew")
        ttk.Label(row, textvariable=self.out_dir).pack(side="left")
        ttk.Button(row, text="Choose save folder…", command=self._choose_out).pack(side="left", padx=6)
        chosen = set(self.s.get("formats", ["KMZ", "GPX"]))
        self.fmt_vars = {}
        for fmt in ExportFormat:
            v = BooleanVar(value=fmt.name in chosen)
            ttk.Checkbutton(f, text=fmt.label, variable=v, command=self._refresh).grid(sticky="w")
            self.fmt_vars[fmt] = v
        self.ge_extras = BooleanVar(value=self.s.get("ge_extras", False))
        self.chk_ge = ttk.Checkbutton(f, variable=self.ge_extras, command=self._refresh, text=(
            "KMZ/KML: add Google Earth extras (moving marker in the tour and time slider, speed labels)"))
        self.chk_ge.grid(sticky="w", pady=(4, 0))
        self.ge_note = ttk.Label(f, text="Other map apps (e.g. Geo Tracker) show the extras as many extra points.")
        self.ge_note.grid(sticky="w")
        self.combine = BooleanVar(value=self.s.get("combine", True))
        self.chk_combine = ttk.Checkbutton(f, text="Combine all videos into one route", variable=self.combine)
        self.chk_combine.grid(sticky="w")
        row = ttk.Frame(f)
        row.grid(sticky="w", pady=(8, 0))
        self.btn_save = ttk.Button(row, text="Save files", command=self._save)
        self.btn_save.pack(side="left")
        self.btn_open_map = ttk.Button(row, text="Open in Google Earth / map", command=self._open_map)
        self.btn_open_map.pack(side="left", padx=6)
        self.btn_open_dir = ttk.Button(row, text="Open folder", command=lambda: _open(Path(self.out_dir.get())))
        self.btn_open_dir.pack(side="left")
        self.saved_label = StringVar()
        ttk.Label(f, textvariable=self.saved_label, justify="left").grid(sticky="w", pady=(6, 0))
        f.grid_remove()

    def _build_clean(self):
        f = self._section("Clean a route file (KML / KMZ / GPX)")
        ttk.Label(f, wraplength=900, justify="left", text=(
            "Removes misread points and corrects misread speeds in a saved route. The original file is not "
            "changed; the result is saved as <name>_clean in the save folder (or next to the file).")).grid(sticky="w")
        row = ttk.Frame(f)
        row.grid(sticky="w", pady=(6, 0))
        self.btn_route = ttk.Button(row, text="Choose route file…", command=self._choose_route)
        self.btn_route.pack(side="left")
        self.btn_route_save = ttk.Button(row, text="Save cleaned file", command=self._save_route)
        self.btn_route_save.pack(side="left", padx=6)
        self.btn_route_open = ttk.Button(row, text="Open in map", command=lambda: _open(self.route_saved.path))
        self.btn_route_open.pack(side="left")
        self.route_status = StringVar()
        ttk.Label(f, textvariable=self.route_status, font=("Segoe UI", 10, "bold")).grid(sticky="w", pady=(6, 0))
        self.route_preview = Canvas(f, height=200, background="#fafafa", highlightthickness=1, highlightbackground="#ccc")
        self.route_preview.grid(sticky="ew")
        self.route_preview.bind("<Configure>", lambda e: self._draw_route_preview())
        self.route_summary = ScrolledText(f, height=7, font=("Consolas", 9), wrap="word")
        self.route_summary.grid(sticky="ew", pady=(6, 0))
        self.route_preview.grid_remove()
        self.route_summary.grid_remove()

    # ---- state -------------------------------------------------------------------------

    def _current_profile(self):
        return next(p for p in profiles.ALL if p.name == self.profile.get())

    def _persist(self):
        self.s.update(
            profile=self._current_profile().id, interval=self.interval.get(), tz=self.tz.get().strip(),
            out_dir=self.out_dir.get(), formats=[f.name for f, v in self.fmt_vars.items() if v.get()],
            ge_extras=self.ge_extras.get(), combine=self.combine.get(),
        )
        _save_settings(self.s)

    def _zone(self) -> Zone:
        name = self.tz.get().strip() or None
        try:
            self.zone = Zone(name)
        except Exception:
            raise ValueError(f"Unknown time zone '{name}'. Use an IANA name such as Asia/Ho_Chi_Minh, or leave it empty.")
        return self.zone

    def _refresh(self):
        b = self.busy
        state = lambda on: "!disabled" if on else "disabled"  # noqa: E731
        for w in (self.btn_videos, self.btn_folder, self.btn_route):
            w.state([state(not b)])
        self.btn_test.state([state(bool(self.videos) and not b)])
        self.btn_extract.state([state(bool(self.videos) and not b)])
        self.btn_cancel.state([state(b)])
        formats = [f for f, v in self.fmt_vars.items() if v.get()]
        self.btn_save.state([state(bool(formats) and self.extraction is not None and bool(self.extraction.track.fixes) and not b)])
        self.btn_open_map.state([state(any(s.format in (ExportFormat.KMZ, ExportFormat.KML, ExportFormat.GPX) for s in self.saved))])
        self.btn_open_dir.state([state(bool(self.saved))])
        self.btn_route_save.state([state(self.route_clean is not None and bool(self.route_clean.track.fixes) and not b)])
        self.btn_route_open.state([state(self.route_saved is not None)])
        kml = self.fmt_vars[ExportFormat.KMZ].get() or self.fmt_vars[ExportFormat.KML].get()
        (self.chk_ge.grid if kml else self.chk_ge.grid_remove)()
        (self.ge_note.grid if kml and self.ge_extras.get() else self.ge_note.grid_remove)()
        (self.chk_combine.grid if len(self.videos) > 1 else self.chk_combine.grid_remove)()
        note = self._current_profile().note
        self.profile_note.set(note)
        self.tz_label.set(f"(empty = this PC: {Zone(None)})")
        self.root.configure(cursor="watch" if b else "")

    def _run(self, work, done):
        """Runs work() on a worker thread; done(result) back on the Tk thread."""
        self.busy = True
        self._refresh()

        def worker():
            try:
                self.q.put(("done", done, work()))
            except Exception as e:  # shown, not swallowed
                traceback.print_exc()
                self.q.put(("error", None, e))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, fn, value = self.q.get_nowait()
                if kind == "progress":
                    p = value
                    self.progress["value"] = p.fraction * 1000
                    self.status.set(f"Video {p.video_index + 1}/{p.video_count}: {p.video_name}  ·  "
                                    f"{int(p.pos_sec)} / {int(p.duration_sec)} s  ·  {p.frames_read} frames read")
                    continue
                self.busy = False
                if kind == "error":
                    self.status.set(f"Error: {value}")
                    messagebox.showerror(TITLE, str(value))
                else:
                    fn(value)
                self._refresh()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    # ---- actions -----------------------------------------------------------------------

    def _set_videos(self, videos):
        from .extract import find_videos
        self.videos = find_videos(map(str, videos))
        n = len(self.videos)
        size = sum(v.stat().st_size for v in self.videos if v.exists())
        names = "\n".join("• " + v.name for v in self.videos[:6]) + (f"\n… and {n - 6} more" if n > 6 else "")
        self.videos_label.set(f"{n} video(s), {_size(size)}\n{names}" if n else "No video files found.")
        self.extraction = None
        self.saved = []
        self.result_frame.grid_remove()
        self.save_frame.grid_remove()
        self.test_frame.grid_remove()
        self.status.set("")
        self.progress["value"] = 0
        self._refresh()

    def _choose_videos(self):
        files = filedialog.askopenfilenames(title="Front-camera videos", filetypes=VIDEO_TYPES,
                                            initialdir=self.s.get("video_dir"))
        if files:
            self.s["video_dir"] = str(Path(files[0]).parent)
            self._set_videos(files)

    def _choose_folder(self):
        d = filedialog.askdirectory(title="Folder with front-camera videos", initialdir=self.s.get("video_dir"))
        if d:
            self.s["video_dir"] = d
            self._set_videos([d])

    def _choose_out(self):
        d = filedialog.askdirectory(title="Save folder", initialdir=self.out_dir.get() or None)
        if d:
            self.out_dir.set(d)
            self._persist()

    def _close(self):
        """Remember size and position; stop a running extraction (its thread is a daemon)."""
        self.cancel.set()
        if self.root.state() == "normal":
            self.s["geometry"] = self.root.geometry()
        self._persist()
        self.root.destroy()

    def _cancel(self):
        self.cancel.set()
        self.status.set("Cancelling…")

    def _test_frame(self):
        from . import extract
        profile = self._current_profile()
        video = self.videos[0]
        self.status.set(f"Reading one frame of {video.name}…")
        self._persist()

        def done(r):
            if r is None:
                self.status.set("No frame could be decoded")
                return
            from PIL import Image, ImageTk
            img = Image.fromarray(r.crop)
            img.thumbnail((920, 200))
            self._test_image = ImageTk.PhotoImage(img)
            self.test_img.configure(image=self._test_image)
            self.test_text.set(f"OCR: {r.text or '(nothing)'}\nParsed: {r.parsed}")
            self.test_frame.configure(text=f"Test frame at {int(r.pos_sec)} s")
            self.test_frame.grid()
            self.status.set(f"Test frame read at {int(r.pos_sec)} s")
        self._run(lambda: extract.test_frame(video, profile, 10.0), done)

    def _extract(self):
        from . import extract
        try:
            zone = self._zone()
        except ValueError as e:
            messagebox.showerror(TITLE, str(e))
            return
        self._persist()
        profile = self._current_profile()
        interval = float(self.interval.get().split()[0])
        videos = list(self.videos)
        self.cancel.clear()
        self.extraction = None
        self.saved = []
        self.saved_label.set("")
        self.progress["value"] = 0
        self.status.set("Starting… (loading the OCR model)")

        def work():
            return extract.extract(videos, profile, interval, self.cancel, lambda p: self.q.put(("progress", None, p)))

        def done(x):
            self.extraction = x
            self.progress["value"] = 1000
            self.summary.delete("1.0", "end")
            self.summary.insert("1.0", extract.summary(x, zone))
            self.result_frame.grid()
            if x.track.fixes:
                self.save_frame.grid()
                self.status.set("Extraction finished" + (" (cancelled - partial result)" if x.cancelled else ""))
            else:
                self.save_frame.grid_remove()
                self.status.set("No GPS data found - check the camera profile with Test frame")
            self._draw_preview()
        self._run(work, done)

    def _save(self):
        from . import extract
        x = self.extraction
        if not self.out_dir.get():
            self._choose_out()
            if not self.out_dir.get():
                return
        self._persist()
        folder = Path(self.out_dir.get())
        formats = [f for f, v in self.fmt_vars.items() if v.get()]
        zone, combine, ge = self.zone, self.combine.get(), self.ge_extras.get()
        self.status.set("Writing files…")

        def done(saved):
            self.saved = saved
            self.saved_label.set("\n".join("• " + s.path.name for s in saved))
            self.status.set(f"Saved {len(saved)} file(s) to {folder}")
        self._run(lambda: extract.export(x, folder, formats, zone, combine, ge), done)

    def _open_map(self):
        for fmt in (ExportFormat.KMZ, ExportFormat.KML, ExportFormat.GPX):
            hit = next((s for s in self.saved if s.format is fmt), None)
            if hit:
                _open(hit.path)
                return

    def _choose_route(self):
        from . import extract
        path = filedialog.askopenfilename(title="Route file", filetypes=ROUTE_TYPES, initialdir=self.out_dir.get() or None)
        if path:
            self._load_route(path)

    def _load_route(self, path: str):
        from . import extract
        try:
            zone = self._zone()
        except ValueError as e:
            messagebox.showerror(TITLE, str(e))
            return
        self.route_clean = None
        self.route_saved = None
        self.route_status.set("Reading route…")

        def done(c):
            self.route_clean = c
            self.route_preview.grid()
            self.route_summary.grid()
            self.route_summary.delete("1.0", "end")
            self.route_summary.insert("1.0", extract.route_summary(c))
            removed = len(c.route.points) - len(c.track.fixes)
            self.route_status.set("No noise found - the route is already clean" if removed == 0 and c.track.speed_corrected == 0
                                  else "Cleaned - save to keep the result")
            self._draw_route_preview()
        self._route_path = Path(path)
        self._run(lambda: extract.load_route(Path(path), zone), done)

    def _save_route(self):
        from . import extract
        c = self.route_clean
        folder = Path(self.out_dir.get()) if self.out_dir.get() else self._route_path.parent
        zone, ge = self.zone, self.ge_extras.get()
        self.route_status.set("Writing file…")

        def done(s):
            self.route_saved = s
            self.route_status.set(f"Saved {s.path.name} to {s.path.parent}")
        self._run(lambda: extract.save_route(c, folder, zone, ge), done)

    # ---- route preview -----------------------------------------------------------------

    def _draw_preview(self):
        if self.extraction is not None:
            _draw_track(self.preview, self.extraction.track)

    def _draw_route_preview(self):
        if self.route_clean is not None:
            _draw_track(self.route_preview, self.route_clean.track)


def _draw_track(c: Canvas, track) -> None:
    """Route outline without map tiles (equirectangular, north up), green start, red end."""
    c.delete("all")
    pts = track.fixes
    if len(pts) < 2:
        return
    w, h = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
    min_lat, max_lat = min(f.lat for f in pts), max(f.lat for f in pts)
    min_lon, max_lon = min(f.lon for f in pts), max(f.lon for f in pts)
    kx = math.cos(math.radians((min_lat + max_lat) / 2))
    span_x = max((max_lon - min_lon) * kx, 1e-6)
    span_y = max(max_lat - min_lat, 1e-6)
    pad = 14
    scale = min((w - 2 * pad) / span_x, (h - 2 * pad) / span_y)
    ox = (w - span_x * scale) / 2
    oy = (h - span_y * scale) / 2

    def at(f):
        return ox + (f.lon - min_lon) * kx * scale, oy + (max_lat - f.lat) * scale

    for seg in track.segments:
        if len(seg) >= 2:
            c.create_line(*[v for f in seg for v in at(f)], fill="#1a5fd0", width=3)
    for f, color in ((pts[0], "#2E7D32"), (pts[-1], "#C62828")):
        x, y = at(f)
        c.create_oval(x - 6, y - 6, x + 6, y + 6, fill=color, outline="white")


def main() -> int:
    root = Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()
    return 0
