#!/bin/bash
# Terminal fallback launcher — keeps the window open so errors are visible.

set -euo pipefail

SOURCE="${BASH_SOURCE[0]:-$0}"
while [ -L "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
PROJECT_ROOT="$(cd -P "$(dirname "$SOURCE")" && pwd)"

# Prefer a Python that actually has Tkinter — Homebrew's python3 often
# doesn't. System Python at /usr/bin/python3 ships with Tkinter.
CANDIDATES=(
    /usr/bin/python3
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3
    /opt/homebrew/bin/python3.12
    /opt/homebrew/bin/python3.11
    /opt/homebrew/bin/python3.10
    /usr/local/bin/python3.12
    /usr/local/bin/python3.11
    /usr/local/bin/python3.10
    python3
)

# Probe actually instantiates a Tk root — catches the case where
# `import tkinter` works but Tk fails to load at runtime (newer Tk SDK
# than the installed macOS can run).
PROBE='
import sys
try:
    import tkinter
    r = tkinter.Tk()
    r.withdraw()
    r.destroy()
except BaseException:
    sys.exit(1)
sys.exit(0)
'

PY=""
for candidate in "${CANDIDATES[@]}"; do
    if command -v "$candidate" >/dev/null 2>&1; then
        resolved="$(command -v "$candidate")"
        if "$resolved" -c "$PROBE" >/dev/null 2>&1; then
            PY="$resolved"
            break
        fi
    fi
done

if [ -z "$PY" ]; then
    echo
    echo "ERROR: No Python with a working Tkinter was found."
    echo
    echo "The recommended fix on this Mac is Homebrew:"
    echo
    echo "    brew install python-tk@3.12"
    echo
    echo "Then re-run this launcher. The app will pick up the brewed Python"
    echo "automatically."
    echo
    echo "(Apple's /usr/bin/python3 on your macOS is linked against a Tk"
    echo "version that requires a newer macOS, so it's being skipped.)"
    echo
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
fi

echo "Using python: $PY"
cd "$PROJECT_ROOT"
exec "$PY" -m dp03app
