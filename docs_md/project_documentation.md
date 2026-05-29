SYSTEM RESOURCE OPTIMIZER
Final Year Project — KNUST Group 4

Technical Documentation: Dataset Collection, Preprocessing,
Model Training, Time-Series Architecture & Library Reference

Generated: May 2026

# 1. What Is a Time-Series Model?

A time-series model is a machine-learning or statistical model designed to learn patterns from data that arrives sequentially over time. Unlike standard tabular models that treat every row as independent, a time-series model explicitly uses the order of observations — what happened 1 second ago, 5 seconds ago, 60 seconds ago — to predict what will happen next.

In this project, every second we collect CPU usage, memory usage, temperature, and other metrics. Each reading depends heavily on the readings before it: a CPU spike rarely appears from nothing; it builds. A time-series model captures that build-up and predicts a bottleneck before it fully arrives, giving the optimizer time to act.

## 1.1 Common Time-Series Model Families

[TABLE]
Model | Core Idea | Limitation for Our Task
ARIMA / SARIMA | Linear statistical model using auto-regression and differencing | Cannot capture non-linear CPU spike patterns; no memory of long-term load trends
Simple RNN | Recurrent neural network: hidden state passed forward each step | Vanishing gradient — forgets events > ~10 steps ago; poor for 60-step windows
LSTM | Adds cell state + forget/input/output gates to fix vanishing gradient | More parameters than GRU; slower to train; similar accuracy for this problem
GRU (chosen) | Simplified LSTM with only reset & update gates | Best trade-off: fewer parameters, faster training, equivalent accuracy
Transformer | Self-attention over all time steps simultaneously | Overkill for 60-step windows; high memory/compute; poor for small datasets
Prophet | Trend + seasonality decomposition (Facebook) | Designed for daily/weekly patterns, not sub-second system telemetry
[/TABLE]

# 2. Why We Chose GRU Over Other Architectures

The Gated Recurrent Unit (GRU) was introduced by Cho et al. (2014) as a streamlined alternative to the Long Short-Term Memory (LSTM) network. Both solve the vanishing-gradient problem that prevents simple RNNs from learning patterns over long sequences, but GRU does so with fewer gates and therefore fewer trainable parameters.

## 2.1 GRU Gate Mechanics

A GRU cell has exactly two gates:
- **Update gate (z):** Controls how much of the previous hidden state to keep versus replace with newly calculated information.
- **Reset gate (r):** Controls how much of the previous hidden state to forget when computing the new candidate hidden state.

The new hidden state $h_t$ is a linear interpolation between the previous state $h_{t-1}$ and the new candidate $\tilde{h}_t$, weighted by the update gate. This simple design lets the model learn to remember long-range dependencies without the complexity of LSTM's separate cell state.

## 2.2 Specific Reasons for This Project

- **Parameter efficiency:** Our custom GRU model has **44,525 parameters**. This is ~25% fewer parameters than an equivalent LSTM (typically ~60K+), reducing overfitting risk on smaller datasets.
- **Training speed:** GRU trains ~20–30% faster than LSTM on CPU. Since this project runs on student laptops without discrete GPUs, training time is a practical constraint.
- **Equivalent accuracy:** For time-series prediction tasks with windows of 30–120 steps, empirical benchmarks (e.g., Chung et al. 2014) show GRU and LSTM achieve nearly identical accuracy.
- **Real-time inference:** GRU's simpler computation means lower latency per inference cycle — critical since the pipeline runs every 1 second (latency is just **0.9 ms** on CPU) on the main system being monitored.
- **ONNX export compatibility:** GRU exports cleanly to ONNX opset 17 with full dynamic batching support, enabling dynamic INT8 weight quantization to further shrink model size for the deployable application.

## 2.3 Our Model Architecture

- **Input:** `(batch, 60 timesteps, F features)` — F = 8 fixed columns + dynamic per-core columns.
- **GRU Stack:** `hidden_size=64, num_layers=2, dropout=0.2, batch_first=True`
- **Regression head:** `Linear(64→32) -> ReLU -> Dropout -> Linear(32→F) -> Sigmoid`
  - **Output:** Predicted feature vector at $t+1$ (next-second forecast).
- **Classification head:** `Linear(64→32) -> ReLU -> Dropout -> Linear(32→1) -> Sigmoid`
  - **Output:** Bottleneck probability (0 to 1) representing risk at $t+30$ seconds.
- **Loss:** Jointly trained with unweighted sum: `MSELoss(regression) + BCELoss(classification)`.

# 3. Data Collection (src/core/collector.py)

The collector runs as a standalone Python script that uses psutil to poll system metrics once per second. It uses a producer-consumer threading model for reliability: the producer samples metrics into a thread-safe queue, and the consumer drains the queue and writes to CSV.

## 3.1 Metrics Collected Per Sample

[TABLE]
Column | Description
timestamp | UTC ISO-8601 string — uniquely identifies each row
label | Session tag: idle | browsing | compiling | gaming
cpu_percent | Overall CPU utilisation % across all cores
cpu_freq_mhz | Current CPU clock frequency in MHz
mem_used_mb | Physical RAM currently in use (MB)
mem_available_mb | Physical RAM still available (MB)
mem_percent | RAM utilisation as a percentage
swap_used_mb | Swap / page file currently in use (MB)
swap_percent | Swap utilisation as a percentage
cpu_temp_c | Average CPU package temperature in °C (-1 if unavailable)
cpu_core_0 … cpu_core_N | Per-core CPU utilisation % (count varies by machine)
[/TABLE]

## 3.2 Collection Procedure

Sessions are collected under four real-world workload labels:
- **idle:** Machine on, no user activity, only background processes.
- **browsing:** Multiple browser tabs open, YouTube, document editing.
- **compiling:** Large Python/C++ compilation, package installs.
- **gaming:** 3D applications, stress tests simulating gaming loads.

Command used:
`python3 src/core/collector.py --label idle --duration 3600`

The `--duration` flag stops collection after N seconds. Without it, the script runs until Ctrl+C, writing to `data/telemetry_raw.csv` (appending, never overwriting, so multiple sessions accumulate in one file).

## 3.3 Producer-Consumer Design

The producer thread polls psutil every POLL_INTERVAL_SEC (1 second) and pushes samples into a thread-safe queue (max 500 entries). The consumer thread drains the queue and batch-writes to CSV every 60 rows for durability. If the queue fills up (consumer lagging), samples are dropped with a warning rather than blocking the producer — ensuring the OS is never slowed by the monitoring process itself.

## 3.4 Telemetry Dataset Augmentation

To prevent the custom 44,525-parameter GRU from overfitting on limited data, we developed a telemetry augmenter (`augment_dataset.py`) to systematically synthesize realistic system stress profiles, expanding the dataset from **1,500 baseline rows to 6,000 total rows** across 4 balanced phases:
1. **Phase 1: Real-World Baseline (1,500 rows)** — Normal Everyday desktop operations (idle, typing, browsing).
2. **Phase 2: CPU Stress Spikes (1,500 rows)** — Synthesized multi-threaded CPU stress tasks. CPU load dynamically surges to 75%–98% and temperature climbs to 88°C.
3. **Phase 3: RAM Leaks & Page Saturation (1,500 rows)** — Simulated severe memory leaks. Physical RAM climbs steadily from 45% to 94% and swap page allocation saturates to 92%.
4. **Phase 4: Cooldown & Recovery Decay (1,500 rows)** — Simulated closing of resource-heavy applications. CPU utilization decays rapidly to 8%–15%, RAM decays to 73%, and temperature drops back to 41°C.

# 4. Data Cleaning & Preprocessing (src/training/preprocess.py)

preprocess.py runs a five-stage pipeline before the data reaches the model trainer:

- **Stage 1 — Load & Deduplicate:** Reads `telemetry_raw.csv`, parses timestamps, drops exact duplicate timestamps, and sorts chronologically.
- **Stage 2 — Handle Missing Temperature:** CPU package temperature sensors are read via `psutil.sensors_temperatures()`. On sandboxed environments or virtual machines where sensors are restricted, a sentinel value of -1.0 is saved. Preprocessing replaces this with NaN and applies forward-filling. If no temperature data is present, a 50.0°C median placeholder is set.
- **Stage 3 — Generate Bottleneck Labels:** A binary label (`bottleneck_label`) is computed per row: 1 if ANY of the following is true at time $t + 30$ seconds:
  - `cpu_percent` >= 90%
  - `mem_percent` >= 85%
  - `cpu_temp_c` >= 85°C
- **Stage 4 — MinMax Normalisation:** A MinMaxScaler is fitted ONLY on the training portion (first 70% of rows, chronologically) to prevent data leakage. It is then applied to the full dataset. The fitted scaler is serialised to `models/scaler.pkl` (or fitted dynamically during the 90-second local calibration phase and stored as `~/.sro_optimizer/scaler_local_v2.pkl`) and loaded at inference time.
- **Stage 5 — Sliding Window Construction:** Segmented into overlapping sequences of W=60 samples sliding at step S=1. Each window produces an input tensor X of shape `(60, F)` and regression/classification targets.

## 4.1 Chronological Split

Splits are chronological (not shuffled) to prevent future data leaking into training:
- **Train:** first 70% of windows (4,137 windows)
- **Validation:** next 15% (886 windows) — used for early stopping and LR scheduling
- **Test:** final 15% (887 windows) — held out until final evaluation, never touched during training

# 5. Model Training (src/training/train.py)

The training script loads the windowed arrays, instantiates the `ResourceGRU` model, and runs training with early stopping.

## 5.1 Training Configuration

[TABLE]
Hyperparameter | Value / Detail
Optimiser | Adam (lr=1e-3)
LR Scheduler | ReduceLROnPlateau: halves LR if val_loss does not improve for 5 epochs
Loss — Regression | MSELoss on normalised feature vectors
Loss — Classification | BCELoss on bottleneck label (equal weight to regression loss)
Early Stopping | Patience=10 epochs: training stops if val_loss does not improve for 10 epochs
Gradient Clipping | max_norm=1.0 — prevents exploding gradients in the GRU
Batch Size | 64
Max Epochs | 100 (usually stops earlier; halted at Epoch 11 restoring Epoch 1 checkpoint)
[/TABLE]

## 5.2 Evaluation Metrics

The restored model was evaluated on the 887 unseen windows in the test set, demonstrating near-perfect metrics:
- **Validation Accuracy:** 98.5% | **Test Accuracy:** 100.0%
- **Validation F1 Score:** 98.6% | **Test F1 Score:** 1.000
- **Validation ROC-AUC:** 1.000 | **Test ROC-AUC:** 1.000
- **Test Set MAE:** 0.2309
- **Confusion Matrix:** True Negatives = 554, False Positives = 0, False Negatives = 0, True Positives = 333 (0 false predictions).

## 5.3 ONNX Export & INT8 Quantisation

After PyTorch training, the graph is exported to ONNX format (opset 17). Dynamic INT8 weight quantisation is then applied using `onnxruntime.quantization.quantize_dynamic()`, shrinking the ONNX graph from **0.182 MB to 0.175 MB** with negligible loss in accuracy.

# 6. Library Reference — All Dependencies Explained

- **psutil (>= 5.9):** Cross-platform system metrics library. Used to read CPU %, per-core %, CPU frequency, RAM usage, swap usage, CPU temperature, and running process information. Also provides process suspension (`proc.suspend()`) and resumption (`proc.resume()`) for the action engine.
- **numpy (>= 1.24):** Core numerical array library. Used for building sliding-window arrays, normalisation arithmetic, computing MAE/RMSE, and passing float32 tensors to the ONNX session.
- **pandas (>= 2.0):** DataFrame library used for loading and cleaning the raw CSV, parsing timestamps, deduplication (`drop_duplicates`), forward/back-fill of missing temperatures, and computing statistics.
- **scikit-learn (>= 1.3):** Machine learning utilities. `MinMaxScaler` normalises all features to $[0,1]$. Accuracy, F1-score, ROC-AUC, and Confusion Matrix functions are used in the test evaluation report.
- **torch (PyTorch >= 2.0):** Deep learning framework. Provides `nn.GRU`, `nn.Linear`, `nn.Sequential`, optimisers (`Adam`), loss functions (`MSELoss`, `BCELoss`), data loading utilities, and ONNX export.
- **onnx (>= 1.14):** Open Neural Network Exchange format library. Used to serialize the GRU model into a standardized cross-platform format.
- **onnxruntime (>= 1.16):** Optimised ONNX inference engine. Loads the quantized ONNX model on a dedicated background thread, restricted strictly to a single core (`intra_op_num_threads=1`) to consume **less than 2% CPU overhead**.
- **flet (>= 0.22):** Python bindings for the Flutter rendering engine. Powers the entire user interface, rendering a modern glassmorphic dashboard smoothly at 60 FPS without Qt signal/slot dependencies.
- **Flet Canvas (Native):** High-performance vector drawing engine built directly into Flet (using `ft.canvas.Canvas`). Used for drawing the three real-time rolling charts: CPU, Memory, and Temperature history. It renders new data points efficiently via path buffers.
- **plyer (>= 2.1):** Cross-platform desktop notification library. Sends native system toast notifications asynchronously on a short-lived thread when background suspensions or boosts occur.
- **pickle (stdlib):** Python standard library serialisation. Saves the fitted MinMaxScaler object to `models/scaler.pkl` (and local calibrations to `scaler_local_v2.pkl`) and reloads it in the pipeline.
- **threading (stdlib):** Handles the application's multi-threaded background loops: the 1Hz telemetry ingestion thread, the Action Engine watchdog daemon thread, and async notification spawns.
- **collections.deque (stdlib):** High-performance rolling queue buffer used to maintain the 60-second sliding input window in the pipeline and the 120-sample rolling history in the Canvas UI charts.

# 7. End-to-End Project Workflow

- **Step 1 — Collect:** `python3 src/core/collector.py --label idle` (collects raw telemetry into `data/telemetry_raw.csv`).
- **Step 2 — Preprocess:** `python3 src/training/preprocess.py` (cleans, imputes, labels, normalizes, and saves sequence windows to `data/windows.npz`).
- **Step 3 — Train:** `python3 src/training/train.py` (trains stacked GRU in PyTorch, quantizes to INT8, and exports to `models/gru_quantized.onnx` and `scaler.pkl`).
- **Step 4 — Run App:** `python3 src/main.py` (launches Flet dashboard with background pipeline running GRU inference, Autopilot triggers, Whitelist process suspensions, and event logs).
