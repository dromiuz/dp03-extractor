# Phase 3 Checklist — Known-signal experiments

> This is the key unlock. Five recordings in a specific order. If the order
> or conditions drift, the diffs lose their diagnostic power.

**Before starting:**

- [ ] DP-03 firmware version recorded in `lab_log.md`: ________
- [ ] Spare SD card only (2GB+). Real recordings are NOT on this card.
- [ ] Card formatted on the DP-03 (System → Format).
- [ ] Tone generator on your phone or laptop ready (1 kHz sine, line-level).
- [ ] Have a cable to feed the tone into Input A of the DP-03.
- [ ] `image_card.sh` script is runnable.

---

## Recording 1 of 5 — `00_empty.img`

- [ ] DP-03: Menu → Song → New. Name it `EMPTY`. Do NOT arm any track. Do NOT record.
- [ ] Menu → Song → Save. Power down with the unit's power button (clean shutdown — not by pulling the SD).
- [ ] Remove card. Insert in Mac.
- [ ] `./shared/dp03/code/scripts/image_card.sh /dev/disk<N> 00_empty`
- [ ] Log to `lab_log.md` with firmware version, card info, and any observations.

**Key finding you expect from this image alone:** the layout of an empty DP-03 card.

---

## Recording 2 of 5 — `01_silence_1s.img`

- [ ] Put card back in DP-03.
- [ ] Keep `EMPTY` song open (or create new song `SIL1` if fresh song needed — note which in lab log).
- [ ] Arm **track 1 only**. **No input cable connected.**
- [ ] Press RECORD and PLAY. Let run exactly **1 second** (count: "one-thousand-one" and stop).
- [ ] STOP. Menu → Song → Save. Power down cleanly.
- [ ] Remove card. Image: `./image_card.sh /dev/disk<N> 01_silence_1s`
- [ ] Log to `lab_log.md`.

**Key finding:** position of track 1 audio region + the metadata fields that change when a track has data.

---

## Recording 3 of 5 — `02_silence_10s.img`

- [ ] Put card back in DP-03. **New song.** Name it `SIL10`.
- [ ] Arm track 1 only. No input.
- [ ] RECORD + PLAY. Let run **10 seconds.** Use a stopwatch — accuracy matters.
- [ ] STOP. Save. Power down cleanly.
- [ ] Image: `./image_card.sh /dev/disk<N> 02_silence_10s`
- [ ] Log.

**Key finding:** whether audio size is linear. 10s silence should be ≈10× the bytes of 1s silence. If 1s=88200 B and 10s=882000 B, 44.1 kHz 16-bit mono PCM is **PROBABLE**.

---

## Recording 4 of 5 — `03_sine_1s_tk1.img`

- [ ] New song `SINE1`. **Plug tone source into Input A.** Level to unity (no clipping).
- [ ] Start 1 kHz sine tone playing.
- [ ] Arm track 1 only, assign Input A → Track 1.
- [ ] RECORD + PLAY. Let run **exactly 1 second.**
- [ ] STOP. Save. Power down cleanly.
- [ ] Image: `./image_card.sh /dev/disk<N> 03_sine_1s_tk1`
- [ ] Log.

**Key finding:** confirms sample format & byte order. A 1 kHz sine at 44.1 kHz oscillates every ~44 samples. Silence bytes replaced by recognizable oscillation proves uncompressed PCM. Peak value tells you bit depth and signedness.

---

## Recording 5 of 5 — `04_sine_1s_tk3.img`

- [ ] New song `SINE3`. Same tone source still running.
- [ ] Arm **track 3 only.** Assign Input A → Track 3.
- [ ] RECORD + PLAY. Let run **exactly 1 second.**
- [ ] STOP. Save. Power down cleanly.
- [ ] Image: `./image_card.sh /dev/disk<N> 04_sine_1s_tk3`
- [ ] Log.

**Key finding:** how tracks are indexed. Same audio content, different track. The audio region should either move to a fixed offset for track 3, or the pointer in metadata should change while audio lives in the same arena.

---

## Analysis (after all 5 are in)

Hermes analysis runs these diffs:

```sh
python3 shared/dp03/code/scripts/parse_mbr.py shared/dp03/images/00_empty.img
# -> identify MTR partition, extract to 00_empty_mtr.img, repeat for each image
python3 shared/dp03/code/scripts/parse_mbr.py shared/dp03/images/00_empty.img --extract 1 --output shared/dp03/images/00_empty_mtr.img
# (repeat --extract for 01..04)

python3 shared/dp03/code/scripts/diff_images.py \
    shared/dp03/images/00_empty_mtr.img \
    shared/dp03/images/01_silence_1s_mtr.img --hex \
    | tee shared/dp03/dumps/diff_empty_vs_silence1s.txt

python3 shared/dp03/code/scripts/diff_images.py \
    shared/dp03/images/01_silence_1s_mtr.img \
    shared/dp03/images/02_silence_10s_mtr.img --hex \
    | tee shared/dp03/dumps/diff_silence1s_vs_silence10s.txt

python3 shared/dp03/code/scripts/diff_images.py \
    shared/dp03/images/02_silence_10s_mtr.img \
    shared/dp03/images/03_sine_1s_tk1_mtr.img --hex \
    | tee shared/dp03/dumps/diff_silence10s_vs_sine.txt

python3 shared/dp03/code/scripts/diff_images.py \
    shared/dp03/images/03_sine_1s_tk1_mtr.img \
    shared/dp03/images/04_sine_1s_tk3_mtr.img --hex \
    | tee shared/dp03/dumps/diff_sine_tk1_vs_tk3.txt
```

Each diff produces findings written into `AUDIO_LAYOUT.md`.

---

## Common failure modes

- **Dirty shutdown.** Pulling the SD card without saving + powering down cleanly can leave the on-disk state inconsistent. Re-do the experiment.
- **Card not unmounted.** If macOS had the FAT partition mounted and sync-pending, let `diskutil unmountDisk` finish before `dd`.
- **Length drift.** A "1 second" recording that actually lasts 1.3 s throws off the byte-ratio check. Use a stopwatch or the unit's transport counter to hit durations tightly.
- **Tone source level clipping.** If the 1 kHz sine clips to digital full scale, the sample values won't look sinusoidal — they'll look square. Back off ~6 dB on the source.
