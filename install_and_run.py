"""
install_and_run.py — System Resource Optimizer
Cross-platform installer + launcher (macOS & Windows)

HOW TO USE:
  1. Make sure Python 3.11+ is installed  →  https://python.org/downloads
  2. Double-click this file  OR  run:  python install_and_run.py
  3. Done — the app will launch automatically after setup.
"""

import sys
import os
import subprocess
import platform

# ── Colour output ─────────────────────────────────────────────────────────────
def green(t):  return f"\033[92m{t}\033[0m"
def yellow(t): return f"\033[93m{t}\033[0m"
def red(t):    return f"\033[91m{t}\033[0m"
def bold(t):   return f"\033[1m{t}\033[0m"

OS   = platform.system()          # "Darwin" | "Windows" | "Linux"
HERE = os.path.dirname(os.path.abspath(__file__))

def fix_executable_permissions():
    """Fix execute permissions for shell scripts."""
    if OS in ["Darwin", "Linux"]:
        scripts = [
            "Run_macOS.command",
            os.path.join("src", "core", "collector.py"),
            os.path.join("src", "training", "train.py"),
            os.path.join("src", "main.py"),
        ]
        for script in scripts:
            path = os.path.join(HERE, script)
            if os.path.isfile(path):
                try:
                    os.chmod(path, 0o755)
                    print(green(f"✅  Fixed permissions: {script}"))
                except Exception as e:
                    print(yellow(f"⚠️  Could not fix {script}: {e}"))

# Call this early in the install process
fix_executable_permissions()

print()
print(bold("=" * 60))
print(bold("  ⚡  System Resource Optimizer — Installer"))
print(bold(f"  Platform : {OS} ({platform.machine()})"))
print(bold(f"  Python   : {sys.version.split()[0]}"))
print(bold("=" * 60))
print()

# ── 1. Python version check ───────────────────────────────────────────────────
major, minor = sys.version_info[:2]
if major < 3 or minor < 10:
    print(red(f"❌  Python 3.10+ required. You have {major}.{minor}."))
    print(red("   Download from https://python.org/downloads"))
    input("\nPress Enter to exit...")
    sys.exit(1)
print(green(f"✅  Python {major}.{minor} — OK"))

# ── 2. Install dependencies ───────────────────────────────────────────────────
REQ = os.path.join(HERE, "requirements.txt")
if not os.path.isfile(REQ):
    print(red("❌  requirements.txt not found. Are you running from the Final_year folder?"))
    input("\nPress Enter to exit...")
    sys.exit(1)

print(yellow("\n📦  Installing dependencies (this may take a few minutes on first run)..."))
result = subprocess.run(
    [sys.executable, "-m", "pip", "install", "-r", REQ, "-q", "--upgrade"],
    capture_output=False
)
if result.returncode != 0:
    print(red("❌  Dependency installation failed. Check your internet connection."))
    input("\nPress Enter to exit...")
    sys.exit(1)
print(green("✅  All dependencies installed"))

# ── 3. Create a desktop shortcut ─────────────────────────────────────────────
def create_shortcut():
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")

    if OS == "Windows":
        try:
            import winreg  # noqa
            shortcut_path = os.path.join(desktop, "System Resource Optimizer.bat")
            launcher = os.path.join(HERE, "Run_Windows.bat")
            if not os.path.isfile(shortcut_path):
                with open(shortcut_path, "w") as f:
                    f.write(f'@echo off\nstart "" "{launcher}"\n')
                print(green(f'✅  Desktop shortcut created: {shortcut_path}'))
        except Exception as e:
            print(yellow(f"⚠️  Could not create shortcut: {e}"))

    elif OS == "Darwin":
        try:
            shortcut_path = os.path.join(desktop, "System Resource Optimizer.command")
            launcher = os.path.join(HERE, "Run_macOS.command")
            if not os.path.isfile(shortcut_path):
                with open(shortcut_path, "w") as f:
                    f.write(f'#!/bin/bash\nbash "{launcher}"\n')
                os.chmod(shortcut_path, 0o755)
                print(green(f'✅  Desktop shortcut created: {shortcut_path}'))
        except Exception as e:
            print(yellow(f"⚠️  Could not create shortcut: {e}"))

create_shortcut()

# ── 4. Check if trained model exists ─────────────────────────────────────────
MODEL = os.path.join(HERE, "src", "models", "gru_quantized.onnx")
SCALER = os.path.join(HERE, "src", "models", "scaler.pkl")

if not os.path.isfile(MODEL) or not os.path.isfile(SCALER):
    print()
    print(yellow("⚠️  No trained model found."))
    print(yellow("   The app will run in HEURISTIC mode (no AI predictions)."))
    print(yellow("   To train the model, run these commands first:"))
    print()
    import pathlib
    collector_path = str(pathlib.Path("src") / "core" / "collector.py")
    preprocess_path = str(pathlib.Path("src") / "training" / "preprocess.py")
    train_path = str(pathlib.Path("src") / "training" / "train.py")
    python_cmd = "python" if OS == "Windows" else "python3"

    print(yellow(f"   {python_cmd} {collector_path} --label idle  (run 5+ min, then Ctrl+C)"))
    print(yellow(f"   {python_cmd} {preprocess_path}"))
    print(yellow(f"   {python_cmd} {train_path}"))
    print()
    proceed = input("Launch app anyway? [Y/n]: ").strip().lower()
    if proceed == "n":
        sys.exit(0)
else:
    print(green("✅  Trained model found — AI predictions enabled"))

# ── 5. Launch the app ─────────────────────────────────────────────────────────
print()
print(bold("🚀  Launching System Resource Optimizer..."))
print()

main_py = os.path.join(HERE, "src", "main.py")

try:
    subprocess.Popen(
        [sys.executable, main_py],
        cwd=HERE,
    )
    print(green("✅  App launched! You can close this window."))
except Exception as e:
    print(red(f"❌  Failed to launch: {e}"))
    input("\nPress Enter to exit...")
    sys.exit(1)

input("\nPress Enter to close this installer...")
