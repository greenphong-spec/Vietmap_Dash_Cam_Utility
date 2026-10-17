"""Tkinter front end (Design Section 9) - Spec Section 6."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
from pathlib import Path
from tkinter import (
    END,
    HORIZONTAL,
    BooleanVar,
    Menu,
    StringVar,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
)
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .core import pipeline
from .core.concat import ffmpeg_version
from .core.config import ConfigError, app_dir, load_config, save_settings
from .core.models import AppConfig, CameraLabel, ClipInfo, RunOptions
from .core.planner import part_key
from .core.progress import ProgressEvent, QueueSink, describe, percent_of
from .core.report import format_duration, format_size, part_label, part_span

log = logging.getLogger("dashcam_merger.gui")

TITLE = f"DashCam Merger {__version__}"
CAMERA_CHOICES = ["Front", "Rear", "Interior", "Skip"]


class CameraMapDialog:
    """Modal camera assignment dialog (Design Section 9.3, FR-2.3)."""

    def __init__(self, parent: Tk, tokens: dict[str, list[ClipInfo]]) -> None:
        self.window = window = Toplevel(parent)
        window.title("Assign cameras")
        window.transient(parent)
        window.grab_set()
        self.result: dict[str, CameraLabel] | None = None
        self.vars: dict[str, StringVar] = {}

        ttk.Label(
            window,
            text="Camera detection was not conclusive. Assign each group:",
        ).grid(row=0, column=0, columnspan=3, padx=10, pady=(10, 6), sticky="w")

        for row, (token, clips) in enumerate(tokens.items(), start=1):
            ttk.Label(window, text=token, font=("TkDefaultFont", 9, "bold")).grid(
                row=row, column=0, padx=10, pady=4, sticky="nw"
            )
            samples = "\n".join(c.path.name for c in clips[:5])
            ttk.Label(window, text=samples, justify="left").grid(
                row=row, column=1, padx=10, pady=4, sticky="w"
            )
            var = StringVar(value="Skip")
            self.vars[token] = var
            ttk.Combobox(
                window, textvariable=var, values=CAMERA_CHOICES, state="readonly", width=12
            ).grid(row=row, column=2, padx=10, pady=4)

        buttons = ttk.Frame(window)
        buttons.grid(row=len(tokens) + 1, column=0, columnspan=3, pady=10)
        ttk.Button(buttons, text="OK", command=self._ok).pack(side="left", padx=6)
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side="left", padx=6)
        window.protocol("WM_DELETE_WINDOW", self._cancel)

    def _ok(self) -> None:
        self.result = {t.upper(): v.get() for t, v in self.vars.items()}  # type: ignore[misc]
        self.window.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.window.destroy()

    def show(self) -> dict[str, CameraLabel] | None:
        self.window.wait_window()
        return self.result


class GuiConfirmer:
    """Bridges the worker thread to dialogs that must run on the Tk main thread."""

    def __init__(self, app: "App") -> None:
        self.app = app

    def confirm_plan(self, summary: str) -> bool:
        return True  # Scan + clicking Merge is the confirmation (FR-2.4)

    def request_camera_map(self, tokens):
        if self.app.camera_map is not None:
            return self.app.camera_map
        answered = threading.Event()
        box: dict[str, object] = {}

        def ask() -> None:
            box["value"] = CameraMapDialog(self.app.root, tokens).show()
            answered.set()

        self.app.root.after(0, ask)
        answered.wait()
        mapping = box.get("value")
        self.app.camera_map = mapping  # type: ignore[assignment]
        return mapping

    def confirm_delete(self, clips: list[ClipInfo]) -> bool:
        answered = threading.Event()
        box: dict[str, object] = {}

        def ask() -> None:
            box["value"] = messagebox.askyesno(
                TITLE,
                f"Permanently delete {len(clips)} original clip file(s) that were "
                "successfully merged?\n\nThis cannot be undone.",
                icon="warning",
            )
            answered.set()

        self.app.root.after(0, ask)
        answered.wait()
        return bool(box.get("value"))


def _minutes_text(seconds: float) -> str:
    """900 -> '15', 90 -> '1.5'."""
    return f"{seconds / 60:g}"


CHECKED, UNCHECKED = "☑", "☐"


class App:
    def __init__(self, cfg: AppConfig, config_path: Path | None = None) -> None:
        self.cfg = cfg
        self.config_path = config_path  # None: trip settings are not saved (tests)
        self.camera_map: dict[str, CameraLabel] | None = None
        self.cancel = threading.Event()
        self.queue: "queue.Queue[ProgressEvent]" = queue.Queue()
        self.worker: threading.Thread | None = None
        self.output_dir: Path | None = None
        self.bars: dict[str, tuple[ttk.Progressbar, ttk.Label]] = {}
        self._auto_output = True
        self._dry_run = True  # kind of the run in progress: a scan fills the table
        # FR-3.6: table row id -> planner.part_key, and the keys ticked for export.
        self.row_keys: dict[str, str] = {}
        self.export_keys: set[str] = set()

        self.root = root = Tk()
        root.title(TITLE)
        root.geometry("760x560")
        root.minsize(640, 480)
        self._build_menu()
        self._build_widgets()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._poll)

    # ---------------------------------------------------------------- layout
    def _build_menu(self) -> None:
        menubar = Menu(self.root)
        file_menu = Menu(menubar, tearoff=0)
        file_menu.add_command(
            label="Open config.json", command=lambda: self._open(app_dir() / "config.json")
        )
        file_menu.add_command(
            label="Open logs folder", command=lambda: self._open(app_dir() / "logs")
        )
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)
        help_menu = Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self._about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.config(menu=menubar)

    def _build_widgets(self) -> None:
        root = self.root
        root.columnconfigure(1, weight=1)
        root.rowconfigure(6, weight=1)

        self.input_var = StringVar()
        self.output_var = StringVar()
        ttk.Label(root, text="Input folder").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        ttk.Entry(root, textvariable=self.input_var, width=70).grid(
            row=0, column=1, padx=4, pady=6, sticky="ew"
        )
        ttk.Button(root, text="Browse...", command=self._browse_input).grid(
            row=0, column=2, padx=8, pady=6
        )
        ttk.Label(root, text="Output folder").grid(row=1, column=0, padx=8, pady=6, sticky="w")
        ttk.Entry(root, textvariable=self.output_var).grid(
            row=1, column=1, padx=4, pady=6, sticky="ew"
        )
        ttk.Button(root, text="Browse...", command=self._browse_output).grid(
            row=1, column=2, padx=8, pady=6
        )

        options = ttk.Frame(root)
        options.grid(row=2, column=0, columnspan=3, padx=8, pady=4, sticky="ew")
        self.scan_button = ttk.Button(options, text="Scan", command=self._scan)
        self.scan_button.pack(side="left", padx=(0, 12))
        self.split_var = BooleanVar(value=self.cfg.split_on_gap)
        self.reencode_var = BooleanVar()
        self.overwrite_var = BooleanVar()
        self.delete_source_var = BooleanVar()
        ttk.Checkbutton(
            options, text="Re-encode if formats differ (slow)", variable=self.reencode_var
        ).pack(side="left")
        ttk.Checkbutton(options, text="Overwrite existing outputs", variable=self.overwrite_var).pack(
            side="left"
        )
        ttk.Checkbutton(
            options,
            text="Delete original clips after successful merge (cannot be undone)",
            variable=self.delete_source_var,
        ).pack(side="left", padx=8)
        # Second options row: at the 760 px default width everything does not
        # fit on one line, and a cut-off control is worse than a second row.
        options2 = ttk.Frame(root)
        options2.grid(row=7, column=0, columnspan=3, padx=8, pady=(0, 4), sticky="w")
        # Trip settings sit together; they are remembered in config.json (FR-10.3).
        ttk.Checkbutton(options2, text="Split into trips: new trip after a break of more than",
                        variable=self.split_var).pack(side="left")
        self.trip_gap_var = StringVar(value=_minutes_text(self.cfg.gap_split_seconds))
        ttk.Spinbox(
            options2, from_=1, to=1440, increment=1, width=5, textvariable=self.trip_gap_var
        ).pack(side="left", padx=4)
        ttk.Label(options2, text="min").pack(side="left")
        ttk.Label(options2, text="Max part size (GB, 0 = unlimited)").pack(side="left", padx=(24, 4))
        self.max_gb_var = StringVar(value="0")
        ttk.Spinbox(
            options2, from_=0, to=64, increment=1, width=5, textvariable=self.max_gb_var
        ).pack(side="left")

        # One row per output file: with "Split into trips" that is one row per trip.
        # The Export column is a checkbox: click a row to include or leave it out (FR-3.6).
        columns = ("export", "camera", "trip", "files", "duration", "size", "start", "end", "codec")
        tree_frame = ttk.Frame(root)
        tree_frame.grid(row=3, column=0, columnspan=3, padx=8, pady=6, sticky="ew")
        tree_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=8)
        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=yscroll.set)
        for key, heading, width in [
            ("export", "Export", 50),
            ("camera", "Camera", 60),
            ("trip", "Trip", 90),
            ("files", "Files", 50),
            ("duration", "Duration", 70),
            ("size", "Size", 80),
            ("start", "Start", 135),
            ("end", "End", 70),
            ("codec", "Codec", 120),
        ]:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, anchor="center" if key == "export" else "w")
        self.tree.grid(row=0, column=0, sticky="ew")
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<space>", self._on_tree_space)

        actions = ttk.Frame(root)
        actions.grid(row=4, column=0, columnspan=3, padx=8, pady=4, sticky="w")
        self.merge_button = ttk.Button(actions, text="Merge", command=self._merge, state="disabled")
        self.merge_button.pack(side="left")
        self.cancel_button = ttk.Button(
            actions, text="Cancel", command=self._cancel_run, state="disabled"
        )
        self.cancel_button.pack(side="left", padx=8)
        self.open_button = ttk.Button(
            actions, text="Open output folder", command=self._open_output, state="disabled"
        )
        self.open_button.pack(side="left")
        ttk.Button(actions, text="Select all", command=lambda: self._select_all(True)).pack(
            side="left", padx=(24, 4)
        )
        ttk.Button(actions, text="Select none", command=lambda: self._select_all(False)).pack(
            side="left"
        )
        self.selection_var = StringVar(value="")
        ttk.Label(actions, textvariable=self.selection_var).pack(side="left", padx=8)

        self.progress_frame = ttk.Frame(root)
        self.progress_frame.grid(row=5, column=0, columnspan=3, padx=8, pady=4, sticky="ew")
        self.progress_frame.columnconfigure(1, weight=1)

        # The report is a fixed-width table: no wrapping, monospace, and a
        # horizontal scrollbar so the columns stay readable.
        log_frame = ttk.Frame(root)
        log_frame.grid(row=6, column=0, columnspan=3, padx=8, pady=(4, 0), sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_pane = ScrolledText(
            log_frame, height=10, state="disabled", wrap="none", font=("Consolas", 9)
        )
        self.log_pane.grid(row=0, column=0, sticky="nsew")
        xscroll = ttk.Scrollbar(
            log_frame, orient=HORIZONTAL, command=self.log_pane.xview
        )
        xscroll.grid(row=1, column=0, sticky="ew")
        self.log_pane.configure(xscrollcommand=xscroll.set)

    # ------------------------------------------------------------- helpers
    def _log(self, text: str) -> None:
        self.log_pane.configure(state="normal")
        self.log_pane.insert(END, text.rstrip() + "\n")
        self.log_pane.see(END)
        self.log_pane.configure(state="disabled")

    @staticmethod
    def _open(path: Path) -> None:
        try:
            if os.name == "nt":
                os.startfile(path)  # noqa: S606 - opening a folder for the user
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except OSError as e:
            messagebox.showerror(TITLE, f"Cannot open {path}:\n{e}")

    def _browse_input(self) -> None:
        chosen = filedialog.askdirectory(title="Choose the folder with your dashcam clips")
        if not chosen:
            return
        self.input_var.set(chosen)
        if self._auto_output or not self.output_var.get():
            self.output_var.set(str(Path(chosen) / self.cfg.output_subfolder))
            self._auto_output = True

    def _browse_output(self) -> None:
        chosen = filedialog.askdirectory(title="Choose where the merged files go")
        if chosen:
            self.output_var.set(chosen)
            self._auto_output = False

    def _options(self, dry_run: bool) -> RunOptions:
        try:
            max_gb = float(self.max_gb_var.get() or 0)
        except ValueError:
            max_gb = 0.0
        output = self.output_var.get().strip()
        return RunOptions(
            input_dir=Path(self.input_var.get().strip()),
            output_dir=Path(output) if output else None,
            camera_map=self.camera_map,
            split_on_gap=self.split_var.get(),
            trip_gap_minutes=self._trip_gap_minutes(),
            max_part_gb=max_gb,
            reencode=self.reencode_var.get(),
            overwrite=self.overwrite_var.get(),
            dry_run=dry_run,
            assume_yes=True,
            delete_source=self.delete_source_var.get() and not dry_run,
            # A scan plans everything; a merge exports only the ticked rows (FR-3.6).
            selected_parts=None if dry_run or not self.row_keys else set(self.export_keys),
        )

    def _trip_gap_minutes(self) -> float:
        """The trip gap box; anything not a number above 0 falls back to config.json."""
        try:
            minutes = float(self.trip_gap_var.get())
        except ValueError:
            minutes = 0.0
        if minutes <= 0:
            minutes = self.cfg.gap_split_seconds / 60
            self.trip_gap_var.set(_minutes_text(self.cfg.gap_split_seconds))
        return minutes

    def _save_trip_settings(self) -> None:
        """Remember the trip settings in config.json for the next start (FR-10.3)."""
        seconds = int(round(self._trip_gap_minutes() * 60))
        split = self.split_var.get()
        if self.config_path is None or (
            seconds == self.cfg.gap_split_seconds and split == self.cfg.split_on_gap
        ):
            return
        try:
            save_settings(self.config_path, {"gap_split_seconds": seconds, "split_on_gap": split})
        except (OSError, ConfigError) as e:
            self._log(f"  ! Trip settings not saved to config.json: {e}")
            return
        self.cfg.gap_split_seconds = seconds
        self.cfg.split_on_gap = split

    # -------------------------------------------------------------- actions
    def _start(self, dry_run: bool) -> None:
        if not self.input_var.get().strip():
            messagebox.showwarning(TITLE, "Choose an input folder first.")
            return
        if self.worker and self.worker.is_alive():
            return
        self.cancel.clear()
        self._dry_run = dry_run
        self._save_trip_settings()
        options = self._options(dry_run)
        self.output_dir = options.output_dir
        self.scan_button.configure(state="disabled")
        self.merge_button.configure(state="disabled")
        self.cancel_button.configure(state="normal" if not dry_run else "disabled")

        def work() -> None:
            sink = QueueSink(self.queue)
            try:
                pipeline.run(self.cfg, options, GuiConfirmer(self), sink, self.cancel)
            except pipeline.PipelineError as e:
                sink.emit(ProgressEvent(kind="error", message=str(e)))
            except Exception as e:  # never let the worker die silently
                log.exception("Unexpected failure")
                sink.emit(ProgressEvent(kind="error", message=f"Unexpected error: {e}"))
            finally:
                sink.emit(ProgressEvent(kind="log", message="__DONE__"))

        self.worker = threading.Thread(target=work, name="pipeline", daemon=True)
        self.worker.start()

    def _scan(self) -> None:
        self.camera_map = None
        for row in self.tree.get_children():
            self.tree.delete(row)
        self._log("Scanning...")
        self._start(dry_run=True)

    def _merge(self) -> None:
        if self.row_keys and not self.export_keys:
            messagebox.showwarning(TITLE, "Tick at least one trip to export.")
            return
        for child in self.progress_frame.winfo_children():
            child.destroy()
        self.bars.clear()
        self._log("Merging...")
        self._start(dry_run=False)

    def _cancel_run(self) -> None:
        self.cancel.set()
        self.cancel_button.configure(text="Cancelling...", state="disabled")

    def _open_output(self) -> None:
        if self.output_dir:
            self._open(self.output_dir)

    def _about(self) -> None:
        messagebox.showinfo(
            TITLE,
            f"DashCam Merger {__version__}\n\n{ffmpeg_version(self.cfg.ffmpeg_path)}\n\n"
            "Merges dashcam clips losslessly. Your original files are never moved "
            "or changed, and are only ever deleted if you explicitly enable "
            "'Delete original clips after successful merge' and confirm it.",
        )

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            self.cancel.set()
            self.worker.join(timeout=10)
        self.root.destroy()

    # --------------------------------------------------------- queue polling
    def _fill_tree(self, plan) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        self.row_keys.clear()
        # Sorted by trip so the Front and Rear of one trip sit together; all ticked.
        for part in sorted(plan.parts, key=lambda p: (p.trip_index, p.trip_part)):
            signatures = {c.probe.signature for c in part.clips if c.probe is not None}
            if len(signatures) == 1:
                sig = next(iter(signatures))
                codec = f"{sig.v_codec} {sig.width}x{sig.height}"
            else:
                codec = "mixed"
            start, end = part_span(part)
            row = self.tree.insert(
                "",
                END,
                values=(
                    CHECKED,
                    part.camera,
                    part_label(part) or "-",
                    len(part.clips),
                    format_duration(part.expected_duration),
                    format_size(part.expected_size),
                    start,
                    end,
                    codec,
                ),
            )
            self.row_keys[row] = part_key(part)
        self.export_keys = set(self.row_keys.values())
        if plan.unclassified:
            self.tree.insert(
                "", END, values=("", "Unclassified", "", len(plan.unclassified), "", "", "", "", "")
            )
        self._update_selection_label()

    # ------------------------------------------------- export selection (FR-3.6)
    def _toggle(self, row: str) -> None:
        key = self.row_keys.get(row)
        if key is None:
            return  # the Unclassified row
        if key in self.export_keys:
            self.export_keys.discard(key)
            self.tree.set(row, "export", UNCHECKED)
        else:
            self.export_keys.add(key)
            self.tree.set(row, "export", CHECKED)
        self._update_selection_label()

    def _on_tree_click(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) == "cell":
            row = self.tree.identify_row(event.y)
            if row:
                self._toggle(row)

    def _on_tree_space(self, _event) -> str:
        for row in self.tree.selection():
            self._toggle(row)
        return "break"

    def _select_all(self, on: bool) -> None:
        self.export_keys = set(self.row_keys.values()) if on else set()
        for row in self.row_keys:
            self.tree.set(row, "export", CHECKED if on else UNCHECKED)
        self._update_selection_label()

    def _update_selection_label(self) -> None:
        if not self.row_keys:
            self.selection_var.set("")
            return
        self.selection_var.set(f"Export {len(self.export_keys)} of {len(self.row_keys)} files")
        if self.worker is None or not self.worker.is_alive():
            self.merge_button.configure(state="normal" if self.export_keys else "disabled")

    def _bar_for(self, part) -> tuple[ttk.Progressbar, ttk.Label]:
        key = f"{part.camera}-{part.part_index}"
        if key not in self.bars:
            row = len(self.bars)
            ttk.Label(self.progress_frame, text=key).grid(row=row, column=0, sticky="w", padx=4)
            bar = ttk.Progressbar(self.progress_frame, orient=HORIZONTAL, length=400, maximum=100)
            bar.grid(row=row, column=1, sticky="ew", padx=4, pady=2)
            label = ttk.Label(self.progress_frame, text="")
            label.grid(row=row, column=2, sticky="w", padx=4)
            self.bars[key] = (bar, label)
        return self.bars[key]

    def _poll(self) -> None:
        try:
            while True:
                ev = self.queue.get_nowait()
                self._handle(ev)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _handle(self, ev: ProgressEvent) -> None:
        if ev.kind == "log" and ev.message == "__DONE__":
            self.scan_button.configure(state="normal")
            self.cancel_button.configure(text="Cancel", state="disabled")
            return
        if ev.kind == "plan" and ev.plan is not None and not self._dry_run:
            return  # a merge re-plans only the ticked rows; the table keeps the full scan
        if ev.kind == "plan" and ev.plan is not None:
            self._fill_tree(ev.plan)
            self.output_dir = ev.plan.output_dir
            self.merge_button.configure(state="normal")
            self._log(f"Scan complete: {len(ev.plan.parts)} output file(s) planned.")
            for warning in ev.plan.warnings:
                self._log(f"  ! {warning}")
        elif ev.kind == "progress" and ev.part is not None:
            bar, label = self._bar_for(ev.part)
            bar.configure(value=percent_of(ev))
            label.configure(text=describe(ev))
        elif ev.kind == "part_start":
            self._log(ev.message)
        elif ev.kind == "part_done" and ev.result is not None:
            bar, label = self._bar_for(ev.result.part)
            if ev.result.status == "PASS":
                bar.configure(value=100)
            label.configure(text=ev.result.status)
            name = ev.result.final_path.name if ev.result.final_path else "-"
            self._log(f"{ev.result.status}: {name}")
        elif ev.kind == "report":
            self._log("")
            self._log(ev.message)
            self.open_button.configure(state="normal")
        elif ev.kind == "dry_run":
            self._log("Dry run: nothing was written.")
        elif ev.kind == "error":
            self._log(f"ERROR: {ev.message}")
            messagebox.showerror(TITLE, ev.message)
        elif ev.kind == "log" and ev.message:
            self._log(ev.message)

    def run(self) -> int:
        self.root.mainloop()
        return 0


def main() -> int:
    try:
        cfg = load_config()
    except ConfigError as e:
        Tk().withdraw()
        messagebox.showerror(TITLE, str(e))
        return 2
    return App(cfg, app_dir() / "config.json").run()
