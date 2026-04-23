from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .constants import TOC_ABSOLUTE_OFFSET, TOC_ENTRY_COUNT, TOC_ENTRY_STRIDE


@dataclass(frozen=True)
class TocEntry:
    slot_index: int
    slot_tag: str
    name: str
    raw_bounce_bytes: int


def _read_u32_be(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "big")


def _decode_ascii(data: bytes) -> str:
    return data.decode("ascii", errors="replace").rstrip(" \x00")


def parse_toc_entry(slot_index: int, record: bytes) -> TocEntry:
    if len(record) != TOC_ENTRY_STRIDE:
        raise ValueError(f"TOC entry must be {TOC_ENTRY_STRIDE} bytes")
    return TocEntry(
        slot_index=slot_index,
        slot_tag=_decode_ascii(record[0:4]),
        name=_decode_ascii(record[8:16]),
        raw_bounce_bytes=_read_u32_be(record, 0x14),
    )


def parse_toc(
    image_path: str | Path,
    *,
    toc_offset: int = TOC_ABSOLUTE_OFFSET,
    entry_count: int = TOC_ENTRY_COUNT,
    entry_stride: int = TOC_ENTRY_STRIDE,
) -> list[TocEntry]:
    image_path = Path(image_path).expanduser()
    with image_path.open("rb") as handle:
        handle.seek(toc_offset)
        toc_bytes = handle.read(entry_count * entry_stride)

    if len(toc_bytes) != entry_count * entry_stride:
        raise ValueError("could not read full TOC region from image")

    entries: list[TocEntry] = []
    for index in range(entry_count):
        start = index * entry_stride
        record = toc_bytes[start:start + entry_stride]
        entries.append(parse_toc_entry(index, record))
    return entries
