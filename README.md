# ⚡ System Resource Optimizer
### KNUST Final Year Project — Group 4

An AI-powered desktop application that monitors your computer's resources in real time, predicts performance bottlenecks before they happen, and automatically optimises your system — all from a modern, responsive dashboard.

---

## 🚀 What Happens When You Open the App

### 1. The Flet Dashboard Launches
The moment you run the application, a sleek dark-themed dashboard built with **Flet** opens. It is organized into three distinct pages accessible from a responsive left-hand navigation sidebar:

- **Dashboard Page**:
  - **AI Circular Gauge Ring** (top-left) — Displays the GRU model's real-time bottleneck confidence score (0–100%) with dynamic, severity-based color grading (Green < 65%, Amber 65–84%, Red ≥ 85%) and detailed natural-language insights.
  - **Metric Tiles Grid** (top-right) — Displays live CPU %, Memory %, Temperature, and Swap usage with color-coded progress bars.
  - **Real-Time Rolling Charts** (middle) — Three GPU-accelerated charts rendered via Flet Canvas updating every second, displaying the last 2 minutes of CPU, Memory, and Temperature history.
  - **Running Processes DataTable** (bottom) — Shows the top 20 resource-consuming processes updated every 3 seconds.
  - **Right Control Rail** — Groups manual actions (One-Click Boost, Undo), Auto-Pilot toggle, Active Suspended process list, and the event-driven active system log.
- **Analytics Page**: Surfaces in-depth system metrics, detailed classification labels, risk parameters, and active model feature sets.
- **Settings Page**: Houses engine parameter indicators, calibration settings, and autopilot policy guidelines.

---

## 🧠 Inference vs. Training on Launch: How it Runs

A common question is: **Does the app train its AI model when you open it?**

> [!IMPORTANT]
> **No, the app never trains on launch.** It operates strictly in **Inference Mode (Forward Pass only)**.
>
> Training a deep learning model is computationally intensive, requiring high CPU/GPU resources and time. Doing so on launch would defeat the purpose of an optimizer by creating a massive performance bottleneck.

### The Lifecycle of the Model
1. **Offline Training (Developer Phase)**: The model is designed as a custom **Gated Recurrent Unit (GRU)** neural network. It is trained offline on pre-collected telemetry datasets using PyTorch (`src/training/train.py`).
2. **Quantization & Export**: The fully trained model weights are exported to the Open Neural Network Exchange (ONNX) format and quantised to 8-bit integers (`src/models/gru_quantized.onnx`). This shrinks the model size to just ~0.17 MB.
3. **App Startup & ONNX Runtime (User Phase)**:
   - When you open the dashboard, the app initializes a single-threaded **ONNX Runtime session** bound to a dedicated background daemon thread.
   - The session is strictly restricted to a single CPU core, consuming **less than 2% CPU overhead** to ensure the optimizer itself never slows your system.
4. **The Warm-Up Sequence**:
   - For the first **60 seconds**, the background thread polls system telemetry using `psutil` once per second and populates a sliding-window queue (`collections.deque`).
   - During this window-filling phase, the AI Gauge Ring shows **0% Confidence (Analyzing system...)** because the GRU requires a historical sequence of 60 consecutive seconds to make an accurate temporal prediction.
5. **Real-Time Inference (1Hz)**:
   - Once 60 seconds of history accumulate, the pipeline feeds the normalized window into the pre-trained GRU model every second.
   - The model runs a quick mathematical forward pass and outputs:
     - A **bottleneck confidence score** (0.0 to 1.0) indicating how likely your system is to experience severe slowdowns in the next 30 seconds.
     - The predicted **CPU %** and **Memory %** values at the 30-second future horizon.
   - Results are pushed into a thread-safe Queue, which Flet regularly consumes to update the UI elements without blocking the rendering thread.

---

## 🔬 Why Calibrate on a New Machine?

When the application is run on a new computer for the first time, it automatically initiates a **90-second silent calibration phase** visible on the central Circular Gauge.

### The Problem: Hardware Divergence
The GRU model was originally trained on telemetry collected from a specific developer machine. Every computer has fundamentally different hardware limits:
- An ultra-book with a 4-core CPU idles and peaks differently than a high-end 16-core desktop.
- Different RAM capacities (e.g., 8 GB vs. 64 GB) mean a 4 GB load represents a 50% critical pressure on one machine but only 6% idle on another.
- Thermal boundaries and clock speed scaling vary heavily by device and manufacturer.

If we normalized live telemetry using the bundled MinMaxScaler fitted on the developer's system, the normalized features fed into the GRU would be completely skewed, leading to out-of-distribution values and rendering the AI predictions inaccurate.

### The Solution: Domain Adaptation via Local Calibration
To adapt the AI to *your* specific hardware without retraining the neural network, the app performs a local **calibration**:
- **Fixed Model Weights**: The pre-trained temporal patterns learned by the GRU (such as spike curves and resource build-up signatures) are universal and remain locked.
- **Re-Fitted Scaler**: The normalisation layer is adapted. For the first 90 seconds of a new installation, the app logs the system's baseline ranges.
- **Calibration File**: At 90 seconds, the app fits a new, personalized `MinMaxScaler` and serializes it to **`~/.sro_optimizer/scaler_local.pkl`** in the user's home directory.
- **Subsequent Runs**: On all future launches, the app detects this local file, skips calibration entirely, and begins active GRU inference within 60 seconds.

*Note: You can delete the `~/.sro_optimizer/scaler_local.pkl` file at any time to force the application to re-calibrate to new hardware configurations.*

---

## 🌡️ Real-Time System Temperature: Calculation & Simulation

A critical component of system health telemetry is monitoring the CPU temperature. The application uses a robust hybrid architecture to calculate and display the system temperature, ensuring maximum reliability across different operating systems and hardware configurations.

### 1. Physical Hardware Sensors (Primary Source)
On supported operating systems (such as Linux) with access to physical hardware sensors, the system queries physical hardware temperatures using the `psutil.sensors_temperatures()` API.
* **Sensor Hierarchy**: The system looks for common CPU-bound sensor keys in the following priority order:
  1. `coretemp` (Intel CPUs)
  2. `k10temp` (AMD CPUs)
  3. `acpitz` (ACPI thermal zones)
  4. `cpu_thermal` / `cpu-thermal` (Raspberry Pi/ARM devices)
* **Aggregation**: When multiple cores or sensor channels are detected, the system calculates the **average temperature** across all reporting entries to provide a stable, single-system index.
* **Hardware Fallback**: If no standard hardware keys are found, it averages all reporting temperature entries across all system sensors.

### 2. High-Fidelity Thermal Simulator (Fallback & VM Telemetry)
In many common user environments, reading raw hardware temperature sensors directly is restricted or impossible. This occurs on:
* **Virtual Machines & Sandboxes** (where hardware registers are abstracted).
* **Standard Windows Installations** (which require administrative privileges or kernel-level drivers like Open Hardware Monitor to expose raw temperatures via WMI).
* **Certain Laptop architectures** with proprietary ACPI configurations.

To prevent telemetry gaps, the application features an in-house developed **ThermalSimulator** that mathematically models CPU heat accumulation, thermal dispersion, and memory heat dissipation.

#### The Simulation Algorithm:
The simulator implements a **First-Order Lag Filter (Exponential Smoothing)** that models thermal inertia (the time delay between system load spikes and heat transfer).

1. **Target Temperature Calculation ($T_{\text{target}}$)**:
   The baseline temperature is anchored at an idle average of $38.0^\circ\text{C}$. The target temperature adjusts dynamically based on instantaneous CPU utilization ($U_{\text{cpu}}$) and Memory usage ($U_{\text{mem}}$):
   $$T_{\text{target}} = 38.0 + (U_{\text{cpu}} \times 0.45) + (U_{\text{mem}} \times 0.1)$$
   *For example, if your CPU is at 100% load and memory is at 80% load, the target temperature rises to $38.0 + 45.0 + 8.0 = 91.0^\circ\text{C}$.*

2. **Temporal Heat Soak & Smoothing ($T_{t+1}$)**:
   Physical heat does not jump instantly. To simulate thermal resistance and heat soaking, the current simulated temperature $T_t$ transitions toward $T_{\text{target}}$ by $10\%$ on each 1Hz update cycle:
   $$T_{t+1} = T_t + (T_{\text{target}} - T_t) \times 0.1$$
   This produces a highly realistic, smooth rolling curve on the Flet Canvas charts that mirrors physical CPU heat-up and cooldown delay curves.

---

## 🎛️ Dashboard Controls

| Control | What It Does |
|---|---|
| 🤖 **Auto-Pilot Switch** | Toggle autonomous mode in the Settings page or the Right Rail. When ON, the optimizer automatically invokes the One-Click Boost routine whenever AI confidence is $\ge 80\%$ (limited by a 45s safety cooldown). |
| 🚀 **One-Click Boost** | Manually triggers an immediate optimization event: resumes all suspended processes, runs Python garbage collection, and clears memory. |
| ↩ **Undo Button** | Immediately restores all processes that were suspended during the current session. Disabled when no processes are suspended. |

### The Action Engine: Safe Process Suspension
When confidence crosses the 80% threshold in Auto-Pilot mode, the optimizer:
1. Iterates through active processes and screens them against a strict, hard-coded **System Whitelist** (protecting core operating system kernel services, security binaries, desktop window managers, and the optimizer itself).
2. Suspends the top 3 non-whitelisted CPU consumers using `psutil.Process(pid).suspend()`.
3. Auto-resumes any suspended processes after a **5-minute safety watchdog timeout** to guarantee no user process remains suspended indefinitely, even if the application is closed.

---

## 📁 Project Structure

All application components are organized within a structured workspace layout:

```
Final_year/
├── src/
│   ├── main.py            # Modern Flet-based dashboard (primary entry point)
│   ├── main_backup.py     # PyQt6-based fallback dashboard
│   ├── config.py          # Central settings, constants, and Whitelists
│   ├── core/
│   │   ├── pipeline.py       # Live 1Hz telemetry polling & inference coordination
│   │   ├── action_engine.py  # Thread-safe process suspend/resume watchdog
│   │   ├── collector.py      # Independent producer-consumer telemetry logger
│   │   └── notifier.py       # Asynchronous cross-platform OS notifications
│   ├── data/
│   │   ├── telemetry_raw.csv   # Raw telemetry gathered during collector runs
│   │   ├── telemetry_clean.csv # Deduplicated and cleaned telemetry
│   │   └── windows.npz         # Saved sliding-window sequences for training
│   ├── models/
│   │   ├── gru_checkpoint.pt   # Raw PyTorch model weight state dictionary
│   │   ├── gru_fp32.onnx       # Full-precision standard ONNX graph export
│   │   ├── gru_quantized.onnx  # INT8 quantized lightweight ONNX model used for live inference
│   │   └── scaler.pkl          # Reference MinMaxScaler fitted on baseline system
│   └── training/
│       ├── gru_model.py      # PyTorch GRU Neural Network class definition
│       ├── preprocess.py     # Training set sequence building & labeling logic
│       └── train.py          # PyTorch optimizer training loop with Early Stopping
├── docs/
│   ├── dataset_collection.docx # Document A: System feature selection & collection telemetry
│   ├── model_documentation.docx # Document B: GRU gate equations & model training metrics
│   ├── data_pipeline.docx      # Document C: Live data scaling, ONNX session, & action watchdog
│   ├── ui_documentation.docx   # Document D: Flet layout, canvas charts, and thread-safe queues
│   └── project_documentation.docx # Complete academic project compendium and reference
├── requirements.txt       # Core Python library dependencies
├── Run_Windows.bat        # Double-click script to run modern Flet on Windows
├── Run_macOS.command      # Double-click script to run modern Flet on macOS
└── README.md              # This comprehensive system guide
```

---

## 🔧 Running From Source

Follow these sequential steps to run, train, or collect data using the system optimizer:

### 1. Environment Setup
Install the necessary system and machine learning libraries:
```bash
pip install -r requirements.txt
```

### 2. Live Telemetry Logging (Optional)
Collect local system data under custom baseline labels:
```bash
python3 src/core/collector.py --label idle --duration 3600
```

### 3. Preprocessing (Optional)
Deduplicate raw telemetry, handle sensor gaps, and structure sliding windows:
```bash
python3 src/training/preprocess.py
```

### 4. Offline Model Training (Optional)
Train a new custom GRU model in PyTorch and export to quantized ONNX format:
```bash
python3 src/training/train.py
```

### 5. Launching the App
Start the high-performance Flet dashboard:
```bash
python3 src/main.py
```

---
*KNUST Final Year Project — Group 4 | Built with Flet, PyTorch, ONNX Runtime, and psutil*
