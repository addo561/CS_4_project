#!/bin/bash
# =============================================================
#  System Resource Optimizer — macOS Launcher
#  Double-click this file in Finder to start the app.
# =============================================================

# Get the folder this script lives in
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Try to open the pre-built .app bundle first
APP="$SCRIPT_DIR/dist/System Resource Optimizer.app"
if [ -d "$APP" ]; then
    echo "Launching System Resource Optimizer.app ..."
    open "$APP"
    exit 0
fi

# Fallback: run directly from Python source
echo "App bundle not found — running from source ..."
cd "$SCRIPT_DIR"

if [ -f "$SCRIPT_DIR/venv/bin/python" ]; then
    echo "Found virtual environment — running app with venv Python ..."
    "$SCRIPT_DIR/venv/bin/python" src/main.py
    exit 0
fi

# Check Python
if ! command -v python3 &>/dev/null; then
    osascript -e 'display alert "Python 3 not found" message "Please install Python 3.11 from python.org, then run: pip3 install -r requirements.txt"'
    exit 1
fi

python3 src/main.py
