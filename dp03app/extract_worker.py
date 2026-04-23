"""Background extraction worker.

Wraps `dp03extract.extract.extract_master_pcm` with a threaded API the
GUI can drive without freezing. Writes one .wav per non-empty master,
named by project + track slot.
"""
from __future__ import annotations

import queue
import threading
import traceback
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from dp03extract.extract import DEFAULT_SAMPLE_RATE, extract_master_pcm

from .project_scanner import ProjectSummary


@dataclass
class ExtractJob:
    project: ProjectSummary
    output_dir: Path  # per-project subdir gets created here


@dataclass
class ExtractResult:
    project: ProjectSummary
    written_files: list[Path]
    skipped_masters: int
    error: str | None = None


def _slugify(name: str) -> str:
    cleaned = []
    for char in name:
        if char.isalnum():
            cleaned.append(char)
        elif char in " -_":
            cleaned.append("_")
    slug = "".join(cleaned).strip("_")
    return slug or "UNKNOWN"


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(DEFAULT_SAMPLE_RATE)
        handle.writeframes(pcm)


def extract_one_project(image_path: Path, job: ExtractJob,
                        *, on_track: Callable[[str], None] | None = None
                        ) -> ExtractResult:
    """Extract every non-empty master from one project. Blocking."""
    project = job.project
    target_dir = job.output_dir / f"{project.project_index:03d}_{_slugify(project.name)}"
    target_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    skipped = 0
    try:
        for master in project.masters:
            if master.declared_bytes <= 0:
                skipped += 1
                continue
            # Use the DP-03 track slot (0..7) so filenames match the GUI mixer
            # labels — empty slots don't renumber the remaining tracks.
            track_number = master.track_id + 1
            label = f"Track{track_number}"
            if on_track:
                on_track(f"{project.name or f'project{project.project_index}'} · {label}")
            pcm = extract_master_pcm(image_path, project.byte_base_mtr_relative, master.slot_offset)
            wav_path = target_dir / f"{label}.wav"
            _write_wav(wav_path, pcm)
            written.append(wav_path)
    except Exception as exc:  # noqa: BLE001 - we surface to the UI
        return ExtractResult(project=project, written_files=written,
                             skipped_masters=skipped,
                             error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    return ExtractResult(project=project, written_files=written, skipped_masters=skipped)


class ExtractionRunner:
    """Runs a sequence of ExtractJobs in a background thread.

    Usage from the UI thread:
        runner = ExtractionRunner(image_path, jobs, on_event=...)
        runner.start()
        # UI polls runner.poll_events() from a tkinter .after loop
    """

    EVENT_STARTED = "started"
    EVENT_PROJECT_START = "project_start"
    EVENT_TRACK = "track"
    EVENT_PROJECT_DONE = "project_done"
    EVENT_ALL_DONE = "all_done"
    EVENT_ERROR = "error"

    def __init__(self, image_path: Path, jobs: Iterable[ExtractJob]) -> None:
        self.image_path = Path(image_path)
        self.jobs = list(jobs)
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self.results: list[ExtractResult] = []

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="dp03-extract-worker")
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def poll_events(self) -> list[tuple[str, object]]:
        out: list[tuple[str, object]] = []
        while True:
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return out

    def _emit(self, event: str, payload: object = None) -> None:
        self._queue.put((event, payload))

    def _run(self) -> None:
        self._emit(self.EVENT_STARTED, len(self.jobs))
        try:
            for index, job in enumerate(self.jobs):
                if self._cancel.is_set():
                    break
                self._emit(self.EVENT_PROJECT_START, (index, job.project))

                def _on_track(label: str) -> None:
                    self._emit(self.EVENT_TRACK, label)

                result = extract_one_project(self.image_path, job, on_track=_on_track)
                self.results.append(result)
                if result.error:
                    self._emit(self.EVENT_ERROR, (job.project, result.error))
                self._emit(self.EVENT_PROJECT_DONE, result)
        except Exception as exc:  # noqa: BLE001
            self._emit(self.EVENT_ERROR, (None, f"Worker crashed: {exc}\n{traceback.format_exc()}"))
        finally:
            self._emit(self.EVENT_ALL_DONE, None)
