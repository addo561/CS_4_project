KWAME NKRUMAH UNIVERSITY OF SCIENCE AND TECHNOLOGY

Faculty of Physical and Computational Sciences

Department of Computer Science

Dataset Collection & Preprocessing

Technical Documentation — Module A

Project: Lightweight AI-Powered System Resource Optimizer

Prepared by:

Lamptey Kwaku Abednego — 3398122

Tugbah Lily Ama Mawuena — 3416522

Korli Larry Addo — 3395922

Date: May 2026

# 1. Introduction

High-quality, representative telemetry data is the foundation upon which the Lightweight AI-Powered System Resource Optimizer's predictive capabilities are built. Before any machine learning model can be trained to forecast CPU and memory bottlenecks, a structured dataset capturing the natural variation of Windows system behaviour across diverse workload conditions must be systematically collected and curated.

This document details the complete data collection and preprocessing pipeline developed for Module A of the project. It covers the rationale behind feature selection, the collection methodology employed, the schema of the resulting dataset, the preprocessing steps applied to prepare the data for time-series model training, and the dataset splitting strategy used to enable unbiased model evaluation.

The data collection infrastructure is implemented in Python using the psutil library (version 5.9+), which provides a cross-platform interface to system-level process and hardware metrics. The resulting dataset serves as the sole input to the preprocessing pipeline described in Section 5.

# 2. Feature Selection and Rationale

The selection of telemetry features was driven by three criteria: (1) direct relevance to the system performance phenomena the model must predict, (2) availability through the psutil library on standard Windows hardware without additional drivers, and (3) low collection overhead to prevent the monitoring tool from contributing to the resource burden it is designed to detect.

## 2.1 Primary CPU Features

- **cpu_percent:** Captures the overall CPU utilisation aggregated across all logical cores. This single figure is the primary signal for imminent CPU-bound bottlenecks.
- **cpu_core_N:** Columns capture per-core utilisation, providing spatial resolution that allows the model to distinguish between a single-threaded workload saturating one core and a multi-threaded workload distributing load evenly.
- **cpu_freq_mhz:** Records the real-time operating frequency of the processor, which drops during thermal throttling — a critical early warning signal.

## 2.2 Memory Features

- **mem_used_mb** and **mem_available_mb:** Represent the absolute memory consumption and headroom in megabytes.
- **mem_percent:** Expresses the same relationship as a normalised ratio and is the primary threshold used for labelling.
- **swap_used_mb** and **swap_percent:** Capture page-file activity, which is a strong lagging indicator that physical memory has been exhausted and performance has already degraded significantly.

## 2.3 Thermal Feature

- **cpu_temp_c:** Records the average CPU package temperature in degrees Celsius, sourced via psutil.sensors_temperatures() or the high-fidelity ThermalSimulator fallback when hardware sensors are locked (e.g. on virtual machines or sandboxed environments). Thermal throttling occurs when the processor exceeds its thermal design threshold (typically 85–105 °C), causing a forced reduction in clock speed. Including temperature allows the model to learn the thermal lead-up patterns that precede throttling events.

[TABLE]
Feature | Unit | Source | Relevance
cpu_percent | % | psutil.cpu_percent() | Overall CPU load — primary prediction target
cpu_freq_mhz | MHz | psutil.cpu_freq() | Throttling indicator
cpu_core_N | % | psutil.cpu_percent(percpu=True) | Per-core load distribution
mem_used_mb | MB | psutil.virtual_memory().used | Absolute memory pressure
mem_available_mb | MB | psutil.virtual_memory().available | Available headroom
mem_percent | % | psutil.virtual_memory().percent | Memory utilisation ratio
swap_used_mb | MB | psutil.swap_memory().used | Page-file overflow indicator
swap_percent | % | psutil.swap_memory().percent | Swap utilisation ratio
cpu_temp_c | °C | psutil.sensors_temperatures() | Thermal throttle precursor
[/TABLE]

Table 2.1: Selected telemetry features, their units, psutil source calls, and relevance to the prediction task.

# 3. Data Collection Methodology

## 3.1 Sampling & Threading Architecture

The collector module (`collector.py`) implements a robust **producer-consumer threading architecture** to capture high-fidelity system telemetry without interrupting system processes:

1. **Producer Thread:** Runs a continuous loop polling psutil at a highly accurate sampling interval of **1.0 second** (POLL_INTERVAL_SEC). The `psutil.cpu_percent(interval=None)` call is non-blocking to prevent latency accumulation. It packages features into a dictionary and pushes them into a thread-safe `queue.Queue` of maximum capacity 500.
2. **Consumer Thread:** Runs in parallel, draining the queue and batch-writing samples as CSV rows to disk. Batches are flushed every **60 samples** (FLUSH_EVERY_N = 60), protecting data in the event of an unexpected application crash while ensuring low disk I/O write overhead.
3. **Graceful Queue Handling:** If the consumer thread stalls and the queue exceeds 500 samples, the producer thread automatically drops older samples with a warning, guaranteeing that the monitoring process itself never blocks OS execution or wastes resources.

## 3.2 System State Labels

To ensure the training dataset captures the full behavioural range of a standard desktop system, data is collected under four distinct system states. Each collection session is tagged with a string label that is stored in a dedicated 'label' column in the CSV. This label is not used as a model input feature but serves as a metadata field for dataset analysis and class-balance auditing.

[TABLE]
State Label | Description | Target Duration | Expected CPU Range
idle | System at rest; only background Windows services running | 30 min | 0 – 15%
browsing | Active web browsing with multiple tabs; occasional video playback | 30 min | 10 – 45%
compiling | Full project compilation (e.g., large Python/C++ codebase) | 20 min | 60 – 100%
gaming | GPU-intensive game running; high sustained CPU/memory usage | 30 min | 50 – 95%
[/TABLE]

Table 3.1: System state labels, their operational definitions, recommended collection duration, and typical CPU utilisation range.

# 4. Telemetry Dataset Augmentation

## 4.1 Rationale (1,500 vs. 6,000 Rows)

A critical challenge encountered in building the predictive engine was the risk of model overfitting. Our custom GRU neural network consists of **44,525 learnable parameters**. Collecting raw telemetry at 1Hz over a brief session yields approximately 1,500 rows, representing 25 minutes of system activity. If trained directly on this small sample, the GRU network tends to overfit, memorizing specific system noise and failing to generalize to new workload regimes.

To overcome this, we developed a 4-phase telemetry augmenter (`augment_dataset.py`) to systematically synthesize realistic system stress states, expanding the dataset from **1,500 baseline rows to 6,000 total rows** (representing 1 hour and 40 minutes of system activity).

## 4.2 The 4-Phase Telemetry Augmentation Design

The augmented 6,000-row dataset is structured into four distinct chronological phases of equal length (1,500 rows each):

1. **Phase 1: Real-World Baseline (1,500 rows)** — The actual baseline system telemetry collected under normal everyday desktop operations (idle, typing, sporadic browser tabs).
2. **Phase 2: Heavy CPU Stress Spikes (1,500 rows)** — Synthesized multi-threaded CPU stress tasks. CPU load dynamically surges to 75%–98%, core loads peak, and package temperature climbs according to first-order lag smoothing to a peak of 88°C, simulating compile-heavy or gaming workloads.
3. **Phase 3: RAM Leaks & Swap Page Saturation (1,500 rows)** — Simulated severe memory leaks. Physical RAM consumption (`mem_percent`) climbs steadily from 45% up to 94%, available memory drops, and swap page allocation saturates to 92%, causing high disk activity.
4. **Phase 4: Cooldown & Recovery Decay (1,500 rows)** — Simulated closing of resource-heavy applications. CPU utilization drops rapidly to 8%–15%, RAM usage decays exponentially back to 73%, and package temperature drops back to a safe baseline of ~41°C.

This balanced distribution exposes the GRU model to critical temporal transition boundaries, allowing it to predict bottlenecks before they actually arrive.

# 5. Preprocessing Pipeline

The preprocessing pipeline is implemented in `preprocess.py` and transforms the raw telemetry CSV into normalised, windowed NumPy arrays.

```
[Raw CSV Ingestion] 
       │
       ▼
[Deduplication & Sorting] ──► Removes duplicate timestamps, chronologically orders rows
       │
       ▼
[Imputation]              ──► Imputes missing cpu_temp_c via forward-fill/50.0°C median
       │
       ▼
[Label Generation]        ──► Calculates bottleneck_label (1/0) 30s ahead
       │
       ▼
[Train MinMaxScaler]      ──► Fitted exclusively on Train split (70%) to avoid data leakage
       │
       ▼
[Sliding Window]          ──► Window W=60s, Step S=1s, Horizon H=30s
       │
       ▼
[Chronological Split]     ──► Contiguous split: 70% Train, 15% Val, 15% Test → data/windows.npz
```

## 5.1 Deduplication and Sorting

Rows with duplicate timestamps are removed to eliminate samples resulting from system clock resets or session-overlap artefacts. The DataFrame is then sorted chronologically by timestamp to enforce the temporal ordering required by the sliding window algorithm.

## 5.2 Missing Value Imputation

The sentinel value of -1.0 stored for cpu_temp_c when hardware sensors are unavailable is replaced with NaN and then forward-filled — carrying the most recent valid reading forward in time. If no valid temperature readings exist in the entire dataset (e.g., when collected on a virtual machine), the column is populated with a fixed placeholder of 50.0 °C, representing a typical idle desktop temperature. Any remaining NaN values in other feature columns are filled with the column-wise median to minimise distributional distortion.

## 5.3 Bottleneck Label Generation

A binary classification label (bottleneck_label) is derived for each row based on the system state LABEL_HORIZON steps (30 seconds) into the future. A label of 1 is assigned if any of the following threshold conditions are satisfied at the future timestep: cpu_percent ≥ 90%, mem_percent ≥ 85%, or cpu_temp_c ≥ 85 °C. The final LABEL_HORIZON rows of the dataset are discarded as their future states are undefined.

## 5.4 Feature Normalisation

All feature columns are scaled to the range [0, 1] using scikit-learn's MinMaxScaler. Critically, the scaler is fitted exclusively on the training portion of the dataset (the first 70% of rows in chronological order) and subsequently applied to the validation and test sets. This strict train-only fitting prevents data leakage — ensuring that future statistics do not influence the model's input representation during evaluation. The fitted scaler object is serialised to models/scaler.pkl and must be loaded at inference time to transform real-time telemetry using identical parameters.

## 5.5 Sliding Window Construction

The normalised feature matrix is segmented into overlapping windows using a sliding window algorithm with configurable parameters: window size W = 60 samples (representing 60 seconds of history), step size S = 1 (one new window per new sample), and label horizon H = 30 (predicting 30 seconds ahead). Each window produces one training example: an input tensor X of shape (W, F) where F is the number of features, a regression target y_reg of shape (F,) representing the feature vector at the next timestep, and a classification target y_clf — the bottleneck label at timestep t+H.

# 6. Dataset Split Strategy

Given the time-series nature of the data, a chronological (non-shuffled) split is employed to preserve temporal ordering and prevent look-ahead bias. Random shuffling is explicitly avoided, as it would allow the model to observe future system states during training, producing misleadingly optimistic evaluation metrics. The dataset is partitioned into three contiguous segments:

[TABLE]
Split | Proportion | Purpose
Training | 70% | Model weight optimisation via backpropagation
Validation | 15% | Hyperparameter tuning and early stopping
Test | 15% | Unbiased performance evaluation (held out)
[/TABLE]

Table 6.1: Chronological dataset split proportions. All splits are contiguous in time.

The three splits are stored as compressed NumPy arrays in data/windows.npz with keys X_train, X_val, X_test, y_reg_train, y_reg_val, y_reg_test, y_clf_train, y_clf_val, and y_clf_test. This file serves as the sole input to the model training script (train.py).

# 7. Dataset Statistical Summary

The following table presents descriptive statistics for each feature computed over the full pre-scaled, 6,000-row augmented dataset:

[TABLE]
Feature | Mean | Std Dev | Min | Max | Notes
cpu_percent | 42.15 % | 28.34 % | 0.20 % | 98.80 % | Spans idle to heavy stress
cpu_freq_mhz | 2845.20 MHz | 620.15 MHz | 1200.00 MHz | 3900.00 MHz | Fluctuates with core spikes
mem_used_mb | 6842.10 MB | 2154.60 MB | 3120.00 MB | 15040.00 MB | Dynamic RAM consumption
mem_available_mb | 9541.90 MB | 2154.60 MB | 1344.00 MB | 13264.00 MB | Derived from 16 GB baseline RAM
mem_percent | 41.76 % | 13.15 % | 19.04 % | 91.80 % | RAM utilisation ratio
swap_used_mb | 1145.30 MB | 985.40 MB | 0.00 MB | 6140.00 MB | Page-file active memory spill
swap_percent | 18.20 % | 15.68 % | 0.00 % | 92.40 % | Page-file allocation ratio
cpu_temp_c | 54.85 °C | 15.42 °C | 38.00 °C | 91.20 °C | Smooth lag smoothing curve
[/TABLE]

Table 7.1: Pre-scaling feature summary statistics computed over the augmented 6,000-row dataset.

[TABLE]
Dataset Property | Value
Total samples collected | 6,000 rows
Samples after deduplication | 6,000 rows
Bottleneck-positive labels (=1) | 2,250 rows (37.5%)
Bottleneck-negative labels (=0) | 3,750 rows (62.5%)
Number of input features (F) | 16 features (8 core + 8 logical core channels)
Total sliding windows | 5,910 windows (WINDOW_SIZE = 60, Horizon = 30)
Training windows | 4,137 windows
Validation windows | 886 windows
Test windows | 887 windows
[/TABLE]

Table 7.2: Summary of final windowed dataset dimensions, split sizes, and bottleneck label balances.

# 8. References

[1] Rodolà, G. (2024). psutil — Cross-platform library for retrieving information on running processes and system utilization. https://psutil.readthedocs.io/

[2] Pedregosa, F. et al. (2011). Scikit-learn: Machine Learning in Python. Journal of Machine Learning Research, 12, 2825–2830.

[3] Cho, K. et al. (2014). Learning Phrase Representations using RNN Encoder-Decoder for Statistical Machine Translation. EMNLP 2014.

[4] Box, G. E. P., Jenkins, G. M., Reinsel, G. C., & Ljung, G. M. (2015). Time Series Analysis: Forecasting and Control (5th ed.). Wiley.

[5] Hyndman, R. J., & Athanasopoulos, G. (2021). Forecasting: Principles and Practice (3rd ed.). OTexts. https://otexts.com/fpp3/
