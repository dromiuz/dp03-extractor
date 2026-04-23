# Contributing to DP-03 Extractor

Thanks for helping improve DP-03 Extractor.

## What is most helpful

- Bug reports from real DP-03 / DP-03SD cards
- Extraction edge cases and unsupported card / firmware variants
- Reproducible CLI failures
- UI polish and packaging improvements
- Documentation fixes

## Before opening a PR

1. Run:
   - `python3 -m unittest discover -s tests -v`
   - `python3 -m dp03extract --help`
2. If you changed packaging metadata or release files, also run:
   - `python3 -m pip install --user build`
   - `python3 -m build`
3. Keep commits focused and explain user-facing impact clearly.
4. Do not commit raw card images, extracted WAVs, or local scratch-analysis artifacts.

## Bug reports

Please include:

- recorder model: DP-03 or DP-03SD
- OS version
- Python version or release asset used
- whether you opened a raw disk or `.img` file
- the exact command, action, or screen where the problem happened
- traceback / logs if available

## Safety rule

This project is read-first and recovery-oriented. Avoid changes that write back to recorder media unless they are explicitly designed, reviewed, and documented for that purpose.

## Release-related changes

If your PR affects packaging, docs, or GitHub release flow, also review:

- `RELEASE_CHECKLIST.md`
- `.github/release-template.md`
- `CHANGELOG.md`
