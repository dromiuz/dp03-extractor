from __future__ import annotations

import struct
from dataclasses import dataclass

REC_SIZE = 0x40
SENTINEL = 0xFFFFFFFF
FLAG_SFD_META = 0x80020007
MASTER_FLAGS = {0x80030000, 0x80030001}
INODE_FLAGS = {0x80104900, 0x80104905, 0x80104906}
EXTENT_LEAF_FLAGS = {
    0x90010001,
    0x90010002,
    0x90010003,
    0x90010004,
    0x90010005,
    0x90010006,
}


@dataclass(frozen=True)
class MasterRecord:
    slot_offset: int
    magic: int
    sfd_meta_offset: int
    data_chain_offset: int
    declared_bytes: int


def u32(data: bytes, offset: int) -> int:
    return struct.unpack(">I", data[offset:offset + 4])[0]


def rec_at(table: bytes, slot: int) -> bytes | None:
    if slot is None or slot < 0 or slot + REC_SIZE > len(table):
        return None
    return table[slot:slot + REC_SIZE]


def parse_master_record(table: bytes, slot_offset: int) -> MasterRecord:
    """Parse a master record.

    Field layout (16-bit mono master, 0x40 bytes):
      +0x04  master magic (0x80030000 / 0x80030001)
      +0x24  SFD-meta offset (follow SFD[+0x14] -> inode)
      +0x2C  duration in samples
      +0x30  duration in samples (duplicate of +0x2C on SONG116 masters)
      +0x34  fallback bare-data-chain leaf head (when there is no inode)

    Declared output size is samples * 2 (16-bit mono -> 2 bytes/sample).
    """
    record = rec_at(table, slot_offset)
    if record is None:
        raise ValueError(f"invalid master slot offset: 0x{slot_offset:X}")
    magic = u32(record, 0x04)
    if magic not in MASTER_FLAGS:
        raise ValueError(f"slot 0x{slot_offset:X} is not a master record: 0x{magic:08X}")
    sfd_meta_offset = u32(record, 0x24)
    data_chain_offset = u32(record, 0x34)
    declared_samples = u32(record, 0x2C)
    declared_bytes = declared_samples * 2
    return MasterRecord(
        slot_offset=slot_offset,
        magic=magic,
        sfd_meta_offset=sfd_meta_offset,
        data_chain_offset=data_chain_offset,
        declared_bytes=declared_bytes,
    )


def resolve_master_extent_head(table: bytes, slot_offset: int) -> int:
    """Resolve the primary extent-leaf head offset for a master.

    Follows master[+0x24] -> SFD[+0x14] -> inode[+0x24]. If the inode
    path is missing, falls back to the bare data-chain pointer at
    master[+0x34]. Only the primary subtree at inode[+0x24] is returned;
    overflow subtrees at +0x10/+0x20/+0x28/+0x38 are redundant and would
    duplicate tail sectors.
    """
    record = rec_at(table, slot_offset)
    if record is None:
        raise ValueError(f"invalid master slot offset: 0x{slot_offset:X}")
    raw_sfd_offset = u32(record, 0x24)
    sfd_record = rec_at(table, raw_sfd_offset)
    if sfd_record is None or u32(sfd_record, 0x04) != FLAG_SFD_META:
        return u32(record, 0x34)
    inode_offset = u32(sfd_record, 0x14)
    inode_record = rec_at(table, inode_offset)
    if inode_record is None or u32(inode_record, 0x04) not in INODE_FLAGS:
        return 0
    head = u32(inode_record, 0x24)
    head_record = rec_at(table, head)
    if head_record is not None and u32(head_record, 0x04) in EXTENT_LEAF_FLAGS:
        return head
    return 0
