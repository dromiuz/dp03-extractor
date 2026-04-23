# DP-03 Extractor — Windows Setup

This is the focused Windows install / run guide. For the full reverse
engineering context see `README.md`; for how to use the extractor once
it's running see `DP-03 Extractor User Guide.pdf`.

---

## 1. Install Python

Install Python 3.10, 3.11, or 3.12 from the official installer:

> https://www.python.org/downloads/windows/

During installation:

- Keep **"tcl/tk and IDLE"** checked (this is Tkinter — required).
- Tick **"Add python.exe to PATH"**.
- The **"py launcher"** option should stay checked (it is on by default).

You do **not** need the Microsoft Store version or Anaconda. A plain
python.org install is the most reliable.

Verify it works by opening a new Command Prompt and running:

```
py -3 --version
py -3 -c "import tkinter; tkinter.Tk().destroy(); print('Tk OK')"
```

Both commands should succeed.

---

## 2. Install the optional mixer dependencies

The extractor itself only needs the standard library, but the **live
mixer** and **Render mix** features need `numpy` and `sounddevice`.
The launcher will try to install them for you the first time it runs;
if that fails or you prefer to do it manually:

```
py -3 -m pip install --user -r requirements.txt
```

`sounddevice` wraps PortAudio; the wheel on PyPI already bundles the
native DLL, so no extra audio driver install is needed.

---

## 3. Run the app

Double-click **`Launch DP-03 Extractor.bat`** in the project folder.

The launcher:

1. Picks the first Python interpreter that has a working Tkinter
   (`py -3.12` → `py -3.11` → `py -3.10` → `py -3` → `python` → `python3`).
2. Makes sure `numpy` + `sounddevice` are installed (installs them via
   `pip --user` if not).
3. Starts the app with `py -3 -m dp03app`.
4. Keeps the Command Prompt window open on failure so you can read the
   error.

If Python is not on PATH, run from a Command Prompt in the project folder:

```
py -3 -m dp03app
```

---

## 4. Reading the SD card raw

Windows gives each inserted card a drive letter (`E:\`, `F:\`, …) for
its small FAT partition. The DP-03's audio content lives **outside**
that partition, in a proprietary region on the raw physical disk, so we
have to read the disk itself — not the drive letter.

The extractor does this automatically:

- Removable drives are enumerated via `GetLogicalDrives` + `GetDriveTypeW`.
- A matching drive (one whose label starts with `DP-03` or whose root
  contains `TEX`/`SONG`/`TASCAM` files) is mapped to its physical disk
  number with PowerShell's `Get-Partition | Get-Disk`.
- The raw device path becomes `\\.\PhysicalDriveN`.

### Administrator requirement

Reading `\\.\PhysicalDriveN` requires **Administrator** privileges on
Windows. You have two options:

1. **Right-click `Launch DP-03 Extractor.bat` → "Run as administrator".**
   This is the simplest path — the whole app runs elevated, and the raw
   reads just work.

2. **Image the card once from an elevated PowerShell**, then point the
   app at the .img file. Example (PowerShell as Administrator):

   ```powershell
   $src = [System.IO.File]::OpenRead('\\.\PhysicalDrive3')
   $dst = [System.IO.File]::Create('C:\Users\you\dp03.img')
   $buf = New-Object byte[] (1MB)
   while (($n = $src.Read($buf, 0, $buf.Length)) -gt 0) {
       $dst.Write($buf, 0, $n)
   }
   $src.Close(); $dst.Close()
   ```

   Then choose **"Load from image file…"** in the app and pick
   `C:\Users\you\dp03.img`. No elevation needed after the image is on
   disk.

> **Find the right `PhysicalDriveN`.** In an elevated PowerShell run
> `Get-Disk` — the DP-03 card will show as a small (~2 GB) removable
> disk with a recognisable friendly name. Match the `Number` column to
> `\\.\PhysicalDriveN`.

---

## 5. Troubleshooting

**"No Python with Tkinter was found."**
Python isn't installed, is not on PATH, or was installed without the
tcl/tk option. Re-install from python.org with the defaults and make
sure both "Add python.exe to PATH" and "tcl/tk and IDLE" are checked.

**"Access is denied" when the app tries to read the card.**
Windows is refusing raw access. Right-click the `.bat` and pick
"Run as administrator", or image the card in an elevated PowerShell
and load the `.img` instead.

**The live mixer button says "Audio not available".**
`numpy` or `sounddevice` didn't install. Run
`py -3 -m pip install --user numpy sounddevice` in a Command Prompt
and restart the app. If `sounddevice` still fails to import, install
the Microsoft Visual C++ Redistributable
(https://aka.ms/vs/17/release/vc_redist.x64.exe) and try again.

**No cards are detected but the DP-03 card is in the reader.**
The card's FAT partition has to mount first — open File Explorer and
confirm you can see the card's drive letter. If the FAT partition is
unreadable (Windows offers to format it), skip straight to
**"Load from image file…"** — image the raw disk with PowerShell
(section 4) and open the `.img`.

**PowerShell is blocked by execution policy.**
The launcher only calls PowerShell for a single `Get-Partition` probe
which is always allowed. If you hit an ExecutionPolicy block in a
different context, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` in an elevated
PowerShell.

---

## 6. Optional: build a standalone .exe

For sharing the extractor with someone who doesn't have Python, there's
a PyInstaller spec at `packaging/dp03app.spec`:

```
py -3 -m pip install --user pyinstaller
py -3 -m PyInstaller packaging/dp03app.spec
```

The output ends up in `dist/DP-03 Extractor/`. Ship the whole folder
(not just the `.exe`) — PyInstaller's one-folder mode keeps Tkinter and
the Python runtime alongside the binary.
