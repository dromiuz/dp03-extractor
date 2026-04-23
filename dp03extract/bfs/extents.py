from __future__ import annotations

from .records import EXTENT_LEAF_FLAGS, INODE_FLAGS, FLAG_SFD_META, SENTINEL, rec_at, u32

# A BFS sector (aka "cluster") on the DP-03SD is 0x18000 bytes = 96 KiB.
# Each extent-leaf cid is a sector number; cid -> mtr_base + cid * 0x18000.
# The full 0x18000 of a sector contributes to one master's audio stream.
SECTOR_BYTES = 0x18000

# Extent-leaf boff values step by 0xC000 between consecutive pairs even
# though each sector contributes 0x18000 bytes. boff is effectively a
# half-sector time index used only for sort ordering of the leaf pairs;
# the output position of a pair is not boff directly.
PAIR_STEP_BYTES = 0xC000

MAX_CLUSTER_ID = 0x200000


def _valid_pair(cluster_id: int, byte_offset: int) -> bool:
    if cluster_id in (0, SENTINEL) or cluster_id > MAX_CLUSTER_ID:
        return False
    if byte_offset == SENTINEL:
        return False
    if byte_offset % PAIR_STEP_BYTES != 0:
        return False
    return True


def _leaf_pairs(record: bytes) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    for index in range(6):
        offset = 0x10 + index * 8
        cluster_id = u32(record, offset)
        byte_offset = u32(record, offset + 4)
        if cluster_id == 0 and byte_offset == 0:
            continue
        if _valid_pair(cluster_id, byte_offset):
            pairs.append((cluster_id, byte_offset))
    return pairs


def _walk_leaf_forward(table: bytes, start: int, global_seen: set[int]) -> list[tuple[int, int]]:
    if start in global_seen:
        return []
    pairs: list[tuple[int, int]] = []
    seen: set[int] = set()
    current = start
    while current not in (0, SENTINEL) and current not in seen:
        record = rec_at(table, current)
        if record is None or u32(record, 0x04) not in EXTENT_LEAF_FLAGS:
            break
        seen.add(current)
        pairs.extend(_leaf_pairs(record))
        current = u32(record, 0x0C)
    global_seen.update(seen)
    return pairs


def _walk_inode_subtree(table: bytes, root_slot: int) -> list[tuple[int, int]]:
    """Walk the primary extent tree rooted at inode[+0x24].

    The primary subtree field is +0x24. Other candidate fields on the
    inode (+0x10, +0x20, +0x28, +0x38) point at redundant/overflow leaves
    that duplicate tail sectors; walking them would produce duplicate
    audio at the end of the track. SONG116 validation against the DP-03's
    own TEX001_N.WAV export confirms that +0x24 alone reproduces the
    native export byte-identically (MD5-equal).
    """
    record = rec_at(table, root_slot)
    if record is None:
        return []
    flag = u32(record, 0x04)
    if flag in EXTENT_LEAF_FLAGS:
        # Caller passed a leaf head directly.
        return _walk_leaf_forward(table, root_slot, set())
    if flag not in INODE_FLAGS:
        return []
    head = u32(record, 0x24)
    if head in (0, SENTINEL):
        return []
    head_record = rec_at(table, head)
    if head_record is None or u32(head_record, 0x04) not in EXTENT_LEAF_FLAGS:
        return []
    return _walk_leaf_forward(table, head, set())


def _walk_bare_data_chain(table: bytes, data_chain_slot: int) -> list[tuple[int, int]]:
    if data_chain_slot in (0, SENTINEL):
        return []
    start = data_chain_slot
    record = rec_at(table, start)
    if record is None or u32(record, 0x04) not in EXTENT_LEAF_FLAGS:
        return []
    guard = 0
    while guard < 100000:
        previous = u32(record, 0x08)
        if previous in (0, SENTINEL):
            break
        previous_record = rec_at(table, previous)
        if previous_record is None or u32(previous_record, 0x04) not in EXTENT_LEAF_FLAGS:
            break
        start = previous
        record = previous_record
        guard += 1
    return _walk_leaf_forward(table, start, set())


def resolve_extent_pairs_for_master(table: bytes, master_offset: int) -> list[tuple[int, int]]:
    master_record = rec_at(table, master_offset)
    if master_record is None:
        return []
    sfd_slot = u32(master_record, 0x24)
    data_chain = u32(master_record, 0x34)
    if 0 < sfd_slot < len(table):
        sfd_record = rec_at(table, sfd_slot)
        if sfd_record is not None and u32(sfd_record, 0x04) == FLAG_SFD_META:
            inode = u32(sfd_record, 0x14)
            if 0 < inode < len(table):
                inode_record = rec_at(table, inode)
                if inode_record is not None and u32(inode_record, 0x04) in INODE_FLAGS:
                    pairs = _walk_inode_subtree(table, inode)
                    if pairs:
                        return pairs
    return _walk_bare_data_chain(table, data_chain)


def dedupe_extent_pairs(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    by_byte_offset: dict[int, int] = {}
    for cluster_id, byte_offset in pairs:
        by_byte_offset[byte_offset] = cluster_id
    return sorted(((cluster_id, byte_offset) for byte_offset, cluster_id in by_byte_offset.items()), key=lambda pair: pair[1])
