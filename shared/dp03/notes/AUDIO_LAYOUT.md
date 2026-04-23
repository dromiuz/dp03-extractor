# DP-03 Audio Data Layout

> Status: TBD. Populated in Phase 3 from five known-signal experiments.

## Sample format — HYPOTHESIS
- Sample rate: 44100 Hz
- Bit depth: 16 bits signed
- Byte order: little-endian
- Channels per track: 1 (mono)
- Bytes per second per track: 88200

## Track storage model — TBD
One of:
- **fixed-region-per-track** — each track gets a pre-allocated slab at a fixed stride
- **pointer-indexed** — track table entry holds a byte-offset to its audio
- **sequential-allocation** — audio regions grow as tracks are filled

## Track 1 region
- Start offset in MTR partition: TBD
- Size allocated: TBD

## Track N → Track N+1 stride (if fixed-region)
- TBD

## Evidence log

No entries yet. Phase 3 diff outputs will populate this section.
