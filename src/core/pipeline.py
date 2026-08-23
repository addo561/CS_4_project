# =============================================================================
# pipeline.py — Real-time inference pipeline
# Connects: psutil collector → feature engineering → ONNX GRU model → action engine
# KNUST Final Year Project — Group 4
#
# This module is the central nervous system of the application.
# It runs entirely on a background thread and communicates results
# to the Flet UI via a thread-safe callback.
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

from config import (
    FEATURE_COLS, WINDOW_SIZE, POLL_INTERVAL_SEC,
    CONFIDENCE_THRESHOLD, TEMP_FALLBACK,
    MODEL_PATH, SCALER_PATH,
    LOCAL_SCALER_PATH, LOCAL_SCALER_DIR, CALIBRATION_SECONDS,
    CPU_BOTTLENECK_PCT, MEM_BOTTLENECK_PCT,
)
from core.action_engine import ActionEngine, ActionResult
from core.notifier import Notifier
from core.collector import ThermalSimulator

log = logging.getLogger("pipeline")


def get_hardware_fingerprint() -> dict:
    """Gets a dictionary of hardware features to identify the current device."""
    try:
        import psutil
        import platform
        # total memory rounded to nearest 100MB to avoid minor OS dynamic reporting differences
        total_mem = round(psutil.virtual_memory().total / (1024 * 1024 * 100)) * (1024 * 1024 * 100)
        return {
            "cpu_count": psutil.cpu_count(logical=True),
            "cpu_count_physical": psutil.cpu_count(logical=False),
            "total_memory": total_mem,
            "os_system": platform.system(),
            "machine": platform.machine()
        }
    except Exception as e:
        log.warning(f"Error generating hardware fingerprint: {e}")
        return {}


# ---------------------------------------------------------------------------
# PipelineResult — data packet sent to the UI on every cycle
# ---------------------------------------------------------------------------

class PipelineResult:
    __slots__ = (
        "timestamp", "features", "confidence",
        "predicted_cpu", "predicted_mem",
        "action", "warning_active", "calibrating", "attributions",
    )

    def __init__(self, timestamp, features, confidence,
                 predicted_cpu, predicted_mem, action, warning_active, calibrating=False, attributions=None):
        self.timestamp     = timestamp        # float (monotonic)
        self.features      = features         # dict  {col: raw_value}
        self.confidence    = confidence        # float 0–1
        self.predicted_cpu = predicted_cpu    # float %
        self.predicted_mem = predicted_mem    # float %
        self.action        = action           # ActionResult
        self.warning_active = warning_active  # bool
        self.calibrating   = calibrating
        self.attributions  = attributions or [0.42, 0.28, 0.18, 0.12]


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
        self._n_features   = None     # feature width the ONNX graph expects (F)
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
            # Remember the feature width baked into the graph (…, WINDOW_SIZE, F).
            # The window fed at inference must match this exactly, regardless of how
            # many CPU cores the host reports.
            shp = self._session.get_inputs()[0].shape
            self._n_features = int(shp[-1]) if isinstance(shp[-1], int) else None
            log.info(f"ONNX model loaded from '{model_path}' (expects {self._n_features} features)")
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

    def compute_attributions(self, window: np.ndarray, baseline_conf: float) -> list[float]:
        """
        Computes perturbation occlusion sensitivity for key features:
        CPU, Memory, Temperature, and Swap.
        """
        features_to_check = ["cpu_percent", "mem_percent", "cpu_temp_c", "swap_percent"]
        deltas = []
        for feat in features_to_check:
            if feat not in FEATURE_COLS:
                deltas.append(0.0)
                continue
            idx = FEATURE_COLS.index(feat)
            
            # Clone window and replace the entire feature column with its temporal baseline
            window_copy = window.copy()
            mean_val = float(np.mean(window[:, idx]))
            window_copy[:, idx] = mean_val
            
            # Run inference on occluded window
            try:
                if self._session is not None:
                    x = window_copy[np.newaxis, ...]
                    outputs = self._session.run(self._output_names, {self._input_name: x})
                    occluded_conf = float(outputs[1][0][0])
                else:
                    occluded_conf, _, _ = self._heuristic(window_copy)
            except Exception:
                occluded_conf = baseline_conf
                
            delta = max(0.0, baseline_conf - occluded_conf)
            deltas.append(delta)
            
        # Normalise deltas
        total_delta = sum(deltas)
        if total_delta > 1e-5:
            attributions = [d / total_delta for d in deltas]
        else:
            attributions = [0.42, 0.28, 0.18, 0.12]
            
        return attributions

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

        # ── Logic Integration from Proposal: Reactive Heuristic ───────────────
        # Uses the current "spike" to drive confidence (replaces jump logic)
        curr_cpu = float(window[-1, cpu_idx])
        curr_mem = float(window[-1, mem_idx])
        
        # Risk factors derived from load levels
        cpu_risk    = max(0.0, (avg_cpu - 0.35) / 0.55)
        mem_risk    = max(0.0, (avg_mem - 0.40) / 0.50)
        spike_risk  = max(0.0, (curr_cpu - 0.70) * 2.5)   # 80% CPU -> 0.25, 90% CPU -> 0.5
        
        # Trend factor (how fast is it rising?)
        trend_boost = max(0.0, min(0.25, trend * 0.7))
        
        confidence  = min(1.0, max(cpu_risk, mem_risk, spike_risk) + trend_boost)

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
# ResourceMonitor — prevents pipeline memory leaks & monitors CPU usage
# ---------------------------------------------------------------------------

class ResourceMonitor:
    """Monitor pipeline resource usage."""
    
    def __init__(self):
        self.last_check = 0
        self.warning_count = 0
        self.proc = psutil.Process()
    
    def check(self):
        """Check resources every 5 seconds."""
        now = time.time()
        if now - self.last_check < 5:
            return
        self.last_check = now
        
        try:
            # Memory check
            mem_mb = self.proc.memory_info().rss / (1024 * 1024)
            if mem_mb > 400:
                log.warning(f"⚠️  High memory: {mem_mb:.1f} MB - triggering GC")
                import gc
                gc.collect()
                self.warning_count += 1
            
            # CPU check
            cpu_pct = self.proc.cpu_percent(interval=0.1)
            if cpu_pct > 80:
                log.warning(f"⚠️  High CPU: {cpu_pct:.1f}%")
                self.warning_count += 1
            
            if self.warning_count > 10:
                log.error("❌  Resource exhaustion - app may freeze!")
                self.warning_count = 0
        
        except Exception as e:
            log.error(f"Error monitoring resources: {e}")


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
        self._thermal_sim  = ThermalSimulator()
        self._thermal_warnings_logged = set()
        self._window       = collections.deque(maxlen=WINDOW_SIZE)
        self._stop_event   = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_action_time = 0.0
        self._last_warning_time = 0.0
        self._feature_cols: Optional[list] = None

        # ── Calibration state ───────────────────────────────────────────────
        # Determine if we should calibrate based on local scaler presence and hardware matching
        hardware_matches = False
        metadata_path = os.path.join(LOCAL_SCALER_DIR, "calibration_metadata.json") if LOCAL_SCALER_DIR else ""
        
        if os.path.isfile(LOCAL_SCALER_PATH) and os.path.isfile(metadata_path):
            try:
                import json
                with open(metadata_path, "r", encoding="utf-8") as f:
                    saved_fingerprint = json.load(f)
                current_fingerprint = get_hardware_fingerprint()
                
                # Compare critical hardware attributes
                if (saved_fingerprint.get("cpu_count") == current_fingerprint.get("cpu_count") and
                    saved_fingerprint.get("cpu_count_physical") == current_fingerprint.get("cpu_count_physical") and
                    saved_fingerprint.get("total_memory") == current_fingerprint.get("total_memory") and
                    saved_fingerprint.get("os_system") == current_fingerprint.get("os_system") and
                    saved_fingerprint.get("machine") == current_fingerprint.get("machine")):
                    hardware_matches = True
                else:
                    log.info("Hardware fingerprint mismatch (new device or configuration changed). Forcing recalibration.")
            except Exception as e:
                log.warning("Failed to verify hardware fingerprint: %s. Assuming mismatch.", e)
        else:
            log.info("No local scaler or metadata found. Forcing recalibration.")

        if hardware_matches and os.path.isfile(LOCAL_SCALER_PATH):
            self._scaler      = self._load_scaler(LOCAL_SCALER_PATH)
            self._calibrating = False
            log.info("Local calibrated scaler loaded from %s", LOCAL_SCALER_PATH)
        else:
            # Delete old mismatched scaler and metadata files if they exist to avoid stale configuration
            if os.path.isfile(LOCAL_SCALER_PATH):
                try:
                    os.remove(LOCAL_SCALER_PATH)
                except Exception:
                    pass
            if os.path.isfile(metadata_path):
                try:
                    os.remove(metadata_path)
                except Exception:
                    pass
            self._scaler      = self._load_scaler(scaler_path)  # bundled fallback
            self._calibrating = True
            self._cal_buffer: list = []
            log.info("Calibration mode active (%ds)", CALIBRATION_SECONDS)

        # Give the engine a reference to the scaler for proper inverse_transform
        self._engine._scaler = self._scaler

        # ── EMA smoothing (from proposal ui.py) ─────────────────────────────
        # Exponential Moving Average reduces spike noise in CPU/MEM readings.
        # alpha=0.3: lower = smoother, higher = more responsive.
        self._ema_cpu: float = 0.0
        self._ema_mem: float = 0.0
        self._ema_init = False   # set False so first value initialises without smoothing

        # ── Sudden Spike Tracking ───────────────────────────────────────────
        self._prev_raw_cpu: Optional[float] = None
        self._prev_raw_mem: Optional[float] = None

        # ── I/O baselines (for net/disk speed calculation) ───────────────────
        try:
            self._last_net  = psutil.net_io_counters()
        except Exception:
            self._last_net  = None
        try:
            self._last_disk = psutil.disk_io_counters()
        except Exception:
            self._last_disk = None
        self._last_io_time = time.time()

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start(self):
        """Start the pipeline on a background daemon thread."""
        if self._thread and self._thread.is_alive():
            log.warning("Pipeline already running.")
            return
        self._stop_event.clear()
        
        # Restart ActionEngine's watchdog thread
        if hasattr(self._action, "start"):
            self._action.start()

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

        # Lower process OS priority to Below Normal / nice=10 to prevent system lag when opening heavy apps
        try:
            proc = psutil.Process()
            if sys.platform == "win32":
                if proc.nice() != psutil.BELOW_NORMAL_PRIORITY_CLASS:
                    proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                    log.info("SRO background process OS priority successfully lowered to BELOW_NORMAL.")
            else:
                # Only lower priority if current nice is higher priority (less than 10)
                if proc.nice() < 10:
                    proc.nice(10)
                    log.info("SRO background process OS priority successfully lowered to nice=10.")
                else:
                    log.info(f"SRO process OS priority is already nice={proc.nice()} (which is low priority).")
        except Exception as e:
            log.warning(f"Could not lower process OS priority: {e}")

        time.sleep(POLL_INTERVAL_SEC)

        monitor = ResourceMonitor()
        iteration = 0
        slow_loops = []

        while not self._stop_event.is_set():
            tick = time.monotonic()
            loop_start = time.perf_counter()
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
                        calibrating    = True,
                    )
                    self._on_result(result)
                    
                    # Every 100 iterations, check resources
                    if iteration % 100 == 0:
                        monitor.check()
                    
                    # Timing check
                    loop_time = time.perf_counter() - loop_start
                    if loop_time > POLL_INTERVAL_SEC * 2:  # 2x slower than expected
                        log.warning(f"⚠️  Slow iteration (calib): {loop_time*1000:.1f}ms")
                    
                    elapsed = time.monotonic() - tick
                    time.sleep(max(0.0, POLL_INTERVAL_SEC - elapsed))
                    iteration += 1
                    continue

                # ── Normal inference phase ────────────────────────────────────────
                if scaled is not None:
                    self._window.append(scaled)

                confidence, pred_cpu, pred_mem = 0.0, 0.0, 0.0
                action = ActionResult()
                attributions = None

                # Detect sudden heavy application launch (massive system load spike)
                raw_cpu = raw.get("cpu_percent_raw", 0.0)
                raw_mem = raw.get("mem_percent_raw", 0.0)
                sudden_spike = False

                if self._prev_raw_cpu is not None:
                    cpu_delta = raw_cpu - self._prev_raw_cpu
                    mem_delta = raw_mem - (self._prev_raw_mem if self._prev_raw_mem is not None else raw_mem)
                    
                    # Spike bypass conditions:
                    # 1. Sudden CPU spike (> 40% rise in 1s)
                    # 2. Extreme CPU load (> 92% raw CPU) with rising trend (> 5% rise)
                    # 3. Sudden memory pressure surge (> 15% rise in 1s)
                    if cpu_delta > 40.0 or (raw_cpu > 92.0 and cpu_delta > 5.0) or mem_delta > 15.0:
                        sudden_spike = True
                        log.info(f"🚀 Sudden heavy application launch detected! CPU delta: +{cpu_delta:.1f}%, Mem delta: +{mem_delta:.1f}%. Bypassing model inference.")

                self._prev_raw_cpu = raw_cpu
                self._prev_raw_mem = raw_mem

                if sudden_spike:
                    # Rule-based bypass: force immediate optimization without calling neural network inference
                    confidence = 0.98
                    pred_cpu = raw_cpu
                    pred_mem = raw_mem
                    attributions = [0.75, 0.15, 0.05, 0.05]  # Attribute bottleneck primarily to CPU load

                    if confidence >= CONFIDENCE_THRESHOLD * 0.85:
                        now_warn = time.monotonic()
                        if now_warn - self._last_warning_time > 60.0:
                            self._last_warning_time = now_warn
                            self._notifier.notify_warning(confidence, pred_cpu, pred_mem)

                    now = time.monotonic()
                    if now - self._last_action_time > 30:
                        self._last_action_time = now
                        action = self._action.evaluate(confidence, pred_cpu, pred_mem)
                        if action.action_taken:
                            self._notifier.notify_suspend(action.affected_names, confidence)

                elif len(self._window) == WINDOW_SIZE:
                    window_arr = np.array(self._window, dtype=np.float32)
                    confidence, pred_cpu, pred_mem = self._engine.predict(window_arr)
                    
                    # Throttle attribution calculation to save CPU under load
                    # Only compute if confidence is elevated, and at most once every 3 seconds
                    now_time = time.monotonic()
                    if confidence >= 0.50 and (not hasattr(self, '_last_attr_time') or now_time - self._last_attr_time >= 3.0):
                        attributions = self._engine.compute_attributions(window_arr, confidence)
                        self._last_attributions = attributions
                        self._last_attr_time = now_time
                    else:
                        if not hasattr(self, '_last_attributions') or self._last_attributions is None:
                            self._last_attributions = [0.42, 0.28, 0.18, 0.12]
                        attributions = self._last_attributions
                    
                    # ── Safety Net from Proposal (Immediate Overload) ───────────────
                    # If EMA load is extreme, force high confidence regardless of window average
                    if self._ema_cpu > 92.0 or self._ema_mem > 90.0:
                        confidence = max(confidence, 0.96)
                        pred_cpu   = max(pred_cpu, self._ema_cpu)
                        
                    if confidence >= CONFIDENCE_THRESHOLD * 0.85:
                        now_warn = time.monotonic()
                        if now_warn - self._last_warning_time > 60.0:
                            self._last_warning_time = now_warn
                            self._notifier.notify_warning(confidence, pred_cpu, pred_mem)
                    
                    # Diagnostic logging
                    if int(time.monotonic()) % 10 == 0:
                        log.debug(f"AI Prediction Engine: Confidence={confidence:.2f}, PredCPU={pred_cpu:.1f}%")

                    now = time.monotonic()
                    if now - self._last_action_time > 30:
                        self._last_action_time = now
                        action = self._action.evaluate(confidence, pred_cpu, pred_mem)
                        if action.action_taken:
                            self._notifier.notify_suspend(action.affected_names, confidence)

                result = PipelineResult(
                    timestamp      = time.time(),
                    features       = raw,
                    confidence     = confidence,
                    predicted_cpu  = pred_cpu,
                    predicted_mem  = pred_mem,
                    action         = action,
                    warning_active = confidence >= CONFIDENCE_THRESHOLD * 0.85,
                    attributions   = attributions,
                )
                self._on_result(result)

                # Every 100 iterations, check resources
                if iteration % 100 == 0:
                    monitor.check()

                # Timing check
                loop_time = time.perf_counter() - loop_start
                if loop_time > POLL_INTERVAL_SEC * 2:  # 2x slower than expected
                    msg = f"⚠️  Slow iteration: {loop_time*1000:.1f}ms"
                    log.warning(msg)
                    slow_loops.append(loop_time)
                    if len(slow_loops) > 5:
                        log.error(f"❌  {len(slow_loops)} slow iterations detected - pipeline may freeze!")
                        slow_loops = []

            except Exception as exc:
                log.error(f"Pipeline loop error: {exc}", exc_info=True)

            elapsed = time.monotonic() - tick
            time.sleep(max(0.0, POLL_INTERVAL_SEC - elapsed))
            iteration += 1

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
            
            # Save the hardware fingerprint metadata
            metadata_path = os.path.join(LOCAL_SCALER_DIR, "calibration_metadata.json") if LOCAL_SCALER_DIR else ""
            if metadata_path:
                try:
                    import json
                    with open(metadata_path, "w", encoding="utf-8") as f:
                        json.dump(get_hardware_fingerprint(), f, indent=4)
                    log.info("Calibration metadata saved successfully to %s", metadata_path)
                except Exception as e:
                    log.error("Failed to save calibration metadata: %s", e)
            
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
        """
        Collect one telemetry sample.
        Mirrors proposal data.py SystemMonitor.get_current_stats() —
        adds network I/O, disk I/O, uptime, and process count on top of
        the existing CPU/mem/freq/temp features.
        EMA smoothing is applied to cpu_percent and mem_percent.
        """
        # ── Core metrics ──────────────────────────────────────────────────────
        try:
            cpu_pct = psutil.cpu_percent(interval=None)
        except Exception as exc:
            if "cpu_percent_error" not in self._thermal_warnings_logged:
                log.warning("Could not read CPU percent; using 0.0%%: %s", exc)
                self._thermal_warnings_logged.add("cpu_percent_error")
            cpu_pct = 0.0
        try:
            per_core = psutil.cpu_percent(interval=None, percpu=True)
        except Exception as exc:
            if "per_core_error" not in self._thermal_warnings_logged:
                log.warning("Could not read per-core CPU percent: %s", exc)
                self._thermal_warnings_logged.add("per_core_error")
            per_core = []
        try:
            freq = psutil.cpu_freq()
        except Exception as exc:
            if "cpu_freq_error" not in self._thermal_warnings_logged:
                log.warning("Could not read CPU frequency; using 0.0 MHz: %s", exc)
                self._thermal_warnings_logged.add("cpu_freq_error")
            freq = None
        try:
            mem = psutil.virtual_memory()
            mem_used_mb = round(mem.used / 1_048_576, 2)
            mem_available_mb = round(mem.available / 1_048_576, 2)
            mem_percent_raw = float(mem.percent)
        except Exception as exc:
            if "memory_error" not in self._thermal_warnings_logged:
                log.warning("Could not read memory stats; using zeros: %s", exc)
                self._thermal_warnings_logged.add("memory_error")
            mem_used_mb = 0.0
            mem_available_mb = 0.0
            mem_percent_raw = 0.0
        try:
            swap = psutil.swap_memory()
            swap_used_mb = round(swap.used / 1_048_576, 2)
            swap_percent = round(swap.percent, 2)
        except Exception as exc:
            if "swap_error" not in self._thermal_warnings_logged:
                log.warning("Could not read swap stats; using zeros: %s", exc)
                self._thermal_warnings_logged.add("swap_error")
            swap_used_mb = 0.0
            swap_percent = 0.0
        # ── Temperature ──────────────────────────────────────────────────────────
        real_temp = self._get_temp()
        if real_temp == TEMP_FALLBACK:
            temp = self._thermal_sim.get_simulated_temp(cpu_pct, mem_percent_raw)
        else:
            temp = real_temp

        # ── EMA smoothing (proposal alpha=0.3) ────────────────────────────────
        alpha = 0.3
        if not self._ema_init:
            self._ema_cpu  = cpu_pct
            self._ema_mem  = mem_percent_raw
            self._ema_init = True
        else:
            self._ema_cpu = cpu_pct * alpha + self._ema_cpu * (1 - alpha)
            self._ema_mem = mem_percent_raw * alpha + self._ema_mem * (1 - alpha)

        # ── Network + Disk I/O speeds ─────────────────────────────────────────
        now      = time.time()
        dt       = max(now - self._last_io_time, 0.001)
        net_sent = net_recv = disk_read = disk_write = 0.0
        try:
            cur_net  = psutil.net_io_counters()
            if self._last_net:
                net_sent  = (cur_net.bytes_sent - self._last_net.bytes_sent) / dt / 1_048_576
                net_recv  = (cur_net.bytes_recv - self._last_net.bytes_recv) / dt / 1_048_576
            self._last_net = cur_net
        except Exception:
            pass
        try:
            cur_disk = psutil.disk_io_counters()
            if self._last_disk and cur_disk:
                disk_read  = (cur_disk.read_bytes  - self._last_disk.read_bytes)  / dt / 1_048_576
                disk_write = (cur_disk.write_bytes - self._last_disk.write_bytes) / dt / 1_048_576
            self._last_disk = cur_disk
        except Exception:
            pass
        self._last_io_time = now

        # ── System health ─────────────────────────────────────────────────────
        try:
            uptime_sec = now - psutil.boot_time()
        except Exception:
            uptime_sec = 0.0
        try:
            process_count = len(psutil.pids())
        except Exception:
            process_count = 0

        raw = {
            # Core features (used by model)
            "cpu_percent":      round(self._ema_cpu, 2),
            "cpu_percent_raw":  round(cpu_pct, 2),        # unsmoothed, for display
            "cpu_freq_mhz":     round(freq.current, 1) if freq else 0.0,
            "mem_used_mb":      mem_used_mb,
            "mem_available_mb": mem_available_mb,
            "mem_percent":      round(self._ema_mem, 2),
            "mem_percent_raw":  round(mem_percent_raw, 2), # unsmoothed, for display
            "swap_used_mb":     swap_used_mb,
            "swap_percent":     swap_percent,
            "cpu_temp_c":       temp,
            # Extended telemetry (for display cards)
            "net_sent_mbps":    round(max(0.0, net_sent),  3),
            "net_recv_mbps":    round(max(0.0, net_recv),  3),
            "disk_read_mbps":   round(max(0.0, disk_read), 3),
            "disk_write_mbps":  round(max(0.0, disk_write),3),
            "uptime_sec":       round(uptime_sec, 0),
            "process_count":    process_count,
        }
        for i, pct in enumerate(per_core):
            raw[f"cpu_core_{i}"] = round(pct, 2)
        return raw

    def _get_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            if not temps:
                if "no_sensors" not in self._thermal_warnings_logged:
                    log.warning("⚠️  No temperature sensors detected")
                    log.warning("   Thermal data unavailable (common on VMs, MacBooks)")
                    log.warning("   Using simulated temperature values for model inputs")
                    self._thermal_warnings_logged.add("no_sensors")
                return TEMP_FALLBACK

            # Try common sensor keys in priority order
            for key in ("coretemp", "k10temp", "acpitz", "cpu_thermal"):
                if key in temps:
                    readings = [t.current for t in temps[key] if t.current]
                    if readings:
                        return round(sum(readings) / len(readings), 2)
        except Exception as e:
            if "sensor_error" not in self._thermal_warnings_logged:
                log.warning(f"⚠️  Could not read temperature sensors: {e}")
                self._thermal_warnings_logged.add("sensor_error")
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
            # The scaler (and the ONNX graph behind it) were fitted with a fixed
            # feature width on the training machine. A host with a different CPU
            # core count produces a different number of `cpu_core_*` columns, so we
            # pad short vectors with zeros and truncate long ones to that exact
            # width. This keeps the app working on any hardware instead of crashing
            # on machines with fewer cores or silently mis-shaping on more.
            values = self._fit_width(values, self._scaler.n_features_in_)
            # Suppress benign sklearn warning: scaler was fitted on a DataFrame
            # but we pass a numpy array at inference time — behaviour is identical.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                return self._scaler.transform(values.reshape(1, -1))[0]
        else:
            # Fallback normalisation (divide by rough maximums). Fit to the width
            # the ONNX model expects so inference never sees a wrong-shaped window.
            target = getattr(self._engine, "_n_features", None) or len(values)
            values = self._fit_width(values, target)
            base = [100, 5000, 32768, 32768, 100, 32768, 100, 120]
            _fallback_max = np.array(base + [100] * max(0, target - len(base)),
                                     dtype=np.float32)[:target]
            return np.clip(values / np.maximum(_fallback_max, 1e-6), 0.0, 1.0)

    @staticmethod
    def _fit_width(vec: np.ndarray, n: int) -> np.ndarray:
        """Pad with zeros or truncate `vec` so it has exactly `n` elements."""
        if n is None or len(vec) == n:
            return vec
        if len(vec) < n:
            return np.concatenate(
                [vec, np.zeros(n - len(vec), dtype=vec.dtype)]
            )
        return vec[:n]

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
