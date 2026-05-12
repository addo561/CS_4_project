# =============================================================================
# pipeline.py — Real-time inference pipeline
# Connects: psutil collector → feature engineering → ONNX GRU model → action engine
# KNUST Final Year Project — Group 4
#
# This module is the central nervous system of the application.
# It runs entirely on a background thread and communicates results
# to the PyQt6 UI via a thread-safe callback.
# =============================================================================

import collections
import logging
import os
import pickle
import queue
import sys
import threading
import time
from typing import Callable, Optional

import numpy as np
import psutil

_THIS_DIR    = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_THIS_DIR)            # Final_year/
_COLLECTOR   = os.path.join(_PROJECT_DIR, "Data_collector")

# Make both this folder and Data_collector available for imports
for _p in (_THIS_DIR, _COLLECTOR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config import (
    FEATURE_COLS, WINDOW_SIZE, POLL_INTERVAL_SEC,
    CONFIDENCE_THRESHOLD, TEMP_FALLBACK,
    MODEL_PATH, SCALER_PATH,
    LOCAL_SCALER_PATH, LOCAL_SCALER_DIR, CALIBRATION_SECONDS,
    CPU_BOTTLENECK_PCT, MEM_BOTTLENECK_PCT,
)
from action_engine import ActionEngine, ActionResult
from notifier import Notifier

log = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# PipelineResult — data packet sent to the UI on every cycle
# ---------------------------------------------------------------------------

class PipelineResult:
    __slots__ = (
        "timestamp", "features", "confidence",
        "predicted_cpu", "predicted_mem",
        "action", "warning_active",
    )

    def __init__(self, timestamp, features, confidence,
                 predicted_cpu, predicted_mem, action, warning_active):
        self.timestamp     = timestamp        # float (monotonic)
        self.features      = features         # dict  {col: raw_value}
        self.confidence    = confidence        # float 0–1
        self.predicted_cpu = predicted_cpu    # float %
        self.predicted_mem = predicted_mem    # float %
        self.action        = action           # ActionResult
        self.warning_active = warning_active  # bool


# ---------------------------------------------------------------------------
# InferenceEngine — wraps the ONNX runtime
# ---------------------------------------------------------------------------

class InferenceEngine:
    """
    Loads the quantized GRU ONNX model and runs inference.
    Falls back to a simple heuristic if the model file is absent
    (useful during development before training is complete).
    """

    def __init__(self, model_path: str, scaler=None):
        self._session      = None
        self._input_name   = None
        self._output_names = None
        self._scaler       = scaler   # used for proper inverse_transform
        self._load(model_path)

    def _load(self, model_path: str):
        if not os.path.isfile(model_path):
            log.warning(
                f"Model file not found at '{model_path}'. "
                "Running in HEURISTIC mode — no AI predictions."
            )
            return
        try:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 1    # keep CPU footprint minimal
            opts.inter_op_num_threads = 1
            self._session      = ort.InferenceSession(model_path, sess_options=opts)
            self._input_name   = self._session.get_inputs()[0].name
            self._output_names = [o.name for o in self._session.get_outputs()]
            log.info(f"ONNX model loaded from '{model_path}'")
        except Exception as exc:
            log.error(f"Failed to load ONNX model: {exc}")

    def predict(self, window: np.ndarray) -> tuple[float, float, float]:
        """
        Parameters
        ----------
        window : np.ndarray, shape (WINDOW_SIZE, n_features), dtype float32

        Returns
        -------
        confidence    : float — bottleneck probability (0–1)
        predicted_cpu : float — predicted CPU % (de-normalised)
        predicted_mem : float — predicted memory % (de-normalised)
        """
        if self._session is None:
            return self._heuristic(window)

        try:
            x = window[np.newaxis, ...]   # (1, W, F)
            outputs = self._session.run(
                self._output_names,
                {self._input_name: x},
            )
            # Expected model outputs: [regression_vector (1,F), confidence_scalar (1,1)]
            reg_out  = outputs[0][0]    # shape (F,)
            conf_out = float(outputs[1][0][0])

            cpu_idx = FEATURE_COLS.index("cpu_percent")
            mem_idx = FEATURE_COLS.index("mem_percent")

            # De-normalise using the scaler so values match the real CPU/MEM %
            pred_cpu = self._denorm(reg_out, cpu_idx)
            pred_mem = self._denorm(reg_out, mem_idx)

            return conf_out, pred_cpu, pred_mem

        except Exception as exc:
            log.error(f"Inference error: {exc}")
            return self._heuristic(window)

    def _denorm(self, norm_vec: np.ndarray, col_idx: int) -> float:
        """
        Properly de-normalise a single feature value from the scaled space
        back to its original units using the scaler's inverse_transform.
        Falls back to *100 if no scaler is available.
        """
        if self._scaler is not None:
            try:
                import warnings
                n = self._scaler.n_features_in_
                row = np.zeros((1, n), dtype=np.float32)
                idx = min(col_idx, n - 1)
                row[0, idx] = float(norm_vec[idx]) if len(norm_vec) > idx else 0.0
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    inv = self._scaler.inverse_transform(row)
                return float(inv[0, idx])
            except Exception:
                pass
        # Fallback: assume scaler mapped 0-100 linearly
        return float(norm_vec[col_idx]) * 100.0

    def _heuristic(self, window: np.ndarray) -> tuple[float, float, float]:
        """
        Fallback when no model is available.
        Uses the mean of the last 10 samples as the 'prediction'
        and derives a simple confidence from CPU trend.
        """
        recent  = window[-10:]
        cpu_idx = FEATURE_COLS.index("cpu_percent")
        mem_idx = FEATURE_COLS.index("mem_percent")

        avg_cpu = float(np.mean(recent[:, cpu_idx]))   # normalised 0-1
        avg_mem = float(np.mean(recent[:, mem_idx]))   # normalised 0-1
        trend   = float(window[-1, cpu_idx] - window[-10, cpu_idx])

        # ── Risk curve (non-linear, distinct from raw CPU%) ──────────────────
        cpu_risk    = max(0.0, (avg_cpu - 0.40) / 0.55)
        mem_risk    = max(0.0, (avg_mem - 0.45) / 0.50)
        trend_boost = max(0.0, min(0.15, trend * 0.5))
        confidence  = min(1.0, max(cpu_risk, mem_risk) + trend_boost)

        # De-normalise using inverse_transform so values tally with the metric cards
        # Build a dummy vector at the average values then invert it
        pred_cpu = self._denorm(
            np.array([avg_cpu] * max(cpu_idx + 1, mem_idx + 1), dtype=np.float32), cpu_idx
        )
        pred_mem = self._denorm(
            np.array([avg_mem] * max(cpu_idx + 1, mem_idx + 1), dtype=np.float32), mem_idx
        )

        return confidence, pred_cpu, pred_mem


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class Pipeline:
    """
    Orchestrates the full real-time loop:
      1. Poll psutil at POLL_INTERVAL_SEC
      2. Transform raw values into scaled feature vector
      3. Maintain a rolling window of WINDOW_SIZE samples
      4. Run inference once the window is full
      5. Pass results to ActionEngine
      6. Fire notifications if needed
      7. Invoke on_result callback (consumed by the UI)

    Usage
    -----
        pipeline = Pipeline(on_result=my_callback)
        pipeline.start()
        ...
        pipeline.stop()
    """

    def __init__(
        self,
        on_result:            Callable[[PipelineResult], None],
        on_calibration_progress: Optional[Callable[[int, int], None]] = None,
        model_path:  str = MODEL_PATH,
        scaler_path: str = SCALER_PATH,
    ):
        self._on_result    = on_result
        self._on_cal_prog  = on_calibration_progress   # (elapsed_s, total_s) → None
        self._engine       = InferenceEngine(model_path)
        self._action       = ActionEngine()
        self._notifier     = Notifier()
        self._window       = collections.deque(maxlen=WINDOW_SIZE)
        self._stop_event   = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_action_time = 0.0
        self._feature_cols: Optional[list] = None

        # ── Calibration state ───────────────────────────────────────────────
        # Priority: local calibrated scaler > bundled scaler > heuristic fallback
        if os.path.isfile(LOCAL_SCALER_PATH):
            self._scaler      = self._load_scaler(LOCAL_SCALER_PATH)
            self._calibrating = False
            log.info("Local calibrated scaler loaded from %s", LOCAL_SCALER_PATH)
        else:
            self._scaler      = self._load_scaler(scaler_path)  # bundled fallback
            self._calibrating = True
            self._cal_buffer: list = []
            log.info("No local scaler found — calibration mode active (%ds)", CALIBRATION_SECONDS)

        # Give the engine a reference to the scaler for proper inverse_transform
        self._engine._scaler = self._scaler

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start(self):
        """Start the pipeline on a background daemon thread."""
        if self._thread and self._thread.is_alive():
            log.warning("Pipeline already running.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target = self._run,
            name   = "PipelineThread",
            daemon = True,
        )
        self._thread.start()
        log.info("Pipeline started.")

    def stop(self):
        """Signal the pipeline to stop and wait for clean exit."""
        self._stop_event.set()
        self._action.shutdown()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Pipeline stopped.")

    # ── Manual controls (called by UI buttons) ───────────────────────────────

    def trigger_undo(self) -> ActionResult:
        result = self._action.undo()
        if result.action_taken:
            self._notifier.notify_resume(result.affected_names)
        return result

    def trigger_boost(self) -> ActionResult:
        result = self._action.boost()
        self._notifier.notify_boost()
        return result

    def get_suspended_processes(self):
        return self._action.get_suspended_list()

    # ── Internal loop ──────────────────────────────────────────────────────

    def _run(self):
        # Warm up cpu_percent (first call always returns 0.0)
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
        time.sleep(POLL_INTERVAL_SEC)

        while not self._stop_event.is_set():
            tick = time.monotonic()
            try:
                raw    = self._collect_raw()
                scaled = self._scale(raw)

                # ── Calibration phase ───────────────────────────────────────────
                if self._calibrating:
                    if scaled is not None:
                        self._cal_buffer.append(scaled)
                    elapsed_s = len(self._cal_buffer)
                    if self._on_cal_prog:
                        self._on_cal_prog(elapsed_s, CALIBRATION_SECONDS)
                    if elapsed_s >= CALIBRATION_SECONDS:
                        self._finish_calibration()
                    # Emit a live result so charts still update during calibration
                    result = PipelineResult(
                        timestamp      = time.time(),
                        features       = raw,
                        confidence     = 0.0,
                        predicted_cpu  = 0.0,
                        predicted_mem  = 0.0,
                        action         = ActionResult(),
                        warning_active = False,
                    )
                    self._on_result(result)
                    elapsed = time.monotonic() - tick
                    time.sleep(max(0.0, POLL_INTERVAL_SEC - elapsed))
                    continue

                # ── Normal inference phase ────────────────────────────────────────
                if scaled is not None:
                    self._window.append(scaled)

                confidence, pred_cpu, pred_mem = 0.0, 0.0, 0.0
                action = ActionResult()

                if len(self._window) == WINDOW_SIZE:
                    window_arr = np.array(self._window, dtype=np.float32)
                    confidence, pred_cpu, pred_mem = self._engine.predict(window_arr)

                    if confidence >= CONFIDENCE_THRESHOLD * 0.85:
                        self._notifier.notify_warning(confidence, pred_cpu, pred_mem)

                    now = time.monotonic()
                    if now - self._last_action_time > 30:
                        action = self._action.evaluate(confidence, pred_cpu, pred_mem)
                        if action.action_taken:
                            self._notifier.notify_suspend(action.affected_names, confidence)
                            self._last_action_time = now

                result = PipelineResult(
                    timestamp      = time.time(),
                    features       = raw,
                    confidence     = confidence,
                    predicted_cpu  = pred_cpu,
                    predicted_mem  = pred_mem,
                    action         = action,
                    warning_active = confidence >= CONFIDENCE_THRESHOLD * 0.85,
                )
                self._on_result(result)

            except Exception as exc:
                log.error(f"Pipeline loop error: {exc}", exc_info=True)

            elapsed = time.monotonic() - tick
            time.sleep(max(0.0, POLL_INTERVAL_SEC - elapsed))

    def _finish_calibration(self):
        """Fit a MinMaxScaler on the collected calibration buffer and save it."""
        import pickle, warnings
        from sklearn.preprocessing import MinMaxScaler
        try:
            data = np.array(self._cal_buffer, dtype=np.float32)
            local_scaler = MinMaxScaler()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                local_scaler.fit(data)
            os.makedirs(LOCAL_SCALER_DIR, exist_ok=True)
            with open(LOCAL_SCALER_PATH, "wb") as f:
                pickle.dump(local_scaler, f)
            self._scaler      = local_scaler
            self._calibrating = False
            self._window.clear()    # fresh window with new scaler
            self._engine._scaler = local_scaler   # update engine for inverse_transform
            log.info("Calibration complete. Local scaler saved to %s", LOCAL_SCALER_PATH)
            # Notify UI via the progress callback with a sentinel (-1, -1)
            if self._on_cal_prog:
                self._on_cal_prog(-1, CALIBRATION_SECONDS)
        except Exception as exc:
            log.error("Calibration failed: %s", exc)
            self._calibrating = False   # fall back to bundled scaler

    # ── Data collection ──────────────────────────────────────────────────────

    def _collect_raw(self) -> dict:
        """Collect one raw telemetry sample (mirrors collector.py logic)."""
        cpu_pct   = psutil.cpu_percent(interval=None)
        per_core  = psutil.cpu_percent(interval=None, percpu=True)
        freq      = psutil.cpu_freq()
        mem       = psutil.virtual_memory()
        swap      = psutil.swap_memory()
        temp      = self._get_temp()

        raw = {
            "cpu_percent":      round(cpu_pct, 2),
            "cpu_freq_mhz":     round(freq.current, 1) if freq else 0.0,
            "mem_used_mb":      round(mem.used / 1_048_576, 2),
            "mem_available_mb": round(mem.available / 1_048_576, 2),
            "mem_percent":      round(mem.percent, 2),
            "swap_used_mb":     round(swap.used / 1_048_576, 2),
            "swap_percent":     round(swap.percent, 2),
            "cpu_temp_c":       temp,
        }
        for i, pct in enumerate(per_core):
            raw[f"cpu_core_{i}"] = round(pct, 2)
        return raw

    @staticmethod
    def _get_temp() -> float:
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for key in ("coretemp", "k10temp", "acpitz", "cpu_thermal"):
                    if key in temps:
                        readings = [t.current for t in temps[key] if t.current]
                        if readings:
                            return round(sum(readings) / len(readings), 2)
        except (AttributeError, NotImplementedError):
            pass
        return TEMP_FALLBACK

    def _scale(self, raw: dict) -> Optional[np.ndarray]:
        """
        Scale raw values using the fitted scaler.
        Falls back to simple min-max approximation if scaler not found.
        """
        if self._feature_cols is None:
            self._feature_cols = FEATURE_COLS + sorted(
                [k for k in raw if k.startswith("cpu_core_")],
                key=lambda c: int(c.split("_")[-1])
            )

        values = np.array(
            [raw.get(col, 0.0) for col in self._feature_cols],
            dtype=np.float32
        )

        if self._scaler is not None:
            import warnings
            # Ensure feature count matches scaler expectation
            n_expected = self._scaler.n_features_in_
            if len(values) != n_expected:
                values = values[:n_expected]
            # Suppress benign sklearn warning: scaler was fitted on a DataFrame
            # but we pass a numpy array at inference time — behaviour is identical.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                return self._scaler.transform(values.reshape(1, -1))[0]
        else:
            # Fallback normalisation (divide by rough maximums)
            _fallback_max = np.array([100, 5000, 32768, 32768, 100, 32768, 100, 120]
                                     + [100] * (len(values) - 8), dtype=np.float32)
            _fallback_max = _fallback_max[:len(values)]
            return np.clip(values / np.maximum(_fallback_max, 1e-6), 0.0, 1.0)

    @staticmethod
    def _load_scaler(path: str):
        if not os.path.isfile(path):
            log.warning(f"Scaler not found at '{path}'. Using fallback normalisation.")
            return None
        try:
            with open(path, "rb") as f:
                import pickle
                scaler = pickle.load(f)
            log.info(f"Scaler loaded from '{path}'")
            return scaler
        except Exception as exc:
            log.error(f"Failed to load scaler: {exc}")
            return None
