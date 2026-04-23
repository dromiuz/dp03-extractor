from __future__ import annotations

from pathlib import Path
import wave

from .bfs.alloc_table import get_cluster_size, get_mtr_base, read_alloc_table
from .bfs.extents import SECTOR_BYTES, dedupe_extent_pairs, resolve_extent_pairs_for_master
from .bfs.records import parse_master_record
from .tracks import parse_track_table

DEFAULT_SAMPLE_RATE = 44100


def extract_master_pcm(image_path: Path, byte_base_mtr_relative: int, master_offset: int) -> bytes:
    """Extract the raw PCM byte stream for one master record.

    The extraction follows the validated DP-03SD BFS formula:

      * Walk master[+0x24] -> SFD[+0x14] -> inode[+0x24] -> primary extent
        leaf chain (forward via leaf[+0x0C]).
      * Collect (cluster_id, byte_offset) pairs from each leaf's 6 slots.
      * Sort pairs ascending by byte_offset (time order).
      * For each pair, read the FULL 0x18000 bytes of the BFS sector at
        mtr_base + cluster_id * 0x18000. Each sector's 96 KiB is one
        contiguous slice of this master's audio.
      * Concatenate sector bytes in boff order, then truncate to the
        master's declared size (samples * 2 for 16-bit mono).

    This reproduces the DP-03's own TEX001_N.WAV export byte-identically.
    """
    table = read_alloc_table(image_path, byte_base_mtr_relative)
    master = parse_master_record(table, master_offset)
    ordered_pairs = dedupe_extent_pairs(resolve_extent_pairs_for_master(table, master_offset))
    mtr_base = get_mtr_base(image_path)
    _cluster_size = get_cluster_size(image_path)
    out = bytearray()
    with image_path.open("rb") as handle:
        for cluster_id, _byte_offset in ordered_pairs:
            handle.seek(mtr_base + cluster_id * SECTOR_BYTES)
            out.extend(handle.read(SECTOR_BYTES))
    if 0 < master.declared_bytes <= len(out):
        return bytes(out[:master.declared_bytes])
    return bytes(out)


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(DEFAULT_SAMPLE_RATE)
        handle.writeframes(pcm)


def extract_project_tracks(image_path: Path, byte_base_mtr_relative: int, out_dir: Path) -> list[Path]:
    table = read_alloc_table(image_path, byte_base_mtr_relative)
    tracks = parse_track_table(table)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for track in tracks:
        if track.master_offset is None:
            continue
        pcm = extract_master_pcm(image_path, byte_base_mtr_relative, track.master_offset)
        target = out_dir / f"Track{track.track_index + 1}.wav"
        _write_wav(target, pcm)
        written.append(target)
    return written
