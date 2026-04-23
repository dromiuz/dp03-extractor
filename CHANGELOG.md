# Changelog

## 0.1.1

Bug fixes and first-launch polish for the public release.

Fixes:
- Mixer window no longer crashes on open with `TclError: invalid command name "9"`. The `ChannelMeter` widget was shadowing `tkinter.Widget._w` (Tk's reserved widget-path attribute) with its pixel width, which broke every subsequent canvas call and left the mixer half-rendered. Renamed the internal attributes to `_px_w` / `_px_h`.
- The "audio unavailable" dialog now shows an install command that targets the actual running Python interpreter (`sys.executable`) instead of a hard-coded `python3.12` path. On macOS Homebrew Pythons it includes `--break-system-packages` so the copy-pasted command actually works under PEP 668.
- Mixer build failures are now caught and surfaced in an error dialog (with a traceback appended to `dp03-debug.log` in the project root) instead of silently leaving a blank window.

Docs:
- README: added screenshots at the top and a macOS note about Homebrew Python 3.14 shipping without Tk — use python.org Python 3.12 on macOS until that's resolved upstream.

## 0.1.0

Initial public GitHub release of DP-03 Extractor.

Highlights:
- Desktop GUI for loading DP-03 card images, scanning projects, and extracting tracks
- CLI backend for listing slots, inspecting projects, and extracting WAVs
- macOS and Windows card-detection helpers
- Optional live mixer / audition workflow using numpy + sounddevice
- PyInstaller spec for Windows standalone packaging
- Reverse-engineering notes and format documentation included in-repo

Known release caveats:
- GUI help is documentation-first; running `python -m dp03app` launches the app directly
- Full real-image smoke tests require local card images that are not committed to Git
- Some CLI extraction flows still depend on `shared/dp03/projects_catalog.csv`
- Windows standalone packaging is supported via PyInstaller one-folder builds and should be smoke-tested on a real Windows machine before publishing a release asset
