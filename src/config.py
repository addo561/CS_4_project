# =============================================================================
# config.py — Central configuration for the System Resource Optimizer
# KNUST Final Year Project — Group 4
# =============================================================================

import os, sys

# ── Paths ─────────────────────────────────────────────────────────────────────
# When packaged with PyInstaller, all bundled data lands in sys._MEIPASS.
# In development, paths are relative to this file's directory.
if getattr(sys, "frozen", False):
    # Running inside .app bundle
    _BUNDLE = sys._MEIPASS
    BASE_DIR  = _BUNDLE
    DATA_DIR  = os.path.join(_BUNDLE, "data")
    MODEL_DIR = os.path.join(_BUNDLE, "models")
    LOG_DIR   = os.path.join(_BUNDLE, "logs")
else:
    BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR  = os.path.join(BASE_DIR, "data")
    MODEL_DIR = os.path.join(BASE_DIR, "models")
    LOG_DIR   = os.path.join(BASE_DIR, "logs")

RAW_CSV     = os.path.join(DATA_DIR, "telemetry_raw.csv")
CLEAN_CSV   = os.path.join(DATA_DIR, "telemetry_clean.csv")
SCALER_PATH = os.path.join(MODEL_DIR, "scaler.pkl")
MODEL_PATH  = os.path.join(MODEL_DIR, "gru_quantized.onnx")

def get_app_version() -> str:
    """Get app version, looking for a bundled or local version.txt first."""
    paths_to_check = [
        os.path.join(BASE_DIR, "assets", "version.txt"),
        os.path.join(os.path.dirname(BASE_DIR), "src", "assets", "version.txt"),
        os.path.join(BASE_DIR, "version.txt"),
    ]
    for p in paths_to_check:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    ver = f.read().strip()
                    if ver:
                        if not ver.lower().startswith("v"):
                            ver = "v" + ver
                        return f"{ver} (Stable)"
            except Exception:
                pass
    return "v4.2.0 (Stable)"

VERSION = get_app_version()

# ── Local (per-machine) calibration scaler ───────────────────────────────────
# Saved in the user's home directory so it persists across app updates and
# works on any machine without touching the bundled model files.
import pathlib

_LOCAL_SCALER_DIR_CACHE = None

def _get_local_scaler_dir():
    """Get writable directory for local scaler, with fallbacks."""
    global _LOCAL_SCALER_DIR_CACHE
    if _LOCAL_SCALER_DIR_CACHE is not None:
        return _LOCAL_SCALER_DIR_CACHE

    # Try 1: ~/.sro_optimizer
    try:
        home_dir = os.path.join(pathlib.Path.home(), ".sro_optimizer")
        os.makedirs(home_dir, exist_ok=True)
        if os.access(home_dir, os.W_OK):
            _LOCAL_SCALER_DIR_CACHE = home_dir
            return home_dir
    except (OSError, PermissionError):
        pass
    
    # Try 2: App-local directory
    try:
        app_local = os.path.join(BASE_DIR, ".sro_optimizer")
        os.makedirs(app_local, exist_ok=True)
        if os.access(app_local, os.W_OK):
            _LOCAL_SCALER_DIR_CACHE = app_local
            return app_local
    except (OSError, PermissionError):
        pass
    
    # Try 3: Temp directory
    import tempfile
    try:
        temp_dir = os.path.join(tempfile.gettempdir(), ".sro_optimizer")
        os.makedirs(temp_dir, exist_ok=True)
        _LOCAL_SCALER_DIR_CACHE = temp_dir
        return temp_dir
    except Exception:
        pass
    
    return None

LOCAL_SCALER_DIR = _get_local_scaler_dir()
if LOCAL_SCALER_DIR:
    LOCAL_SCALER_PATH = os.path.join(LOCAL_SCALER_DIR, "scaler_local_v2.pkl")
else:
    LOCAL_SCALER_PATH = ""
CALIBRATION_SECONDS = 90   # seconds of idle data to collect on first launch

# Auto-create required directories (Issue #11)
for directory in [DATA_DIR, MODEL_DIR, LOG_DIR, LOCAL_SCALER_DIR]:
    if directory:
        pathlib.Path(directory).mkdir(parents=True, exist_ok=True)

# ── Data Collection ───────────────────────────────────────────────────────────
POLL_INTERVAL_SEC   = 1.0       # seconds between each telemetry sample
QUEUE_MAX_SIZE      = 500       # max samples buffered in memory before flush
FLUSH_EVERY_N       = 60        # flush to CSV every N samples (~60 seconds)
TEMP_FALLBACK       = -1.0      # sentinel value when temps unavailable (e.g. VMs)
IPC_PORT            = 5050      # Default port for IPC between service and dashboard

# ── Feature Columns (order must stay consistent across all modules) ───────────
FEATURE_COLS = [
    "cpu_percent",          # Overall CPU utilisation (%)
    "cpu_freq_mhz",         # Current CPU frequency (MHz)
    "mem_used_mb",          # Physical memory used (MB)
    "mem_available_mb",     # Physical memory available (MB)
    "mem_percent",          # Memory utilisation (%)
    "swap_used_mb",         # Swap/page file used (MB)
    "swap_percent",         # Swap utilisation (%)
    "cpu_temp_c",           # Package/average CPU temperature (°C)
]

# Per-core CPU columns are appended dynamically by the collector at runtime
# because core count varies across machines. They are named: cpu_core_0, cpu_core_1 …

# ── Sliding Window (for model input) ─────────────────────────────────────────
WINDOW_SIZE     = 60    # W  — number of past samples per input window (60 s)
STEP_SIZE       = 1     # S  — sliding step between windows
LABEL_HORIZON   = 30    # H  — predict resource state H steps (seconds) ahead

# ── Bottleneck Thresholds (used by action engine AND for labelling) ───────────
CPU_BOTTLENECK_PCT      = 90.0  # CPU % above which a bottleneck label = 1
MEM_BOTTLENECK_PCT      = 85.0  # Memory % above which a bottleneck label = 1
TEMP_BOTTLENECK_C       = 85.0  # Temperature °C above which label = 1

# ── Model Training ────────────────────────────────────────────────────────────
TRAIN_SPLIT     = 0.70
VAL_SPLIT       = 0.15
# TEST_SPLIT is the remainder: 0.15

GRU_HIDDEN_SIZE = 64
GRU_NUM_LAYERS  = 2
DROPOUT         = 0.2
LEARNING_RATE   = 1e-3
BATCH_SIZE      = 64
MAX_EPOCHS      = 100
PATIENCE        = 10    # early stopping patience

# ── Action Engine ─────────────────────────────────────────────────────────────
CONFIDENCE_THRESHOLD    = 0.80  # minimum model confidence to trigger action
UNDO_TIMEOUT_SEC        = 300   # auto-resume suspended processes after 5 min

# Processes that must NEVER be suspended (Windows system whitelist)
PROCESS_WHITELIST = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe",
    "explorer.exe", "dwm.exe", "taskmgr.exe", "pythonw.exe", "python.exe",
    "optimizer.exe",  # our own process
}

# ── Dynamic Performance Profiles ──────────────────────────────────────────────
PROFILES = {
    "Eco": {
        "CPU_BOTTLENECK_PCT": 80.0,
        "MEM_BOTTLENECK_PCT": 75.0,
        "TEMP_BOTTLENECK_C": 75.0,
        "CONFIDENCE_THRESHOLD": 0.70,
    },
    "Balanced": {
        "CPU_BOTTLENECK_PCT": 90.0,
        "MEM_BOTTLENECK_PCT": 85.0,
        "TEMP_BOTTLENECK_C": 85.0,
        "CONFIDENCE_THRESHOLD": 0.80,
    },
    "Gaming": {
        "CPU_BOTTLENECK_PCT": 96.0,
        "MEM_BOTTLENECK_PCT": 92.0,
        "TEMP_BOTTLENECK_C": 90.0,
        "CONFIDENCE_THRESHOLD": 0.90,
    }
}

import json

USER_WHITELIST_PATH = os.path.join(LOCAL_SCALER_DIR, "user_whitelist.json") if LOCAL_SCALER_DIR else ""
SETTINGS_PATH = os.path.join(LOCAL_SCALER_DIR, "user_settings.json") if LOCAL_SCALER_DIR else ""

def load_user_whitelist() -> set[str]:
    """Loads user whitelist from ~/.sro_optimizer/user_whitelist.json"""
    if USER_WHITELIST_PATH and os.path.isfile(USER_WHITELIST_PATH):
        try:
            with open(USER_WHITELIST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(name.lower().strip() for name in data)
        except Exception:
            pass
    return set()

def save_user_whitelist(whitelist: set[str]) -> bool:
    """Saves user whitelist to ~/.sro_optimizer/user_whitelist.json"""
    if not USER_WHITELIST_PATH:
        return False
    try:
        with open(USER_WHITELIST_PATH, "w", encoding="utf-8") as f:
            json.dump(list(sorted(whitelist)), f, indent=4)
        return True
    except Exception:
        return False

def load_user_settings() -> dict:
    """Loads settings like active performance profile."""
    defaults = {"profile": "Balanced", "autopilot": True}
    if SETTINGS_PATH and os.path.isfile(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {**defaults, **data}
        except Exception:
            pass
    return defaults

def save_user_settings(settings: dict) -> bool:
    """Saves settings to file."""
    if not SETTINGS_PATH:
        return False
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4)
        return True
    except Exception:
        return False
