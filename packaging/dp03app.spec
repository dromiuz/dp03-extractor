# PyInstaller spec for DP-03 Extractor (Windows one-folder build).
#
# Usage (from the project root):
#     py -3 -m pip install --user pyinstaller
#     py -3 -m PyInstaller packaging/dp03app.spec
#
# Output: dist/DP-03 Extractor/DP-03 Extractor.exe plus a folder of
# support files. Ship the whole folder, not just the .exe.
#
# Notes
# -----
# * One-folder, not one-file: one-file mode extracts to a tempdir on every
#   launch, which is slow and can get blocked by corporate AV. One-folder
#   is instant and ships Tkinter/Tcl cleanly.
# * `console=False` makes it a GUI app with no Command Prompt window. If
#   you're debugging a crash, flip this to True temporarily.
# * We explicitly pull in sounddevice's `_sounddevice_data` (the bundled
#   PortAudio DLL) via `collect_dynamic_libs` — PyInstaller sometimes
#   misses it on custom pip layouts.
# * numpy + sounddevice are listed as `hiddenimports` for safety even
#   though app.py imports them indirectly.

from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

project_root = Path(SPECPATH).resolve().parent

block_cipher = None

binaries = []
try:
    binaries += collect_dynamic_libs("sounddevice")
except Exception:
    pass

hiddenimports = [
    "dp03app",
    "dp03app.app",
    "dp03app.audio_engine",
    "dp03app.extract_worker",
    "dp03app.mixer_window",
    "dp03app.project_scanner",
    "dp03app.sd_detector",
    "dp03extract",
    "dp03extract.extract",
    "numpy",
    "sounddevice",
    "_sounddevice",
]

a = Analysis(
    [str(project_root / "dp03app" / "__main__.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavy optional deps we never import.
        "pytest",
        "IPython",
        "matplotlib",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DP-03 Extractor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # set True while debugging
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DP-03 Extractor",
)
