# Changelog

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
