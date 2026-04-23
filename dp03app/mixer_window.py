"""Live mixer window — dark minimal / retro studio aesthetic.

Dark panel background, warm amber accents, colored track ID badges,
muted LCD-green time readout, and a master channel on the right.

Controls per channel strip: volume fader (-inf..+6 dB), pan slider,
MUTE + SOLO buttons. Transport bar below has PLAY / PAUSE / STOP, a
scrub bar, the LCD time readout, and a Render Mix button.
"""
from __future__ import annotations

import math
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Callable, Optional
import tkinter as tk
from tkinter import (BOTH, BOTTOM, HORIZONTAL, LEFT, RIGHT, TOP, VERTICAL,
                     BooleanVar, DoubleVar, StringVar, Tk, Toplevel,
                     filedialog, messagebox, ttk, W, E, N, S, X, Y)

from .audio_engine import AVAILABLE as AUDIO_AVAILABLE
from .project_scanner import ProjectSummary
from .waveform_view import WaveformPanel

if AUDIO_AVAILABLE:
    from .audio_engine import MixerEngine, TrackState, SAMPLE_RATE


# ---- debug log ----
# When the mixer half-renders (common symptom: user sees titlebar but
# nothing below it), Tk swallows the exception that killed _build_ui.
# Write it to a file so we can diagnose without begging for tracebacks.
_DEBUG_LOG = Path(__file__).resolve().parent.parent / "dp03-debug.log"


def _log_exception(tag: str) -> None:
    """Append the current exception (if any) to the user's debug log."""
    try:
        import datetime
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with _DEBUG_LOG.open("a") as fh:
            fh.write(f"\n==== {stamp}  {tag} ====\n")
            fh.write(traceback.format_exc())
            fh.write("\n")
    except Exception:  # noqa: BLE001 — logging must never crash the app
        pass


# ---------- dark minimal / retro studio palette ----------
BG_DARK = "#0a0b0d"          # outer panel — near-black
BG_PANEL = "#13151a"         # channel strip background
BG_RAISED = "#1c1e24"        # button / inset background
BG_SCALE_TROUGH = "#08090b"  # fader trough
FG_TEXT = "#d4d4d4"          # primary text — soft off-white
FG_MUTED = "#6b6e75"         # secondary / label text
ACCENT = "#c8844a"           # warm amber — Tascam-inspired
ACCENT_BRIGHT = "#daa06d"    # lighter amber for hover/active
LCD_BG = "#060d06"           # dark LCD panel
LCD_FG = "#4aad5c"           # muted green readout
SOLO_COLOR = "#c4a23d"       # muted gold
MUTE_COLOR = "#b5453a"       # muted red
MASTER_TINT = "#6898c4"      # soft blue

# Channel color badges — the DP-03 exposes 8 physical tracks × 2
# V-takes = 16 possible track slots. Two palettes: refined saturated
# colors for tracks 1-8, muted tones for V-take partners (9-16).
TRACK_COLORS = [
    "#d94a4c",  # 1  red
    "#c8784a",  # 2  orange
    "#c4a23d",  # 3  yellow
    "#5aad5a",  # 4  green
    "#3aabab",  # 5  teal
    "#4a82d9",  # 6  blue
    "#9470cc",  # 7  violet
    "#d44e96",  # 8  pink
    "#a04548",  # 9  muted red
    "#a06e3d",  # 10 muted orange
    "#a08c38",  # 11 muted yellow
    "#4a8a4a",  # 12 muted green
    "#3a8282",  # 13 muted teal
    "#4a6899",  # 14 muted blue
    "#7a56a0",  # 15 muted violet
    "#a05680",  # 16 muted pink
]

# ---- level-meter palette (muted / tasteful) ----
METER_TROUGH = "#050505"
METER_BEZEL = "#000000"
METER_GREEN = "#3aad5c"
METER_YELLOW = "#c4a23d"
METER_RED = "#b5453a"
METER_PEAK = "#d4d4d4"


class ChannelMeter(tk.Canvas):
    """Vertical LED-style peak meter with attack-instant / decay-smooth
    ballistics and a peak-hold indicator.

    Called from the UI tick (~80 ms); ballistics tuned to that rate:
      * Attack is instantaneous — a new peak jumps the bar up.
      * Decay multiplies the displayed level by DECAY each tick, so the
        meter falls over ~500 ms from full to 10 %.
      * Peak hold shows a thin white line that lingers ~1 s at the
        highest recent peak, then falls more slowly (PEAK_DECAY).
    Levels are mapped from linear peak to a dBFS scale so normal music
    signal lives in the top two-thirds of the bar (-60 dB == bottom).
    """

    DECAY = 0.80             # bar fall rate per UI tick
    PEAK_DECAY = 0.955       # peak-hold fall after hold expires
    PEAK_HOLD_TICKS = 14     # ~1.1 s @ 80 ms tick rate
    MIN_DB = -60.0

    def __init__(self, parent: tk.Widget, *, height: int,
                 width: int = 9) -> None:
        super().__init__(parent, width=width, height=height,
                         bg=METER_TROUGH, bd=0,
                         highlightthickness=1,
                         highlightbackground=METER_BEZEL)
        # NOTE: do NOT assign to self._w / self._h — those names are
        # reserved by tkinter.Widget (self._w is the widget's Tcl path,
        # e.g. ".!frame.!canvas"). Overwriting them makes every
        # subsequent Tcl call on this widget fail with
        #   _tkinter.TclError: invalid command name "<int>"
        # which is exactly what happened and nuked the mixer render.
        self._px_w = width
        self._px_h = height
        self._displayed = 0.0         # normalized [0, 1]
        self._peak = 0.0
        self._hold_ticks = 0

        # Create the two visual items once; update via coords()/itemconfig
        # from here on — avoids the flicker and GC churn of delete+create
        # per tick.
        self._bar = self.create_rectangle(
            1, height - 1, width - 1, height - 1,
            fill=METER_GREEN, outline="")
        self._peak_line = self.create_line(
            1, height - 1, width - 1, height - 1,
            fill=METER_PEAK, width=1)

    def push_peak(self, peak_linear: float) -> None:
        """Feed one post-gain peak sample in [0, 1]. Call every UI tick."""
        target = self._db_normalize(peak_linear)
        # Attack: jump up to new peak instantly; Decay: smooth fall.
        if target > self._displayed:
            self._displayed = target
        else:
            self._displayed *= self.DECAY
            if self._displayed < 0.005:
                self._displayed = 0.0
        # Peak-hold logic — retriggers on any new high, then lingers.
        if target >= self._peak:
            self._peak = target
            self._hold_ticks = self.PEAK_HOLD_TICKS
        elif self._hold_ticks > 0:
            self._hold_ticks -= 1
        else:
            self._peak *= self.PEAK_DECAY
            if self._peak < 0.01:
                self._peak = 0.0
        self._redraw()

    def clear(self) -> None:
        """Force the meter dark (used on stop/load)."""
        self._displayed = 0.0
        self._peak = 0.0
        self._hold_ticks = 0
        self._redraw()

    @classmethod
    def _db_normalize(cls, linear: float) -> float:
        """Map linear peak [0,1] -> normalized dB scale [0,1]."""
        if linear <= 1e-6:
            return 0.0
        db = 20.0 * math.log10(min(1.0, linear))
        if db <= cls.MIN_DB:
            return 0.0
        return (db - cls.MIN_DB) / (-cls.MIN_DB)

    def _redraw(self) -> None:
        h = self._px_h
        w = self._px_w
        level = self._displayed
        peak = self._peak
        usable = h - 2  # leave 1px at top and bottom so the bezel shows
        bar_top = (h - 1) - int(level * usable)
        # Color the bar by its current height — green body, yellow hot,
        # red pinned. Thresholds picked on the normalized dB scale:
        #   0.73 ~= -16 dB (yellow begins)
        #   0.88 ~= -7  dB (red begins; near clipping)
        if level > 0.88:
            color = METER_RED
        elif level > 0.73:
            color = METER_YELLOW
        else:
            color = METER_GREEN
        self.coords(self._bar, 1, bar_top, w - 1, h - 1)
        self.itemconfig(self._bar, fill=color)
        # Peak line — sits one pixel above its level for visibility.
        peak_y = (h - 1) - int(peak * usable)
        self.coords(self._peak_line, 1, peak_y, w - 1, peak_y)


def _apply_style(widget: tk.Widget) -> ttk.Style:
    """Configure ttk styles scoped to the mixer window."""
    style = ttk.Style(widget)
    # clam gives us full color control across platforms; aqua/native
    # themes on macOS ignore most color options.
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    # Scales
    style.configure("Mixer.Vertical.TScale",
                    background=BG_PANEL,
                    troughcolor=BG_SCALE_TROUGH,
                    sliderthickness=18,
                    borderwidth=0)
    style.configure("Mixer.Horizontal.TScale",
                    background=BG_PANEL,
                    troughcolor=BG_SCALE_TROUGH,
                    sliderthickness=14,
                    borderwidth=0)
    style.configure("Scrub.Horizontal.TScale",
                    background=BG_DARK,
                    troughcolor=BG_SCALE_TROUGH,
                    sliderthickness=16,
                    borderwidth=0)
    # Progressbar (render + load)
    style.configure("Zoom.Horizontal.TProgressbar",
                    background=ACCENT,
                    troughcolor=BG_SCALE_TROUGH,
                    borderwidth=0, thickness=14)
    return style


class ChannelStrip(tk.Frame):
    """One vertical channel strip, R16-styled."""

    FADER_MIN_DB = -60.0
    FADER_MAX_DB = 6.0

    def __init__(self, parent: tk.Widget, index: int, name: str,
                 color: str,
                 on_volume: Callable[[int, float], None],
                 on_pan: Callable[[int, float], None],
                 on_mute: Callable[[int, bool], None],
                 on_solo: Callable[[int, bool], None],
                 display_number: Optional[int] = None) -> None:
        super().__init__(parent, bg=BG_PANEL, bd=0,
                         highlightthickness=1,
                         highlightbackground=BG_DARK,
                         highlightcolor=BG_DARK)
        self.index = index
        self._on_volume = on_volume
        self._on_pan = on_pan
        self._on_mute = on_mute
        self._on_solo = on_solo
        badge_num = display_number if display_number is not None else index + 1

        # --- header badge ---
        badge = tk.Frame(self, bg=color, width=62, height=28)
        badge.pack(side=TOP, fill=X, padx=5, pady=(6, 1))
        badge.pack_propagate(False)
        tk.Label(badge, text=f"TR {badge_num:02d}",
                 bg=color, fg="#111111",
                 font=("Helvetica", 11, "bold")).pack(expand=True)

        # --- name label ---
        short_name = (name if len(name) <= 14 else name[:13] + "\u2026")
        tk.Label(self, text=short_name, bg=BG_PANEL, fg=FG_MUTED,
                 font=("Helvetica", 8)).pack(side=TOP, pady=(1, 4))

        # --- fader ---
        self._vol_var = DoubleVar(value=0.0)
        self._vol_display = StringVar(value="0.0 dB")
        fader_frame = tk.Frame(self, bg=BG_PANEL)
        fader_frame.pack(side=TOP, fill=BOTH, expand=True,
                         padx=10, pady=(2, 2))
        # dB tick marks on the side
        ticks = tk.Frame(fader_frame, bg=BG_PANEL, width=22)
        ticks.pack(side=LEFT, fill=Y)
        for label in ["+6", "0", "-12", "-24", "-48"]:
            tk.Label(ticks, text=label, bg=BG_PANEL, fg=FG_MUTED,
                     font=("Helvetica", 7)).pack(pady=(0, 11))
        fader = ttk.Scale(fader_frame, from_=self.FADER_MAX_DB,
                          to=self.FADER_MIN_DB,
                          orient=VERTICAL, length=190,
                          style="Mixer.Vertical.TScale",
                          variable=self._vol_var,
                          command=self._on_vol_changed)
        fader.pack(side=LEFT, fill=Y, padx=(2, 0))
        # Double-click the fader to snap to unity gain — matches
        # Logic, Pro Tools, Reaper, Ableton, every hardware console.
        fader.bind("<Double-Button-1>", self._on_fader_reset)

        # --- level meter (sits right beside the fader like real hardware) ---
        self._meter = ChannelMeter(fader_frame, height=190, width=9)
        self._meter.pack(side=LEFT, fill=Y, padx=(4, 0))

        # --- dB readout ---
        db_box = tk.Frame(self, bg=LCD_BG, highlightthickness=1,
                          highlightbackground="#0a0b0d")
        db_box.pack(side=TOP, fill=X, padx=8, pady=(2, 4))
        tk.Label(db_box, textvariable=self._vol_display, bg=LCD_BG,
                 fg=LCD_FG, font=("Menlo", 9, "bold")).pack(pady=1)

        # --- pan ---
        tk.Label(self, text="PAN", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Helvetica", 7, "bold")).pack(side=TOP,
                                                    pady=(1, 0))
        self._pan_var = DoubleVar(value=0.0)
        self._pan_display = StringVar(value="C")
        pan_scale = ttk.Scale(self, from_=-1.0, to=1.0,
                              orient=HORIZONTAL, length=100,
                              style="Mixer.Horizontal.TScale",
                              variable=self._pan_var,
                              command=self._on_pan_changed)
        pan_scale.pack(side=TOP, padx=8)
        # Double-click pan to snap back to center.
        pan_scale.bind("<Double-Button-1>", self._on_pan_reset)
        tk.Label(self, textvariable=self._pan_display, bg=BG_PANEL,
                 fg=FG_MUTED,
                 font=("Menlo", 9)).pack(side=TOP, pady=(0, 4))

        # --- MUTE / SOLO toggle buttons ---
        btn_row = tk.Frame(self, bg=BG_PANEL)
        btn_row.pack(side=TOP, pady=(1, 8), fill=X, padx=8)
        self._mute_on = False
        self._solo_on = False
        self._mute_btn = tk.Button(
            btn_row, text="M", bg=BG_RAISED, fg=FG_MUTED,
            activebackground=MUTE_COLOR, activeforeground="#111111",
            relief="flat", bd=0, font=("Helvetica", 9, "bold"),
            width=3, height=1, command=self._toggle_mute)
        self._mute_btn.pack(side=LEFT, expand=True, fill=X, padx=(0, 2))
        self._solo_btn = tk.Button(
            btn_row, text="S", bg=BG_RAISED, fg=FG_MUTED,
            activebackground=SOLO_COLOR, activeforeground="#111111",
            relief="flat", bd=0, font=("Helvetica", 9, "bold"),
            width=3, height=1, command=self._toggle_solo)
        self._solo_btn.pack(side=LEFT, expand=True, fill=X)

    # --- handlers ---

    def _on_vol_changed(self, _value: str) -> None:
        db = float(self._vol_var.get())
        if db <= self.FADER_MIN_DB + 0.01:
            self._vol_display.set("-INF")
        else:
            self._vol_display.set(f"{db:+.1f} dB")
        self._on_volume(self.index, db)

    def _on_pan_changed(self, _value: str) -> None:
        pan = float(self._pan_var.get())
        if abs(pan) < 0.02:
            label = "C"
        elif pan < 0:
            label = f"L{int(round(abs(pan) * 100))}"
        else:
            label = f"R{int(round(pan * 100))}"
        self._pan_display.set(label)
        self._on_pan(self.index, pan)

    def _toggle_mute(self) -> None:
        self._mute_on = not self._mute_on
        self._mute_btn.configure(
            bg=MUTE_COLOR if self._mute_on else BG_RAISED,
            fg="#111111" if self._mute_on else FG_MUTED)
        self._on_mute(self.index, self._mute_on)

    def _toggle_solo(self) -> None:
        self._solo_on = not self._solo_on
        self._solo_btn.configure(
            bg=SOLO_COLOR if self._solo_on else BG_RAISED,
            fg="#111111" if self._solo_on else FG_MUTED)
        self._on_solo(self.index, self._solo_on)

    # helpers for default pan / volume from code
    def set_initial_pan(self, pan: float) -> None:
        self._pan_var.set(pan)
        self._on_pan_changed("")

    # --- metering ---
    def update_meter(self, peak_linear: float) -> None:
        """Called each UI tick with the engine-reported track peak."""
        self._meter.push_peak(peak_linear)

    def clear_meter(self) -> None:
        self._meter.clear()

    # --- reset gestures ---
    def _on_fader_reset(self, _event) -> str:
        """Double-click fader → unity gain (0 dB). Returns "break" so the
        widget's own double-click binding (would jump to the clicked
        position) doesn't fight us."""
        self._vol_var.set(0.0)
        self._on_vol_changed("")
        return "break"

    def _on_pan_reset(self, _event) -> str:
        """Double-click pan → dead center."""
        self._pan_var.set(0.0)
        self._on_pan_changed("")
        return "break"


class MasterStrip(tk.Frame):
    """Master bus strip — fader only, distinct color."""

    FADER_MIN_DB = -60.0
    FADER_MAX_DB = 6.0

    def __init__(self, parent: tk.Widget,
                 on_master: Callable[[float], None],
                 on_clip_reset: Optional[Callable[[], None]] = None) -> None:
        super().__init__(parent, bg=BG_PANEL, bd=0,
                         highlightthickness=1,
                         highlightbackground="#1a2a3a")
        self._on_master = on_master
        self._on_clip_reset = on_clip_reset
        self._clip_lit: bool = False

        badge = tk.Frame(self, bg=MASTER_TINT, width=84, height=28)
        badge.pack(side=TOP, fill=X, padx=5, pady=(6, 1))
        badge.pack_propagate(False)
        tk.Label(badge, text="MASTER", bg=MASTER_TINT, fg="#111111",
                 font=("Helvetica", 11, "bold")).pack(expand=True)

        tk.Label(self, text="stereo bus", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Helvetica", 8, "italic")).pack(side=TOP,
                                                       pady=(1, 4))

        self._vol_var = DoubleVar(value=0.0)
        self._vol_display = StringVar(value="0.0 dB")
        fader_frame = tk.Frame(self, bg=BG_PANEL)
        fader_frame.pack(side=TOP, fill=BOTH, expand=True,
                         padx=10, pady=(2, 2))
        ticks = tk.Frame(fader_frame, bg=BG_PANEL, width=22)
        ticks.pack(side=LEFT, fill=Y)
        for label in ["+6", "0", "-12", "-24", "-48"]:
            tk.Label(ticks, text=label, bg=BG_PANEL, fg=FG_MUTED,
                     font=("Helvetica", 7)).pack(pady=(0, 11))
        master_fader = ttk.Scale(fader_frame, from_=self.FADER_MAX_DB,
                                 to=self.FADER_MIN_DB, orient=VERTICAL,
                                 length=190,
                                 style="Mixer.Vertical.TScale",
                                 variable=self._vol_var,
                                 command=self._on_changed)
        master_fader.pack(side=LEFT, fill=Y, padx=(2, 0))
        master_fader.bind("<Double-Button-1>", self._on_master_reset)

        # --- stereo output meters (L then R) beside the master fader ---
        self._meter_l = ChannelMeter(fader_frame, height=190, width=9)
        self._meter_l.pack(side=LEFT, fill=Y, padx=(6, 1))
        self._meter_r = ChannelMeter(fader_frame, height=190, width=9)
        self._meter_r.pack(side=LEFT, fill=Y, padx=(0, 0))

        db_box = tk.Frame(self, bg=LCD_BG, highlightthickness=1,
                          highlightbackground="#0a0b0d")
        db_box.pack(side=TOP, fill=X, padx=8, pady=(2, 4))
        tk.Label(db_box, textvariable=self._vol_display, bg=LCD_BG,
                 fg=LCD_FG, font=("Menlo", 9, "bold")).pack(pady=1)

        # --- CLIP LED: sticky red indicator, click to reset ---
        clip_row = tk.Frame(self, bg=BG_PANEL)
        clip_row.pack(side=TOP, fill=X, padx=8, pady=(0, 4))
        self._clip_btn = tk.Button(
            clip_row, text="CLIP", bg=BG_RAISED, fg=FG_MUTED,
            activebackground=MUTE_COLOR, activeforeground="#111111",
            relief="flat", bd=0, font=("Helvetica", 8, "bold"),
            command=self._on_clip_click)
        self._clip_btn.pack(fill=X)

        tk.Label(self, text="OUTPUT", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Helvetica", 7, "bold")).pack(side=TOP,
                                                     pady=(2, 12))

    # --- metering ---
    def update_meters(self, l_peak: float, r_peak: float) -> None:
        self._meter_l.push_peak(l_peak)
        self._meter_r.push_peak(r_peak)

    def clear_meters(self) -> None:
        self._meter_l.clear()
        self._meter_r.clear()

    def set_clip(self, clipped: bool) -> None:
        """Light or extinguish the sticky CLIP indicator."""
        if clipped == self._clip_lit:
            return
        self._clip_lit = clipped
        if clipped:
            self._clip_btn.configure(bg=MUTE_COLOR, fg="#111111")
        else:
            self._clip_btn.configure(bg=BG_RAISED, fg=FG_MUTED)

    def _on_clip_click(self) -> None:
        """User acknowledged the clip — clear the engine-side flag."""
        if self._on_clip_reset is not None:
            self._on_clip_reset()
        self.set_clip(False)

    def _on_master_reset(self, _event) -> str:
        self._vol_var.set(0.0)
        self._on_changed("")
        return "break"

    def _on_changed(self, _v: str) -> None:
        db = float(self._vol_var.get())
        if db <= self.FADER_MIN_DB + 0.01:
            self._vol_display.set("-INF")
        else:
            self._vol_display.set(f"{db:+.1f} dB")
        self._on_master(db)


class LoadDialog(Toplevel):
    """Modal progress dialog while decoding all masters from a project."""

    def __init__(self, parent: tk.Widget, image_path: Path,
                 project: ProjectSummary) -> None:
        super().__init__(parent)
        self.configure(bg=BG_DARK)
        self.title("Loading project")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.geometry(f"+{parent.winfo_rootx() + 80}"
                      f"+{parent.winfo_rooty() + 80}")

        self._stage = StringVar(value="Decoding tracks\u2026")
        tk.Label(self, textvariable=self._stage, bg=BG_DARK, fg=FG_TEXT,
                 font=("Helvetica", 11), width=44, anchor=W).pack(
            padx=18, pady=(16, 8), fill=X)
        self._progress = ttk.Progressbar(
            self, length=340, mode="determinate", maximum=1000,
            style="Zoom.Horizontal.TProgressbar")
        self._progress.pack(padx=18, pady=(0, 16))

        # Cancel button — users with 16-track projects on slow cards were
        # hitting 60-second loads with no escape hatch. The worker checks
        # self._cancelled between tracks and bails cleanly.
        self.cancelled: bool = False
        self._cancelled = threading.Event()
        cancel_btn = tk.Button(
            self, text="Cancel", bg=BG_RAISED, fg=FG_TEXT,
            activebackground=MUTE_COLOR, activeforeground="#111111",
            relief="flat", bd=0, font=("Helvetica", 10, "bold"),
            padx=16, pady=4, command=self._on_cancel)
        cancel_btn.pack(pady=(0, 14))
        # Also honor the window's close button the same way.
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self.tracks: list = []
        self.error: Optional[str] = None
        self._done = False
        self._total = max(1, sum(1 for m in project.masters
                                 if m.declared_bytes > 0))
        self._completed = 0
        # Stage text is written by the worker thread and read by _poll
        # on the main thread. Tk vars are NOT thread-safe, so stash the
        # string plainly and let _poll push it into the StringVar.
        self._stage_text: str = "Decoding tracks\u2026"

        self._thread = threading.Thread(
            target=self._worker, args=(image_path, project), daemon=True)
        self._thread.start()
        self.after(60, self._poll)

    def _on_cancel(self) -> None:
        self._cancelled.set()
        self.cancelled = True
        self._stage_text = "Cancelling\u2026"

    def _worker(self, image_path: Path, project: ProjectSummary) -> None:
        try:
            from dp03extract.extract import extract_master_pcm
            loaded = 0
            for master in project.masters:
                if self._cancelled.is_set():
                    break
                if master.declared_bytes <= 0:
                    continue
                track_number = master.track_id + 1  # DP-03 shows 1..8
                self._stage_text = (
                    f"Decoding track {track_number}\u2026")
                pcm = extract_master_pcm(
                    image_path, project.byte_base_mtr_relative,
                    master.slot_offset)
                if self._cancelled.is_set():
                    break
                track = MixerEngine.track_from_pcm(
                    pcm, f"Track {track_number}")
                self.tracks.append(track)
                loaded += 1
                self._completed += 1
        except Exception as exc:  # noqa: BLE001 surfaced in UI
            self.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        finally:
            self._done = True

    def _poll(self) -> None:
        try:
            self._stage.set(self._stage_text)
            if self._total:
                self._progress["value"] = (self._completed / self._total) * 1000
        except tk.TclError:
            return
        if self._done:
            try:
                self.grab_release()
                self.destroy()
            except tk.TclError:
                pass
            return
        self.after(100, self._poll)


class MixerWindow(Toplevel):
    """Zoom R16-styled mixer for a single project."""

    def __init__(self, parent: tk.Widget, image_path: Path,
                 project: ProjectSummary, output_dir: Path) -> None:
        super().__init__(parent)
        self.configure(bg=BG_DARK)
        proj_label = project.name or f"project{project.project_index}"
        self.title(f"Mixer — {proj_label}")
        self.geometry("1060x760")
        self.minsize(820, 540)
        self.image_path = image_path
        self.project = project
        self.output_dir = output_dir

        self.engine = MixerEngine()
        self._scrub_dragging = False
        self._poll_after_id: Optional[str] = None
        self._render_window: Optional[Toplevel] = None
        self._waveform_visible: bool = True

        _apply_style(self)
        try:
            self._build_ui(proj_label)
        except Exception as exc:  # noqa: BLE001
            _log_exception("MixerWindow._build_ui")
            messagebox.showerror(
                "DP-03 Extractor",
                f"Mixer UI failed to build:\n\n{type(exc).__name__}: {exc}\n\n"
                f"Details appended to {_DEBUG_LOG}")
            self.after(0, self._on_close)
            return
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Keyboard shortcuts — spacebar toggles play/pause like every DAW,
        # Home rewinds, Esc stops. Bindings live on the toplevel; we
        # guard Space so a focused button (e.g. after clicking MUTE)
        # doesn't both fire the button AND re-toggle playback.
        self.bind("<space>", self._on_space_shortcut)
        self.bind("<Home>", lambda _e: (self._on_rewind(), "break")[1])
        self.bind("<Escape>", lambda _e: (self._on_stop(), "break")[1])

        dialog = LoadDialog(self, image_path, project)
        self.wait_window(dialog)
        if dialog.cancelled:
            # User backed out of the load — just close the mixer.
            self.after(0, self._on_close)
            return
        if dialog.error:
            messagebox.showerror("DP-03 Extractor",
                                 f"Could not decode tracks:\n\n{dialog.error}")
            self.after(0, self._on_close)
            return
        if not dialog.tracks:
            messagebox.showinfo("DP-03 Extractor",
                                "No audio tracks in this project.")
            self.after(0, self._on_close)
            return
        try:
            self.engine.load_tracks(dialog.tracks)
            self._populate_strips(dialog.tracks)
            self._waveform_panel.load_tracks(dialog.tracks)
            self._scrub_scale.configure(
                to=max(1.0, self.engine.length_seconds()))
            self._time_var.set(self._format_time(0.0))
            self._length_var.set(
                self._format_time(self.engine.length_seconds()))
            self._start_poll()
        except Exception as exc:  # noqa: BLE001
            _log_exception("MixerWindow post-load")
            messagebox.showerror(
                "DP-03 Extractor",
                f"Mixer post-load step failed:\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                f"Details appended to {_DEBUG_LOG}")
            self.after(0, self._on_close)

    # ----------------------- UI layout -----------------------

    def _build_ui(self, proj_label: str) -> None:
        # ---- top title bar ----
        titlebar = tk.Frame(self, bg=BG_DARK)
        titlebar.pack(side=TOP, fill=X, padx=12, pady=(8, 0))
        tk.Label(titlebar, text="DP-03", bg=BG_DARK, fg=ACCENT,
                 font=("Helvetica", 12, "bold")).pack(side=LEFT)
        tk.Label(titlebar, text=f"  {proj_label}", bg=BG_DARK,
                 fg=FG_MUTED,
                 font=("Helvetica", 12)).pack(side=LEFT)
        tk.Label(titlebar, text="44.1 kHz \u2022 16-bit",
                 bg=BG_DARK, fg="#3a3d44",
                 font=("Helvetica", 8)).pack(side=RIGHT)

        # thin accent line under title
        tk.Frame(self, bg=ACCENT, height=1).pack(side=TOP, fill=X,
                                                  padx=12, pady=(4, 6))

        # ---- waveform timeline (FL-studio-style, hideable) ----
        self._waveform_panel = WaveformPanel(
            self,
            sample_rate=SAMPLE_RATE,
            track_colors=TRACK_COLORS,
            on_seek_seconds=self._on_waveform_seek,
        )
        # Packed here by default; toggle button below can hide it.
        self._waveform_panel.pack(side=TOP, fill=X, padx=10, pady=(0, 4))

        # ---- main mixing area (strips area + master on the right) ----
        self._mix_area = tk.Frame(self, bg=BG_DARK)
        self._mix_area.pack(side=TOP, fill=BOTH, expand=True, padx=10)
        mix_area = self._mix_area

        # strips area with horizontal scroll
        strips_outer = tk.Frame(mix_area, bg=BG_DARK)
        strips_outer.pack(side=LEFT, fill=BOTH, expand=True)

        self._strips_canvas = tk.Canvas(strips_outer, bg=BG_DARK,
                                        highlightthickness=0, height=380)
        hbar = ttk.Scrollbar(strips_outer, orient=HORIZONTAL,
                             command=self._strips_canvas.xview)
        self._strips_canvas.configure(xscrollcommand=hbar.set)
        self._strips_canvas.pack(side=TOP, fill=BOTH, expand=True)
        hbar.pack(side=BOTTOM, fill=X)

        self._strips_frame = tk.Frame(self._strips_canvas, bg=BG_DARK)
        self._strips_canvas.create_window((0, 0),
                                          window=self._strips_frame,
                                          anchor="nw")
        self._strips_frame.bind(
            "<Configure>", lambda _e: self._strips_canvas.configure(
                scrollregion=self._strips_canvas.bbox("all")))

        # master strip (always visible, no scroll)
        master_holder = tk.Frame(mix_area, bg=BG_DARK)
        master_holder.pack(side=RIGHT, fill=Y, padx=(10, 0))
        self._master = MasterStrip(master_holder,
                                   on_master=self._on_master_changed,
                                   on_clip_reset=self._on_clip_reset)
        self._master.pack(side=TOP, fill=Y, expand=True, pady=0)

        # ---- transport bar ----
        transport = tk.Frame(self, bg=BG_PANEL, padx=10, pady=6)
        transport.pack(side=TOP, fill=X, padx=10, pady=(8, 0))

        self._play_btn = tk.Button(
            transport, text="\u25B6  PLAY", bg=BG_RAISED, fg=FG_TEXT,
            activebackground=ACCENT, activeforeground="#111111",
            relief="flat", bd=0, font=("Helvetica", 10, "bold"),
            width=8, command=self._on_play)
        self._play_btn.pack(side=LEFT)
        tk.Button(transport, text="\u275A\u275A",
                  bg=BG_RAISED, fg=FG_TEXT,
                  activebackground=ACCENT_BRIGHT,
                  activeforeground="#111111", relief="flat", bd=0,
                  font=("Helvetica", 10, "bold"),
                  width=4, command=self._on_pause).pack(side=LEFT,
                                                         padx=4)
        tk.Button(transport, text="\u25A0",
                  bg=BG_RAISED, fg=FG_TEXT,
                  activebackground=MUTE_COLOR,
                  activeforeground="#111111", relief="flat", bd=0,
                  font=("Helvetica", 10, "bold"),
                  width=4, command=self._on_stop).pack(side=LEFT,
                                                        padx=(0, 10))

        # ---- LCD time display ----
        lcd = tk.Frame(transport, bg=LCD_BG, padx=10, pady=4,
                       highlightthickness=1, highlightbackground="#0a0b0d")
        lcd.pack(side=LEFT, padx=(4, 10))
        self._time_var = StringVar(value="0:00.00")
        self._length_var = StringVar(value="0:00.00")
        tk.Label(lcd, textvariable=self._time_var, bg=LCD_BG, fg=LCD_FG,
                 font=("Menlo", 16, "bold"), width=8, anchor=E).pack(
            side=LEFT)
        tk.Label(lcd, text=" / ", bg=LCD_BG, fg="#1a5a2a",
                 font=("Menlo", 14)).pack(side=LEFT)
        tk.Label(lcd, textvariable=self._length_var, bg=LCD_BG,
                 fg="#2a7a3d",
                 font=("Menlo", 16, "bold"), width=8, anchor=W).pack(
            side=LEFT)

        # ---- scrub bar ----
        self._scrub_var = DoubleVar(value=0.0)
        self._scrub_scale = ttk.Scale(transport, from_=0.0, to=1.0,
                                      orient=HORIZONTAL,
                                      style="Scrub.Horizontal.TScale",
                                      variable=self._scrub_var,
                                      command=self._on_scrub_changed)
        self._scrub_scale.pack(side=LEFT, fill=X, expand=True,
                               padx=(0, 12))
        self._scrub_scale.bind("<ButtonPress-1>", self._on_scrub_press)
        self._scrub_scale.bind("<ButtonRelease-1>",
                               self._on_scrub_release)

        tk.Button(transport, text="RENDER\u2026", bg=ACCENT,
                  fg="#111111", activebackground=ACCENT_BRIGHT,
                  activeforeground="#111111", relief="flat", bd=0,
                  font=("Helvetica", 9, "bold"), padx=12, pady=3,
                  command=self._on_render).pack(side=RIGHT)

        # WAVES toggle
        self._waves_btn = tk.Button(
            transport, text="WAVES", bg=BG_RAISED, fg=FG_MUTED,
            activebackground=ACCENT_BRIGHT, activeforeground="#111111",
            relief="flat", bd=0, font=("Helvetica", 8, "bold"),
            padx=8, pady=3, command=self._on_toggle_waves)
        self._waves_btn.pack(side=RIGHT, padx=(0, 6))

        # ---- footer tip strip ----
        footer = tk.Frame(self, bg=BG_DARK, padx=10, pady=4)
        footer.pack(side=BOTTOM, fill=X)
        tk.Label(footer,
                 text=("Space = play/pause  \u2022  "
                       "Home = rewind  \u2022  Esc = stop  \u2022  "
                       "Click waveform to seek"),
                 bg=BG_DARK, fg="#3a3d44",
                 font=("Helvetica", 8)).pack(side=LEFT)

    def _populate_strips(self, tracks: list) -> None:
        import re
        self._strips: list[ChannelStrip] = []
        default_pans = None
        if len(tracks) == 2:
            default_pans = [-0.6, 0.6]
        for i, track in enumerate(tracks):
            # Pull the DP-03 track number out of the name ("Track N") so
            # the badge and color stay consistent with the hardware's
            # track numbering even when some slots are empty. Fall back
            # to position if the name doesn't match.
            m = re.match(r"Track\s+(\d+)", track.name)
            display_num = int(m.group(1)) if m else (i + 1)
            color_idx = (display_num - 1) % len(TRACK_COLORS)
            color = TRACK_COLORS[color_idx]
            strip = ChannelStrip(
                self._strips_frame, index=i, name=track.name,
                color=color,
                on_volume=self._on_volume_changed,
                on_pan=self._on_pan_changed,
                on_mute=self._on_mute_changed,
                on_solo=self._on_solo_changed,
                display_number=display_num)
            strip.pack(side=LEFT, padx=4, pady=2, fill=Y)
            if default_pans is not None:
                strip.set_initial_pan(default_pans[i])
            self._strips.append(strip)

    # ----------------------- control callbacks -----------------------

    def _on_volume_changed(self, index: int, db: float) -> None:
        self.engine.set_volume_db(index, db)

    def _on_pan_changed(self, index: int, pan: float) -> None:
        self.engine.set_pan(index, pan)

    def _on_mute_changed(self, index: int, mute: bool) -> None:
        self.engine.set_mute(index, mute)

    def _on_solo_changed(self, index: int, solo: bool) -> None:
        self.engine.set_solo(index, solo)

    def _on_master_changed(self, db: float) -> None:
        self.engine.set_master_db(db)

    def _on_clip_reset(self) -> None:
        """User clicked the CLIP LED — clear the engine-side sticky flag."""
        self.engine.reset_clip()

    # ----------------------- transport -----------------------

    def _on_play(self) -> None:
        try:
            self.engine.play()
        except Exception as exc:  # noqa: BLE001
            # PortAudio surfaces a zoo of failure modes depending on the
            # OS and whether a default output device exists. Translate
            # the common ones into something a musician can act on.
            raw = f"{type(exc).__name__}: {exc}"
            lower = str(exc).lower()
            if ("no default output" in lower
                    or "no such device" in lower
                    or "device unavailable" in lower
                    or "invalid device" in lower):
                msg = ("No audio output device was found.\n\n"
                       "Plug in headphones or speakers (or enable your "
                       "built-in output in Sound settings) and try again.")
            elif "unable to open" in lower or "busy" in lower:
                msg = ("The audio output is in use by another app.\n\n"
                       "Close any other audio app (DAW, browser tab "
                       "playing sound, Discord call) and press PLAY again.")
            else:
                msg = f"Couldn't start audio:\n\n{raw}"
            messagebox.showerror("DP-03 Extractor", msg)

    def _on_pause(self) -> None:
        self.engine.pause()

    def _on_stop(self) -> None:
        self.engine.stop()
        self._scrub_var.set(0.0)
        self._time_var.set(self._format_time(0.0))
        # Snap meters dark the instant STOP is pressed — the next _tick
        # will keep them there since engine levels are now zero.
        try:
            for strip in getattr(self, "_strips", []):
                strip.clear_meter()
            if hasattr(self, "_master"):
                self._master.clear_meters()
        except tk.TclError:
            pass

    def _on_toggle_play(self) -> None:
        """Spacebar shortcut — pause if playing, otherwise play."""
        if self.engine.is_playing():
            self._on_pause()
        else:
            self._on_play()

    def _on_space_shortcut(self, _event):
        """Swallow Space when fired over interactive widgets (so focused
        MUTE/SOLO buttons don't double-fire with the transport toggle)."""
        try:
            focused = self.focus_get()
        except Exception:  # noqa: BLE001
            focused = None
        if isinstance(focused, (tk.Button, ttk.Button, tk.Entry, ttk.Entry,
                                tk.Scale, ttk.Scale, ttk.Treeview,
                                ttk.Combobox)):
            # Let the focused widget handle its own Space keystroke
            # (e.g. a fader gets nudged, an entry types a space).
            return None
        self._on_toggle_play()
        return "break"

    def _on_rewind(self) -> None:
        """Home key — rewind transport to 0:00 without stopping playback."""
        self.engine.seek_seconds(0.0)
        self._scrub_var.set(0.0)
        self._time_var.set(self._format_time(0.0))

    def _on_scrub_press(self, _event) -> None:
        self._scrub_dragging = True

    def _on_scrub_release(self, _event) -> None:
        self._scrub_dragging = False
        self.engine.seek_seconds(float(self._scrub_var.get()))

    def _on_scrub_changed(self, _value: str) -> None:
        if self._scrub_dragging:
            seconds = float(self._scrub_var.get())
            self._time_var.set(self._format_time(seconds))

    # ----------------------- waveform panel -----------------------

    def _on_waveform_seek(self, seconds: float) -> None:
        """Click/drag on the waveform jumps the transport."""
        length = self.engine.length_seconds()
        if length <= 0:
            return
        seconds = max(0.0, min(seconds, length))
        self.engine.seek_seconds(seconds)
        self._scrub_var.set(seconds)
        self._time_var.set(self._format_time(seconds))

    def _on_toggle_waves(self) -> None:
        if self._waveform_visible:
            self._waveform_panel.pack_forget()
            self._waves_btn.configure(text="WAVES", fg=FG_MUTED)
            self._waveform_visible = False
        else:
            # Re-pack above the mix area so the order is preserved.
            self._waveform_panel.pack(side=TOP, fill=X, padx=10,
                                      pady=(0, 4),
                                      before=self._mix_area)
            self._waves_btn.configure(text="WAVES", fg=FG_TEXT)
            self._waveform_visible = True

    # ----------------------- render -----------------------

    def _on_render(self) -> None:
        default_name = (
            (self.project.name or f"project{self.project.project_index}")
            + "_mix.wav")
        path_str = filedialog.asksaveasfilename(
            title="Render stereo mix to\u2026",
            initialdir=str(self.output_dir),
            initialfile=default_name,
            defaultextension=".wav",
            filetypes=[("WAV audio", "*.wav")],
        )
        if not path_str:
            return
        out_path = Path(path_str)

        win = Toplevel(self)
        win.configure(bg=BG_DARK)
        win.title("Rendering mix")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        # Closing the dialog mid-render just dismisses the UI — the worker
        # thread keeps writing the WAV in the background, and _poll picks
        # up completion. Much less surprising than the window grabbing
        # input forever or crashing on close.
        def _on_render_dialog_close() -> None:
            try:
                win.grab_release()
                win.destroy()
            except tk.TclError:
                pass
            self._render_window = None
        win.protocol("WM_DELETE_WINDOW", _on_render_dialog_close)
        tk.Label(win, text=f"RENDERING\n{out_path.name}",
                 bg=BG_DARK, fg=FG_TEXT,
                 font=("Helvetica", 11),
                 justify="center").pack(padx=22, pady=(18, 10))
        bar = ttk.Progressbar(win, length=360, maximum=1000,
                              mode="determinate",
                              style="Zoom.Horizontal.TProgressbar")
        bar.pack(padx=22, pady=(0, 18))
        self._render_window = win
        frac_holder = {"value": 0.0}
        done_holder = {"done": False, "error": None}

        def _on_progress(f: float) -> None:
            frac_holder["value"] = f

        def _worker() -> None:
            try:
                was_playing = self.engine.is_playing()
                if was_playing:
                    self.engine.pause()
                self.engine.render_mix(out_path, progress=_on_progress)
            except Exception as exc:  # noqa: BLE001
                done_holder["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                done_holder["done"] = True

        threading.Thread(target=_worker, daemon=True).start()

        def _poll() -> None:
            # If the mixer or render dialog was closed while the worker
            # thread is still churning, stop polling cleanly.
            try:
                if not self.winfo_exists():
                    return
                if not win.winfo_exists():
                    self._render_window = None
                    return
            except tk.TclError:
                return
            try:
                bar["value"] = frac_holder["value"] * 1000
            except tk.TclError:
                return
            if done_holder["done"]:
                try:
                    win.grab_release()
                    win.destroy()
                except tk.TclError:
                    pass
                self._render_window = None
                if done_holder["error"]:
                    messagebox.showerror(
                        "DP-03 Extractor",
                        f"Render failed:\n\n{done_holder['error']}")
                    return
                if messagebox.askyesno(
                        "DP-03 Extractor",
                        f"Wrote {out_path.name}.\nReveal in Finder?"):
                    if sys.platform == "darwin":
                        os.system(f"open -R {str(out_path)!r}")
                    elif sys.platform.startswith("linux"):
                        os.system(f"xdg-open {str(out_path.parent)!r}")
                    else:
                        os.startfile(str(out_path.parent))  # type: ignore[attr-defined]
                return
            self.after(120, _poll)

        self.after(120, _poll)

    # ----------------------- poll / housekeeping -----------------------

    def _start_poll(self) -> None:
        def _tick() -> None:
            # Bail cleanly if the window was destroyed while a tick was
            # queued — otherwise we'd hit AttributeError / TclError
            # touching Tk vars and widgets that no longer exist.
            try:
                if not self.winfo_exists():
                    return
            except tk.TclError:
                return
            try:
                if not self._scrub_dragging:
                    seconds = self.engine.position_seconds()
                    length = self.engine.length_seconds()
                    if length > 0:
                        self._scrub_var.set(min(seconds, length))
                    self._time_var.set(self._format_time(seconds))
                    if self.engine.is_playing():
                        self._play_btn.configure(text="\u25B6  PLAY",
                                                 bg=ACCENT, fg="#111111")
                    else:
                        self._play_btn.configure(text="\u25B6  PLAY",
                                                 bg=BG_RAISED, fg=FG_TEXT)
                    if self._waveform_visible:
                        self._waveform_panel.update_playhead(
                            self.engine.position_frames())
                    # --- level meters ---
                    # When not playing the audio callback isn't running, so
                    # engine levels stay frozen at the last block's peaks —
                    # push zeros instead so meters smoothly decay on
                    # pause/stop instead of sticking lit.
                    if self.engine.is_playing():
                        levels = self.engine.get_track_levels()
                        l_peak, r_peak = self.engine.get_master_levels()
                    else:
                        levels = []
                        l_peak = r_peak = 0.0
                    for idx, strip in enumerate(self._strips):
                        peak = levels[idx] if idx < len(levels) else 0.0
                        strip.update_meter(peak)
                    self._master.update_meters(l_peak, r_peak)
                    # Clip LED is sticky on the engine side — we just
                    # mirror its state each tick. The button's own
                    # click handler flips it back off.
                    self._master.set_clip(self.engine.has_clipped())
            except tk.TclError:
                # Window was torn down during the tick — stop rescheduling.
                return
            self._poll_after_id = self.after(80, _tick)
        _tick()

    def _on_close(self) -> None:
        if self._poll_after_id:
            try:
                self.after_cancel(self._poll_after_id)
            except Exception:  # noqa: BLE001
                pass
            self._poll_after_id = None
        # Tear down an in-flight render dialog if one is up. The render
        # worker thread will finish writing to disk in the background —
        # we just make sure its completion poll and its UI don't try to
        # touch a destroyed mixer window.
        if self._render_window is not None:
            try:
                if self._render_window.winfo_exists():
                    self._render_window.grab_release()
                    self._render_window.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._render_window = None
        try:
            self.engine.stop()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()

    # ----------------------- helpers -----------------------

    @staticmethod
    def _format_time(seconds: float) -> str:
        if seconds < 0:
            seconds = 0.0
        minutes = int(seconds // 60)
        rem = seconds - minutes * 60
        return f"{minutes}:{rem:05.2f}"


def show_audio_unavailable_dialog(parent: tk.Widget) -> None:
    """Open a help dialog when numpy/sounddevice aren't installed."""
    win = Toplevel(parent)
    win.configure(bg=BG_DARK)
    win.title("Mixer requires extra packages")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()
    # Build the install command around the ACTUAL interpreter running the
    # app — hardcoding /opt/homebrew/bin/python3.12 (or anything else)
    # lies to the user whenever they're on a different Python (3.13, 3.14,
    # system /usr/bin/python3, a venv, etc.) and sends them pip-installing
    # into an interpreter that never gets imported here.
    py = sys.executable or "python3"
    if sys.platform.startswith("win"):
        # Windows: sys.executable is usually a full path; quote it and
        # skip --break-system-packages (Windows pip doesn't use PEP 668).
        install_line = f'"{py}" -m pip install --user numpy sounddevice'
    elif sys.platform == "darwin":
        # macOS Homebrew Python refuses pip installs without
        # --break-system-packages; --user keeps things out of the system
        # site-packages so it's safe on Apple-shipped Python too.
        install_line = (f"{py} -m pip install --user "
                        "--break-system-packages numpy sounddevice")
    else:
        install_line = f"{py} -m pip install --user numpy sounddevice"
    body = (
        "The live mixer needs NumPy and sounddevice,\n"
        "which aren't installed for the Python running\n"
        "this app.\n\n"
        "In a terminal, run:\n\n"
        f"    {install_line}\n\n"
        "Then re-launch DP-03 Extractor."
    )
    tk.Label(win, text=body, bg=BG_DARK, fg=FG_TEXT, justify="left",
             font=("Menlo", 11)).pack(padx=20, pady=(16, 12))
    tk.Button(win, text="OK", bg=ACCENT, fg="#111111",
              activebackground=ACCENT_BRIGHT, activeforeground="#111111",
              relief="flat", bd=0, font=("Helvetica", 10, "bold"),
              padx=16, pady=4,
              command=win.destroy).pack(pady=(0, 16))
