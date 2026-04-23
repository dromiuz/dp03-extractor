# DP-03 MTR Partition Reverse Engineering — Master Plan

> **Owner:** Zach
> **Status:** Phase 0 (pre-kickoff)
> **Shared with:** OpenClaw, Hermes
> **Goal:** Read the Tascam DP-03 (and DP-03SD) MTR partition directly, parse its proprietary filesystem, and extract every track from every song as standard WAV files — no per-track export on the unit required.

---

## Why this matters

The Tascam DP-03 uses a two-partition SD card layout:
- **FAT partition** — standard, visible to any computer. Holds `WAVE/`, `BACKUP/`, `UTILITY/`.
- **MTR partition** — proprietary format, invisible to normal OS. This is where all live song and track data actually lives.

Tascam's only sanctioned way to get tracks out is to export them one-at-a-time through the unit's menu system. For an 8-track song that's 8 export operations per song. For a card with 10 songs, that's 80 button-mashing operations.

Forum requests to read the MTR partition directly go back 15+ years with no public solution. Cracking it would be a genuinely novel contribution: useful to thousands of Portastudio owners (DP-004, DP-008, DP-03, DP-03SD, likely others share the format) and a solid reverse-engineering writeup.

---

## Working hypotheses

These are the assumptions driving the approach. Each one gets validated in an early phase.

1. **Audio samples are raw PCM, little-endian, 16-bit, 44.1kHz, mono.** The DP-03 is a cheap real-time DSP — it is not compressing/decompressing audio on the fly. The "proprietary" part is the filesystem and metadata wrapping the samples, not the samples themselves.
2. **The filesystem is a simple custom layout**, not a standard one. Likely a fixed volume header → song table → per-song metadata → flat or lightly-fragmented audio regions. Designed by a firmware team circa 2010 for an embedded device, not a general-purpose FS.
3. **The format is shared across the DP-004, DP-008, DP-03, DP-03SD.** If true, anything discovered generalizes.
4. **Metadata (song names, track names, lengths) is stored as plain ASCII or UTF-16LE** somewhere findable via grep. Firmware teams rarely bother encrypting these.

---

## Phased approach

### Phase 1 — Acquire a raw partition image
**Goal:** Get a byte-exact copy of the full SD card onto disk, locate the MTR partition within it, and extract just the MTR partition to its own file.

**Steps:**
1. Dedicate a spare SD card for experiments. Do NOT use a card with real music on it.
2. On macOS: `diskutil list` → identify the card (e.g. `/dev/disk4`) → `sudo dd if=/dev/disk4 of=~/dp03_full.img bs=1m`
   On Linux: same, but `/dev/sdX`.
   On Windows: use Win32 Disk Imager or `dd for Windows`.
3. Make three copies of the image immediately. Treat the original as sacred.
4. Hex-dump the first 512 bytes (the MBR). Partition entries live at offsets `0x1BE`, `0x1CE`, `0x1DE`, `0x1EE`. Each is 16 bytes with the structure: status(1), CHS_start(3), type(1), CHS_end(3), LBA_start(4, little-endian), sector_count(4, little-endian).
5. Identify the FAT partition (type `0x0B` or `0x0C`) and the MTR partition (likely a non-standard type ID such as `0xDA`, `0xDB`, or something Tascam-specific).
6. Extract just the MTR partition: `dd if=dp03_full.img of=mtr.img bs=512 skip=<LBA_start> count=<sector_count>`.

**Output:** `mtr.img` — the raw MTR partition bytes, ready for analysis.

---

### Phase 2 — Map the partition structure
**Goal:** Identify the volume header, any superblock-like structures, and the overall layout.

**Steps:**
1. Hex-dump the first 4KB of `mtr.img`. Look for magic numbers (ASCII strings like `TASCAM`, `MTR`, `DP03`, or repeating binary signatures).
2. Note any size/count fields near the top — these are often partition size, song count, block size, etc.
3. Look for repeated structures (e.g. 256-byte blocks with similar layouts) — likely a song table.
4. Map regions: header, metadata tables, audio data region boundaries.

**Output:** An annotated hex-dump and a first-pass layout diagram.

---

### Phase 3 — The known-signal experiments (★ the key unlock)
**Goal:** Derive the audio data layout by comparing partition images of precisely controlled recordings.

This is where the real discovery happens. Five controlled captures, each dumped with the Phase 1 procedure:

1. **`00_empty.img`** — Format the card on the DP-03. Create one empty song. Image.
2. **`01_silence_1s.img`** — Arm track 1, record exactly 1 second with no signal input. Image.
3. **`02_silence_10s.img`** — Same, 10 seconds. Image.
4. **`03_sine_1s_tk1.img`** — Feed a 1kHz sine tone (phone tone generator into input) to track 1, record 1 second. Image.
5. **`04_sine_1s_tk3.img`** — Same 1kHz sine, but record to track 3 instead. Image.

**Analysis:**
- `cmp -l 00_empty.img 01_silence_1s.img | head -200` — shows exactly which bytes changed when track 1 got a 1-second recording. This locates: the track 1 audio region, the track 1 length field in metadata, any "track has data" flag, and the timestamp update.
- `01 vs 02` — 10x the audio bytes. Confirms sample rate and bit depth (44100 samples × 2 bytes = 88200 bytes for 1 second mono 16-bit).
- `02 vs 03` — silence vs sine. Silence is all `0x00 0x00` pairs; sine is recognizable oscillating values. Confirms the sample format and byte order.
- `03 vs 04` — same audio content, different track. Reveals how tracks are indexed (separate regions? interleaved? indexed by header pointer?).

**Output:** `AUDIO_LAYOUT.md` — a document describing exactly how track audio is stored.

---

### Phase 4 — Locate and decode metadata
**Goal:** Fully decode song and track metadata (names, lengths, track count, BPM, creation date, virtual takes).

**Steps:**
1. Create a song on the unit named `ZZZZTEST99` with a track renamed `GUITARLEAD`. Image the card.
2. Grep the image for both ASCII and UTF-16LE encodings of those strings:
   ```
   grep -abo "ZZZZTEST99" mtr.img
   python3 -c "import sys; sys.stdout.buffer.write('ZZZZTEST99'.encode('utf-16-le'))" | xxd
   ```
3. Walk outward from each hit. What bytes precede the name? (Length prefix? Fixed-offset struct field?) What follows? (Pointers, flags, timestamps?)
4. Record a song with all 8 tracks of deliberately distinct lengths (track 1 = 1s, track 2 = 2s, ..., track 8 = 8s) and 8 distinct renamed names. That single image reveals the entire track table schema.
5. Document the full struct layout for: volume header, song table entry, song header, track table entry.

**Output:** `FORMAT_SPEC.md` — a complete specification of the on-disk layout.

---

### Phase 5 — Build the extractor
**Goal:** A Python CLI that takes `mtr.img` (or a raw SD card device) and outputs `~/DP03Extract/<SongName>/<TrackName>.wav` for every song and every track.

**Stack:** Python 3, `struct` for binary parsing, `numpy` optional for bulk sample manipulation, `wave` from stdlib for WAV writing.

**Architecture:**
```
dp03_extract/
├── __init__.py
├── cli.py               # entrypoint
├── partition.py         # MBR parsing, MTR partition extraction
├── volume.py            # MTR volume header parser
├── song.py              # song table + song header parsers
├── track.py             # track table + audio block walker
├── wav_writer.py        # wraps raw PCM in standard WAV header
└── tests/
    └── test_roundtrip.py  # compares extractor output vs DP-03 native exports
```

**CLI:**
```
dp03-extract /Volumes/TASCAM_SD --output ~/DP03Extract
dp03-extract ~/dp03_full.img --output ~/DP03Extract   # works on an image too
dp03-extract ~/dp03_full.img --song "ZZZZTEST99"      # single song
dp03-extract ~/dp03_full.img --list                    # dry-run: list songs + tracks
```

---

### Phase 6 — Ground-truth validation
**Goal:** Prove the extractor is sample-accurate.

For each test song:
1. Export tracks normally via the DP-03's built-in WAV export. Collect those WAVs.
2. Run the extractor on the same card. Collect those WAVs.
3. Byte-compare the PCM payloads. `cmp`, or load both into Python and verify `numpy.array_equal`.
4. If they match sample-for-sample across multiple test songs with different track counts, lengths, and sample content — the extractor is validated.

Null-testing in a DAW (invert one, sum with the other, confirm silence) is a good secondary check.

---

### Phase 7 — Publish
1. `FORMAT_SPEC.md` published as a GitHub repo: `dp03-format-spec`.
2. `dp03-extract` as a pip-installable CLI on GitHub + PyPI.
3. Optional Phase 7.5: a Tauri or Electron GUI for non-CLI users.
4. Writeups posted to: TASCAM forums, HomeRecording.com, r/audioengineering, Hacker News.

---

## Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Audio is compressed, not raw PCM | Low | Phase 3 sine-wave test immediately reveals this. If compressed, look for ADPCM signatures first (simplest possibility on embedded DSPs). |
| Filesystem uses a block allocation table with fragmentation | Medium | Phase 4 long-recording test (record >10 min on one track) reveals whether audio is contiguous or block-linked. Build the walker accordingly. |
| Metadata is obfuscated/XORed | Low | If grep finds no plaintext strings, try common XOR keys (`0xFF`, `0x55`, etc.) and scan for patterns. Unlikely but possible. |
| Card image contains wear-leveling artifacts | Very low | SD cards present a clean LBA view; wear leveling is internal to the controller. Not a concern. |
| Different firmware versions have different formats | Medium | Note firmware version on the unit. Test with at least two cards if possible. Structure the parser to be version-aware. |

---

## Prerequisites / what's needed from Zach

- A spare SD card (2GB+, any SDHC-compatible) dedicated to experiments
- macOS / Linux / Windows — commands adjust per OS
- Free hex editor: Hex Fiend (Mac), HxD (Windows), `hexyl` / `xxd` (Linux)
- ~2GB free disk space for images during experimentation
- Access to the DP-03 unit (obviously)
- A phone or laptop that can output a 1kHz test tone for Phase 3

---

## Agent coordination notes

- **OpenClaw** — use this doc as the reference plan. The companion `SKILL.md` tells you when and how to act on it.
- **Hermes** — same. When Zach drops partition images or hex dumps in the shared folder, follow the current phase's analysis steps.
- Shared folder convention: `shared/dp03/` with subfolders `images/`, `dumps/`, `notes/`, `code/`.
- Log every experiment in `shared/dp03/notes/lab_log.md` with timestamp, what was recorded, and which image file resulted.
