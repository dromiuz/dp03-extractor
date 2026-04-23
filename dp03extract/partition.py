from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PartitionEntry:
    index: int
    status: int
    partition_type: int
    lba_start: int
    sector_count: int


def parse_mbr(mbr: bytes) -> list[PartitionEntry]:
    if len(mbr) < 512:
        raise ValueError("MBR must be at least 512 bytes")
    if mbr[510:512] != b"\x55\xaa":
        raise ValueError("invalid MBR signature")

    partitions: list[PartitionEntry] = []
    table = mbr[0x1BE:0x1FE]
    for index in range(4):
        entry = table[index * 16:(index + 1) * 16]
        status = entry[0]
        partition_type = entry[4]
        lba_start = int.from_bytes(entry[8:12], "little")
        sector_count = int.from_bytes(entry[12:16], "little")
        if partition_type == 0 or sector_count == 0:
            continue
        partitions.append(
            PartitionEntry(
                index=index,
                status=status,
                partition_type=partition_type,
                lba_start=lba_start,
                sector_count=sector_count,
            )
        )
    return partitions


def find_first_partition_of_type(
    partitions: Iterable[PartitionEntry],
    partition_type: int,
) -> PartitionEntry | None:
    for partition in partitions:
        if partition.partition_type == partition_type:
            return partition
    return None
