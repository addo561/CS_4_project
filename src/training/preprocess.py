# =============================================================================
# preprocess.py — Clean raw telemetry CSV and build sliding-window dataset
# KNUST Final Year Project — Group 4
#
# Usage:
#   python preprocess.py                  # uses paths from config.py
#   python preprocess.py --input data/telemetry_raw.csv --output data/telemetry_clean.csv
# =============================================================================

import argparse
import logging
import os
import pickle
import sys

import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler

# Add parent directory (src/) to sys.path so config can be found when run directly
_PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from config import (
    RAW_CSV, CLEAN_CSV, SCALER_PATH, FEATURE_COLS,
    WINDOW_SIZE, STEP_SIZE, LABEL_HORIZON,
    CPU_BOTTLENECK_PCT, MEM_BOTTLENECK_PCT, TEMP_BOTTLENECK_C,
    TRAIN_SPLIT, VAL_SPLIT, DATA_DIR, MODEL_DIR,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("preprocess")


# ---------------------------------------------------------------------------
# Step 1 — Load & basic clean
# ---------------------------------------------------------------------------

def load_and_clean(csv_path: str) -> pd.DataFrame:
    log.info(f"Loading raw data from: {csv_path}")
    df = pd.read_csv(csv_path, parse_dates=["timestamp"])
    log.info(f"  Raw shape: {df.shape}")

    # Drop exact duplicate timestamps (restarts / overlapping sessions)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)

    # Identify per-core columns present in this file
    core_cols = sorted(
        [c for c in df.columns if c.startswith("cpu_core_")],
        key=lambda c: int(c.split("_")[-1])
    )
    all_feature_cols = FEATURE_COLS + core_cols

    # Handle TEMP_FALLBACK (-1.0) — replace with forward-fill, then median
    df["cpu_temp_c"] = df["cpu_temp_c"].replace(-1.0, np.nan)
    df["cpu_temp_c"] = df["cpu_temp_c"].ffill().bfill()
    if df["cpu_temp_c"].isna().all():
        log.warning("No valid temperature data found. Filling with 50.0 °C placeholder.")
        df["cpu_temp_c"] = 50.0

    # Fill any remaining NaNs in feature cols with column median
    for col in all_feature_cols:
        if col in df.columns and df[col].isna().any():
            median_val = df[col].median()
            df[col] = df[col].fillna(median_val)
            log.info(f"  NaN fill on '{col}' with median={median_val:.2f}")

    log.info(f"  Clean shape: {df.shape}")
    return df, all_feature_cols


# ---------------------------------------------------------------------------
# Step 2 — Generate bottleneck labels
# ---------------------------------------------------------------------------

def generate_labels(df: pd.DataFrame) -> pd.DataFrame:
    """
    Bottleneck label = 1 if ANY of the following are true H steps ahead:
      - cpu_percent  >= CPU_BOTTLENECK_PCT
      - mem_percent  >= MEM_BOTTLENECK_PCT
      - cpu_temp_c   >= TEMP_BOTTLENECK_C   (if valid)
    """
    cpu_spike  = df["cpu_percent"].shift(-LABEL_HORIZON) >= CPU_BOTTLENECK_PCT
    mem_spike  = df["mem_percent"].shift(-LABEL_HORIZON) >= MEM_BOTTLENECK_PCT
    temp_spike = df["cpu_temp_c"].shift(-LABEL_HORIZON)  >= TEMP_BOTTLENECK_C

    df["bottleneck_label"] = (cpu_spike | mem_spike | temp_spike).astype(int)

    # Drop last LABEL_HORIZON rows (no ground truth available)
    df = df.iloc[:-LABEL_HORIZON].reset_index(drop=True)

    pos = df["bottleneck_label"].sum()
    neg = len(df) - pos
    log.info(f"  Labels: {pos} bottleneck ({pos/len(df)*100:.1f}%), {neg} normal")
    return df


# ---------------------------------------------------------------------------
# Step 3 — Normalise
# ---------------------------------------------------------------------------

def fit_and_scale(df: pd.DataFrame, feature_cols: list, scaler_path: str) -> tuple:
    """
    Fit MinMaxScaler on training portion only (chronological split) to prevent
    data leakage. Returns scaled DataFrame and fitted scaler.
    """
    os.makedirs(MODEL_DIR, exist_ok=True)

    train_end = int(len(df) * TRAIN_SPLIT)
    scaler = MinMaxScaler(feature_range=(0, 1))

    df_scaled = df.copy()
    scaler.fit(df.iloc[:train_end][feature_cols])
    df_scaled[feature_cols] = scaler.transform(df[feature_cols])

    with open(scaler_path, "wb") as f:
        pickle.dump(scaler, f)
    log.info(f"  Scaler fitted on {train_end} rows and saved to: {scaler_path}")

    return df_scaled, scaler


# ---------------------------------------------------------------------------
# Step 4 — Build sliding windows
# ---------------------------------------------------------------------------

def build_windows(df: pd.DataFrame, feature_cols: list) -> tuple:
    """
    Returns:
      X : np.ndarray of shape (N, WINDOW_SIZE, n_features)
      y_reg : np.ndarray of shape (N, n_features)  — next-step forecast targets
      y_clf : np.ndarray of shape (N,)              — bottleneck binary label
    """
    X, y_reg, y_clf = [], [], []
    values = df[feature_cols].values
    labels = df["bottleneck_label"].values

    for start in range(0, len(df) - WINDOW_SIZE - 1, STEP_SIZE):
        end = start + WINDOW_SIZE
        X.append(values[start:end])
        y_reg.append(values[end])           # next timestep feature vector
        y_clf.append(labels[end])           # bottleneck flag at that step

    X     = np.array(X,     dtype=np.float32)
    y_reg = np.array(y_reg, dtype=np.float32)
    y_clf = np.array(y_clf, dtype=np.float32)

    log.info(f"  Windows: X={X.shape}, y_reg={y_reg.shape}, y_clf={y_clf.shape}")
    return X, y_reg, y_clf


# ---------------------------------------------------------------------------
# Step 5 — Chronological train/val/test split
# ---------------------------------------------------------------------------

def split_dataset(X, y_reg, y_clf) -> dict:
    n = len(X)
    train_end = int(n * TRAIN_SPLIT)
    val_end   = int(n * (TRAIN_SPLIT + VAL_SPLIT))

    splits = {
        "X_train":     X[:train_end],
        "X_val":       X[train_end:val_end],
        "X_test":      X[val_end:],
        "y_reg_train": y_reg[:train_end],
        "y_reg_val":   y_reg[train_end:val_end],
        "y_reg_test":  y_reg[val_end:],
        "y_clf_train": y_clf[:train_end],
        "y_clf_val":   y_clf[train_end:val_end],
        "y_clf_test":  y_clf[val_end:],
    }

    for k, v in splits.items():
        log.info(f"  {k}: {v.shape}")

    return splits


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Preprocess raw telemetry for model training.")
    parser.add_argument("--input",  default=RAW_CSV,   help="Path to raw telemetry CSV.")
    parser.add_argument("--output", default=CLEAN_CSV,  help="Path to save cleaned CSV.")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    # ── Run pipeline ──────────────────────────────────────────────────────────
    df, feature_cols = load_and_clean(args.input)
    df               = generate_labels(df)
    df_scaled, _     = fit_and_scale(df, feature_cols, SCALER_PATH)

    # Save clean CSV (unwindowed, for inspection)
    df_scaled.to_csv(args.output, index=False)
    log.info(f"Clean CSV saved: {args.output}")

    # Build & save windowed arrays
    X, y_reg, y_clf = build_windows(df_scaled, feature_cols)
    splits          = split_dataset(X, y_reg, y_clf)

    arrays_path = os.path.join(DATA_DIR, "windows.npz")
    np.savez_compressed(arrays_path, **splits)
    log.info(f"Windowed arrays saved: {arrays_path}")

    # ── Summary stats (for monograph Table) ──────────────────────────────────
    log.info("\n── Feature Summary Statistics (pre-scaling) ──")
    summary = df[feature_cols].describe().T[["mean", "std", "min", "max"]]
    print(summary.to_string())

    log.info("\nPreprocessing complete.")


if __name__ == "__main__":
    main()
