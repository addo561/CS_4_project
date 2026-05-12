# ⚡ System Resource Optimizer
### KNUST Final Year Project — Group 4

An AI-powered desktop application that monitors your computer's resources in real time, predicts performance bottlenecks before they happen, and automatically optimises your system — all from a single dashboard.

---

## 🚀 What Happens When You Open the App

### 1. The Dashboard Launches
The moment you double-click the app, a dark-themed dashboard opens with four sections:

- **Metric Cards** (top-left) — live CPU %, Memory %, Temperature, and Swap usage, each with a colour-coded progress bar that turns orange at 70% and red at 90%.
- **Real-Time Charts** (centre-left) — three rolling line graphs updating every second showing the last 2 minutes of CPU, Memory, and Temperature history.
- **Process Table** (bottom-left) — the top 20 running processes by CPU usage, refreshed every 3 seconds. Suspended processes are highlighted in red.
- **AI Prediction Panel** (right sidebar) — the GRU model's current bottleneck confidence score (0–100%), with a description of what it expects your system to do in the next 30 seconds.

### 2. The Pipeline Starts Automatically
In the background, the app immediately begins:
1. **Collecting** — polling your CPU, memory, temperature, and per-core metrics every 1 second using `psutil`.
2. **Scaling** — normalising each reading using the fitted MinMax scaler (`models/scaler.pkl`).
3. **Windowing** — accumulating 60 consecutive readings (60 seconds) into a rolling input window.
4. **Predicting** — once 60 seconds of data are collected, the GRU model (`models/gru_quantized.onnx`) runs inference every second, outputting:
   - A **bottleneck probability** (0–1) — how likely a CPU/memory spike is in the next 30 seconds.
   - A **predicted CPU %** and **Memory %** at the 30-second horizon.

> **During the first 60 seconds** the AI panel shows 0% confidence — this is normal. The model needs its full 60-second window before making predictions.

### 3. The Status Indicator Updates
The coloured dot in the top-right corner reflects the system state:
- 🟢 **Green "Live"** — system healthy, no action needed
- 🟡 **Orange "Warning"** — moderate risk detected, monitoring closely
- 🔴 **Red "Action Mode"** — high confidence bottleneck predicted, optimizer is ready to act

### 4. The AI Takes Action (if confidence ≥ 80%)
When the model's confidence crosses the 80% threshold, the action engine automatically:
1. Identifies the top 3 CPU-consuming processes that are safe to suspend (non-whitelisted system processes).
2. Suspends them using `psutil`'s `proc.suspend()`.
3. Logs the action in the **Event Log** with a timestamp.
4. Enables the **Undo** button so you can instantly restore any suspended process.
5. Auto-resumes all suspended processes after **5 minutes** even if you don't click Undo.

---

## 🎛️ Controls

| Button | What It Does |
|---|---|
| 🤖 **Auto-Pilot OFF/ON** | Toggle autonomous mode. When ON, the optimizer automatically boosts your system whenever the AI confidence is high — no manual clicks needed. Fires at most once every 45 seconds. |
| 🚀 **One-Click Boost** | Manually trigger an immediate optimisation: resumes all suspended processes and runs Python garbage collection to free memory. |
| ↩ **Undo Last Action** | Immediately resumes all processes that were suspended by the optimizer. Only active when processes are suspended. |

---

## 🧠 How the AI Model Works

The model is a **Gated Recurrent Unit (GRU)** neural network trained on real system telemetry collected from this machine. It learns the temporal patterns of CPU and memory usage — recognising the build-up signatures that precede bottlenecks — and predicts them 30 seconds before they peak.

- **Input:** 60 seconds of 8 system metrics (sliding window)
- **Output:** Predicted resource values + bottleneck probability
- **Format:** INT8-quantised ONNX model (~0.5 MB) for fast, lightweight inference

---

## 🔬 Adaptive Hardware Calibration

### The Problem
The GRU model was originally trained on data collected from one specific machine. Every computer has different hardware characteristics:
- A 4-core laptop idles at different CPU% than a 16-core desktop
- Different RAM capacities mean different memory pressure thresholds
- Clock speeds and temperatures vary by chipset

If the bundled MinMax scaler (fitted on the training machine) normalises data from a different machine, the GRU sees patterns it was never trained on — reducing prediction accuracy.

### The Solution — Domain Adaptation via Local Scaler Calibration
On **first launch on any new machine**, the app automatically runs a **90-second silent calibration phase**. This is a form of **domain adaptation**: the GRU weights (learned temporal patterns) stay fixed, but the normalisation layer is re-fitted to the new machine's specific value ranges.

### How It Is Implemented

**1. Detection (`pipeline.py` — `Pipeline.__init__`)**
```python
if os.path.isfile(LOCAL_SCALER_PATH):       # ~/.sro_optimizer/scaler_local.pkl
    self._scaler      = load(LOCAL_SCALER_PATH)   # use local scaler
    self._calibrating = False
else:
    self._scaler      = load(SCALER_PATH)   # use bundled fallback
    self._calibrating = True                # trigger calibration
    self._cal_buffer  = []
```

**2. Data Collection (`pipeline.py` — `_run` loop)**  
During calibration, every telemetry sample (already scaled with the bundled scaler) is appended to `_cal_buffer`. The UI is kept alive — charts and cards update normally. The AI panel shows 0% confidence (correct — we don't infer during calibration).

**3. Fitting the Local Scaler (`pipeline.py` — `_finish_calibration`)**  
After 90 samples are collected:
```python
data         = np.array(self._cal_buffer)   # shape: (90, n_features)
local_scaler = MinMaxScaler()
local_scaler.fit(data)                      # learns THIS machine's min/max ranges
pickle.dump(local_scaler, open(LOCAL_SCALER_PATH, "wb"))
self._scaler      = local_scaler
self._calibrating = False
self._window.clear()                        # start fresh window with new scaler
```
The local scaler is saved to **`~/.sro_optimizer/scaler_local.pkl`** — the user's home directory, outside the app bundle, so it survives app updates.

**4. Subsequent Launches**  
`LOCAL_SCALER_PATH` is found on startup → calibration is skipped entirely → AI inference begins within 60 seconds of opening the app.

### What You See During Calibration

A green banner appears at the top of the dashboard:

```
🔬  Calibrating to your hardware — 73s remaining...   [████████░░░░░░░░░░░░]
```

When complete:
```
✅  Calibrated to your hardware! AI mode active.
```
A desktop notification also fires, and the banner fades after 3.5 seconds.

### Scaler vs Model — Why Only the Scaler Is Re-fitted

| Component | Action | Why |
|---|---|---|
| **GRU weights** | Unchanged | Temporal spike patterns (fast rise → plateau → drop) are universal across hardware |
| **MinMax Scaler** | Re-fitted locally | Value ranges (what counts as "high" CPU) are machine-specific |

This approach is computationally cheap (no GPU, no retraining), takes only 90 seconds, and requires zero user input — it runs silently in the background.

---

## 📁 Project Structure

```
Final_year/
├── Data_collector/
│   ├── collector.py       # Telemetry collection script
│   ├── preprocess.py      # Data cleaning & window generation
│   ├── config.py          # Central configuration + calibration constants
│   └── data/              # Collected CSV data
│       └── models/        # Trained model + scaler
├── data_pipeline/
│   ├── pipeline.py        # Real-time inference loop + calibration logic
│   ├── action_engine.py   # Process suspend/resume logic
│   └── notifier.py        # Desktop notifications (osascript on macOS)
├── files/
│   ├── main.py            # PyQt6 dashboard (entry point)
│   ├── gru_model.py       # GRU architecture definition
│   └── train.py           # Model training script
├── assets/                # App icon (icon.icns / icon.png)
├── requirements.txt       # Python dependencies
└── README.md              # This file
```

> **Local calibration file:** `~/.sro_optimizer/scaler_local.pkl`  
> Created automatically on first launch. Delete this file to force re-calibration.

---

## 🔧 Running From Source

```bash
# Install dependencies
pip install -r requirements.txt

# 1. Collect data (run for at least 5 minutes, then Ctrl+C)
python3 Data_collector/collector.py --label idle

# 2. Preprocess & build training windows
python3 Data_collector/preprocess.py

# 3. Train the GRU model
python3 files/train.py

# 4. Launch the dashboard
python3 files/main.py
```

---

*KNUST Final Year Project — Group 4 | Built with PyQt6, PyTorch, ONNX Runtime & psutil*
