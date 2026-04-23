"""Real-time mixing engine for DP-03 projects.

Loads extracted PCM for every master in a project into memory, then feeds
a sounddevice OutputStream via a low-latency callback. Per-track state
(volume_dB, pan, mute, solo) can be updated from the UI thread under a
short-lived lock; the callback snapshots state once per block so we never
wait on the lock inside the audio thread.

Audio math
----------
  gain      = 10 ** (volume_dB / 20)      (linear amplitude)
  pan angle = (pan + 1) * pi / 4          (pan in [-1, +1])
  L_scale   = cos(angle)
  R_scale   = sin(angle)
  out_L    += src * gain * L_scale
  out_R    += src * gain * R_scale
Final buffer is clipped to [-1.0, +1.0] before output.

If numpy or sounddevice aren't installed the module still imports but
`AVAILABLE` is False; the UI can then disable the mixer button and
surface an install hint.
"""
from __future__ import annotations

import math
import threading
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

try:
    import numpy as np  # type: ignore
    _HAS_NUMPY = True
except Exception:  # noqa: BLE001
    np = None  # type: ignore
    _HAS_NUMPY = False

try:
    import sounddevice as sd  # type: ignore
    _HAS_SD = True
except Exception:  # noqa: BLE001
    sd = None  # type: ignore
    _HAS_SD = False

AVAILABLE = _HAS_NUMPY and _HAS_SD

SAMPLE_RATE = 44100
BLOCK_SIZE = 1024
CHANNELS_OUT = 2


@dataclass
class TrackState:
    name: str
    samples: "np.ndarray"  # int16, shape (N,)
    volume_db: float = 0.0
    pan: float = 0.0          # -1 = hard L, 0 = center, +1 = hard R
    mute: bool = False
    solo: bool = False

    @property
    def length_samples(self) -> int:
        return int(self.samples.shape[0])


class MixerEngine:
    """Real-time stereo mixer over a collection of mono tracks."""

    def __init__(self) -> None:
        if not AVAILABLE:
            raise RuntimeError(
                "MixerEngine needs numpy and sounddevice installed.")
        self._tracks: list[TrackState] = []
        self._lock = threading.Lock()
        self._stream: Optional["sd.OutputStream"] = None
        self._position: int = 0          # frame index into the mix
        self._length: int = 0            # length of the longest track
        self._is_playing: bool = False
        self._master_db: float = 0.0     # master bus gain in dB
        # Monotonic token the UI can watch if it wants to know when the
        # callback advanced the position; not currently used but cheap.
        self._tick: int = 0
        # Level meters: last-block peak for each track (post-gain,
        # pre-pan) and stereo peak on the master bus. Audio thread
        # writes, UI thread reads — single-float writes are atomic under
        # the GIL so no lock needed on the fast path.
        self._track_levels: list[float] = []
        self._master_levels: list[float] = [0.0, 0.0]
        # Sticky clip flag — set by the audio callback whenever the mix
        # bus hits ±1.0 (post master gain, pre-clip). The UI turns a red
        # indicator on until the user clicks it to reset. This is the
        # #1 thing a tracking musician needs to see.
        self._clip_flag: bool = False

    def set_master_db(self, db: float) -> None:
        with self._lock:
            self._master_db = float(db)

    # ------------------------- loading -------------------------

    def load_tracks(self, tracks: list[TrackState]) -> None:
        self.stop()
        with self._lock:
            self._tracks = list(tracks)
            self._length = max((t.length_samples for t in tracks), default=0)
            self._position = 0
            # One meter slot per loaded track — the audio thread indexes
            # into this list positionally.
            self._track_levels = [0.0] * len(tracks)
            self._master_levels = [0.0, 0.0]
            self._clip_flag = False

    @classmethod
    def track_from_wav(cls, wav_path: Path, name: str) -> TrackState:
        """Load a mono 16-bit 44.1 kHz WAV into a TrackState."""
        if not AVAILABLE:
            raise RuntimeError("numpy/sounddevice not available.")
        with wave.open(str(wav_path), "rb") as handle:
            nchannels = handle.getnchannels()
            sampwidth = handle.getsampwidth()
            framerate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
        if sampwidth != 2:
            raise ValueError(f"Expected 16-bit PCM, got {sampwidth*8}-bit.")
        arr = np.frombuffer(frames, dtype=np.int16)
        if nchannels > 1:
            arr = arr.reshape(-1, nchannels)[:, 0].copy()
        if framerate != SAMPLE_RATE:
            # Not resampling here — DP-03 is always 44.1k. Just warn by
            # raising so the UI notices a weird file before playback.
            raise ValueError(
                f"Expected {SAMPLE_RATE} Hz, got {framerate} Hz "
                f"({wav_path.name}).")
        return TrackState(name=name, samples=arr)

    @classmethod
    def track_from_pcm(cls, pcm_bytes: bytes, name: str) -> TrackState:
        """Wrap an already-decoded mono 16-bit PCM byte buffer."""
        if not AVAILABLE:
            raise RuntimeError("numpy/sounddevice not available.")
        arr = np.frombuffer(pcm_bytes, dtype=np.int16).copy()
        return TrackState(name=name, samples=arr)

    # ------------------------- state updates -------------------------

    def set_volume_db(self, index: int, db: float) -> None:
        with self._lock:
            if 0 <= index < len(self._tracks):
                self._tracks[index].volume_db = float(db)

    def set_pan(self, index: int, pan: float) -> None:
        with self._lock:
            if 0 <= index < len(self._tracks):
                self._tracks[index].pan = max(-1.0, min(1.0, float(pan)))

    def set_mute(self, index: int, mute: bool) -> None:
        with self._lock:
            if 0 <= index < len(self._tracks):
                self._tracks[index].mute = bool(mute)

    def set_solo(self, index: int, solo: bool) -> None:
        with self._lock:
            if 0 <= index < len(self._tracks):
                self._tracks[index].solo = bool(solo)

    def get_track(self, index: int) -> TrackState:
        with self._lock:
            return self._tracks[index]

    def track_count(self) -> int:
        return len(self._tracks)

    # ------------------------- transport -------------------------

    def play(self) -> None:
        # If we ran to the end of the longest track on the last play, the
        # callback auto-stopped via CallbackStop and position sits at
        # length. Re-pressing PLAY should rewind to the top instead of
        # appearing to do nothing.
        with self._lock:
            if self._length > 0 and self._position >= self._length:
                self._position = 0
        if self._stream is None:
            self._stream = sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS_OUT,
                dtype="float32",
                blocksize=BLOCK_SIZE,
                callback=self._callback,
            )
            self._stream.start()
        elif not self._stream.active:
            # After CallbackStop the stream is still open but finished;
            # closing + reopening is the most reliable way to restart
            # playback across sounddevice/PortAudio backends.
            try:
                self._stream.close()
            except Exception:  # noqa: BLE001 - best effort
                pass
            self._stream = sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS_OUT,
                dtype="float32",
                blocksize=BLOCK_SIZE,
                callback=self._callback,
            )
            self._stream.start()
        self._is_playing = True

    def pause(self) -> None:
        self._is_playing = False
        if self._stream is not None and self._stream.active:
            self._stream.stop()

    def stop(self) -> None:
        self._is_playing = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001 - cleanup best effort
                pass
            self._stream = None
        with self._lock:
            self._position = 0
            for i in range(len(self._track_levels)):
                self._track_levels[i] = 0.0
            self._master_levels[0] = 0.0
            self._master_levels[1] = 0.0

    def seek(self, frame_index: int) -> None:
        with self._lock:
            self._position = max(0, min(int(frame_index), self._length))

    def seek_seconds(self, seconds: float) -> None:
        self.seek(int(round(seconds * SAMPLE_RATE)))

    # ------------------------- queries -------------------------

    def is_playing(self) -> bool:
        return bool(self._is_playing and self._stream is not None
                    and self._stream.active)

    def position_frames(self) -> int:
        return self._position

    def position_seconds(self) -> float:
        return self._position / SAMPLE_RATE

    def length_frames(self) -> int:
        return self._length

    def length_seconds(self) -> float:
        return self._length / SAMPLE_RATE

    def get_track_levels(self) -> list[float]:
        """Snapshot of per-track post-gain peaks in [0, 1].

        Returned list is a fresh copy — safe to use from the UI thread
        without worrying about the audio callback clobbering it mid-read.
        """
        # list(...) is O(n) and atomic enough for meter purposes — at
        # worst we observe a mix of two adjacent callback blocks, which
        # is invisible at 80ms UI cadence.
        return list(self._track_levels)

    def get_master_levels(self) -> tuple[float, float]:
        """Master bus (L, R) peaks in [0, 1] post-clip."""
        return (self._master_levels[0], self._master_levels[1])

    def has_clipped(self) -> bool:
        """True if the output has clipped since the last reset_clip()."""
        return bool(self._clip_flag)

    def reset_clip(self) -> None:
        """Clear the sticky clip flag (user clicked the CLIP LED)."""
        self._clip_flag = False

    # ------------------------- callback -------------------------

    def _callback(self, outdata, frames, _time_info, status):  # noqa: ARG002
        # Snapshot state under lock so we never touch Python during mixing.
        with self._lock:
            start = self._position
            any_solo = any(t.solo for t in self._tracks)
            master_gain = 10.0 ** (self._master_db / 20.0)
            snapshots = []
            for t in self._tracks:
                gain = 10.0 ** (t.volume_db / 20.0)
                angle = (t.pan + 1.0) * math.pi / 4.0
                l_scale = math.cos(angle)
                r_scale = math.sin(angle)
                snapshots.append((t.samples, t.mute, t.solo, gain,
                                  l_scale, r_scale))
            end = start + frames
            self._position = end
            self._tick += 1
            num_levels = len(self._track_levels)

        outdata.fill(0.0)

        # Nothing loaded or we've run off the end.
        if not snapshots or start >= self._length:
            # Zero all meter levels so stalled meters don't look "hot".
            for i in range(num_levels):
                self._track_levels[i] = 0.0
            self._master_levels[0] = 0.0
            self._master_levels[1] = 0.0
            if start >= self._length and self._length > 0:
                # Wrap: auto-stop at end of longest track.
                self._is_playing = False
                raise sd.CallbackStop()
            return

        effective_end = min(end, self._length)
        out_frames = effective_end - start
        if out_frames <= 0:
            for i in range(num_levels):
                self._track_levels[i] = 0.0
            self._master_levels[0] = 0.0
            self._master_levels[1] = 0.0
            return

        for i, (samples, mute, solo, gain, l_scale, r_scale) in enumerate(snapshots):
            # Levels for skipped tracks always report silent — a muted
            # or solo-exclude track should show a dark meter, not whatever
            # it was doing pre-mute.
            if mute or (any_solo and not solo):
                if i < num_levels:
                    self._track_levels[i] = 0.0
                continue
            track_len = samples.shape[0]
            if start >= track_len:
                if i < num_levels:
                    self._track_levels[i] = 0.0
                continue
            take = min(out_frames, track_len - start)
            if take <= 0:
                if i < num_levels:
                    self._track_levels[i] = 0.0
                continue
            # int16 -> float32 in [-1, 1]
            src = samples[start:start + take].astype(np.float32) / 32768.0
            src *= gain
            # Per-track peak meter (post-gain, pre-pan) so the fader's
            # meter reacts to volume moves.
            if i < num_levels and src.size > 0:
                self._track_levels[i] = float(np.abs(src).max())
            outdata[:take, 0] += src * l_scale
            outdata[:take, 1] += src * r_scale

        # Master bus gain.
        if master_gain != 1.0:
            outdata *= master_gain
        # Clip to avoid wrap-around distortion.
        np.clip(outdata, -1.0, 1.0, out=outdata)

        # Master bus stereo peaks (post-clip — shows what the user will
        # actually hear out of the device).
        if outdata.shape[0] > 0:
            l_peak = float(np.abs(outdata[:, 0]).max())
            r_peak = float(np.abs(outdata[:, 1]).max())
            self._master_levels[0] = l_peak
            self._master_levels[1] = r_peak
            # If either channel pegged at full-scale the clip() call
            # above just chopped a sample — flag it for the UI.
            if l_peak >= 0.999 or r_peak >= 0.999:
                self._clip_flag = True

    # ------------------------- rendering -------------------------

    def render_mix(self,
                   out_path: Path,
                   *,
                   progress: Callable[[float], None] | None = None
                   ) -> Path:
        """Render the current state to a stereo 16-bit WAV (non-realtime)."""
        if not AVAILABLE:
            raise RuntimeError("numpy/sounddevice not available.")
        # Snapshot every field we need under the lock so a fader move
        # during the render can't tear the mix. Copying mutable fields
        # (volume_db, pan, mute, solo) into local tuples divorces the
        # render from the live TrackState objects.
        with self._lock:
            any_solo = any(t.solo for t in self._tracks)
            length = self._length
            master_gain = 10.0 ** (self._master_db / 20.0)
            track_snap = [
                (t.samples, float(t.volume_db), float(t.pan),
                 bool(t.mute), bool(t.solo))
                for t in self._tracks
            ]

        if length == 0:
            raise ValueError("Nothing to render — no tracks loaded.")

        # Pre-compute per-track scales
        scaled = []
        for samples, volume_db, pan, mute, solo in track_snap:
            if mute:
                continue
            if any_solo and not solo:
                continue
            gain = 10.0 ** (volume_db / 20.0)
            angle = (pan + 1.0) * math.pi / 4.0
            scaled.append((samples, gain * math.cos(angle),
                           gain * math.sin(angle)))

        mix = np.zeros((length, 2), dtype=np.float32)
        if not scaled:
            # Everything muted — write silence.
            if progress:
                progress(1.0)
        else:
            # Chunked to keep memory sane and to emit progress.
            chunk = 44100 * 4  # 4s chunks
            pos = 0
            while pos < length:
                stop = min(pos + chunk, length)
                for samples, l_scale, r_scale in scaled:
                    if pos >= samples.shape[0]:
                        continue
                    take = min(stop - pos, samples.shape[0] - pos)
                    src = samples[pos:pos + take].astype(np.float32) / 32768.0
                    mix[pos:pos + take, 0] += src * l_scale
                    mix[pos:pos + take, 1] += src * r_scale
                pos = stop
                if progress:
                    progress(pos / length)

        if master_gain != 1.0:
            mix *= master_gain
        np.clip(mix, -1.0, 1.0, out=mix)
        int16_mix = (mix * 32767.0).astype(np.int16)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_path), "wb") as handle:
            handle.setnchannels(2)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(int16_mix.tobytes())
        return out_path
