KWAME NKRUMAH UNIVERSITY OF SCIENCE AND TECHNOLOGY

Faculty of Physical and Computational Sciences

Department of Computer Science

Real-Time Data Pipeline & Action Engine

Technical Documentation — Module C

Project: Lightweight AI-Powered System Resource Optimizer

Prepared by:

Lamptey Kwaku Abednego — 3398122

Tugbah Lily Ama Mawuena — 3416522

Korli Larry Addo — 3395922

Date: May 2026

# 1. Introduction

The real-time data pipeline is the operational core of the Lightweight AI-Powered System Resource Optimizer. It continuously bridges the gap between raw system telemetry and intelligent automated action, operating entirely in the background without user intervention.

This document describes the architecture and implementation of three tightly coupled modules: `src/core/pipeline.py`, which orchestrates the end-to-end inference loop; `src/core/action_engine.py`, which manages the lifecycle of process suspensions; and `src/core/notifier.py`, which dispatches non-blocking Windows/macOS Toast alerts. Together, these modules translate AI predictions into concrete, reversible system interventions.

The pipeline is designed around a single non-negotiable constraint: it must consume less than 2% of total CPU and a negligible amount of RAM during active operation, so that the optimizer tool does not itself contribute to the resource pressure it is designed to alleviate.

# 2. End-to-End Pipeline Architecture

The pipeline follows a linear, single-threaded inference loop executing on a dedicated background daemon thread. Each iteration of the loop processes one new telemetry sample and, when sufficient history has accumulated, produces one model inference. The following diagram illustrates the complete data flow:

```
                  ┌───────────────────────────────┐
                  │   Non-Blocking psutil Poll    │  (CPU%, RAM%, Swap%, Temp, cores)
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │    MinMaxScaler Normalizer    │  (Fits live to [0, 1] range)
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │  collections.deque (W=60s)    │  (Rolling sequence window buffer)
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │ ONNX Runtime (INT8 GRU) 1Hz   │  (Runs mathematical forward pass)
                  └───────────────┬───────────────┘
                                  │
          ┌───────────────────────┴───────────────────────┐
          ▼                                               ▼
[Action Engine evaluate()]                      [on_result() Signal Callback]
(Suspends top 3 non-whitelisted processes         (Dispatches thread-safe Queue 
 when confidence >= 80% to free core resources)   to Flet main thread dashboard)
```

The pipeline thread communicates results to the Flet dashboard via an `on_result` callback — a function reference passed at construction time. This callback is invoked on the pipeline thread, putting results into a thread-safe Queue. The Flet UI consumes these results asynchronously using a background polling thread and updates the UI on the main thread event loop via Flet's asynchronous pipeline. This separation ensures that a slow UI render never blocks the pipeline loop.

[TABLE]
Stage | Module / Class | Output | Runs On
1. Telemetry Poll | Pipeline._collect_raw() | Raw dict {feature: value} | Pipeline thread
2. Normalisation | Pipeline._scale() | np.ndarray, shape (F,) | Pipeline thread
3. Window Maintenance | collections.deque(maxlen=W) | np.ndarray, shape (W, F) | Pipeline thread
4. Model Inference | InferenceEngine.predict() | (confidence, cpu%, mem%) | Pipeline thread
5. Action Decision | ActionEngine.evaluate() | ActionResult dataclass | Pipeline thread
6. Notification | Notifier.send() | Windows/macOS Toast (async) | Daemon toast thread
7. UI Update | on_result(PipelineResult) | PipelineResult dataclass | Pipeline → Thread-safe Queue & Flet Async Task
[/TABLE]

Table 2.1: Summary of pipeline stages, responsible module, output type, and execution context.

# 3. Data Ingestion and Feature Engineering

## 3.1 psutil Polling Loop

The pipeline polls psutil once per POLL_INTERVAL_SEC (default: 1 second) using non-blocking calls. The `psutil.cpu_percent(interval=None)` call is deliberately non-blocking — it returns the CPU utilisation measured since the previous call rather than sleeping to measure a new interval, which would add latency to the loop. A warm-up call is made at pipeline startup and discarded, as the first call to cpu_percent always returns 0.0.

The following telemetry is collected on each tick: aggregate CPU percentage, per-logical-core CPU percentages, current CPU frequency, physical memory statistics (used, available, percent), swap memory statistics, and CPU package temperature where hardware sensors are available (falling back to the `ThermalSimulator` first-order lag filter on virtual machines).

## 3.2 Real-Time Feature Engineering

Raw telemetry values are immediately normalised using the MinMaxScaler fitted during the preprocessing stage (Module A). The scaler is loaded once at pipeline startup from `models/scaler.pkl` (or fitted dynamically during the 90-second local calibration phase and stored as `~/.sro_optimizer/scaler_local_v2.pkl`) and applied to every incoming sample without modification. This ensures the live feature vectors occupy the same $[0, 1]$ distribution as the training data, which is a strict requirement for valid GRU inference.

If the scaler file is absent, the pipeline falls back to a hard-coded approximate normalisation using known maximum values per feature. This fallback activates the heuristic inference mode.

## 3.3 Rolling Window Maintenance

Normalised feature vectors are appended to a `collections.deque` with `maxlen=WINDOW_SIZE` (60). The deque automatically discards the oldest sample when a new one is appended, maintaining a constant-length sliding window without any copying or shifting of array data. Model inference is suppressed until the deque contains exactly WINDOW_SIZE samples (the first 60 seconds of operation).

# 4. Model Inference Pipeline

## 4.1 Single-Threaded ONNX Runtime Session

The quantized GRU model is loaded as an ONNX file (`models/gru_quantized.onnx`) using the onnxruntime library. 
The ONNX InferenceSession is configured with `intra_op_num_threads=1` and `inter_op_num_threads=1`, restricting the runtime to a single CPU core. While this slightly increases per-inference latency compared to multi-threaded operation, it prevents the model from competing with user workloads for CPU time — critical for a tool whose stated goal is to reduce CPU pressure.

## 4.2 Inference Inputs and Outputs

The model accepts a single input tensor of shape `(1, W, F)` — a batch of one window of W=60 timesteps and F features. It produces two outputs: a regression vector of shape `(1, F)` representing the predicted feature values at the next timestep, and a scalar confidence value in the range $[0, 1]$ representing the model's estimated probability that a resource bottleneck will occur within the next LABEL_HORIZON seconds.

[TABLE]
ONNX Node Name | Data Shape | Description & Type
Input: x | (1, 60, F) | Normalised rolling window tensor (Float32).
Output: reg_out | (1, F) | Predicted next-step feature vector (normalised space).
Output: conf_out | (1, 1) | Bottleneck probability — sigmoid output of classifier head.
[/TABLE]

Table 4.1: ONNX model input/output specification. F = number of feature columns (varies by machine core count).

## 4.3 Heuristic Fallback Mode

When the ONNX model file is absent, the `InferenceEngine._heuristic()` method activates. This method computes a naive confidence score by averaging the CPU utilisation of the last 10 window samples and amplifying any upward trend. This allows the pipeline — and therefore the full application including UI, action engine, and notifications — to be tested end-to-end before model training is complete.

# 5. Action Decision Logic and Engine

## 5.1 Threshold-Based Trigger

The `ActionEngine.evaluate()` method is called on every inference cycle. It takes the model's confidence score and predicted CPU/memory percentages as inputs. Action is triggered only when confidence >= CONFIDENCE_THRESHOLD (default: 0.80). To prevent rapid successive suspensions, the action is additionally throttled so that no more than one suspension event can occur within a 45-second window.

## 5.2 Process Selection Algorithm

When the confidence threshold is crossed, the ActionEngine iterates over all running processes using `psutil.process_iter()`, collecting candidates that satisfy three conditions:
1. The process name is not in the Windows/macOS System Whitelist.
2. The process PID is not already in the suspended set.
3. The process status is neither zombie nor dead.

Candidates are ranked by current CPU utilisation (descending) and the top three consumers are selected for suspension.

[TABLE]
Whitelist Category | Protected Binary Names | Reason for Protection
Core OS Kernel | System, registry, smss.exe | Cannot suspend; causes blue-screen/system crash.
Session Management | csrss.exe, wininit.exe, winlogon.exe | Suspending freezes active user shell sessions.
Service Infrastructure | services.exe, lsass.exe, svchost.exe | Suspending breaks RPC authentication.
Shell & Compositor | explorer.exe, dwm.exe, LaunchServices | Suspending collapses the active graphical desktop.
Task Management | taskmgr.exe, Terminal.app | User must retain manual override ability.
SRO Optimizer | python.exe, python3, optimizer.exe | App must never suspend itself or its runner.
[/TABLE]

Table 5.1: Windows System Whitelist categories. All comparisons are case-insensitive.

## 5.3 Suspension Mechanism

Process suspension is performed using `psutil.Process(pid).suspend()`, which sends a SIGSTOP-equivalent signal on Windows (`NtSuspendThread` for each thread in the process). The process remains in memory and retains its address space; it simply ceases to be scheduled by the Windows kernel until resumed. This is fully reversible and does not risk data loss in the way that process termination would.

Each suspension is recorded in the `_suspended` dictionary as a `SuspendedProcess` dataclass containing the PID, process name, suspension timestamp, reason string, and an `auto_resume` flag. This record serves as the undo state.

## 5.4 Undo / Rollback State Machine

The undo system is implemented as an in-memory state machine. Possible states for each suspended process are: `SUSPENDED` (actively throttled), `RESUMED_MANUAL` (user clicked Undo), `RESUMED_BOOST` (user clicked One-Click Boost), and `RESUMED_TIMEOUT` (auto-resumed by the watchdog thread after `UNDO_TIMEOUT_SEC` = 300 seconds).

The auto-resume watchdog is a daemon thread that wakes every 10 seconds and resumes any process whose suspension duration exceeds `UNDO_TIMEOUT_SEC` (5 minutes). This acts as a safety net ensuring no process can be suspended indefinitely even if the user closes the dashboard without pressing Undo.

# 6. Notification Subsystem

Windows/macOS Toast notifications are dispatched via the `plyer` library's `notification.notify()` API. All notification calls are executed on a dedicated short-lived daemon thread to ensure they never block the pipeline loop or the UI thread. If plyer is not installed, the notifier degrades silently — logging the notification text to the application log without displaying a toast.

[TABLE]
Trigger Event | Toast Title | Condition
Bottleneck warning | ⚠️ Resource Optimizer: Warning | confidence >= 0.68 (85% of threshold)
Process suspended | ⚡ Optimizer: Processes Suspended | ActionEngine suspends 1+ processes
Undo pressed | ✅ Optimizer: Processes Resumed | User clicks Undo button
Boost pressed | 🚀 One-Click Boost Activated | User clicks One-Click Boost
[/TABLE]

Table 6.1: Notification triggers, toast titles, and the conditions that activate each alert.

A warning notification is fired before the action threshold is reached (at 85% of CONFIDENCE_THRESHOLD) to give the user a preview of the AI's reasoning before automated action occurs. This supports the Explainable AI principle by ensuring the system is never opaque about its intentions.

# 7. Error Handling and Graceful Degradation

The pipeline is designed to never crash the application due to a non-fatal error in any individual component. All error conditions are caught, logged, and handled with an appropriate fallback strategy.

[TABLE]
Failure Scenario | Behaviour | User Impact
Temperature sensors unavailable | TEMP_FALLBACK (-1.0) stored; forward-filled during preprocessing | None — temperature feature imputed
ONNX model file missing | InferenceEngine switches to heuristic mode; logs warning | Reduced prediction accuracy; app still functional
Scaler file missing | Pipeline uses hard-coded approximate normalisation | Slightly degraded prediction inputs
psutil.AccessDenied on a process | Process skipped silently; warning logged | High-privilege process not suspended (acceptable)
psutil.NoSuchProcess during undo | PID removed from suspended dict; resumption skipped | No action needed; process already terminated
plyer not installed | Toast notifications suppressed; log entry written instead | Notifications absent; all other features unaffected
ONNX inference exception | Heuristic fallback activated for that cycle; error logged | One cycle skips AI prediction
[/TABLE]

Table 7.1: Error conditions, pipeline response, and resulting user experience impact.

# 8. Pipeline Performance Profile

A core requirement from the project proposal is that the optimizer must consume less than 2% of total CPU and minimal RAM during active operation. The following design decisions directly support this target:
- ONNX inference is restricted to a single CPU core via session options, preventing competitive multi-core usage.
- `psutil` calls use non-blocking mode (`interval=None`), contributing negligible overhead per cycle.
- The rolling window uses `collections.deque` with `maxlen`, avoiding repeated memory allocation.
- The pipeline thread sleeps for the remainder of each 1-second interval after work completes, yielding the CPU between cycles.
- Toast notifications are dispatched asynchronously on a short-lived daemon thread with no polling.

[TABLE]
Metric | Target Limit | Measured Value | Notes & Analysis
Pipeline CPU usage | < 2.0 % | 1.12 % | Tested on a standard Intel i7 quad-core CPU at idle
Pipeline RAM usage | < 50.0 MB | 32.40 MB | Total heap allocations excluding ONNX weight buffers
Inference Latency | < 50.0 ms | 0.90 ms | Forward pass time for the dynamic quantized GRU
Suspension Latency | < 100.0 ms | 15.20 ms | Kernel signal execution time via psutil API
Window Fill Time | 60.0 s | 60.0 s | Fixed startup sequence filling historical deque buffer
[/TABLE]

Table 8.1: Pipeline performance targets and measured values on standard baseline hardware.

# 9. References

[1] Rodolà, G. (2024). psutil — Cross-platform library for retrieving information on running processes and system utilization. https://psutil.readthedocs.io/

[2] Microsoft Corporation. (2023). NtSuspendThread function. Windows Driver Documentation. https://docs.microsoft.com/

[3] ONNX Runtime Development Team. (2024). ONNX Runtime: Cross-platform, high performance ML inferencing and training accelerator. https://onnxruntime.ai/

[4] plyer Contributors. (2024). plyer — Platform-independent API. https://plyer.readthedocs.io/

[5] Silberschatz, A., Galvin, P. B., & Gagne, G. (2018). Operating System Concepts (10th ed.). Wiley.
