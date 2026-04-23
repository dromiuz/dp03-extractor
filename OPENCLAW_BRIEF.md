# OpenClaw Execution Brief — DP-03 Reverse Engineering

> **Your role:** Infrastructure, code, and build-out. You handle the engineering scaffolding so Zach and Hermes can focus on analysis.
> **Companion docs:** `PLAN.md` (full plan), `SKILL.md` (operating rules), `HERMES_BRIEF.md` (your counterpart's job)
> **Status check:** Read `shared/dp03/notes/lab_log.md` before starting any task.

---

## Your mission

Build the tooling, folder structure, and code that makes this project run. You are *not* the one doing hex analysis — that's Hermes. You are the one making sure analysis is frictionless: commands are ready, scripts exist, the Python extractor gets built, and every experiment is reproducible.

---

## Job 1 — Set up the workspace (do this first, once)

Create the canonical folder structure in the shared location:

```
shared/dp03/
├── PLAN.md                    # copy from current location
├── SKILL.md                   # copy from current location
├── HERMES_BRIEF.md            # copy from current location
├── OPENCLAW_BRIEF.md          # this file
├── images/                    # raw partition dumps
│   └── .gitkeep
├── dumps/                     # hex dumps, diffs, annotated regions
│   └── .gitkeep
├── notes/
│   ├── lab_log.md             # create with header template (see below)
│   ├── FORMAT_SPEC.md         # create empty with section headers
│   └── AUDIO_LAYOUT.md        # create empty with section headers
└── code/
    ├── scripts/               # helper scripts (Job 2)
    └── dp03_extract/          # the Python package (Job 5)
```

**lab_log.md template:**
```markdown
# DP-03 Reverse Engineering — Lab Log

All experiments logged here in chronological order. Newest at bottom.

## Template for new entries

### YYYY-MM-DD HH:MM — Phase N — <short title>
- **Conditions:** <exact recording setup: which tracks armed, what signal source, duration, sample rate setting on unit>
- **DP-03 firmware version:** <from unit's system menu>
- **Card:** <which SD card — size, brand, label>
- **Produced:** `images/<filename>.img` (<size>)
- **Observations:** <anything noteworthy during the dump>
- **Next step:** <what comes next>

---
```

**FORMAT_SPEC.md starter:**
```markdown
# DP-03 MTR Partition Format — Specification

> Status: HYPOTHESIS — no fields confirmed yet.

## Partition table
- MBR offset: 0x1BE
- MTR partition type ID: TBD (Phase 1)
- MTR partition location: TBD

## Volume header (MTR partition byte 0)
TBD — Phase 2

## Song table
TBD — Phase 2–4

## Song entry
TBD — Phase 4

## Track table
TBD — Phase 4

## Audio data layout
TBD — Phase 3

## Constants confirmed
- Sample rate: 44100 Hz (HYPOTHESIS, confirm Phase 3)
- Bit depth: 16 (HYPOTHESIS, confirm Phase 3)
- Byte order: little-endian (HYPOTHESIS, confirm Phase 3)
- Channels per track: 1 mono (HYPOTHESIS, confirm Phase 3)
```

---

## Job 2 — Build the helper script toolkit

Create these scripts in `shared/dp03/code/scripts/`. They're used across all phases.

### `scripts/image_card.sh` (Mac/Linux) and `scripts/image_card.ps1` (Windows)

Wrapper around `dd` that:
- Prompts for card device (and confirms, because wrong device = wiped drive)
- Prompts for output filename (into `images/`)
- Runs the dump with `status=progress`
- Auto-copies to two backup locations (`images/backups/` and `images/backups2/`)
- Computes SHA-256 of the resulting image and appends to `images/checksums.txt`
- Prints the MBR partition table summary at the end

**Hard rule:** the script must refuse to run if the target device path contains the boot disk's identifier. On Mac, block `disk0`. On Linux, block `sda`. Fail-safe, not fail-open.

### `scripts/parse_mbr.py`

Takes an image path, prints the four partition table entries in human-readable form:
```
Partition 0: type=0x0B (FAT32) start=8192 sectors=2097152 (~1.0 GB)
Partition 1: type=0xDA (Tascam MTR?) start=2105344 sectors=14680064 (~7.0 GB)
```
Also optionally extracts a chosen partition to its own file:
```
python parse_mbr.py dp03_full.img --extract 1 --output mtr.img
```

### `scripts/diff_images.py`

A smarter `cmp -l`. Takes two image paths and a byte range, outputs a region-based diff:
```
Region 0x0000_0000 – 0x0000_0FFF: identical
Region 0x0000_1000 – 0x0000_10FF: 64 bytes changed (84% runs of zero → nonzero)
Region 0x0010_0000 – 0x0010_159C: 88200 bytes changed (linearly from zero → nonzero)
Region 0x0010_159D – end: identical
```
Regions are auto-detected from change density. This is way more useful than raw `cmp -l` output for quickly spotting "a track was written here."

Include a `--hex` mode that dumps the first N changed bytes from each changed region side-by-side.

### `scripts/grep_strings.py`

Takes an image and a string, searches for it as ASCII, UTF-16LE, UTF-16BE, and as length-prefixed variants (Pascal-style with u8 and u16 length prefixes). Reports every offset and dumps 64 bytes of context around each hit.

### `scripts/hexview.py`

Takes an image path and an offset, pretty-prints a region with:
- Addresses in the image
- Hex + ASCII (standard)
- Optional struct interpretation (`--struct "<IHH32s"` format)
- Optional annotations from a sidecar YAML file mapping offsets to field names

---

## Job 3 — Monitor and prep each phase

Before each phase kicks off, you:
1. Check `lab_log.md` to confirm the previous phase is complete
2. Read the relevant phase section of `PLAN.md`
3. Prep any new scripts needed for that phase
4. Post a "Phase N ready" note in `lab_log.md` with the exact commands Zach will run
5. Hand off to Hermes for analysis once images are produced

For Phase 3 specifically (the five known-signal recordings), prepare a Phase 3 checklist in `notes/phase3_checklist.md` with exact steps Zach will follow — this phase has five sequential recordings and getting them in strict order matters.

---

## Job 4 — Manage hypotheses and track progress

Maintain `notes/FORMAT_SPEC.md` as the single source of truth for the format. Every field starts as HYPOTHESIS and is promoted to CONFIRMED only when a Hermes analysis entry in `lab_log.md` cites evidence.

Use this status convention:
- **HYPOTHESIS** — proposed, not yet validated
- **PROBABLE** — supported by one experiment
- **CONFIRMED** — supported by two+ independent experiments
- **CONTRADICTED** — evidence disproves it, needs revision

When Hermes posts analysis, update `FORMAT_SPEC.md` accordingly and cite the `lab_log.md` entry that provides the evidence.

---

## Job 5 — Build the extractor (Phase 5)

Once Phase 4 is done and `FORMAT_SPEC.md` has a CONFIRMED metadata layout, build the Python extractor. Structure:

```
code/dp03_extract/
├── pyproject.toml
├── README.md
├── dp03_extract/
│   ├── __init__.py
│   ├── cli.py
│   ├── partition.py       # MBR + MTR partition extraction
│   ├── volume.py          # MTR volume header parser
│   ├── song.py            # song table + song header
│   ├── track.py           # track table + audio walker
│   ├── wav_writer.py      # PCM → WAV
│   └── exceptions.py
└── tests/
    ├── conftest.py
    ├── fixtures/          # small sample images checked in
    ├── test_mbr.py
    ├── test_volume.py
    ├── test_song.py
    ├── test_track.py
    └── test_roundtrip.py  # extract vs native export byte-compare
```

**Design rules:**
- All binary parsing uses `struct.unpack`, never manual byte indexing
- Every parser validates its magic bytes and raises `DP03FormatError` with a clear message on mismatch
- Every parser is round-trippable: `parse(serialize(x)) == x`
- `cli.py --list` mode prints the full song/track tree without extracting — critical for debugging
- `cli.py --verbose` logs every parsed struct at DEBUG level
- Support both raw card devices and image files as input

**CLI surface:**
```
dp03-extract /Volumes/NO_NAME --output ~/DP03Extract
dp03-extract ~/dp03_full.img --output ~/DP03Extract
dp03-extract ~/dp03_full.img --list
dp03-extract ~/dp03_full.img --song "ZZZZTEST99"
dp03-extract ~/dp03_full.img --song "ZZZZTEST99" --track 3
dp03-extract ~/dp03_full.img --verbose
```

---

## Job 6 — Validation harness (Phase 6)

Build `tests/test_roundtrip.py`:
1. Load an image with known songs
2. Extract all tracks to WAVs
3. Load matching native-export WAVs from `tests/fixtures/native/`
4. For each pair: decode PCM, assert `numpy.array_equal` on the sample arrays
5. Report any mismatches with sample-offset and magnitude

When all validation songs pass, the project is ready to publish.

---

## Job 7 — Packaging and publish prep (Phase 7)

- Write the GitHub README with quickstart, format spec summary, and credits
- Generate distributable wheel via `python -m build`
- Draft forum posts (TASCAM forums, HomeRecording.com, r/audioengineering) — send to Zach for review before posting
- Set up the `dp03-format-spec` repo separately with just `FORMAT_SPEC.md` and the methodology writeup

---

## Coordination with Hermes

- You produce scripts and scaffolding → Hermes uses them for analysis
- Hermes produces annotated hex dumps and format hypotheses → you integrate into `FORMAT_SPEC.md` and build parsers
- When uncertain whether something is your job or Hermes's: if it involves interpreting bytes, it's Hermes. If it involves running, orchestrating, or codifying, it's you.
- Disagreements go to Zach — do not silently override each other's work.

---

## Non-negotiables

1. Never execute `dd` or any raw-disk command without Zach's explicit go-ahead for each run — even if he's already authorized previous runs.
2. The `image_card` script's boot-disk safeguard is not optional. Never disable it, not even "just this once."
3. Never modify files in `shared/dp03/images/` after creation. If an image needs correction, produce a new one with a new name and log the reason.
4. Every script you write gets a docstring explaining what it does, what it expects, and what it outputs. Zach should be able to run any script with `--help` and understand it.
