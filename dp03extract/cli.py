from __future__ import annotations

import argparse
from pathlib import Path

from .bfs.alloc_table import read_alloc_table
from .bfs.records import parse_master_record
from .bfs.toc import parse_toc
from .catalog import ProjectCatalogEntry, get_project_catalog_entry, load_project_catalog
from .extract import extract_project_tracks
from .tracks import parse_track_table


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dp03extract")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List TOC slots from a DP-03 image")
    list_parser.add_argument("image", type=Path, help="Path to full disk image")
    list_parser.add_argument("--limit", type=int, default=250, help="Maximum slots to print")

    info_parser = subparsers.add_parser("project-info", help="Show project catalog and track mapping info")
    info_parser.add_argument("image", type=Path)
    info_parser.add_argument("--catalog", type=Path, required=True)
    info_parser.add_argument("--project-idx", type=int, required=True)

    extract_parser = subparsers.add_parser("extract-project", help="Extract one project's visible tracks to WAV")
    extract_parser.add_argument("image", type=Path)
    extract_parser.add_argument("--catalog", type=Path, required=True)
    extract_parser.add_argument("--project-idx", type=int, required=True)
    extract_parser.add_argument("--out-dir", type=Path, required=True)

    extract_all_parser = subparsers.add_parser("extract-all-projects", help="Extract every project in a catalog to per-project folders")
    extract_all_parser.add_argument("image", type=Path)
    extract_all_parser.add_argument("--catalog", type=Path, required=True)
    extract_all_parser.add_argument("--out-dir", type=Path, required=True)

    return parser


def cmd_list(image: Path, limit: int) -> int:
    entries = parse_toc(image)
    print(f"{'slot':>4}  {'tag':<4}  name")
    print("----  ----  --------")
    for entry in entries[:limit]:
        print(f"{entry.slot_index:>4}  {entry.slot_tag:<4}  {entry.name}")
    return 0


def _resolve_project_song_name(image: Path, catalog: Path, project_idx: int) -> str:
    project = get_project_catalog_entry(catalog, project_idx)
    table = read_alloc_table(image, project.byte_base_mtr_relative)
    raw_name = table[0x50:0x58]
    name = "".join(chr(byte) if 32 <= byte < 127 else "" for byte in raw_name).strip()
    return name or "UNKNOWN"


def _slugify_project_name(name: str) -> str:
    cleaned = []
    for char in name.strip():
        if char.isalnum():
            cleaned.append(char)
        elif char in {" ", "-", "_"}:
            cleaned.append("_")
    slug = "".join(cleaned).strip("_")
    return slug or "UNKNOWN"


def _project_output_dir(base_out_dir: Path, project: ProjectCatalogEntry, song_name: str) -> Path:
    return base_out_dir / f"Project{project.project_index}_{_slugify_project_name(song_name)}"


def cmd_project_info(image: Path, catalog: Path, project_idx: int) -> int:
    project = get_project_catalog_entry(catalog, project_idx)
    song_name = _resolve_project_song_name(image, catalog, project_idx)
    table = read_alloc_table(image, project.byte_base_mtr_relative)
    tracks = parse_track_table(table)
    print(f"Project {project_idx}: {song_name}")
    print(f"byte_base_mtr_relative = 0x{project.byte_base_mtr_relative:X}")
    print(f"master slots = {', '.join(f'0x{slot:X}' for slot in project.master_slots)}")
    for track in tracks:
        if track.master_offset is None:
            continue
        master = parse_master_record(table, track.master_offset)
        print(
            f"Track {track.track_index + 1}: master=0x{track.master_offset:X} "
            f"declared_bytes=0x{master.declared_bytes:X}"
        )
    return 0


def cmd_extract_project(image: Path, catalog: Path, project_idx: int, out_dir: Path) -> int:
    project = get_project_catalog_entry(catalog, project_idx)
    written = extract_project_tracks(image, project.byte_base_mtr_relative, out_dir)
    for path in written:
        print(f"wrote {path}")
    return 0


def cmd_extract_all_projects(image: Path, catalog: Path, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    project_count = 0
    track_count = 0
    for project in load_project_catalog(catalog):
        song_name = _resolve_project_song_name(image, catalog, project.project_index)
        project_dir = _project_output_dir(out_dir, project, song_name)
        written = extract_project_tracks(image, project.byte_base_mtr_relative, project_dir)
        project_count += 1
        track_count += len(written)
        print(f"Project {project.project_index}: {song_name} -> {project_dir}")
        for path in written:
            print(f"wrote {path}")
    print(f"Extracted {project_count} project(s), wrote {track_count} track file(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "list":
        return cmd_list(args.image, args.limit)
    if args.command == "project-info":
        return cmd_project_info(args.image, args.catalog, args.project_idx)
    if args.command == "extract-project":
        return cmd_extract_project(args.image, args.catalog, args.project_idx, args.out_dir)
    if args.command == "extract-all-projects":
        return cmd_extract_all_projects(args.image, args.catalog, args.out_dir)

    parser.error(f"unknown command: {args.command}")
    return 2
