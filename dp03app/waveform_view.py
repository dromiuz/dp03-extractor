"""FL Studio-style waveform timeline for the mixer.

A single panel stacked above the channel strips. Every track gets a
horizontal lane showing a min/max peak envelope across the full project
duration, and a single playhead line scrubs across all lanes in sync
with the transport.

Sizing model
------------
The panel has a *fixed* pixel height that the user can adjust by
dragging the resize handle at the bottom of the panel (or via the
"−" / "+" buttons in the panel header). Per-lane height is computed
to fill whatever height the user has given the panel, divided equally
among tracks, clamped to [MIN_LANE_HEIGHT, PREFERRED_LANE_HEIGHT].

This means an 8-track project at 130 px panel height = ~16 px/lane —
cramped but visible and it doesn't steal space from the faders. Drag
the panel taller to make each lane bigger.

Implementation notes
--------------------
* Peak envelope is computed with numpy min/max on a reshaped slice.
  Fast enough (<50 ms for a few million int16 samples at ~1200 bins)
  that we can recompute on resize without caching pyramids.
* The envelope is drawn as ONE filled polygon per track.
* The playhead is one `create_line` item per lane; we only update its
  coords() each tick, never redraw the envelope.
"""
from __future__ import annotations

import re
from typing import Callable, Optional
import tkinter as tk

try:
    import numpy as np  # type: ignore
except Exception:  # noqa: BLE001
    np = None  # type: ignore


# --- palette (mirrors mixer_window.py) ---
BG_DARK = "#0a0b0d"
BG_PANEL = "#13151a"
BG_RAISED = "#1c1e24"
FG_TEXT = "#d4d4d4"
FG_MUTED = "#6b6e75"
ACCENT = "#c8844a"
PLAYHEAD = "#d4d4d4"
LANE_DIVIDER = "#08090b"
ZERO_LINE = "#1c1e24"
RESIZE_HANDLE_BG = "#08090b"
RESIZE_HANDLE_HOT = "#1c1e24"

# --- sizing ---
DEFAULT_HEIGHT = 130
MIN_HEIGHT = 50
MAX_HEIGHT = 520
HEIGHT_STEP = 40                 # +/- button step
PREFERRED_LANE_HEIGHT = 46       # upper cap on per-lane height
MIN_LANE_HEIGHT = 10             # below this we bail on drawing the lane

# --- layout constants ---
LABEL_WIDTH = 110                # left gutter with track badge + name
TOP_PAD = 2
BOTTOM_PAD = 2
MIN_LANE_WIDTH = 40              # bail below this (window too narrow)
MAX_BINS = 2000                  # cap envelope resolution


def _compute_peaks(samples, num_bins: int):
    """Return (mins, maxs) normalized to [-1, 1] at `num_bins` resolution."""
    if np is None or samples is None or num_bins <= 0 or samples.size == 0:
        return None, None
    n = int(samples.shape[0])
    if n <= num_bins:
        f = samples.astype(np.float32) / 32768.0
        idx = np.linspace(0, n - 1, num_bins).astype(np.int64)
        val = f[idx]
        return val.copy(), val.copy()
    per_bin = n // num_bins
    trimmed = samples[: per_bin * num_bins].reshape(num_bins, per_bin)
    mins = trimmed.min(axis=1).astype(np.float32) / 32768.0
    maxs = trimmed.max(axis=1).astype(np.float32) / 32768.0
    return mins, maxs


def _clamp(v, lo, hi):
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


class WaveformPanel(tk.Frame):
    """Fixed-height, user-resizable waveform timeline."""

    def __init__(self, parent: tk.Widget, sample_rate: int,
                 track_colors: list[str],
                 on_seek_seconds: Callable[[float], None],
                 height: int = DEFAULT_HEIGHT) -> None:
        super().__init__(parent, bg=BG_DARK)
        self._sample_rate = sample_rate
        self._colors = list(track_colors)
        self._on_seek = on_seek_seconds
        self._tracks: list = []
        self._length_frames: int = 0

        # Per-lane (y_top, y_bottom) tuples stashed during _redraw — used
        # by update_playhead to know where each playhead segment lives.
        self._lane_bounds: list[tuple[float, float]] = []
        self._playhead_ids: list[int] = []

        self._canvas_width: int = 0
        self._resize_after: Optional[str] = None
        self._current_height = _clamp(height, MIN_HEIGHT, MAX_HEIGHT)

        # Drag-resize bookkeeping
        self._drag_start_y: int = 0
        self._drag_start_h: int = 0

        # ---- header row ----
        header = tk.Frame(self, bg=BG_DARK)
        header.pack(side=tk.TOP, fill=tk.X)
        tk.Label(header, text="WAVEFORM", bg=BG_DARK, fg=FG_MUTED,
                 font=("Helvetica", 9, "bold")).pack(side=tk.LEFT,
                                                     padx=(12, 6),
                                                     pady=(2, 0))

        # size nudge buttons
        size_btn_style = dict(
            bg=BG_RAISED, fg=FG_TEXT, activebackground=ACCENT,
            activeforeground="#111111", relief="flat", bd=0,
            font=("Helvetica", 9, "bold"), padx=6, pady=0, width=2,
        )
        tk.Button(header, text="\u2212",
                  command=self._shrink, **size_btn_style
                  ).pack(side=tk.LEFT, padx=(0, 2), pady=(1, 0))
        tk.Button(header, text="+",
                  command=self._grow, **size_btn_style
                  ).pack(side=tk.LEFT, padx=(0, 8), pady=(1, 0))

        self._duration_var = tk.StringVar(value="")
        tk.Label(header, textvariable=self._duration_var, bg=BG_DARK,
                 fg=FG_MUTED, font=("Helvetica", 9)).pack(side=tk.RIGHT,
                                                          padx=(0, 12),
                                                          pady=(2, 0))

        # ---- canvas ----
        canvas_host = tk.Frame(self, bg=BG_PANEL)
        canvas_host.pack(side=tk.TOP, fill=tk.X, expand=False,
                         padx=10, pady=(4, 0))
        self._canvas = tk.Canvas(canvas_host, bg=BG_PANEL,
                                 highlightthickness=0, bd=0,
                                 height=self._current_height)
        self._canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._canvas.bind("<Configure>", self._on_configure)
        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<B1-Motion>", self._on_click)

        # ---- resize grip (drag the bottom edge) ----
        grip = tk.Frame(self, bg=RESIZE_HANDLE_BG, height=6,
                        cursor="sb_v_double_arrow")
        grip.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 4))
        grip.bind("<Enter>",
                  lambda _e: grip.configure(bg=RESIZE_HANDLE_HOT))
        grip.bind("<Leave>",
                  lambda _e: grip.configure(bg=RESIZE_HANDLE_BG))
        grip.bind("<ButtonPress-1>", self._on_grip_press)
        grip.bind("<B1-Motion>", self._on_grip_drag)
        # Two narrow rails so the grip reads as an affordance
        for dx in (-18, 18):
            rail = tk.Frame(grip, bg="#2a2d34", width=24, height=2)
            rail.place(relx=0.5, rely=0.5, anchor="center",
                       x=dx, y=0)

    # ---------------------------------------------------------- public

    def load_tracks(self, tracks: list) -> None:
        """Call once per project after decoding completes."""
        self._tracks = list(tracks)
        self._length_frames = max((t.length_samples for t in tracks),
                                  default=0)
        if self._length_frames > 0:
            total_s = self._length_frames / self._sample_rate
            m, s = divmod(total_s, 60)
            self._duration_var.set(f"{int(m)}:{s:05.2f} total")
        self.update_idletasks()
        self._redraw()

    def set_panel_height(self, h: int) -> None:
        new_h = _clamp(int(h), MIN_HEIGHT, MAX_HEIGHT)
        if new_h == self._current_height:
            return
        self._current_height = new_h
        self._canvas.configure(height=new_h)
        self._redraw()

    def update_playhead(self, frame_index: int) -> None:
        """Cheap per-tick call. Only moves playhead segments; no redraw."""
        if not self._playhead_ids or self._length_frames <= 0:
            return
        w = self._canvas_width
        if w <= LABEL_WIDTH:
            return
        frac = frame_index / self._length_frames
        frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
        x = LABEL_WIDTH + (w - LABEL_WIDTH) * frac
        for ph_id, (y0, y1) in zip(self._playhead_ids, self._lane_bounds):
            self._canvas.coords(ph_id, x, y0 + 1, x, y1 - 1)

    # ------------------------------------------------ size adjustment

    def _shrink(self) -> None:
        self.set_panel_height(self._current_height - HEIGHT_STEP)

    def _grow(self) -> None:
        self.set_panel_height(self._current_height + HEIGHT_STEP)

    def _on_grip_press(self, event) -> None:
        self._drag_start_y = event.y_root
        self._drag_start_h = self._current_height

    def _on_grip_drag(self, event) -> None:
        dy = event.y_root - self._drag_start_y
        self.set_panel_height(self._drag_start_h + dy)

    # ---------------------------------------------------- event handlers

    def _on_configure(self, event) -> None:
        self._canvas_width = int(event.width)
        if self._resize_after:
            try:
                self.after_cancel(self._resize_after)
            except Exception:  # noqa: BLE001
                pass
            self._resize_after = None
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        self._resize_after = self.after(120, self._safe_redraw)

    def _safe_redraw(self) -> None:
        """Redraw wrapper — no-op if the widget was torn down."""
        self._resize_after = None
        try:
            if not self.winfo_exists():
                return
            if not self._canvas.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            self._redraw()
        except tk.TclError:
            # Canvas may have been destroyed between the check and the
            # first drawing call — swallow and move on.
            return

    def _on_click(self, event) -> None:
        if self._length_frames <= 0:
            return
        w = self._canvas_width
        if w <= LABEL_WIDTH:
            return
        x = _clamp(event.x, LABEL_WIDTH, w)
        frac = (x - LABEL_WIDTH) / (w - LABEL_WIDTH)
        frac = _clamp(frac, 0.0, 1.0)
        seconds = (self._length_frames / self._sample_rate) * frac
        self._on_seek(seconds)

    # ------------------------------------------------------- rendering

    def _redraw(self) -> None:
        c = self._canvas
        c.delete("all")
        self._playhead_ids = []
        self._lane_bounds = []
        if np is None or not self._tracks:
            return
        w = self._canvas_width or int(c.winfo_width())
        if w <= 0:
            return
        lane_w = w - LABEL_WIDTH
        if lane_w < MIN_LANE_WIDTH:
            return
        num_bins = int(min(lane_w, MAX_BINS))
        if num_bins < 2:
            return
        bin_step = lane_w / num_bins

        num_tracks = len(self._tracks)
        avail = self._current_height - TOP_PAD - BOTTOM_PAD
        # Equal-share lane height, capped so lanes don't balloon with
        # few tracks + a tall panel.
        lane_h = avail / max(1, num_tracks)
        lane_h = min(lane_h, float(PREFERRED_LANE_HEIGHT))
        if lane_h < 2:
            return  # Panel too short to render anything useful
        # Center the lanes vertically if the cap leaves headroom.
        lanes_total = lane_h * num_tracks
        y_start = TOP_PAD + max(0.0, (avail - lanes_total) / 2)

        # When lanes are thin, hide badges/names to reduce visual noise.
        show_text = lane_h >= 22
        show_badge = lane_h >= 16

        for i, track in enumerate(self._tracks):
            y0 = y_start + i * lane_h
            y1 = y0 + lane_h
            y_mid = (y0 + y1) / 2
            half = max(1.5, (lane_h - 4) / 2)
            # Pull DP-03 track number out of the name so color + badge
            # match the mixer strips (and the hardware's own numbering).
            m = re.match(r"Track\s+(\d+)", track.name)
            display_num = int(m.group(1)) if m else (i + 1)
            color = self._colors[(display_num - 1) % len(self._colors)]

            # Lane background + dividers
            c.create_rectangle(0, y0, w, y1, fill=BG_PANEL, outline="")
            c.create_line(LABEL_WIDTH, y0, LABEL_WIDTH, y1,
                          fill=LANE_DIVIDER, width=1)
            c.create_line(0, y1, w, y1, fill=LANE_DIVIDER, width=1)

            # Track gutter: badge + short name
            if show_badge:
                badge_half_h = min(9, max(4, (lane_h - 4) / 2))
                c.create_rectangle(6, y_mid - badge_half_h,
                                   34, y_mid + badge_half_h,
                                   fill=color, outline="")
                c.create_text(20, y_mid, text=f"{display_num:02d}",
                              fill="#111111",
                              font=("Helvetica", 8, "bold"))
            else:
                # Just a 4-pixel color slab at far left so you can still
                # identify the track by color.
                c.create_rectangle(0, y0, 4, y1,
                                   fill=color, outline="")

            if show_text:
                short = (track.name if len(track.name) <= 14
                         else track.name[:13] + "\u2026")
                c.create_text(42, y_mid, text=short, anchor="w",
                              fill=FG_TEXT, font=("Helvetica", 9))

            # Zero-amplitude center line
            c.create_line(LABEL_WIDTH, y_mid, w, y_mid,
                          fill=ZERO_LINE, width=1)

            # Envelope polygon
            mins, maxs = _compute_peaks(track.samples, num_bins)
            if mins is None or maxs is None:
                continue
            pts: list[float] = []
            for k in range(num_bins):
                x = LABEL_WIDTH + k * bin_step
                pts.extend((x, y_mid - float(maxs[k]) * half))
            for k in range(num_bins - 1, -1, -1):
                x = LABEL_WIDTH + k * bin_step
                pts.extend((x, y_mid - float(mins[k]) * half))
            c.create_polygon(pts, fill=color, outline=color,
                             width=1, smooth=False)

            # Per-lane playhead segment (parked at left initially)
            ph = c.create_line(LABEL_WIDTH, y0 + 1, LABEL_WIDTH, y1 - 1,
                               fill=PLAYHEAD, width=1)
            self._playhead_ids.append(ph)
            self._lane_bounds.append((y0, y1))

        c.configure(scrollregion=(0, 0, w, self._current_height))
