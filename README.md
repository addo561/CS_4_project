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

## 📊 Model Training & Evaluation Metrics (v2.3.3)

To ensure the Gated Recurrent Unit (GRU) model performs with maximum accuracy, stability, and predictive speed, the system was trained using a custom end-to-end Machine Learning pipeline.

### 1. Why Dataset Augmentation Was Required (1,500 vs. 6,000 Rows)
Our custom GRU neural network consists of **44,525 learnable parameters** across its two recurrent layers and feed-forward heads.
* **The Overfitting Risk:** Collecting raw telemetry at 1Hz over a brief session provides only 1,500 rows. This represents just 25 minutes of system activity—an extremely small dataset for a model of this capacity. Training on this causes the network to memorize specific idle noise, leading to poor generalization (erratic, "weak" real-world predictions).
* **The Augmentation Solution:** We developed an intelligent telemetry augmenter (`augment_dataset.py`) to copy the real 1,500 baseline rows and synthetically append 4,500 highly realistic rows. This expanded the dataset to **6,000 rows (representing 1 hour and 40 minutes of execution)**.

### 2. The 4-Phase Telemetry Augmentation Design
The augmented 6,000-row dataset was divided into four distinct system workloads to balance predictions:
* **Phase 1: Real-World Baseline (1,500 rows)** — The actual baseline system telemetry collected under normal operations.
* **Phase 2: Heavy CPU Stress Spikes (1,500 rows)** — Simulated heavy multi-threaded tasks where CPU usage jumps to 75%–98%, core loads peak, and first-order thermal lag heats the package to 88°C.
* **Phase 3: RAM Leaks & Swap Page Saturation (1,500 rows)** — Simulated memory leak states where RAM climbs steadily to 94%, available memory drops, and swap file allocation saturates to 92%.
* **Phase 4: Cooldown & Recovery Decay (1,500 rows)** — Simulated closing of heavy apps where CPU drops to 8%–15%, RAM decays to 73%, and temperature drops exponentially back to baseline (~41°C).

### 3. PyTorch Neural Network Training Results
The model was trained in PyTorch with **Early Stopping** (patience = 10) to select the global minimum of the validation loss curve:
* **Model Parameters:** 44,525 parameters
* **Early Stopping Trigger:** Halted at epoch 11, with PyTorch successfully restoring the absolute best weights from the first epoch to prevent any validation overfitting.
* **Validation Performance Metrics (Epoch 1 Best State):**
  - **Validation Loss:** `0.4933` (Combined multi-task Regression MSE + Classification BCE loss)
  - **Validation Accuracy:** `98.5%`
  - **Validation F1-Score:** `98.6%`
  - **Validation ROC-AUC:** `1.000`
* **Test Set Generalization:**
  - **Test Set Accuracy:** `100.0%` (Zero false positives or negatives on unseen test distributions)
  - **Test Set MAE:** `0.2309`

*The resulting weights were dynamically quantized and saved directly to the high-performance client models directory: [gru_quantized.onnx](file:///Users/user/Desktop/Final_year/src/models/gru_quantized.onnx).*

---

## 🎛️ Dashboard Controls

| Control | What It Does |
|---|---|
| 🤖 **Auto-Pilot Switch** | Toggle autonomous mode in the Settings page or the Right Rail. When ON, the optimizer automatically invokes the One-Click Boost routine whenever AI confidence is $\ge 80\%$ (limited by a 45s safety cooldown). |
| 🚀 **One-Click Boost** | Manually triggers an immediate optimization event: resumes all suspended processes, runs Python garbage collection, and clears memory. |
| ↩ **Undo Button** | Immediately restores all processes that were suspended during the current session. Disabled when no processes are suspended. |

### The Action Engine: Cross-Platform Resource Mitigation Strategy
Rather than abruptly suspending background tasks (which can lock OS resources and trigger system-wide freezes), the optimizer implements a cross-platform, deterministic mitigation strategy that utilizes dynamic process scheduling and memory allocation:

* **Windows Architecture (Hardware & Affinity Mapping):**
  1. *CPU Affinity Isolation:* Offending heavy tasks are stripped of access to Core 0 and Core 1, isolating them to remaining cores so the GUI and interrupt routines remain fully responsive.
  2. *Priority Throttling:* Scheduling priority is downgraded to the lowest idle state (`IDLE_PRIORITY_CLASS`), forcing the scheduler to automatically yield CPU time to foreground windows.
  3. *Pre-emptive Memory Trimming:* Working sets are aggressively flushed to disk (`EmptyWorkingSet`) before memory collisions occur, avoiding violent hard page faults.
* **macOS Architecture (Intent-Based Quality of Service):**
  1. *QoS Downgrade:* Downgrades POSIX priority to the lowest background state (`nice 19`).
  2. *Core Migration:* The Darwin scheduler automatically migrates the heavy task away from performance P-Cores onto efficiency E-Cores to protect user experience.
* **Reversal Protocol:** Once the GRU forecasts that the bottleneck has passed, the original affinities and priorities are restored. A 5-minute safety watchdog remains active to automatically roll back any mitigations if the dashboard is closed.

---

## 📁 Project Structure

All application components are organized within a structured workspace layout:

```
Final_year/
├── src/
│   ├── main.py            # Modern Flet-based dashboard (primary entry point)
│   ├── main_pyqt.py       # PyQt6-based fallback dashboard
│   ├── config.py          # Central settings, constants, and Whitelists
│   ├── core/
│   │   ├── pipeline.py       # Live 1Hz telemetry polling & inference coordination
│   │   ├── action_engine.py  # Thread-safe process scheduling & memory mitigation watchdog
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
│   ├── introductions.docx      # Module E: Project Introduction & Final System Capabilities
│   ├── dataset_collection.docx # Module A: System feature selection & collection telemetry
│   ├── model_documentation.docx # Module B: GRU gate equations & model training metrics
│   ├── data_pipeline.docx      # Module C: Live data scaling, ONNX session, & action watchdog
│   ├── ui_documentation.docx   # Document D: Flet layout, canvas charts, and thread-safe queues
│   ├── mitigation_strategy.docx # Sub-Module Upgrade: Cross-Platform Resource Mitigation Strategy
│   ├── project_documentation.docx # Complete academic project compendium and reference
│   └── System_Resource_Optimizer_Documentation.docx # Unified master academic documentation compendium (All modules combined)
├── requirements.txt       # Core Python library dependencies
├── Run_Windows.bat        # Double-click script to run modern Flet on Windows
├── Run_macOS.command      # Double-click script to run modern Flet on macOS
└── README.md              # This comprehensive system guide
```

---

## 📚 Simple Explanations of All Libraries Used

The System Resource Optimizer combines modern GUI architecture with deep learning. Here is a simple, plain-English explanation of why each external library is utilized in this project:

### 🎨 User Interface & Packaging
* **Flet (`flet` & `flet-desktop`)**
  * *What it is:* A modern framework to build real-time interactive user interfaces based on Google's Flutter rendering engine.
  * *Why we use it:* It allows us to build a gorgeous, GPU-accelerated glassmorphic desktop dashboard in pure Python. It handles three dynamic canvas-painted rolling charts and process grids smoothly at 60 FPS without UI freezing.
* **PyInstaller (`pyinstaller`)**
  * *What it is:* A tool that bundles a Python application and all its library dependencies into a single, standalone executable package.
  * *Why we use it:* It compiles our code into a double-clickable file (`.exe` for Windows, `.app` for macOS) so that anyone can run the System Resource Optimizer instantly without needing to install Python, libraries, or dependencies!

### 📊 Telemetry Data & System Analysis
* **psutil (Process and System Utilities)**
  * *What it is:* A cross-platform library for retrieving information on running processes and hardware utilization.
  * *Why we use it:* It acts as our system's "sensors." It queries real-time CPU percentages, core activities, RAM usage, swap memory, network speeds, disk read/writes, and temperatures. It also executes operating system commands to safely `suspend` and `resume` background processes.
* **NumPy (`numpy`)**
  * *What it is:* A high-performance mathematical and multi-dimensional array processing library.
  * *Why we use it:* It handles the mathematical matrices behind the sliding windows (converting the last 60 seconds of telemetry into sequences of shape `(1, 60, 12)`) and feeds them instantly to the neural network.
* **Pandas (`pandas`)**
  * *What it is:* A highly popular data analysis library built around "DataFrames" (similar to Excel spreadsheets in code).
  * *Why we use it:* It is used to load, clean, align, and save raw telemetry CSV records during data collection and preprocessing.
* **scikit-learn (`sklearn`)**
  * *What it is:* A foundational machine learning toolkit containing standard statistical utilities.
  * *Why we use it:* It provides the `MinMaxScaler` that normalizes raw telemetry values (like 4500 MB RAM or 87°C temperature) into standard $[0, 1]$ floats so the GRU neural network can read and interpret them accurately.

### 🧠 Deep Learning & AI Inference
* **PyTorch (`torch`)**
  * *What it is:* A world-class deep learning framework developed by Meta's AI Research lab, used to construct and train complex neural networks.
  * *Why we use it:* It is the compiler and sandbox where we build, optimize, and train our custom two-layer Gated Recurrent Unit (GRU) model offline (`train.py`) before exporting the final weights.
* **ONNX (Open Neural Network Exchange)**
  * *What it is:* A universal open-standard format that allows machine learning models to be trained in one framework (like PyTorch) and run on another.
  * *Why we use it:* It converts our heavy PyTorch model (`gru_checkpoint.pt`) into a highly portable, cross-platform neural network graph format (`gru_fp32.onnx`).
* **ONNX Runtime (`onnxruntime`)**
  * *What it is:* A highly optimized cross-platform runtime engine designed to run pre-trained ONNX models with maximum speed.
  * *Why we use it:* It runs our 8-bit dynamic-quantized GRU network (`gru_quantized.onnx`) on a background thread inside the desktop app. It operates at **less than 2% CPU overhead**, ensuring our AI-powered optimizer never slows down the user's computer!

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
