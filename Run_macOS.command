#!/bin/bash
# =============================================================
#  System Resource Optimizer — macOS Launcher
#  Double-click this file in Finder to start the app.
#
#  Behaviour:
#    1. If a pre-built .app bundle exists, launch it (it auto-starts
#       its own background service).
#    2. Otherwise run from Python source: ensure a virtual environment
#       with dependencies exists, start the background optimizer service
#       as a DETACHED process (survives closing the Dashboard / Terminal),
#       then launch the Dashboard UI in the foreground.
# =============================================================

set -u

# Get the folder this script lives in
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Fix permissions dynamically
chmod +x "$0" 2>/dev/null || true
for f in "install_and_run.py" "src/dashboard.py" "src/optimizer_service.py" "src/main.py"; do
    [ -f "$SCRIPT_DIR/$f" ] && chmod +x "$SCRIPT_DIR/$f" 2>/dev/null || true
done

# ── 1. Pre-built .app bundle ─────────────────────────────────
APP="$SCRIPT_DIR/dist/System Resource Optimizer.app"
if [ -d "$APP" ]; then
    echo "Launching System Resource Optimizer.app ..."
    open "$APP"
    exit 0
fi

echo "App bundle not found — running from source ..."

# ── 2. Resolve a Python interpreter ──────────────────────────
VENV_PY="$SCRIPT_DIR/venv/bin/python"
PYTHON=""

if [ -x "$VENV_PY" ]; then
    PYTHON="$VENV_PY"
elif command -v python3 &>/dev/null; then
    SYS_PY="$(command -v python3)"
    echo "Creating virtual environment (first run only)..."
    if "$SYS_PY" -m venv "$SCRIPT_DIR/venv" 2>/dev/null && [ -x "$VENV_PY" ]; then
        PYTHON="$VENV_PY"
        echo "Installing dependencies (this may take a few minutes)..."
        "$PYTHON" -m pip install --upgrade pip -q
        "$PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt" -q
    else
        # Could not create a venv — fall back to system python3.
        PYTHON="$SYS_PY"
    fi
else
    osascript -e 'display alert "Python 3 not found" message "Please install Python 3.11 from python.org, then run: pip3 install -r requirements.txt"'
    exit 1
fi

# ── 3. Start the background service (detached) ───────────────
# nohup + setsid-style detachment so the service keeps running after the
# Dashboard window and this Terminal are closed.
echo "Starting background optimizer service..."
nohup "$PYTHON" "$SCRIPT_DIR/src/optimizer_service.py" >/dev/null 2>&1 &
disown 2>/dev/null || true

# ── 4. Launch the Dashboard UI (foreground) ──────────────────
echo "Launching Dashboard UI..."
"$PYTHON" "$SCRIPT_DIR/src/dashboard.py"

# When the Dashboard window closes, this script exits but the detached
# background service above keeps running.
exit 0
