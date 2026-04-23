# DP-03 MTR Partition Format — Specification

> Status: Early-reverse-engineering. Structure of the MTR region is PROBABLE;
> audio layout and metadata schema remain HYPOTHESIS pending Phase 3 known-
> signal recordings.

Status convention:
- **HYPOTHESIS** — proposed, not yet validated
- **PROBABLE** — supported by one experiment
- **CONFIRMED** — supported by two+ independent experiments
- **CONTRADICTED** — evidence disproves it; needs revision

Every status claim cites a `lab_log.md` entry and specific byte offsets.

Test image referenced below: `images/00_song0001.img` (31,016,878,080 B,
31 GB SD card, single song `SONG0001` with 2 tracks ≈ 10 s).

---

## 1. Partition table (MBR)

| Offset | Size | Field                | Status   | Notes |
|-------:|-----:|----------------------|----------|-------|
| 0x000..0x1BD | 446 | Boot code / zero | n/a | Not analyzed |
| 0x1BE  | 16   | Partition entry 0    | PROBABLE | FAT32 (type 0x0B), LBA 63, 8,385,867 sectors (≈ 4.00 GiB) |
| 0x1CE  | 16   | Partition entry 1    | PROBABLE | All zero |
| 0x1DE  | 16   | Partition entry 2    | PROBABLE | All zero |
| 0x1EE  | 16   | Partition entry 3    | PROBABLE | All zero |
| 0x1FE  | 2    | Boot sig (0x55 0xAA) | STANDARD | MBR signature |

Each partition entry is 16 bytes: `status(1), CHS_start(3), type(1), CHS_end(3), LBA_start(4 LE), sector_count(4 LE)`.

### H-PT-01 — Single-partition MBR (PROBABLE)
- Only partition 0 is declared. The remaining 26.7 GB is an **undeclared raw
  region** that we call the **MTR region**. (See `lab_log.md` 2026-04-20
  MBR-parse entry.)
- Promotion rule: CONFIRMED after a second independent image (different song,
  different card state) shows the same structure.
- Consequence: the extractor derives MTR bounds as
  `mtr_start_sector = part0.lba_start + part0.sector_count`
  `mtr_end_sector   = total_card_sectors`.
  For the 31 GB test card: MTR sector range = 8,386,442 .. 60,580,351
  (`0x007FF58A` .. `0x039C5FFF`), byte range `0xFFEB1400` .. `0x738BFFFFF`
  (26,723,281,920 B ≈ 26.72 GB).

---

## 2. MTR region layout (high-level)

| MTR offset      | Abs. byte offset | Sector (MTR rel.) | Status   | Contents |
|----------------:|-----------------:|------------------:|----------|----------|
| 0x00000..0x007FF | 0xFFEB1400..0xFFEB1BFF | 0..3    | PROBABLE | Zero (reserved; suspected backup-superblock slot — H-FS-03) |
| 0x00800..0x0087B | 0xFFEB1C00..0xFFEB1C7B | 4       | PROBABLE | **MTR main header** (see §3). 124 B of structured BE-32 metadata |
| 0x0087C..0x013FF | 0xFFEB1C7C..0xFFEB27FF | 4..9    | PROBABLE | Zero |
| 0x01400..0x01407 | 0xFFEB2800..0xFFEB2807 | 10      | HYPOTHESIS | 8-byte allocation-bitmap fragment `ff ff ff ff ff ff ff 0f` (60 set bits then 4 clear) |
| 0x01408..0x17FFF | 0xFFEB2808..0xFFEC93FF | 10..191 | PROBABLE | Zero |
| 0x18000..        | 0xFFEC9400..          | 192..   | PROBABLE | **Audio data begins** (16-bit signed LE PCM oscillating around zero — H-AUDIO-01) |
| 0x19000          | 0xFFECA400            | 200     | PROBABLE | **"BFS ROOT"** sector (see §4) |
| 0x100000         | 0xFFFB1400            | 2,048   | HYPOTHESIS | Secondary / backup BFS root? Pointed to by main header +0x6C (H-FS-04) |

### H-FS-02 — Metadata is big-endian 32-bit (PROBABLE)
On-disk metadata fields are stored as BE u32s even though the audio PCM is
little-endian. Verified by the round-trip of four independent sector
pointers at 0x08/0x38/0x60/0x6C: each, when interpreted BE and multiplied by
the header-declared sector size (512 B, offset 0x24), lands exactly on a
known feature (self, MTR start, BFS ROOT, MTR+1 MiB).

---

## 3. MTR main header (offset 0xFFEB1C00 / MTR sector 4)

All fields PROBABLE unless noted. Interpretation column is HYPOTHESIS
unless marked otherwise. Encoding: big-endian unsigned 32-bit words.

| Hdr off | BE u32       | Decimal     | Interpretation                                                 | Status |
|--------:|--------------|------------:|----------------------------------------------------------------|--------|
| 0x00 | `38 97 49 89` | 0x38974989 | Magic or CRC-32 of trailing header bytes (not ASCII)            | HYPOTHESIS |
| 0x04 | 0x00000088    | 136         | Header record length (payload is 124 B, 128 rounded + tag)     | HYPOTHESIS |
| 0x08 | 0x007FF58E    | 8,386,446   | Self-pointer (sector of this block). ×512 → 0xFFEB1C00 ✓       | PROBABLE |
| 0x0C | 0x00000000    | 0           | reserved                                                       | PROBABLE |
| 0x10 | 0x00000000    | 0           | reserved                                                       | PROBABLE |
| 0x14 | 0x00000002    | 2           | Count. Possibly track count for SONG0001.                      | HYPOTHESIS |
| 0x18 | 0x00000000    | 0           | reserved                                                       | PROBABLE |
| 0x1C | 0x00000005    | 5           | Count / enum                                                   | HYPOTHESIS |
| 0x20 | 0x00000001    | 1           | Flag / song count (unit had exactly 1 song)                    | HYPOTHESIS |
| 0x24 | 0x00000200    | 512         | **Sector size (bytes)** — CONFIRMED by alignment of all pointers | PROBABLE |
| 0x28 | 0x00000004    | 4           | Count / header-start sector offset (this block is MTR sector 4)| HYPOTHESIS |
| 0x2C | 0x000000C0    | 192         | Reserved-region length in sectors. 192 × 512 = 0x18000         | PROBABLE |
| 0x30 | 0x00018000    | 98,304      | Reserved-region length in bytes (= 192 × 512). Matches 0x2C.   | PROBABLE |
| 0x34 | 0x000425E3    | 271,843     | Block/allocation counter                                        | HYPOTHESIS |
| 0x38 | 0x007FF58A    | 8,386,442   | MTR-region start sector (absolute). ×512 → 0xFFEB1400 ✓        | PROBABLE |
| 0x3C | 0x007FF594    | 8,386,452   | Sector of the 8-byte bitmap fragment at 0xFFEB2800 ✓           | PROBABLE |
| 0x40 | 0x000084C0    | 33,984      | unknown                                                        | HYPOTHESIS |
| 0x44 | 0x00000016    | 22          | unknown (count?)                                               | HYPOTHESIS |
| 0x48 | 0x00000000    | 0           | reserved                                                       | PROBABLE |
| 0x4C | 0x00000000    | 0           | reserved                                                       | PROBABLE |
| 0x50 | 0x00000000    | 0           | reserved                                                       | PROBABLE |
| 0x54 | 0x000425A7    | 271,783     | Near 0x425E3 at 0x34 (delta 60). Block-count variant?          | HYPOTHESIS |
| 0x58 | 0x00000000    | 0           | reserved                                                       | PROBABLE |
| 0x5C | 0x000425E3    | 271,843     | Duplicate of 0x34                                              | HYPOTHESIS |
| 0x60 | 0x007FF652    | 8,386,642   | **BFS ROOT sector pointer** (absolute). ×512 → 0xFFECA400 ✓    | PROBABLE |
| 0x64 | 0x000000C0    | 192         | Duplicate of 0x2C                                              | PROBABLE |
| 0x68 | 0x00000040    | 64          | unknown (entries? shift?)                                      | HYPOTHESIS |
| 0x6C | 0x0080058A    | 8,389,002   | Sector 0xFFFB1400 (MTR+1 MiB). Probable backup BFS root        | HYPOTHESIS (H-FS-04) |
| 0x70 | 0x031C59C0    | 52,190,656  | Total MTR-region sector count (≈ card's free-space sector count minus ~3,254) | HYPOTHESIS |
| 0x74..0x7B | …         | …           | tail / CRC / padding — not yet decoded                         | TBD |

### §3 consequences
- **Sector size 512 B is confirmed** by alignment of four independent header
  pointers.
- **The MTR header knows where it lives**: a self-pointer at offset 0x08
  makes the header CRC-verifiable and movable.
- **Field 0x14 = 2** is a plausible track-count for SONG0001 (2 tracks).
  Should be exactly 4 on an image with a 4-track song (Phase 3 diff).
- **Field 0x20 = 1** is a plausible song-count. Should be exactly 2 on an
  image with two songs (Phase 4 diff).

---

## 4. BFS ROOT sector (offset 0xFFECA400 / MTR sector 200)

Magic: ASCII **"BFS ROOT"** at sector offset +0x55 (byte 0xFFECA455).
Preceded by `33 20 20 20` (ASCII `"3   "`, i.e. the digit "3" padded with
spaces) at +0x04, which looks like an index field for root-block "3".

### H-FS-01 — BFS filesystem (PROBABLE)
The DP-03 MTR region is a custom filesystem labelled "BFS" (Brain File
System? Baseband? — unknown). The root directory inode lives at absolute
sector 0x007FF652. All upstream pointers from the MTR main header agree.

### BFS ROOT sector pointers (decoded so far)

| Sector offset | Value (BE u32) | Absolute ×512       | Status    | Notes |
|--------------:|----------------|---------------------|-----------|-------|
| +0x??         | 0x0036E83E     | 0x6DD07C00 (absolute, out of image) | HYPOTHESIS | Likely MTR-relative: 0x6DD07C00 − 0xFFEB1400 = 0x5DE1F800 (MTR offset) — to verify |
| +0x??         | 0x0036E97E     | 0x6DD2FC00 (absolute, out of image) | HYPOTHESIS | Same as above |
| +0x??         | 0x0036E9FE     | 0x6DD3FC00 (absolute, out of image) | HYPOTHESIS | Same as above |
| +0x??         | 0x007FF652     | 0xFFECA400          | PROBABLE  | Self-pointer, absolute card sector |

### H-BFS-01 — Pointer-interpretation convention (HYPOTHESIS)
The three `0x0036Exxx` values in the BFS ROOT sector are **MTR-relative** or
**BFS-block-relative** (absolute interpretation points past EOF). Verify by
dumping each candidate sector and looking for structured content.
If MTR-relative: 0x0036E83E × 512 = 0x6DD07C00 ⇒ MTR byte 0x6DD07C00 ⇒
absolute 0x6DD07C00 + 0xFFEB1400 = 0x7DBB9000. Still within the 26.7 GB MTR
region — plausible.

---

## 5. Song table / song entries

TBD — Phase 2/4. Likely lives in sectors pointed to by BFS ROOT.

Anticipated per-song fields (HYPOTHESIS):
- Song name (ASCII or UTF-16 fixed-size slot). `SONG0001` not found as
  ASCII/UTF-16LE/UTF-16BE/length-prefixed ASCII in the first scanned range.
  Either stored later in the BFS tree, stored as a non-text index, or
  projected by firmware (e.g. `SONG%04u`) and not persisted verbatim.
- Track count
- Creation / modified timestamp
- Sample rate code
- Pointer to track table
- BPM / tempo / length

---

## 6. Track table entry

TBD — Phase 4. Anticipated fields:
- Track name (short ASCII)
- Pointer to audio extent(s) — sector + length, or extent list
- Length in samples
- Pan / volume / fader
- Mute / solo flags
- EQ settings
- Virtual-take index

---

## 7. Audio data layout

### H-AUDIO-01 — PCM, 16-bit signed LE, mono, 44.1 kHz (HYPOTHESIS)
- Raw samples observed at 0xFFEC9400 (MTR+0x18000 = sector 192) climb
  smoothly and cross zero. Consistent with 16-bit signed little-endian
  mono PCM. The reserved-region length in the header (field 0x2C = 192
  sectors = 96 KiB) matches where this data begins exactly.
- Sample rate, bit depth, and channel count need Phase 3 confirmation
  via a deliberate known-signal recording (sine-wave, then diff).

### Allocation model (HYPOTHESIS)
Three candidates, to be resolved by Phase 3's diff 4:
1. **Fixed-region-per-track**: each track gets a contiguous sector run.
2. **Pointer-indexed extents**: each track's `track entry` holds an extent
   list pointing into a freeform audio pool.
3. **Sequential allocation**: tracks written one after another, linked
   list style.

The 8-byte bitmap fragment at MTR sector 10 (`ff ff ff ff ff ff ff 0f`)
suggests a bit-per-something allocation table; 60 bits set, 4 clear.
Whether "something" is a sector, a cluster, or a track slot is TBD.

---

## 8. Global constants

| Constant              | Value      | Status      | Source |
|-----------------------|-----------:|-------------|--------|
| MBR sector size       | 512 B      | CONFIRMED   | MBR structure |
| MTR sector size       | 512 B      | PROBABLE    | Header +0x24 = 0x200; all pointers × 512 land on known features |
| MTR reserved prefix   | 192 sectors (96 KiB) | PROBABLE | Header +0x2C and +0x30 |
| MTR start sector (abs)| 0x007FF58A = 8,386,442 | PROBABLE | MBR part0 end + 1 |
| Sample rate           | 44,100 Hz  | HYPOTHESIS  | Phase 3 pending |
| Bit depth             | 16         | HYPOTHESIS  | Sample observation |
| Byte order (audio)    | LE         | HYPOTHESIS  | Sample observation |
| Channels / track      | 1 (mono)   | HYPOTHESIS  | Each track is 1 mono stream on DP-03 |
| Bytes / sec / track   | 88,200     | HYPOTHESIS  | Derived from above |

---

## 9. Open hypotheses (summary)

| ID       | Claim                                                                                   | Status      | Where to test |
|----------|-----------------------------------------------------------------------------------------|-------------|---------------|
| H-PT-01  | Single-partition MBR; MTR region is undeclared raw sectors after partition 0           | PROBABLE    | Second independent image |
| H-FS-01  | Filesystem is labelled "BFS"; root at MTR sector 200                                    | PROBABLE    | "BFS ROOT" magic at 0xFFECA455 |
| H-FS-02  | Metadata is big-endian u32                                                             | PROBABLE    | Four pointer round-trips agree |
| H-FS-03  | MTR+0..MTR+0x7FF is a backup-superblock slot (currently zero)                          | HYPOTHESIS  | Check on a card with more writes |
| H-FS-04  | MTR+0x100000 is a secondary/backup BFS ROOT                                            | HYPOTHESIS  | Dump that sector |
| H-BFS-01 | 0x0036Exxx pointers in BFS ROOT are MTR-relative, not absolute                         | HYPOTHESIS  | Dump MTR+(0x0036E83E × 512) etc. |
| H-AUDIO-01 | Audio begins at MTR sector 192; 16-bit signed LE mono PCM @ 44.1 kHz                | HYPOTHESIS  | Phase 3 known-signal diff |

---

## 10. BFS allocation table and audio extraction (CONFIRMED)

Status: **CONFIRMED** — Zach listened to the decoded `SONG001_STEREO_NORMALIZED.wav`
on 2026-04-20 and confirmed "YES YOU CRACKED IT this is it". This section
supersedes the earlier HYPOTHESIS-level audio layout notes in §7.

### 10.1 Allocation table location

| Range (MTR offset) | Absolute byte range     | Contents                      |
|-------------------:|-------------------------|--------------------------------|
| 0x288000..0x388000 | 0x1000E9400..0x1001E9400 | **Allocation table** (1 MiB)  |
| 0x388000..0x390000 | 0x1001E9400..0x1001F1400 | Padding / zero                |
| 0x390000..end      | 0x1001F1400..            | **Audio data region**         |

The table is a flat array of **64-byte records**, 16,384 records per MiB.
Each record's "A-slot" is its byte offset within the table (0x00, 0x40,
0x80, …). Records reference each other by A-slot, forming doubly-linked
chains.

### 10.2 Record layout (64 bytes, big-endian u32)

```
+0x00  u32  A          Self-index (byte offset in alloc table)
+0x04  u32  flags      Record type (see §10.3)
+0x08  u32  prev       A-slot of previous record in chain (0 = head)
+0x0C  u32  next       A-slot of next record in chain (0 = tail)
+0x10  payload (48 B)  Type-dependent body
```

### 10.3 Record types observed

| `flags`      | Meaning                                                      |
|--------------|--------------------------------------------------------------|
| `0x80040003` | **Data cluster head** (`prev=0` marks the true chain head)   |
| `0x80104900` | Metadata partner paired with a data cluster                  |
| `0x80030000` | **Song master record** (one per song/track root)             |
| `0x80020007` | SFD-meta (Song File Descriptor metadata link)                |
| `0x90010001` | Audio inode (per-track root)                                 |
| `0x90010005` | **Audio extent descriptor — first half** (6 pairs)           |
| `0x90010006` | **Audio extent descriptor — second half** (6 pairs)          |

### 10.4 Song master record (`flags = 0x80030000`)

Payload layout, per the decoded record at A=0x9FC0 (Track 1):

```
+0x08  u32  prev song master        (0 for first song)
+0x0C  u32  next song master / sibling track
+0x24  u32  SFD-meta link
+0x2C  u32  datetime (epoch-style)
+0x30  u32  datetime (duplicate / modified)
+0x34  u32  data cluster chain head A-slot
+0x38  u32  sample rate (0xAC44 = 44100 Hz)
```

Per the 00_song0001.img decode, Track 1's master sits at A=0x9FC0 with
next=0x9D00, and Track 2's master sits at A=0x9D00 with prev=0x9FC0.

### 10.5 Audio extent descriptors (`flags = 0x90010005 / 0x90010006`)

Payload holds **six (sector_id, file_offset) pairs** starting at +0x10:

```
+0x10  u32  sector_id_0   +0x14  u32  file_offset_0
+0x18  u32  sector_id_1   +0x1C  u32  file_offset_1
...six pairs, 48 B total
```

Each pair says: "read a chunk from sector *sector_id* and place it at
*file_offset* within the reconstructed track." A pair of consecutive
extent records (`0x90010005` + `0x90010006`) covers the full track —
11 pairs for a 12.3 s DP-03 mono track.

For 00_song0001.img:

```
A=0x9E00  flags=0x90010005  T1 pairs:
  (0x26,0)(0x28,0xC000)(0x2A,0x18000)(0x2C,0x24000)(0x2E,0x30000)(0x30,0x3C000)
A=0x9EC0  flags=0x90010006  T1 pairs:
  (0x30,0x3C000)(0x32,0x48000)(0x34,0x54000)(0x36,0x60000)(0x38,0x6C000)(0x3A,0x78000)
A=0x9E40  flags=0x90010005  T2 pairs:
  (0x27,0)(0x29,0xC000)(0x2B,0x18000)(0x2D,0x24000)(0x2F,0x30000)(0x31,0x3C000)
A=0x9F40  flags=0x90010006  T2 pairs:
  (0x31,0x3C000)(0x33,0x48000)(0x35,0x54000)(0x37,0x60000)(0x39,0x6C000)(0x3B,0x78000)
```

Track 1 uses **even** sectors 0x26..0x3A; Track 2 uses **odd** sectors
0x27..0x3B. The final pair in the first half equals the first pair in
the second half (0x30 / 0x31 at offset 0x3C000), so the second half
extends rather than restarts.

### 10.6 Sector addressing (the thing that made this hard)

"Sector" in the allocation table means **BFS cluster**, not MBR sector.
BFS cluster size = `0x18000` bytes (96 KiB — field +0x2C / +0x30 of the
MTR main header, §3). Sector 0 is MTR_BASE itself (not an offset past
the allocation table). To reach sector N:

```
absolute_byte_offset = MTR_BASE + N * 0x18000
```

Each extent pair reads **the full `0x18000`** (96 KiB, the entire BFS
cluster) from the sector's base and appends it to the track stream.
Tracks end up trimmed to the master's declared `dur_samples * 2` bytes.

> **⚠️ STRIDE CORRECTION (2026-04-22, H-STRIDE-02 → CONFIRMED).**
> Earlier SONG001 extraction worked while reading only `0xC000` per
> pair because SONG001 is stride-1 (one full track stored in sector 0,
> one in sector 1, etc. — there is no "second half" to miss). Multi-
> sector songs like SONG116 store **both 0xC000 halves of each sector
> as consecutive audio of the same track**, so the extractor must read
> all `0x18000`. Pairing-order is determined by `boff` — sort pairs by
> `boff` before reading. Confirmed by MD5 equality between the
> extractor output and the DP-03's own `TEX001_N.WAV` export for
> SONG116.

Concretely for 00_song0001.img:

```
Track 1, sector 0x26 → MTR_BASE + 0x26 * 0x18000 = MTR+0x390000
Track 2, sector 0x27 → MTR_BASE + 0x27 * 0x18000 = MTR+0x3A8000
```

### 10.7 Minimum extraction recipe

1. Parse MBR, compute `MTR_BASE = (part0.lba_start + part0.sector_count) * 512`.
2. Read the MTR main header at MTR_BASE+0x800. Grab BFS cluster size from +0x2C (= 0x18000 on the DP-03).
3. Scan the alloc table at `MTR_BASE + 0x288000` for records with
   `flags = 0x80030000` — these are song masters.
4. For each master, follow `+0x34` to the data-chain head; walk
   `prev/next` via flags `0x80040003` to enumerate clusters if needed.
5. Separately, scan the alloc table for records with
   `flags ∈ {0x90010005, 0x90010006}` and group them by the track they
   describe (the song master's SFD-meta link connects them).
6. Sort the `(sector_id, file_offset)` pairs by `file_offset`, then for
   each pair append **all `0x18000` bytes** of `MTR_BASE + sector_id *
   0x18000` to the output stream.
7. Truncate to the master's declared `dur_samples * 2` bytes (from
   master record +0x2C).
8. Emit as 16-bit signed LE mono WAV @ 44,100 Hz.

> **Stride note:** step 6 used to read only `0xC000` per pair. That
> worked for stride-1 SONG001 but gave choppy output for multi-sector
> recordings. Read the full sector. See §10.9 H-STRIDE-02.

### 10.8 Observed statistics for 00_song0001.img

| Metric                | Track 1    | Track 2     |
|-----------------------|-----------:|------------:|
| Size (bytes)          | 1,084,948  | 1,084,948   |
| Duration (s)          | 12.30      | 12.30       |
| Peak (int16)          | 3,496      | 12,433      |
| RMS                   | 175        | 773         |
| Peak dBFS             | -19.4      | -8.4        |
| Silent fraction (|x|<50) | 0.92    | 0.81        |
| First audible moment  | 5.57 s     | 3.90 s      |

Characteristic of real audio: smooth low-amplitude samples stepping
~1 LSB per frame (e.g. `101, 102, 103, 102, …`).

### 10.9 Promotion log

- H-AUDIO-01 → **CONFIRMED**: PCM is 16-bit signed LE mono @ 44,100 Hz
  (sample rate from song master +0x38 = 0xAC44; audible playback
  verified by Zach).
- Allocation model §7 candidate #2 — "pointer-indexed extents" —
  **CONFIRMED**. Each track is described by an inode (`0x90010001`)
  plus two extent records (`0x90010005`/`0x90010006`) holding 12 total
  pairs, with actual chunks of size `0xC000` stitched at recorded
  `file_offset`s.
- BFS "sector" unit **= BFS cluster** (`0x18000` bytes), referenced
  from MTR_BASE.
- **2026-04-22 H-STRIDE-02 → CONFIRMED**: each extent pair contributes
  the full `0x18000` bytes of its sector to the output stream, not just
  the first `0xC000` half. `boff` is a time-ordering key; sort pairs
  by `boff` and concatenate sectors in that order. Validated by
  byte-identical MD5 match between extractor output and the DP-03's
  own `TEX001_N.WAV` exports for SONG116 (T1 + T2, 648.30 s each).
- **2026-04-22 H-PRIMARY-TREE-01**: a master's inode holds multiple
  subtree pointers (observed at `+0x24`, `+0x28`, `+0x38`). Only
  `+0x24` is needed for playback — the others are redundant tail
  overflow leaves that duplicate the end-of-track sectors. Confirmed
  for SONG116; needs re-validation on any song whose `+0x24` alone
  does not yield the declared `dur_samples * 2` bytes.

---

## Change log

- **2026-04-20** — Document created (all fields TBD).
- **2026-04-20** — After Phase 1 imaging and first MTR scan, promoted H-PT-01 to PROBABLE, added MTR layout §2, MTR main header §3 (20+ decoded fields), BFS ROOT §4, and hypotheses §9.
- **2026-04-20** — Added §10 BFS allocation table and audio extraction. Promoted H-AUDIO-01 and the extent-pointer allocation model to CONFIRMED after Zach audibly verified the extracted SONG001 stereo WAV ("YES YOU CRACKED IT").
