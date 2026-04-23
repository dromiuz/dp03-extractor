"""Detect inserted DP-03SD cards on macOS and Windows.

A mounted DP-03 card shows up as a small FAT32 volume. The visible FAT
partition is tiny (the card's main storage lives in the proprietary BFS
region on the raw device), so we can't pull audio from the mounted volume
directly — but we CAN use the mount to discover which physical device is
backing it and then read that device as a raw image.

macOS detection strategy:
  1. Walk /Volumes for mount points whose name contains "DP" or whose
     root contains DP-03-shaped filenames (TEXnnn*.WAV, SONGnnn*).
  2. Look up each candidate via `mount` and extract /dev/diskNsM.
  3. Map to the whole disk raw node (/dev/rdiskN).

Windows detection strategy:
  1. Enumerate removable logical drives (DRIVE_REMOVABLE) via ctypes.
  2. Probe each drive's root for DP-03 file hints or a "DP-03" label.
  3. Use PowerShell's Get-Partition/Get-Disk to map drive letter -> disk
     number, then build a \\.\\PhysicalDriveN path for raw reads.
     (Reading that path requires Administrator — see WINDOWS.md.)

On unsupported platforms this module returns [].
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SDCard:
    volume_path: Path      # e.g. /Volumes/DP-03
    raw_device: str        # e.g. /dev/rdisk5  — read with sudo
    display_name: str      # e.g. "DP-03 (on /dev/rdisk5)"


_DP03_FILE_HINTS = ("TEX", "SONG", "TASCAM")


def _looks_like_dp03(volume: Path) -> bool:
    name = volume.name.upper()
    if name.startswith("DP-03") or name.startswith("DP03"):
        return True
    try:
        entries = [p.name.upper() for p in volume.iterdir()]
    except (PermissionError, OSError):
        return False
    return any(any(e.startswith(hint) for hint in _DP03_FILE_HINTS) for e in entries)


# ------------------------------------------------------------------ macOS

def _device_for_volume_mac(volume: Path) -> str | None:
    """Use `mount` to map /Volumes/FOO -> /dev/diskNsM, then convert to rdiskN."""
    try:
        output = subprocess.check_output(["mount"], text=True, timeout=2)
    except (subprocess.SubprocessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    target = str(volume)
    for line in output.splitlines():
        # macOS mount format: "/dev/disk5s1 on /Volumes/DP-03 (msdos, ..."
        match = re.match(r"^(/dev/\S+) on (.+?) \(", line)
        if not match:
            continue
        if match.group(2) != target:
            continue
        device = match.group(1)
        # Strip trailing sN partition and swap disk -> rdisk (raw, faster).
        whole = re.sub(r"s\d+$", "", device)
        whole = whole.replace("/dev/disk", "/dev/rdisk")
        return whole
    return None


def _detect_mac() -> list[SDCard]:
    volumes_root = Path("/Volumes")
    if not volumes_root.exists():
        return []
    results: list[SDCard] = []
    for volume in sorted(volumes_root.iterdir()):
        if not volume.is_dir():
            continue
        try:
            if not _looks_like_dp03(volume):
                continue
        except OSError:
            continue
        device = _device_for_volume_mac(volume)
        if not device:
            continue
        results.append(
            SDCard(
                volume_path=volume,
                raw_device=device,
                display_name=f"{volume.name}  ({device})",
            )
        )
    return results


# ---------------------------------------------------------------- Windows

_DRIVE_REMOVABLE = 2


def _enum_removable_drives_win() -> list[Path]:
    """Return e.g. [WindowsPath('E:\\\\'), WindowsPath('F:\\\\')]."""
    if not hasattr(ctypes, "windll"):
        return []
    kernel32 = ctypes.windll.kernel32
    bitmask = kernel32.GetLogicalDrives()
    drives: list[Path] = []
    for i in range(26):
        if not (bitmask & (1 << i)):
            continue
        letter = chr(ord("A") + i)
        root = f"{letter}:\\"
        try:
            drive_type = kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        except OSError:
            continue
        if drive_type == _DRIVE_REMOVABLE:
            drives.append(Path(root))
    return drives


def _disk_number_for_letter_win(letter: str) -> int | None:
    """Ask PowerShell for the physical disk number behind a drive letter."""
    letter = letter.rstrip(":\\/").upper()
    if not letter or len(letter) != 1:
        return None
    cmd = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        f"(Get-Partition -DriveLetter {letter} | "
        f"Get-Disk | Select-Object -First 1).Number",
    ]
    try:
        out = subprocess.check_output(
            cmd, text=True, timeout=5,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.SubprocessError, FileNotFoundError,
            subprocess.TimeoutExpired):
        return None
    out = out.strip()
    if not out.isdigit():
        return None
    return int(out)


def _detect_win() -> list[SDCard]:
    results: list[SDCard] = []
    for volume in _enum_removable_drives_win():
        try:
            if not _looks_like_dp03(volume):
                continue
        except OSError:
            continue
        letter = volume.drive  # e.g. 'E:'
        disk_num = _disk_number_for_letter_win(letter)
        if disk_num is None:
            continue
        raw = rf"\\.\PhysicalDrive{disk_num}"
        results.append(
            SDCard(
                volume_path=volume,
                raw_device=raw,
                display_name=f"{letter}  ({raw})",
            )
        )
    return results


# ----------------------------------------------------------------- public

def detect_sd_cards() -> list[SDCard]:
    """Return inserted DP-03 cards. Empty list if none / unsupported OS."""
    if sys.platform == "darwin":
        return _detect_mac()
    if sys.platform.startswith("win"):
        return _detect_win()
    return []


def is_readable(device: str) -> bool:
    """Quick check — can the current user open the raw device for read?"""
    try:
        fd = os.open(device, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except OSError:
        return False
    os.close(fd)
    return True
