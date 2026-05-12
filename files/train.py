# =============================================================================
# train.py — Train, evaluate, quantize, and export the GRU model
# KNUST Final Year Project — Group 4
#
# Usage:
#   python train.py
#   python train.py --epochs 50 --lr 0.0005
# =============================================================================

import argparse
import logging
import os
import sys
import time

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from sklearn.metrics import (
    accuracy_score, f1_score, roc_auc_score,
    confusion_matrix, mean_absolute_error
)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from config import (          # resolved via files/config.py shim
    DATA_DIR, MODEL_DIR, WINDOW_SIZE,
    GRU_HIDDEN_SIZE, GRU_NUM_LAYERS, DROPOUT,
    LEARNING_RATE, BATCH_SIZE, MAX_EPOCHS, PATIENCE,
)
from gru_model import ResourceGRU

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("train")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_splits(data_dir: str) -> dict:
    path = os.path.join(data_dir, "windows.npz")
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"windows.npz not found at '{path}'.\n"
            "Run:  python preprocess.py   first."
        )
    arrays = np.load(path)
    log.info(f"Loaded windows.npz from '{path}'")
    return dict(arrays)


def make_loaders(splits: dict, batch_size: int) -> tuple:
    def _loader(X_key, yr_key, yc_key, shuffle):
        X   = torch.tensor(splits[X_key],   dtype=torch.float32)
        yr  = torch.tensor(splits[yr_key],  dtype=torch.float32)
        yc  = torch.tensor(splits[yc_key],  dtype=torch.float32).unsqueeze(1)
        return DataLoader(TensorDataset(X, yr, yc), batch_size=batch_size, shuffle=shuffle)

    return (
        _loader("X_train", "y_reg_train", "y_clf_train", shuffle=True),
        _loader("X_val",   "y_reg_val",   "y_clf_val",   shuffle=False),
        _loader("X_test",  "y_reg_test",  "y_clf_test",  shuffle=False),
    )


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------

def train_epoch(model, loader, optimiser, mse_loss, bce_loss, device):
    model.train()
    total_loss = reg_loss_sum = clf_loss_sum = 0.0

    for X, yr, yc in loader:
        X, yr, yc = X.to(device), yr.to(device), yc.to(device)
        optimiser.zero_grad()

        reg_out, clf_out = model(X)
        loss_reg = mse_loss(reg_out, yr)
        loss_clf = bce_loss(clf_out, yc)
        loss     = loss_reg + loss_clf          # equal weighting

        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimiser.step()

        total_loss    += loss.item()
        reg_loss_sum  += loss_reg.item()
        clf_loss_sum  += loss_clf.item()

    n = len(loader)
    return total_loss / n, reg_loss_sum / n, clf_loss_sum / n


@torch.no_grad()
def evaluate(model, loader, mse_loss, bce_loss, device) -> dict:
    model.eval()
    total_loss = 0.0
    all_conf, all_labels, all_reg, all_yr = [], [], [], []

    for X, yr, yc in loader:
        X, yr, yc = X.to(device), yr.to(device), yc.to(device)
        reg_out, clf_out = model(X)
        loss = mse_loss(reg_out, yr) + bce_loss(clf_out, yc)
        total_loss += loss.item()

        all_conf.append(clf_out.cpu().numpy())
        all_labels.append(yc.cpu().numpy())
        all_reg.append(reg_out.cpu().numpy())
        all_yr.append(yr.cpu().numpy())

    conf   = np.concatenate(all_conf).flatten()
    labels = np.concatenate(all_labels).flatten().astype(int)
    reg    = np.concatenate(all_reg)
    yr_arr = np.concatenate(all_yr)
    preds  = (conf >= 0.5).astype(int)

    mae  = mean_absolute_error(yr_arr, reg)
    rmse = float(np.sqrt(np.mean((yr_arr - reg) ** 2)))
    acc  = accuracy_score(labels, preds)
    f1   = f1_score(labels, preds, zero_division=0)
    try:
        auc = roc_auc_score(labels, conf)
    except ValueError:
        auc = float("nan")

    cm = confusion_matrix(labels, preds)

    return {
        "loss": total_loss / len(loader),
        "mae": mae, "rmse": rmse,
        "accuracy": acc, "f1": f1, "auc": auc,
        "confusion_matrix": cm,
    }


# ---------------------------------------------------------------------------
# ONNX export + INT8 quantization
# ---------------------------------------------------------------------------

def export_onnx(model: ResourceGRU, n_features: int, save_path: str):
    model.eval().cpu()
    dummy = torch.zeros(1, WINDOW_SIZE, n_features)
    torch.onnx.export(
        model, dummy, save_path,
        input_names  = ["x"],
        output_names = ["reg_out", "conf_out"],
        dynamic_axes = {"x": {0: "batch_size"}},
        opset_version = 17,
        do_constant_folding = True,
    )
    size_mb = os.path.getsize(save_path) / 1_048_576
    log.info(f"ONNX model exported → '{save_path}'  ({size_mb:.2f} MB)")
    return size_mb


def quantize_onnx(onnx_path: str, quantized_path: str) -> float:
    try:
        from onnxruntime.quantization import quantize_dynamic, QuantType
        quantize_dynamic(onnx_path, quantized_path, weight_type=QuantType.QInt8)
        size_mb = os.path.getsize(quantized_path) / 1_048_576
        log.info(f"INT8 quantized model → '{quantized_path}'  ({size_mb:.2f} MB)")
        return size_mb
    except ImportError:
        log.warning("onnxruntime.quantization not available. Copying FP32 model as quantized.")
        import shutil
        shutil.copy(onnx_path, quantized_path)
        return os.path.getsize(quantized_path) / 1_048_576


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=MAX_EPOCHS)
    parser.add_argument("--lr",     type=float, default=LEARNING_RATE)
    parser.add_argument("--batch",  type=int, default=BATCH_SIZE)
    args = parser.parse_args()

    os.makedirs(MODEL_DIR, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info(f"Training device: {device}")

    # ── Data ──────────────────────────────────────────────────────────────────
    splits = load_splits(DATA_DIR)
    n_features = splits["X_train"].shape[2]
    log.info(f"n_features={n_features}  |  train={len(splits['X_train'])}  "
             f"val={len(splits['X_val'])}  test={len(splits['X_test'])}")

    train_loader, val_loader, test_loader = make_loaders(splits, args.batch)

    # ── Model ─────────────────────────────────────────────────────────────────
    model = ResourceGRU(n_features, GRU_HIDDEN_SIZE, GRU_NUM_LAYERS, DROPOUT).to(device)
    log.info(f"Model parameters: {model.count_parameters():,}")

    optimiser  = torch.optim.Adam(model.parameters(), lr=args.lr)
    scheduler  = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimiser, mode="min", factor=0.5, patience=5
    )
    mse_loss   = nn.MSELoss()
    bce_loss   = nn.BCELoss()

    # ── Training loop ─────────────────────────────────────────────────────────
    best_val_loss = float("inf")
    patience_ctr  = 0
    best_state    = None

    log.info("=" * 60)
    log.info("Epoch  TrainLoss  ValLoss  ValACC  ValF1   ValAUC")
    log.info("=" * 60)

    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        train_loss, _, _ = train_epoch(model, train_loader, optimiser, mse_loss, bce_loss, device)
        val_metrics      = evaluate(model, val_loader, mse_loss, bce_loss, device)
        val_loss         = val_metrics["loss"]

        scheduler.step(val_loss)

        log.info(
            f"[{epoch:03d}]  {train_loss:.4f}     {val_loss:.4f}   "
            f"{val_metrics['accuracy']:.3f}   {val_metrics['f1']:.3f}   "
            f"{val_metrics['auc']:.3f}   ({time.time()-t0:.1f}s)"
        )

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state    = {k: v.clone() for k, v in model.state_dict().items()}
            patience_ctr  = 0
        else:
            patience_ctr += 1
            if patience_ctr >= PATIENCE:
                log.info(f"Early stopping at epoch {epoch}.")
                break

    # ── Restore best weights ──────────────────────────────────────────────────
    if best_state:
        model.load_state_dict(best_state)

    # ── Test evaluation ───────────────────────────────────────────────────────
    test_metrics = evaluate(model, test_loader, mse_loss, bce_loss, device)
    log.info("\n── Test Set Results ──────────────────────────────────────")
    log.info(f"  MAE      : {test_metrics['mae']:.4f}")
    log.info(f"  RMSE     : {test_metrics['rmse']:.4f}")
    log.info(f"  Accuracy : {test_metrics['accuracy']:.4f}")
    log.info(f"  F1 Score : {test_metrics['f1']:.4f}")
    log.info(f"  AUC-ROC  : {test_metrics['auc']:.4f}")
    log.info(f"  Confusion Matrix:\n{test_metrics['confusion_matrix']}")

    # ── Export ────────────────────────────────────────────────────────────────
    fp32_path  = os.path.join(MODEL_DIR, "gru_fp32.onnx")
    int8_path  = os.path.join(MODEL_DIR, "gru_quantized.onnx")

    fp32_mb = export_onnx(model, n_features, fp32_path)
    int8_mb = quantize_onnx(fp32_path, int8_path)

    log.info(f"\n  Model size  FP32: {fp32_mb:.2f} MB  →  INT8: {int8_mb:.2f} MB  "
             f"(reduction: {(1 - int8_mb/fp32_mb)*100:.1f}%)")

    # Save PyTorch checkpoint too
    ckpt_path = os.path.join(MODEL_DIR, "gru_checkpoint.pt")
    torch.save({"model_state": best_state, "n_features": n_features, "metrics": test_metrics}, ckpt_path)
    log.info(f"PyTorch checkpoint saved → '{ckpt_path}'")
    log.info("\nTraining complete.")


if __name__ == "__main__":
    main()
