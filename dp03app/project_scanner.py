"""Full-card project + master scan for DP-03SD card images.

The DP-03SD's allocation-table region places every project's 0x20000-byte
structural block at an absolute offset that we can locate by signature:

  base_abs + 0x40  ->  00 00 00 40 80 00 00 00 ?? ?? ?? ?? E3 1B A5 FF
  base_abs + 0x50  ->  8-char ASCII project name

We scan the whole image for this signature (it only appears at real
project roots), then for each project we parse its local alloc-table
header to find master records and read their declared durations.

This lets the app work on ANY DP-03 card image without needing a
pre-built catalog CSV.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from dp03extract.bfs.records import MASTER_FLAGS, REC_SIZE, parse_master_record, u32
from dp03extract.tracks import parse_track_table

# Signature pattern at (base_abs + 0x40).
_ANCHOR = b"\x00\x00\x00\x40\x80\x00\x00\x00"
_SIG = b"\xE3\x1B\xA5\xFF"
_ANCHOR_ALIGN = 0x40
_NAME_OFFSET_IN_BLOCK = 0x50
_NAME_LEN = 8

# The per-project alloc-table region is 0x20000 bytes (128 KiB) starting
# at base_abs. We scan it at 0x40 stride for master records.
ALLOC_TABLE_SIZE = 0x20000
SAMPLE_RATE = 44100


@dataclass(frozen=True)
class MasterSummary:
    slot_offset: int
    magic: int
    declared_bytes: int  # samples * 2 (output size of the mono WAV)
    track_id: int = 0    # DP-03 track slot (0..7); -1 if unknown

    @property
    def declared_samples(self) -> int:
        return self.declared_bytes // 2

    @property
    def duration_seconds(self) -> float:
        return self.declared_samples / SAMPLE_RATE


@dataclass
class ProjectSummary:
    project_index: int        # position in discovery order
    byte_base_abs: int        # absolute byte offset on card
    byte_base_mtr_relative: int  # offset relative to MTR base (for extract)
    name: str
    masters: list[MasterSummary] = field(default_factory=list)

    @property
    def track_count(self) -> int:
        """Count of non-zero-length masters — a rough 'track' count."""
        return sum(1 for m in self.masters if m.declared_bytes > 0)

    @property
    def total_duration_seconds(self) -> float:
        return max((m.duration_seconds for m in self.masters), default=0.0)

    @property
    def display_duration(self) -> str:
        secs = self.total_duration_seconds
        if secs < 1:
            return "—"
        m = int(secs // 60)
        s = int(secs - m * 60)
        return f"{m}:{s:02d}"


def _printable_ascii(buffer: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in buffer)


def find_project_roots(image_path: Path, *, chunk_size: int = 16 * 1024 * 1024,
                       progress: Callable[[int, int], None] | None = None) -> list[tuple[int, str]]:
    """Scan the whole image for project-root signatures.

    Returns a list of (byte_base_abs, name) tuples, sorted by offset.
    `progress(done_bytes, total_bytes)` is called periodically if provided.
    """
    image_path = Path(image_path)
    total = image_path.stat().st_size
    overlap = 64
    hits: dict[int, str] = {}

    with image_path.open("rb") as handle:
        position = 0
        while position < total:
            handle.seek(position)
            buffer = handle.read(chunk_size + overlap)
            if not buffer:
                break
            cursor = 0
            while True:
                idx = buffer.find(_ANCHOR, cursor)
                if idx < 0 or idx > len(buffer) - 24:
                    break
                absolute = position + idx
                if absolute % _ANCHOR_ALIGN != 0:
                    cursor = idx + 1
                    continue
                if buffer[idx + 12:idx + 16] != _SIG:
                    cursor = idx + 1
                    continue
                base = absolute - 0x40
                name_bytes = buffer[idx + 16:idx + 16 + _NAME_LEN]
                name = _printable_ascii(name_bytes).rstrip(". ").strip()
                hits.setdefault(base, name or "")
                cursor = idx + _ANCHOR_ALIGN
            if position + chunk_size >= total:
                break
            position += chunk_size
            if progress is not None:
                progress(min(position, total), total)

    if progress is not None:
        progress(total, total)
    return sorted(hits.items())


def parse_project_masters(image_path: Path, byte_base_abs: int) -> list[MasterSummary]:
    """Enumerate this project's user-facing tracks — up to 8 per DP-03 spec.

    Walks the 8-slot track table at +0x1A80 (stride 0x80) via
    dp03extract.tracks.parse_track_table. Each allocated slot resolves via
    inode[+0x20] to the master record for the slot's *active* V-take —
    so multiple V-takes collapse to the single currently-selected take,
    and stale/bounce masters scattered in the alloc table are ignored.

    Returns masters in track-slot order (track 0 first), skipping empty
    slots. Result length is always ≤ 8.
    """
    with image_path.open("rb") as handle:
        handle.seek(byte_base_abs)
        table = handle.read(ALLOC_TABLE_SIZE)

    masters: list[MasterSummary] = []
    if len(table) < ALLOC_TABLE_SIZE:
        return masters

    assignments = parse_track_table(table)
    for assignment in assignments:
        if assignment.master_offset is None:
            continue
        try:
            record = parse_master_record(table, assignment.master_offset)
        except ValueError:
            continue
        masters.append(
            MasterSummary(
                slot_offset=assignment.master_offset,
                magic=record.magic,
                declared_bytes=record.declared_bytes,
                track_id=assignment.track_index,
            )
        )
    return masters


def scan_card(image_path: Path,
              *,
              progress: Callable[[str, float], None] | None = None) -> list[ProjectSummary]:
    """Full scan: find every project root, read names, and enumerate masters.

    `progress(stage_label, fraction_0_to_1)` is called as the scan advances.
    """
    image_path = Path(image_path)

    def _scan_progress(done: int, total: int) -> None:
        if progress:
            progress("Scanning card", done / max(total, 1))

    roots = find_project_roots(image_path, progress=_scan_progress)
    if not roots:
        return []

    # Derive MTR base: project 0 (first root) has its base at MTR + 0x288000
    # (the first alloc-table region). But on some cards the first project
    # root is at that exact address; the "MTR base" can also be found by
    # parsing the MBR. We compute byte_base_mtr_relative relative to the
    # FIRST project's known MTR offset for simplicity: the MTR base always
    # lies immediately after the small FAT32 partition (parsed by the
    # backend). We re-use dp03extract.bfs.alloc_table.get_mtr_base.
    from dp03extract.bfs.alloc_table import get_mtr_base

    mtr_base = get_mtr_base(image_path)

    projects: list[ProjectSummary] = []
    total_roots = len(roots)
    for index, (base, name) in enumerate(roots):
        if progress:
            progress(f"Reading project {index + 1}/{total_roots}", index / max(total_roots, 1))
        mtr_relative = base - mtr_base
        if mtr_relative < 0:
            # root is before MTR base — shouldn't happen but skip defensively
            continue
        masters = parse_project_masters(image_path, base)
        projects.append(
            ProjectSummary(
                project_index=index,
                byte_base_abs=base,
                byte_base_mtr_relative=mtr_relative,
                name=name,
                masters=masters,
            )
        )

    if progress:
        progress("Done", 1.0)
    return projects


def filter_projects_with_audio(projects: Iterable[ProjectSummary]) -> list[ProjectSummary]:
    """Return only projects that have at least one non-empty master."""
    return [p for p in projects if p.track_count > 0]
