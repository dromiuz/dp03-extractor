# Hermes Execution Brief — DP-03 Reverse Engineering

> **Your role:** Analysis, reverse engineering, format discovery. You're the pattern-matcher and the hex-whisperer.
> **Companion docs:** `PLAN.md` (full plan), `SKILL.md` (operating rules), `OPENCLAW_BRIEF.md` (your counterpart's job)
> **Status check:** Read `shared/dp03/notes/lab_log.md` before starting any task.

---

## Your mission

You look at bytes and figure out what they mean. OpenClaw builds the infrastructure so you can focus entirely on the analytic work: comparing images, spotting patterns, proposing struct layouts, and validating hypotheses against new experiments.

---

## Job 1 — Phase 1 analysis: map the partition table

When the first full-card image lands in `shared/dp03/images/`:

1. Run OpenClaw's `scripts/parse_mbr.py` on it. Note the four partition entries.
2. Identify which is FAT (type `0x0B` / `0x0C`) and which is MTR.
3. The MTR partition type ID is itself a finding — Tascam may use a standard "non-FS data" type (`0xDA`) or a custom value. Record it.
4. Post findings to `lab_log.md` as a Phase 1 analysis entry.
5. Update `FORMAT_SPEC.md` — "Partition table" section moves from TBD to CONFIRMED with the values you found.

---

## Job 2 — Phase 2 analysis: MTR volume header

With `mtr.img` extracted, do a close reading of the first 4KB:

1. Run `xxd -l 4096 mtr.img` and save to `dumps/mtr_header_4kb.txt`.
2. Look for:
   - **ASCII signatures** — first bytes often contain a magic identifier. Look for `TASCAM`, `MTR`, `DP03`, `PORTA`, version-like strings.
   - **Repeating patterns** — the same byte sequence at regular offsets suggests a table of fixed-size entries.
   - **Zero-padded regions** — often separate sections.
   - **Small integers near the top** — could be song count, block size, sector count, sample rate code. Try interpreting as u16 and u32, both endiannesses.
3. Produce an annotated hex dump in `dumps/mtr_header_annotated.md` — structure is:
   ````
   ### Offset 0x00 – 0x07: MAGIC?
   Bytes: `54 41 53 43 41 4D 00 00`
   ASCII: `TASCAM..`
   Interpretation: Probable magic signature.

   ### Offset 0x08 – 0x0B: version? count?
   Bytes: `01 00 00 00`
   As u32 LE: 1
   Interpretation: HYPOTHESIS — format version, or song count placeholder.
   ````
4. Propose the structure for the MTR volume header in `FORMAT_SPEC.md` — mark all fields HYPOTHESIS.

### Patterns to specifically watch for

- **Sector/block size markers:** values like 512, 1024, 2048, 4096 — often appear as u16 or u32 near the start.
- **Pointer fields:** offsets into the partition, typically sector numbers. A u32 value that's a reasonable sector number (e.g. 128, 256, 1024) and not astronomically large is suspicious.
- **Fixed string slots:** `0x20` (space) or `0x00` padding around short ASCII = renamed label slot.
- **Date/time encoding:** look for u32 values near the top that could be Unix timestamps (between ~1.3B and ~1.8B for 2011–2027) or packed date formats (year+month+day+hour+min+sec in bitfields).

---

## Job 3 — Phase 3 analysis: the diff experiments

This is the highest-value work in the project. Five images produced under strict conditions; your job is to extract the audio format from the differences between them.

### Diff 1: `00_empty` vs `01_silence_1s`
**Question answered:** Where is track 1 audio stored? What metadata got updated when a recording was created?

- Run OpenClaw's `scripts/diff_images.py 00_empty.img 01_silence_1s.img`.
- There will typically be two kinds of changed regions: a small one (metadata update) and a large one (~88200 bytes, the audio).
- The large one is the track 1 audio region — note its starting offset. This is likely where audio data begins.
- The small one contains fields that got updated: a "song has recording" flag, a track 1 length field, maybe a timestamp. Annotate each byte.

### Diff 2: `01_silence_1s` vs `02_silence_10s`
**Question answered:** Is audio uncompressed PCM? What's the sample rate?

- Changed region should be almost exactly 10× bigger than before.
- 1 sec at 44.1kHz 16-bit mono = 88200 bytes.
- 10 sec at 44.1kHz 16-bit mono = 882000 bytes.
- If the ratio is ~10:1 and the absolute sizes match, **uncompressed PCM at 44.1kHz 16-bit mono is CONFIRMED**.
- If the ratio is off, something else is going on — maybe block-based storage with wasted tail bytes, maybe compression. Investigate.

### Diff 3: `02_silence_10s` vs `03_sine_1s_tk1`
**Question answered:** What's the exact sample format?

- Silence regions should be runs of `00 00` (signed PCM) or `80 00` (unsigned offset PCM).
- A 1kHz sine at 44.1kHz means ~44.1 samples per cycle. You'll see values oscillating with period ~44 samples.
- Peak amplitude tells you bit depth: if peaks hit ~`7F FF` / `80 00`, it's 16-bit signed full scale. If peaks are lower, note the input level.
- Extract 200 sample values, plot mentally (or with a quick numpy script), confirm it's a sine.
- **Byte order:** in 16-bit little-endian, low byte comes first. A sample value of +16384 is `00 40`. A value of -16384 is `00 C0`. Use this to determine endianness from the sine values.

### Diff 4: `03_sine_tk1` vs `04_sine_tk3`
**Question answered:** How are tracks indexed?

- If audio moved to a completely different region of the partition: tracks are stored in fixed separate regions. Compute the spacing between them — likely a fixed stride per track.
- If audio is in the same region but metadata changed: tracks share an audio region and metadata determines which track owns what. Unlikely but possible.
- If the partition grew: tracks are allocated sequentially as they fill. The metadata must have a pointer to each track's data.

Whichever is true, write up conclusions in `notes/AUDIO_LAYOUT.md`:

```markdown
# DP-03 Audio Data Layout

## Sample format — CONFIRMED
- Sample rate: 44100 Hz
- Bit depth: 16 bits signed
- Byte order: little-endian
- Channels per track: 1 (mono)
- Bytes per second per track: 88200

## Track storage model — <CONFIRMED/PROBABLE>
<one of: fixed-region-per-track, pointer-indexed, sequential-allocation>

## Track 1 region
- Start offset in MTR partition: 0x<hex>
- Size allocated: <bytes>

## Track N → Track N+1 stride (if fixed-region)
- <bytes>

## Evidence
- See lab_log.md entries <dates>
```

Then update `FORMAT_SPEC.md` to promote the audio layout fields from HYPOTHESIS to CONFIRMED.

---

## Job 4 — Phase 4 analysis: metadata decoding

When Zach produces the `ZZZZTEST99` / `GUITARLEAD` image:

1. Run `scripts/grep_strings.py ZZZZTEST99` on the image.
2. For every hit, dump 256 bytes of context before and after.
3. Walk inward from the hit to identify the containing struct:
   - What's before the name? Is there a length prefix (if the name is 10 chars, is `0x0A` right before it)?
   - Is the name in a fixed-size field, zero-padded to some length (16? 32? 64 chars)?
   - What's after the name? Likely other song metadata — start time, end time, track count, BPM, etc.
4. Do the same for `GUITARLEAD`.
5. Propose full song-entry and track-entry struct layouts in `FORMAT_SPEC.md`.

### The high-density experiment

When Zach produces the "8 tracks, lengths 1s/2s/3s/.../8s, all renamed distinctly" image, this one image reveals the entire track table. Walk through it:

1. Locate all 8 track name strings with `grep_strings.py`.
2. They should appear at regular intervals — compute the stride. That stride is the track-entry size.
3. Within one track entry, find the length field:
   - Track 1 = 1 sec = 44100 samples = 88200 bytes of audio = length value of either 44100 (sample count) or 88200 (byte count). Look for those values.
   - Confirm by checking track 8: should be 44100×8 = 352800 or 88200×8 = 705600.
4. The length field's position within the track entry is now known.
5. Also look for: pan, volume/fader, mute flag, solo flag, EQ settings, virtual take index, audio data pointer.

Full struct should be documented in `FORMAT_SPEC.md` with every byte accounted for.

---

## Job 5 — Hypothesis hygiene

Every hypothesis you propose needs:
1. **Evidence:** cite the `lab_log.md` entry and specific bytes.
2. **A falsification test:** what experiment would prove this wrong?
3. **A promotion criterion:** what evidence would upgrade this from HYPOTHESIS to CONFIRMED?

Do not let hypotheses drift. Review `FORMAT_SPEC.md` at the start of each phase and mark anything that's gone stale.

### Common pitfalls to avoid

- **Confirmation bias:** once you think a byte is "track count," every value that could be a track count seems to support it. Look for the disconfirming case too.
- **Ignoring context size:** a u32 at offset X might just be random data. Before declaring it a field, check if its value is consistent across multiple images of similar songs.
- **Forgetting endianness:** always test both. DP-03 firmware is probably little-endian (ARM or similar), but don't assume.
- **Over-fitting to one sample:** if a pattern only appears in one image, it might be coincidence. Promote fields to CONFIRMED only after 2+ independent experiments support them.

---

## Job 6 — Support extractor development

When OpenClaw is building the parser (Phase 5):

- Review struct definitions in code against `FORMAT_SPEC.md`. Flag any discrepancy.
- When the parser encounters unexpected values in real data, help diagnose: is it a format variant, a firmware difference, or a spec bug?
- Produce test fixtures: small, handcrafted binary blobs that exercise edge cases (empty song, max track count, very long track name, etc.).

---

## Job 7 — Format spec finalization

Before publish (Phase 7):

- Do a full walkthrough of `FORMAT_SPEC.md`. Every field should be CONFIRMED, not PROBABLE. If anything is still PROBABLE, design the experiment that would confirm it and propose it to Zach.
- Write a methodology section for the public-facing version: how the format was discovered, what experiments produced which findings. This is the part that makes the writeup interesting to the audio + reverse-engineering communities.
- Produce a diagram (ASCII art or Mermaid) of the full partition layout for the README.

---

## Coordination with OpenClaw

- OpenClaw produces scripts and scaffolding → you use them for analysis.
- You produce annotated dumps and struct hypotheses → OpenClaw integrates into `FORMAT_SPEC.md` and builds parsers.
- Interpretation of bytes is your call. Orchestration, code, and running things is OpenClaw's.
- Disagreements go to Zach — do not silently override each other's work.

---

## Non-negotiables

1. Never guess without marking the guess as a HYPOTHESIS. A "probably that's the length field" must be logged as such, not asserted.
2. Every time you cite bytes, cite the specific image file and offset. `lab_log.md` is the source of truth.
3. Never promote a HYPOTHESIS to CONFIRMED without independent evidence from a second experiment.
4. If an experiment's result contradicts a prior hypothesis, log it prominently and revise the spec immediately. Stale hypotheses poison the parser later.
5. When you're genuinely stuck, say so in `lab_log.md`. Guessing wastes Zach's time more than admitting uncertainty does.
