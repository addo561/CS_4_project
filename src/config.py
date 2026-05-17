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

# ── Local (per-machine) calibration scaler ───────────────────────────────────
# Saved in the user's home directory so it persists across app updates and
# works on any machine without touching the bundled model files.
import pathlib
_LOCAL_DIR       = os.path.join(pathlib.Path.home(), ".sro_optimizer")
LOCAL_SCALER_DIR = _LOCAL_DIR
LOCAL_SCALER_PATH = os.path.join(_LOCAL_DIR, "scaler_local_v2.pkl")
CALIBRATION_SECONDS = 90   # seconds of idle data to collect on first launch

# ── Data Collection ───────────────────────────────────────────────────────────
POLL_INTERVAL_SEC   = 1.0       # seconds between each telemetry sample
QUEUE_MAX_SIZE      = 500       # max samples buffered in memory before flush
FLUSH_EVERY_N       = 60        # flush to CSV every N samples (~60 seconds)
TEMP_FALLBACK       = -1.0      # sentinel value when temps unavailable (e.g. VMs)

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
