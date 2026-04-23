# DP-03 Extractor release checklist

## Before tagging

- [ ] Confirm `.gitignore` excludes raw card images, extracted audio, and scratch reverse-engineering artifacts
- [ ] Run `python3 -m unittest discover -s tests -v`
- [ ] Run `python3 -m dp03extract --help`
- [ ] Run `python3 -m pip install --user build && python3 -m build`
- [ ] Verify `pyproject.toml` version matches `dp03app/__init__.py` and `dp03extract/__init__.py`
- [ ] Review README, `WINDOWS.md`, and `.github/release-template.md`
- [ ] Make sure release screenshots are current
- [ ] Confirm the user guide PDF opens correctly
- [ ] Check GitHub Actions smoke workflow is green

## Windows release build

- [ ] Install build tooling: `py -3 -m pip install --user pyinstaller`
- [ ] Build: `py -3 -m PyInstaller packaging/dp03app.spec`
- [ ] Smoke-test the built app on a Windows machine
- [ ] Zip the entire `dist/DP-03 Extractor/` folder

## GitHub release draft

- [ ] Create tag: `v0.1.0`
- [ ] Draft release title: `DP-03 Extractor v0.1.0`
- [ ] Paste highlights from `CHANGELOG.md`
- [ ] Upload Windows zip
- [ ] Upload `DP-03 Extractor User Guide.pdf`
- [ ] Add 2–4 screenshots to the release body or linked landing page
- [ ] Mention current limitations clearly: image-first workflow, admin requirement for direct card reads, beta support status

## Nice-to-have right after first release

- [ ] Add screenshots to the repo README
- [ ] Add a signed macOS app bundle or notarized archive if distribution demand is high
- [ ] Replace catalog-dependent CLI flows with full auto-discovery everywhere possible
- [ ] Add reproducible Windows asset-building notes for future contributors
