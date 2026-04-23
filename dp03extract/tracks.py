from __future__ import annotations

from dataclasses import dataclass

from .bfs.records import MASTER_FLAGS, SENTINEL, u32


@dataclass(frozen=True)
class TrackAssignment:
    track_index: int
    master_offset: int | None


TRACK_SLOT_START = 0x1A80
TRACK_SLOT_END = 0x9A80
TRACK_SLOT_STRIDE = 0x80


def parse_track_table(table: bytes) -> list[TrackAssignment]:
    tracks: list[TrackAssignment] = [TrackAssignment(track_index=index, master_offset=None) for index in range(8)]
    for slot_offset in range(TRACK_SLOT_START, TRACK_SLOT_END, TRACK_SLOT_STRIDE):
        slot_marker = u32(table, slot_offset + 0x28)
        if (slot_marker >> 24) & 0xFF != 0x09:
            continue
        track_index = (slot_marker >> 16) & 0xFF
        inode_offset = slot_offset + 0x40
        leaf_offset = u32(table, inode_offset + 0x20)
        if leaf_offset == SENTINEL:
            continue
        master_offset = None
        for pair_index in range(6):
            pair_offset = leaf_offset + 0x10 + pair_index * 8
            if pair_offset + 4 > len(table):
                break
            candidate = u32(table, pair_offset)
            if candidate == 0 or candidate == SENTINEL or candidate + 8 >= len(table):
                continue
            magic = u32(table, candidate + 0x04)
            if magic in MASTER_FLAGS:
                master_offset = candidate
                break
        if 0 <= track_index < len(tracks):
            tracks[track_index] = TrackAssignment(track_index=track_index, master_offset=master_offset)
    return tracks
