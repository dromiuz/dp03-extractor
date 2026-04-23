"""DP-03 Extractor main window.

Tkinter UI modeled after the ZOOM R16 project manager:

  +-------------------------------------------------------------+
  | DP-03 Extractor                                             |
  | [Open card image...]  [Use SD card ▼]   Card: /path/to/img  |
  +-------------------------------------------------------------+
  | #   Name       Tracks   Duration   Size                     |
  |  0  SONG001       2      24.6s     2.1 MB                   |
  |  1  SONG002       4      3:42      42 MB                    |
  |  … (selectable, multi-select)                               |
  +-------------------------------------------------------------+
  | Output: /Users/.../Desktop    [Choose...]                   |
  | [Extract selected]  [Extract all]   [Cancel]                |
  | Status: Extracting SONG116 · Track2          [###       ]   |
  +-------------------------------------------------------------+
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import (BOTH, BOTTOM, END, LEFT, RIGHT, W, E, N, S, X, Y,
                     BooleanVar, StringVar, Tk, Toplevel, filedialog,
                     messagebox, ttk)
from typing import Callable

from .audio_engine import AVAILABLE as AUDIO_AVAILABLE
from .extract_worker import ExtractJob, ExtractionRunner
from .imaging import (ERROR_CANCELLED, ERROR_DEVICE_BUSY, ERROR_TCC_DENIED,
                      ImagingJob, ImagingProgress, default_image_path,
                      is_supported as imaging_supported)
from .mixer_window import MixerWindow, show_audio_unavailable_dialog
from .project_scanner import ProjectSummary, scan_card
from .sd_detector import detect_sd_cards, is_readable


APP_TITLE = "DP-03 Extractor"
DEFAULT_OUTPUT_NAME = "DP-03 Extracts"

# ---------- dark minimal palette ----------
_BG_BASE = "#0a0b0d"
_BG_PANEL = "#13151a"
_BG_SURFACE = "#1c1e24"
_BG_HOVER = "#252830"
_ACCENT = "#c8844a"
_ACCENT_HOVER = "#daa06d"
_FG_PRIMARY = "#d4d4d4"
_FG_SECONDARY = "#6b6e75"
_FG_DIM = "#3a3d44"
_BORDER = "#1a1c22"
_SELECTION = "#1a2a3a"


class ScanDialog(Toplevel):
    """Modal progress dialog while scanning a card image."""

    def __init__(self, parent: Tk, image_path: Path) -> None:
        super().__init__(parent)
        self.configure(bg=_BG_BASE)
        self.title("Scanning card")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.geometry("+{x}+{y}".format(
            x=parent.winfo_rootx() + 120,
            y=parent.winfo_rooty() + 120,
        ))

        self._stage = StringVar(value="Starting scan…")
        self._stage_text = "Starting scan…"
        self._fraction = 0.0
        ttk.Label(self, textvariable=self._stage, width=50, anchor=W).pack(
            padx=16, pady=(16, 6), fill=X)

        self._progress = ttk.Progressbar(self, length=360, mode="determinate",
                                         maximum=1000)
        self._progress.pack(padx=16, pady=(0, 16))

        self.result: list[ProjectSummary] | None = None
        self.error: str | None = None
        self._done = False

        self._thread = threading.Thread(
            target=self._worker, args=(image_path,), daemon=True)
        self._thread.start()
        self.after(60, self._poll)

    def _worker(self, image_path: Path) -> None:
        try:
            projects = scan_card(image_path, progress=self._on_progress)
            self.result = projects
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        finally:
            self._done = True

    def _on_progress(self, stage: str, fraction: float) -> None:
        # Called from the worker thread — stash plainly so the main
        # thread's _poll is the only one touching Tk vars. Tk is not
        # thread-safe; cross-thread .set() can crash on some platforms.
        self._stage_text = stage
        self._fraction = max(0.0, min(1.0, fraction))

    def _poll(self) -> None:
        try:
            self._stage.set(self._stage_text)
            self._progress["value"] = self._fraction * 1000
        except tk.TclError:
            return
        if self._done:
            try:
                self.grab_release()
                self.destroy()
            except tk.TclError:
                pass
            return
        self.after(80, self._poll)


class ImagingDialog(Toplevel):
    """Modal progress dialog while dd runs (admin prompt or Terminal)."""

    def __init__(self, parent: Tk, device: str, out_path: Path, *,
                 mode: str = ImagingJob.MODE_ADMIN_PROMPT) -> None:
        super().__init__(parent)
        self.configure(bg=_BG_BASE)
        self.title("Imaging SD card")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.geometry("+{x}+{y}".format(
            x=parent.winfo_rootx() + 120,
            y=parent.winfo_rooty() + 120,
        ))

        intro = (f"Reading {device}  →\n"
                 f"{out_path}")
        ttk.Label(self, text=intro, width=56, anchor=W, justify="left"
                  ).pack(padx=16, pady=(16, 4), fill=X)

        if mode == ImagingJob.MODE_TERMINAL:
            initial = ("Opening Terminal…\n"
                       "Type your Mac password there if prompted.")
        else:
            initial = ("Waiting for admin password…\n"
                       "(a macOS dialog should appear)")
        self._stage = StringVar(value=initial)
        ttk.Label(self, textvariable=self._stage, width=56,
                  anchor=W, justify="left", foreground=_FG_SECONDARY
                  ).pack(padx=16, pady=(0, 8), fill=X)

        self._progress = ttk.Progressbar(self, length=420, mode="determinate",
                                         maximum=1000)
        self._progress.pack(padx=16, pady=(0, 14))

        self._cancel_btn = ttk.Button(self, text="Cancel",
                                      command=self._on_cancel)
        self._cancel_btn.pack(pady=(0, 14))

        self.result_path: Path | None = None
        self.error: str | None = None
        self.error_kind: str | None = None

        self._job = ImagingJob(device, out_path, mode=mode)
        self._job.start()
        self.after(120, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _on_cancel(self) -> None:
        self._job.cancel()
        # dd is running as root under osascript — we can't kill it from here.
        # Best effort: close the dialog and let it finish in the background.
        self.error = "Cancelled."
        self.error_kind = ERROR_CANCELLED
        self.grab_release()
        self.destroy()

    def _poll(self) -> None:
        for event, payload in self._job.poll():
            if event == ImagingJob.EVENT_STARTED:
                total = int(payload or 0)
                if total:
                    self._stage.set(
                        f"Imaging {total / (1024 * 1024):.0f} MB…")
                else:
                    self._stage.set(
                        "Imaging (size unknown — running in background)…")
            elif event == ImagingJob.EVENT_PROGRESS:
                prog: ImagingProgress = payload  # type: ignore[assignment]
                self._progress["value"] = prog.fraction * 1000
                self._stage.set(prog.stage)
            elif event == ImagingJob.EVENT_DONE:
                self.result_path = payload  # type: ignore[assignment]
                self._progress["value"] = 1000
                self.grab_release()
                self.destroy()
                return
            elif event == ImagingJob.EVENT_ERROR:
                if isinstance(payload, dict):
                    self.error_kind = str(payload.get("kind", ""))
                    self.error = (payload.get("stderr")
                                  or f"dd exited with code "
                                     f"{payload.get('returncode')}")
                else:
                    self.error_kind = None
                    self.error = str(payload)
                self.grab_release()
                self.destroy()
                return
        self.after(200, self._poll)


class ExtractorApp:
    def __init__(self) -> None:
        self.root = Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("920x620")
        self.root.minsize(720, 480)

        self.image_path: Path | None = None
        self.projects: list[ProjectSummary] = []
        self.output_dir: Path = Path.home() / "Desktop" / DEFAULT_OUTPUT_NAME
        self.runner: ExtractionRunner | None = None

        self._build_ui()
        self._refresh_buttons()

    # -------------------------- UI construction --------------------------

    def _build_ui(self) -> None:
        style = ttk.Style()
        # Always use "clam" for full color control — native themes
        # ignore most color options.
        if "clam" in style.theme_names():
            style.theme_use("clam")

        self.root.configure(bg=_BG_BASE)

        # ---------- comprehensive dark ttk style ----------
        style.configure(".",
                        background=_BG_BASE,
                        foreground=_FG_PRIMARY,
                        borderwidth=0,
                        focusthickness=0,
                        focuscolor=_ACCENT)
        style.configure("TFrame", background=_BG_BASE)
        style.configure("TLabel",
                        background=_BG_BASE,
                        foreground=_FG_PRIMARY)
        style.configure("Dim.TLabel",
                        background=_BG_BASE,
                        foreground=_FG_SECONDARY)
        style.configure("TButton",
                        background=_BG_SURFACE,
                        foreground=_FG_PRIMARY,
                        padding=(12, 5),
                        borderwidth=0)
        style.map("TButton",
                  background=[("active", _BG_HOVER),
                              ("pressed", _BG_HOVER),
                              ("disabled", _BG_PANEL)])
        style.configure("Accent.TButton",
                        background=_ACCENT,
                        foreground="#111111",
                        padding=(14, 5))
        style.map("Accent.TButton",
                  background=[("active", _ACCENT_HOVER),
                              ("pressed", _ACCENT_HOVER)])
        style.configure("TMenubutton",
                        background=_BG_SURFACE,
                        foreground=_FG_PRIMARY,
                        padding=(12, 5),
                        borderwidth=0)
        style.map("TMenubutton",
                  background=[("active", _BG_HOVER),
                              ("disabled", _BG_PANEL)])
        style.configure("Treeview",
                        background=_BG_PANEL,
                        foreground=_FG_PRIMARY,
                        fieldbackground=_BG_PANEL,
                        borderwidth=0,
                        rowheight=30)
        style.configure("Treeview.Heading",
                        background=_BG_SURFACE,
                        foreground=_FG_SECONDARY,
                        borderwidth=0,
                        padding=(8, 4))
        style.map("Treeview.Heading",
                  background=[("active", _BG_HOVER)])
        style.map("Treeview",
                  background=[("selected", _SELECTION)],
                  foreground=[("selected", "#ffffff")])
        style.configure("TEntry",
                        fieldbackground=_BG_SURFACE,
                        foreground=_FG_PRIMARY,
                        insertcolor=_FG_PRIMARY,
                        borderwidth=1,
                        padding=(6, 4))
        style.configure("TProgressbar",
                        background=_ACCENT,
                        troughcolor="#08090b",
                        borderwidth=0,
                        thickness=6)
        style.configure("Vertical.TScrollbar",
                        background=_BG_SURFACE,
                        troughcolor=_BG_BASE,
                        borderwidth=0,
                        arrowsize=12)
        style.map("Vertical.TScrollbar",
                  background=[("active", _BG_HOVER)])

        # ---------- header / branding ----------
        header = ttk.Frame(self.root, padding=(14, 10, 14, 0))
        header.pack(fill=X)
        tk.Label(header, text="DP-03", bg=_BG_BASE, fg=_ACCENT,
                 font=("Helvetica", 14, "bold")).pack(side=LEFT)
        tk.Label(header, text="  Extractor", bg=_BG_BASE, fg=_FG_SECONDARY,
                 font=("Helvetica", 14)).pack(side=LEFT)

        # thin accent rule
        tk.Frame(self.root, bg=_ACCENT, height=1).pack(
            fill=X, padx=14, pady=(6, 0))

        # ---------- toolbar ----------
        top = ttk.Frame(self.root, padding=(14, 10, 14, 0))
        top.pack(fill=X)

        ttk.Button(top, text="Open image…",
                   command=self._on_open_image).pack(side=LEFT)

        self._sd_menu = ttk.Menubutton(top, text="SD card \u25be",
                                       state="disabled")
        self._sd_menu.pack(side=LEFT, padx=(6, 0))
        self._sd_tk_menu = tk.Menu(self._sd_menu, tearoff=False,
                                   bg=_BG_SURFACE, fg=_FG_PRIMARY,
                                   activebackground=_ACCENT,
                                   activeforeground="#111111",
                                   borderwidth=0)
        self._sd_menu["menu"] = self._sd_tk_menu

        ttk.Button(top, text="Rescan",
                   command=self._refresh_sd_menu).pack(side=LEFT, padx=(6, 0))

        self._card_label_var = StringVar(value="No card loaded")
        ttk.Label(top, textvariable=self._card_label_var,
                  style="Dim.TLabel").pack(side=LEFT, padx=(16, 0))

        # ---------- project list ----------
        mid = ttk.Frame(self.root, padding=(14, 10, 14, 0))
        mid.pack(fill=BOTH, expand=True)

        columns = ("idx", "name", "tracks", "duration")
        self._tree = ttk.Treeview(mid, columns=columns, show="headings",
                                  selectmode="extended")
        self._tree.heading("idx", text="#")
        self._tree.heading("name", text="Name")
        self._tree.heading("tracks", text="Tracks")
        self._tree.heading("duration", text="Duration")
        self._tree.column("idx", width=50, anchor=E, stretch=False)
        self._tree.column("name", width=280, anchor=W)
        self._tree.column("tracks", width=70, anchor=E, stretch=False)
        self._tree.column("duration", width=100, anchor=E, stretch=False)

        vsb = ttk.Scrollbar(mid, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)

        self._tree.grid(row=0, column=0, sticky=N + S + E + W)
        vsb.grid(row=0, column=1, sticky=N + S)
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)

        self._tree.bind("<<TreeviewSelect>>", lambda _e: self._refresh_buttons())

        # ---------- output row ----------
        out_frame = ttk.Frame(self.root, padding=(14, 8, 14, 4))
        out_frame.pack(fill=X)

        ttk.Label(out_frame, text="Output:", style="Dim.TLabel").pack(
            side=LEFT)
        self._out_var = StringVar(value=str(self.output_dir))
        ttk.Entry(out_frame, textvariable=self._out_var, width=48).pack(
            side=LEFT, padx=(6, 6), fill=X, expand=True)
        ttk.Button(out_frame, text="Choose…",
                   command=self._on_choose_output).pack(side=LEFT)

        # ---------- action row ----------
        action = ttk.Frame(self.root, padding=(14, 4, 14, 4))
        action.pack(fill=X)
        self._btn_extract_selected = ttk.Button(
            action, text="Extract selected",
            command=self._on_extract_selected)
        self._btn_extract_selected.pack(side=LEFT)
        self._btn_extract_all = ttk.Button(
            action, text="Extract all",
            command=self._on_extract_all)
        self._btn_extract_all.pack(side=LEFT, padx=(6, 0))
        self._btn_cancel = ttk.Button(
            action, text="Cancel", command=self._on_cancel,
            state="disabled")
        self._btn_cancel.pack(side=LEFT, padx=(6, 0))

        self._btn_mixer = ttk.Button(
            action, text="Open mixer\u2026",
            style="Accent.TButton",
            command=self._on_open_mixer)
        self._btn_mixer.pack(side=LEFT, padx=(14, 0))

        self._reveal_btn = ttk.Button(
            action, text="Reveal output",
            command=self._on_reveal_output)
        self._reveal_btn.pack(side=RIGHT)

        # ---------- status bar ----------
        status_bar = tk.Frame(self.root, bg=_BG_PANEL, padx=14, pady=6)
        status_bar.pack(fill=X, side=BOTTOM)
        self._status_var = StringVar(
            value="Ready — open a card image to begin.")
        tk.Label(status_bar, textvariable=self._status_var,
                 bg=_BG_PANEL, fg=_FG_SECONDARY,
                 font=("Helvetica", 10)).pack(side=LEFT, padx=(0, 8))
        self._progress_bar = ttk.Progressbar(
            status_bar, mode="determinate", maximum=1000, length=200)
        self._progress_bar.pack(side=RIGHT)

        self._refresh_sd_menu()

    # ---------------------- event handlers ----------------------

    def _on_open_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose DP-03 card image",
            filetypes=[("Card image", "*.img *.IMG *.dmg"),
                       ("All files", "*.*")],
        )
        if not path:
            return
        self._load_card(Path(path))

    def _on_choose_output(self) -> None:
        path = filedialog.askdirectory(
            title="Choose output folder",
            initialdir=str(self.output_dir.parent if self.output_dir else Path.home()),
        )
        if not path:
            return
        self.output_dir = Path(path)
        self._out_var.set(str(self.output_dir))

    def _on_extract_selected(self) -> None:
        selected = [self.projects[int(self._tree.item(i, "values")[0])]
                    for i in self._tree.selection()]
        if not selected:
            messagebox.showinfo(APP_TITLE, "Select one or more projects first.")
            return
        self._start_extraction(selected)

    def _on_extract_all(self) -> None:
        with_audio = [p for p in self.projects if p.track_count > 0]
        if not with_audio:
            messagebox.showinfo(APP_TITLE, "No projects with audio were found.")
            return
        if not messagebox.askyesno(
                APP_TITLE,
                f"Extract all {len(with_audio)} projects with audio?\n"
                f"Output: {self.output_dir}"):
            return
        self._start_extraction(with_audio)

    def _on_cancel(self) -> None:
        if self.runner:
            self.runner.cancel()
            self._status_var.set("Cancelling…")

    def _on_open_mixer(self) -> None:
        if not self.image_path:
            messagebox.showinfo(APP_TITLE, "Load a card image or SD card first.")
            return
        selected = self._tree.selection()
        if not selected:
            messagebox.showinfo(APP_TITLE,
                                "Select a single project to mix.")
            return
        if len(selected) > 1:
            messagebox.showinfo(APP_TITLE,
                                "Open the mixer for one project at a time.")
            return
        project = self.projects[int(self._tree.item(selected[0], "values")[0])]
        if project.track_count == 0:
            messagebox.showinfo(APP_TITLE, "That project has no audio tracks.")
            return
        if not AUDIO_AVAILABLE:
            show_audio_unavailable_dialog(self.root)
            return
        self.output_dir = Path(self._out_var.get())
        MixerWindow(self.root, self.image_path, project, self.output_dir)

    def _on_reveal_output(self) -> None:
        path = self.output_dir
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            os.system(f"open {str(path)!r}")
        elif sys.platform.startswith("linux"):
            os.system(f"xdg-open {str(path)!r}")
        else:
            os.startfile(str(path))  # type: ignore[attr-defined]

    # ---------------------- loading + scanning ----------------------

    def _load_card(self, image_path: Path) -> None:
        if not image_path.exists():
            messagebox.showerror(APP_TITLE, f"File not found:\n{image_path}")
            return
        dialog = ScanDialog(self.root, image_path)
        self.root.wait_window(dialog)
        if dialog.error:
            messagebox.showerror(APP_TITLE, f"Scan failed:\n\n{dialog.error}")
            return
        self.image_path = image_path
        self.projects = dialog.result or []
        self._card_label_var.set(f"Card: {image_path.name}  "
                                 f"({len(self.projects)} projects, "
                                 f"{sum(p.track_count > 0 for p in self.projects)} with audio)")
        self._populate_tree()
        audio_count = sum(1 for p in self.projects if p.track_count > 0)
        self._status_var.set(
            f"Found {len(self.projects)} projects ({audio_count} with audio). "
            f"Select projects, then Extract.")
        self._refresh_buttons()

    def _populate_tree(self) -> None:
        self._tree.delete(*self._tree.get_children())
        for project in self.projects:
            size_mb = sum(m.declared_bytes for m in project.masters) / (1024 * 1024)
            self._tree.insert(
                "", END,
                values=(project.project_index,
                        project.name or "—",
                        project.track_count,
                        project.display_duration if project.track_count else "—"),
                tags=("has_audio",) if project.track_count > 0 else ("empty",),
            )
        self._tree.tag_configure("empty", foreground="#4a4d55")

    # ---------------------- SD card detection ----------------------

    def _refresh_sd_menu(self) -> None:
        self._sd_tk_menu.delete(0, "end")
        cards = detect_sd_cards()
        if not cards:
            self._sd_menu.configure(state="disabled")
            return
        self._sd_menu.configure(state="normal")
        for card in cards:
            self._sd_tk_menu.add_command(
                label=card.display_name,
                command=lambda c=card: self._on_pick_sd(c),
            )

    def _on_pick_sd(self, card) -> None:
        if is_readable(card.raw_device):
            self._load_card(Path(card.raw_device))
            return

        # Raw read blocked. On macOS we can fix this with a GUI admin
        # prompt + dd; Windows needs the user to re-launch elevated.
        if sys.platform == "darwin" and imaging_supported():
            self._offer_image_and_load(card)
            return

        if sys.platform.startswith("win"):
            hint = (
                "Windows blocks raw disk reads for non-admin users.\n\n"
                "Fix: close the app, right-click the launcher .bat "
                "and pick 'Run as administrator', then try again.\n\n"
                "Alternative: image the card from an elevated "
                "PowerShell (see WINDOWS.md, section 4) and open the "
                "resulting .img file with 'Open card image…'."
            )
        else:
            hint = (
                f"Cannot open {card.raw_device} for read.\n\n"
                "Try re-running the app with root privileges."
            )
        messagebox.showwarning(APP_TITLE, hint)

    def _offer_image_and_load(self, card) -> None:
        """macOS: admin-prompt + dd the card, then scan the resulting .img."""
        suggested = default_image_path(card.raw_device)
        if not messagebox.askyesno(
                APP_TITLE,
                f"Reading {card.raw_device} needs admin access.\n\n"
                "Image the card now?  macOS will prompt you for your "
                "password, then copy the card to:\n"
                f"\n    {suggested}\n\n"
                "After it finishes, the app loads the image automatically."):
            return

        # Let the user retarget the output if they want, but default to
        # the suggested path so the common case is one click.
        chosen = filedialog.asksaveasfilename(
            title="Save card image as…",
            initialdir=str(suggested.parent),
            initialfile=suggested.name,
            defaultextension=".img",
            filetypes=[("Disk image", "*.img"), ("All files", "*.*")],
        )
        if not chosen:
            return
        out_path = Path(chosen)

        self._run_imaging(card.raw_device, out_path,
                          mode=ImagingJob.MODE_ADMIN_PROMPT)

    def _run_imaging(self, device: str, out_path: Path, *, mode: str) -> None:
        dialog = ImagingDialog(self.root, device, out_path, mode=mode)
        self.root.wait_window(dialog)
        if dialog.error:
            if dialog.error_kind == ERROR_TCC_DENIED:
                # Offer the Terminal fallback — it has a different TCC
                # attribution and usually succeeds.
                self._show_full_disk_access_help(device, out_path)
            elif dialog.error_kind == ERROR_CANCELLED:
                pass  # user backed out; no dialog needed
            elif dialog.error_kind == ERROR_DEVICE_BUSY:
                messagebox.showwarning(
                    APP_TITLE,
                    "The card is in use by another process (Finder, "
                    "Photos, a camera app…).\n\n"
                    "Eject and re-insert the card, or quit the other "
                    "app, then try again.")
            else:
                messagebox.showwarning(
                    APP_TITLE,
                    f"Imaging didn't finish:\n\n{dialog.error}")
            return
        if dialog.result_path and dialog.result_path.exists():
            self._load_card(dialog.result_path)

    def _show_full_disk_access_help(self, device: str,
                                    out_path: Path) -> None:
        """Offer a Terminal-based imaging path and FDA instructions."""
        help_win = Toplevel(self.root)
        help_win.configure(bg=_BG_BASE)
        help_win.title("Raw disk access blocked")
        help_win.resizable(False, False)
        help_win.transient(self.root)
        help_win.grab_set()

        msg = (
            f"macOS blocked the raw read of {device} (\"Operation not\n"
            "permitted\"). This is the Full Disk Access / TCC gate.\n\n"
            "Easiest fix: re-run the dd command inside Terminal.app.\n"
            "If Terminal has Full Disk Access (most installs do by\n"
            "default), this works immediately — click \"Image in\n"
            "Terminal\" below. A Terminal window will open with the\n"
            "command already filled in; type your Mac password when\n"
            "prompted. When it finishes the image loads automatically.\n\n"
            "If Terminal doesn't have Full Disk Access yet:\n"
            "   1.  Click \"Open System Settings\" below.\n"
            "   2.  In Full Disk Access, switch Terminal on.\n"
            "         (On newer macOS you may also need to add\n"
            "         /usr/bin/python3 via the '+' button →\n"
            "         Command-Shift-G → /usr/bin/python3.)\n"
            "   3.  Quit Terminal entirely (Cmd-Q), re-open the\n"
            "         DP-03 Extractor, and try again."
        )
        ttk.Label(help_win, text=msg, justify="left",
                  padding=(16, 14, 16, 10)).pack(anchor=W)

        row = ttk.Frame(help_win, padding=(12, 0, 12, 14))
        row.pack(fill=X)

        def _open_settings() -> None:
            url = ("x-apple.systempreferences:com.apple.preference."
                   "security?Privacy_AllFiles")
            try:
                subprocess.run(["open", url], check=False)
            except Exception:  # noqa: BLE001
                pass

        def _image_in_terminal() -> None:
            help_win.destroy()
            self._run_imaging(device, out_path,
                              mode=ImagingJob.MODE_TERMINAL)

        ttk.Button(row, text="Image in Terminal",
                   command=_image_in_terminal).pack(side=LEFT)
        ttk.Button(row, text="Open System Settings",
                   command=_open_settings).pack(side=LEFT, padx=(8, 0))
        ttk.Button(row, text="Close",
                   command=help_win.destroy).pack(side=RIGHT)

    # ---------------------- extraction ----------------------

    def _start_extraction(self, selected: list[ProjectSummary]) -> None:
        if not self.image_path:
            return
        self.output_dir = Path(self._out_var.get())
        self.output_dir.mkdir(parents=True, exist_ok=True)
        jobs = [ExtractJob(project=p, output_dir=self.output_dir)
                for p in selected]
        self.runner = ExtractionRunner(self.image_path, jobs)
        self.runner.start()
        self._set_extracting(True, total=len(jobs))
        self._status_var.set(f"Starting — {len(jobs)} project(s)…")
        self.root.after(120, self._poll_runner)

    def _poll_runner(self) -> None:
        if not self.runner:
            return
        for event, payload in self.runner.poll_events():
            self._handle_event(event, payload)
        if self.runner.is_running():
            self.root.after(120, self._poll_runner)
        else:
            # Drain any final events the worker queued just before exiting.
            for event, payload in self.runner.poll_events():
                self._handle_event(event, payload)

    def _handle_event(self, event: str, payload: object) -> None:
        assert self.runner is not None
        if event == ExtractionRunner.EVENT_STARTED:
            total = int(payload or 0)  # type: ignore[arg-type]
            self._progress_bar.configure(maximum=max(total, 1) * 1000)
            self._progress_bar["value"] = 0
        elif event == ExtractionRunner.EVENT_PROJECT_START:
            index, project = payload  # type: ignore[misc]
            self._status_var.set(
                f"Extracting {project.name or f'project{project.project_index}'}…")
            self._progress_bar["value"] = index * 1000
        elif event == ExtractionRunner.EVENT_TRACK:
            self._status_var.set(f"Extracting {payload}")
        elif event == ExtractionRunner.EVENT_PROJECT_DONE:
            result = payload  # type: ignore[assignment]
            self._progress_bar["value"] = self._progress_bar["value"] + 1000
        elif event == ExtractionRunner.EVENT_ERROR:
            project, message = payload  # type: ignore[misc]
            label = project.name if project else "(worker)"
            messagebox.showerror(APP_TITLE, f"Error extracting {label}:\n\n{message}")
        elif event == ExtractionRunner.EVENT_ALL_DONE:
            self._set_extracting(False)
            written_total = sum(len(r.written_files) for r in self.runner.results)
            self._status_var.set(
                f"Done — wrote {written_total} .wav file(s) to {self.output_dir}")
            self._progress_bar["value"] = self._progress_bar["maximum"]

    def _set_extracting(self, running: bool, total: int = 0) -> None:
        state_primary = "disabled" if running else "normal"
        state_cancel = "normal" if running else "disabled"
        self._btn_extract_selected.configure(state=state_primary)
        self._btn_extract_all.configure(state=state_primary)
        self._btn_mixer.configure(state=state_primary)
        self._btn_cancel.configure(state=state_cancel)
        if not running:
            self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        selection = self._tree.selection()
        any_selected = bool(selection)
        single_selected = len(selection) == 1
        has_projects = bool(self.projects)
        state_sel = "normal" if (any_selected and has_projects) else "disabled"
        state_all = "normal" if has_projects else "disabled"
        state_mixer = "normal" if (single_selected and has_projects) else "disabled"
        self._btn_extract_selected.configure(state=state_sel)
        self._btn_extract_all.configure(state=state_all)
        self._btn_mixer.configure(state=state_mixer)

    # ---------------------- run ----------------------

    def run(self) -> int:
        self.root.mainloop()
        return 0


def main() -> int:
    try:
        return ExtractorApp().run()
    except Exception as exc:  # noqa: BLE001
        # Without a mainloop running yet there's nothing pretty to show.
        print(f"Fatal: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
