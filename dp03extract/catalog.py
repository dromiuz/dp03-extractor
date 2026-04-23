from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectCatalogEntry:
    project_index: int
    byte_base_mtr_relative: int
    byte_base_absolute: int
    master_count: int
    master_slots: list[int]


def _parse_int(value: str) -> int:
    return int(value.strip(), 0)


def _parse_master_slots(value: str) -> list[int]:
    if not value.strip():
        return []
    return [_parse_int(part) for part in value.split(";") if part.strip()]


def load_project_catalog(path: Path) -> list[ProjectCatalogEntry]:
    entries: list[ProjectCatalogEntry] = []
    with path.open("r", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            entries.append(
                ProjectCatalogEntry(
                    project_index=_parse_int(row["project_idx"]),
                    byte_base_mtr_relative=_parse_int(row["byte_base_mtr_rel"]),
                    byte_base_absolute=_parse_int(row["byte_base_abs"]),
                    master_count=_parse_int(row["master_count"]),
                    master_slots=_parse_master_slots(row["master_slots"]),
                )
            )
    return entries


def get_project_catalog_entry(path: Path, project_index: int) -> ProjectCatalogEntry:
    for entry in load_project_catalog(path):
        if entry.project_index == project_index:
            return entry
    raise KeyError(f"project index not found in catalog: {project_index}")
