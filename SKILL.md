---
name: dp03-reverse-engineering
description: Reverse-engineer the Tascam DP-03 / DP-03SD proprietary MTR partition format to extract multitrack song data as standard WAV files without using the unit's built-in export. Use this skill whenever Zach mentions the DP-03, DP-03SD, Portastudio, MTR partition, SD card imaging for Tascam devices, track extraction, raw partition analysis for multitrack recorders, hex analysis of audio device filesystems, or any work related to reading DP-03 project/song data directly from an SD card. Trigger even on tangential mentions like "my 8-track," "the Tascam," "pulling tracks off the card," or debugging hex dumps that came from a DP-03 card — this project is ongoing and most DP-03-adjacent work should route through this skill. Also trigger when Zach shares partition images, hex dumps, or asks to compare byte-level differences between SD card states, since those are core workflow artifacts here.
---

# DP-03 MTR Reverse Engineering

A long-running reverse engineering project to read the Tascam DP-03 MTR partition directly and extract multitrack audio as WAV files. Goal: eliminate the per-track manual export workflow entirely.

The companion document `PLAN.md` in this folder holds the full phased plan, hypotheses, and risks. Read it when you need full context. This skill is the operational guide — what to do when Zach engages you on this project.

## Current status

Check `shared/dp03/notes/lab_log.md` for the latest state. Phases proceed strictly in order:

1. Acquire raw partition image (`dd` the full card, extract MTR partition)
2. Map partition structure (identify volume header, tables, regions)
3. Known-signal experiments (five controlled recordings, diffed)
4. Locate and decode metadata (song names, track names, lengths)
5. Build the extractor (Python CLI)
6. Ground-truth validation (byte-compare vs. native exports)
7. Publish (format spec, CLI tool, writeups)

If `lab_log.md` doesn't exist yet, the project is at Phase 1 kickoff.

## Core operating principles

**Never touch the real card during analysis.** Work only from copies of images. If asked to operate on the SD card directly, pause and confirm — experiments always go through a dumped image.

**Log every experiment.** Every time a new image is produced, append an entry to `shared/dp03/notes/lab_log.md` with:
- Timestamp
- Phase number
- Exact recording conditions (which tracks, what signal, duration)
- Resulting image filename
- Any observations during the dump

**Assume 44.1kHz 16-bit little-endian mono PCM for audio** until Phase 3 proves otherwise. This is the top-line hypothesis.

**Grep before you guess.** When looking for any string (song name, track name), grep the image in both ASCII and UTF-16LE before assuming it's encoded/obfuscated.

## Standard directory layout

Work happens in a shared folder, conventionally:
```
shared/dp03/
├── PLAN.md              # master plan (read-only reference)
├── SKILL.md             # this file
├── images/              # raw partition dumps — treat as write-once
│   ├── 00_empty.img
│   ├── 01_silence_1s.img
│   └── ...
├── dumps/               # hex dumps, annotated regions, diffs
├── notes/
│   ├── lab_log.md       # chronological experiment log
│   ├── FORMAT_SPEC.md   # grows across Phases 2–4 into the full spec
│   └── AUDIO_LAYOUT.md  # Phase 3 output
└── code/
    └── dp03_extract/    # the Python extractor (Phase 5+)
```

If this structure doesn't exist, create it before doing work.

## Per-phase operational guides

### Phase 1: Image acquisition

When Zach says he's ready to image a card, give him the exact command for his OS:

- **macOS:** `diskutil list` → identify the card → `diskutil unmountDisk /dev/diskN` → `sudo dd if=/dev/rdiskN of=~/shared/dp03/images/<name>.img bs=1m status=progress`
- **Linux:** `lsblk` → identify → `sudo umount /dev/sdXN` (if mounted) → `sudo dd if=/dev/sdX of=~/shared/dp03/images/<name>.img bs=1M status=progress`
- **Windows:** recommend Win32 Disk Imager's "Read" function, or `dd` from the Cygwin/MSYS port

Remind him to make two backup copies of every image before any analysis.

Then parse the MBR:
```python
with open('<image>.img', 'rb') as f:
    mbr = f.read(512)
for i in range(4):
    entry = mbr[0x1BE + i*16 : 0x1BE + (i+1)*16]
    status, ptype = entry[0], entry[4]
    lba_start = int.from_bytes(entry[8:12], 'little')
    sectors = int.from_bytes(entry[12:16], 'little')
    print(f"Partition {i}: type=0x{ptype:02X} start={lba_start} sectors={sectors}")
```
Extract the MTR partition to its own file with `dd if=<full>.img of=mtr.img bs=512 skip=<lba_start> count=<sectors>`.

### Phase 2: Structure mapping

Dump the first 4KB of `mtr.img` as hex + ASCII:
```
xxd -l 4096 mtr.img > dumps/mtr_header_4kb.txt
```
Look for:
- ASCII magic strings (`TASCAM`, `MTR`, `DP03`, etc.)
- Repeating binary patterns suggesting tables of fixed-size records
- 16/32-bit values near the start that look like sizes, counts, or pointers

Propose candidate structures in `FORMAT_SPEC.md` and mark each as "HYPOTHESIS" until validated in later phases.

### Phase 3: Known-signal experiments (the critical phase)

Walk Zach through the five recordings in exact order. The value is in the comparisons, so each image must be produced under strictly controlled conditions — any deviation invalidates the diff.

After all five images exist, do the diffs:
```bash
cmp -l 00_empty.img 01_silence_1s.img | head -500 > dumps/diff_empty_vs_silence1s.txt
cmp -l 01_silence_1s.img 02_silence_10s.img | head -500 > dumps/diff_silence1s_vs_silence10s.txt
cmp -l 02_silence_10s.img 03_sine_1s_tk1.img | head -500 > dumps/diff_silence10s_vs_sine.txt
cmp -l 03_sine_1s_tk1.img 04_sine_1s_tk3.img | head -500 > dumps/diff_sine_tk1_vs_tk3.txt
```

Analyze each diff to answer specific questions:
- **empty vs silence_1s:** Where is track 1's audio region? What metadata fields updated?
- **silence_1s vs silence_10s:** Does audio size scale linearly? (88200 bytes per second expected for 44.1kHz/16-bit mono.) Confirms sample rate.
- **silence_10s vs sine_1s:** Silence bytes should be `00 00` repeating. Sine should be recognizable oscillation. Confirms sample format and byte order.
- **sine_tk1 vs sine_tk3:** Where did the audio region move? Are tracks stored in separate fixed regions, or linked by pointer?

Write conclusions to `notes/AUDIO_LAYOUT.md`.

### Phase 4: Metadata decoding

Have Zach create a song named `ZZZZTEST99` with a renamed track `GUITARLEAD`, then image. Grep:
```bash
grep -aboE "ZZZZTEST99" mtr.img
python3 -c "s='ZZZZTEST99'; import sys; sys.stdout.buffer.write(s.encode('utf-16-le'))" > /tmp/utf16_pattern
grep -abof /tmp/utf16_pattern mtr.img
```

For each hit, dump the surrounding 256 bytes and annotate structure. Build up `FORMAT_SPEC.md` with full struct definitions.

The "all 8 tracks, distinct lengths 1-8 seconds, distinct names" experiment reveals the track table schema in one shot. This is the highest-information-density experiment in the project — make sure it's done carefully.

### Phase 5: Extractor build

Python 3, no heavy dependencies. Structure per `PLAN.md`. Use `struct.unpack` for all binary parsing — never manual byte indexing. Use `wave` from stdlib for WAV output. Include a `--list` mode that parses metadata only and prints the song/track tree before any extraction — useful for debugging parsers.

Every struct parser should:
- Read exactly the bytes the format spec says it should
- Validate magic numbers / known-constant fields and raise clearly if mismatched
- Be round-trippable (ideally: serialize back to bytes that match input)

### Phase 6: Validation

For each validation song, the extractor's WAV output and the DP-03's native WAV export must match byte-for-byte at the PCM-payload level (headers may differ). Run:
```python
import wave, numpy as np
a = np.frombuffer(wave.open('extracted.wav','rb').readframes(-1), dtype='<i2')
b = np.frombuffer(wave.open('native.wav','rb').readframes(-1), dtype='<i2')
assert np.array_equal(a, b), f"Mismatch: {np.sum(a != b)} samples differ"
```
If there's any mismatch, treat it as a format-spec bug and return to Phase 4.

## Things to never do

- Never write to the SD card during an experiment (keep the write-protect tab engaged when imaging).
- Never delete an image. Storage is cheap; regenerating experiments is expensive.
- Never skip logging an experiment — undocumented images are worthless because you can't reproduce the conditions.
- Never assume an encoding without grep-verifying.
- Never move past a phase without the key question for that phase having a confident answer written in `notes/`.

## When Zach is stuck

If he has a hex dump and is unsure what he's looking at, default to:
1. Paste the hex into context and annotate it region by region
2. Cross-reference with the latest `FORMAT_SPEC.md`
3. If unrecognized, propose 2–3 hypotheses and design the minimum experiment that would distinguish them

## Useful background reading

The DP-004, DP-008, and DP-03 series reportedly share a filesystem family. If you find documentation for the DP-004 or DP-008 MTR format, it very likely applies here too with minor adjustments. As of project kickoff (2026-04), no public documentation of this format exists — which is exactly why this project exists.
