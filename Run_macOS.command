#!/bin/bash
# =============================================================
#  System Resource Optimizer — macOS Launcher  (v4.1)
#  Double-click this file in Finder to start the app.
#
#  Behaviour:
#    1. If a pre-built .app bundle exists, launch it (it auto-starts
#       its own background service).
#    2. Otherwise run from source: ensure a virtual environment with
#       dependencies exists, clean up any stale background service,
#       start a fresh DETACHED background service (survives closing the
#       Dashboard / Terminal), then launch the Dashboard in foreground.
# =============================================================

set -u

# ── Resolve the folder this script lives in ──────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VERSION="$(cat "$SCRIPT_DIR/src/assets/version.txt" 2>/dev/null || echo v4.1.0)"
echo "============================================================"
echo "  System Resource Optimizer  ${VERSION}"
echo "============================================================"

# Make sure the relevant scripts are executable
chmod +x "$0" 2>/dev/null || true
for f in "install_and_run.py" "src/dashboard.py" "src/optimizer_service.py"; do
    [ -f "$SCRIPT_DIR/$f" ] && chmod +x "$SCRIPT_DIR/$f" 2>/dev/null || true
done

# ── 1. Pre-built .app bundle ─────────────────────────────────
APP="$SCRIPT_DIR/dist/System Resource Optimizer.app"
if [ -d "$APP" ]; then
    echo "Launching pre-built app bundle ..."
    open "$APP"
    exit 0
fi
echo "App bundle not found — running from source ..."

# ── 2. Resolve / create a Python interpreter ─────────────────
VENV_PY="$SCRIPT_DIR/venv/bin/python"
PYTHON=""

if [ -x "$VENV_PY" ]; then
    PYTHON="$VENV_PY"
elif command -v python3 >/dev/null 2>&1; then
    SYS_PY="$(command -v python3)"
    echo "Creating virtual environment (first run only)..."
    if "$SYS_PY" -m venv "$SCRIPT_DIR/venv" 2>/dev/null && [ -x "$VENV_PY" ]; then
        PYTHON="$VENV_PY"
        echo "Installing dependencies (this may take a few minutes)..."
        "$PYTHON" -m pip install --upgrade pip -q
        "$PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt" -q
    else
        PYTHON="$SYS_PY"
    fi
else
    osascript -e 'display alert "Python 3 not found" message "Please install Python 3.11 from python.org, then run: pip3 install -r requirements.txt"'
    exit 1
fi

# ── 3. Verify dependencies are importable; install if missing ─
if ! "$PYTHON" -c "import flet, psutil" >/dev/null 2>&1; then
    echo "Installing missing dependencies..."
    "$PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt" -q || true
fi

# ── 4. Clean up any stale background service from a prior run ─
# (Resumes any processes it had suspended via its own clean shutdown.)
OLD_PIDS="$(pgrep -f "src/optimizer_service.py" 2>/dev/null || true)"
if [ -n "$OLD_PIDS" ]; then
    echo "Stopping previous background service (PID: $OLD_PIDS)..."
    # shellcheck disable=SC2086
    kill -TERM $OLD_PIDS 2>/dev/null || true
    sleep 1
    # shellcheck disable=SC2086
    kill -KILL $OLD_PIDS 2>/dev/null || true
fi

# ── 5. Start the background service (detached) ───────────────
# nohup + disown so the service keeps running after the Dashboard
# window and this Terminal are closed.
echo "Starting background optimizer service..."
nohup "$PYTHON" "$SCRIPT_DIR/src/optimizer_service.py" >/dev/null 2>&1 &
disown 2>/dev/null || true

# Give the IPC socket a moment to come up before the UI connects
sleep 1

# ── 6. Launch the Dashboard UI (foreground) ──────────────────
echo "Launching Dashboard UI..."
"$PYTHON" "$SCRIPT_DIR/src/dashboard.py"

# When the Dashboard window closes, this script exits but the detached
# background service above keeps running and protecting the system.
echo "Dashboard closed. Background service continues running."
exit 0
