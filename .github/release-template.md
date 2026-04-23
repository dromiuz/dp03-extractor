## DP-03 Extractor v0.1.0

Initial public beta release of DP-03 Extractor.

### Highlights
- Desktop GUI for scanning DP-03 / DP-03SD images and extracting tracks
- CLI backend for listing song slots, inspecting projects, and exporting WAVs
- macOS and Windows card-detection helpers
- Optional rough-mix / audition workflow
- Included reverse-engineering notes and format documentation

### Recommended workflow
1. Make a raw image of the card first
2. Open that image in DP-03 Extractor
3. Review discovered projects
4. Extract tracks to WAVs
5. Open the WAVs in your DAW

### Release assets currently attached
- DP-03 Extractor User Guide PDF
- GitHub source code archives

### Asset still to add after Windows smoke test
- Windows standalone zip built from `dist/DP-03 Extractor/`

### Current limitations
- Beta support status: not every unknown card / firmware variant is guaranteed yet
- Some CLI flows still use `shared/dp03/projects_catalog.csv` from a source checkout or source archive
- Direct raw-disk reads on Windows require Administrator privileges
- Real-image validation fixtures are intentionally not committed to the repo

### Feedback
If a card fails to scan or extraction looks wrong, please open an issue and include:
- recorder model (DP-03 or DP-03SD)
- OS and Python version
- whether you used a raw card or `.img` file
- the exact command or GUI action
- logs / traceback if available
