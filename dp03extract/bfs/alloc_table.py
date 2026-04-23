from __future__ import annotations

import struct
from pathlib import Path

from ..partition import parse_mbr

ALLOC_TABLE_SIZE = 0x100000
MTR_HEADER_OFFSET = 0x800
MTR_HDR_SECTOR_SIZE = 0x24
MTR_HDR_SECT_PER_CLUS = 0x2C


def get_mtr_base(image_path: Path) -> int:
    with image_path.open("rb") as handle:
        mbr = handle.read(512)
    partitions = parse_mbr(mbr)
    if not partitions:
        raise ValueError("no partitions found in image")
    first = partitions[0]
    return (first.lba_start + first.sector_count) * 512


def get_cluster_size(image_path: Path) -> int:
    mtr_base = get_mtr_base(image_path)
    with image_path.open("rb") as handle:
        handle.seek(mtr_base + MTR_HEADER_OFFSET)
        header = handle.read(0x80)
    sector_size = struct.unpack(">I", header[MTR_HDR_SECTOR_SIZE:MTR_HDR_SECTOR_SIZE + 4])[0]
    sectors_per_cluster = struct.unpack(">I", header[MTR_HDR_SECT_PER_CLUS:MTR_HDR_SECT_PER_CLUS + 4])[0]
    return sector_size * sectors_per_cluster


def read_alloc_table(image_path: Path, byte_base_mtr_relative: int) -> bytes:
    mtr_base = get_mtr_base(image_path)
    with image_path.open("rb") as handle:
        handle.seek(mtr_base + byte_base_mtr_relative)
        return handle.read(ALLOC_TABLE_SIZE)
