# System Resource Optimizer
**KNUST Final Year Project — Group 4**  
Lamptey Kwaku Abednego · Tugbah Lily Ama Mawuena · Korli Larry Addo

A lightweight AI-powered Windows desktop tool that monitors CPU, memory, and temperature in real-time, predicts resource bottlenecks using a quantized GRU neural network, and automatically suspends low-priority processes — with full user control to undo any action.

---

## Requirements

- **Python 3.11+** (Windows 10/11 x64)
- **Administrator rights** — required for process suspension via psutil

---

## Installation

```bash
git clone <your-repo-url>
cd optimizer
pip install -r requirements.txt
```

**`requirements.txt`**
```
psutil>=5.9.0
numpy>=1.26.0
pandas>=2.1.0
scikit-learn>=1.4.0
torch>=2.2.0
onnx>=1.16.0
onnxruntime>=1.18.0
PyQt6>=6.6.0
pyqtgraph>=0.13.0
plyer>=2.1.0
```

---

## How to Use

### Step 1 — Collect training data
Run this under different workloads. Each session appends to the same CSV.
```bash
# Collect 30 minutes of idle telemetry
python collector.py --label idle --duration 1800

# Collect while compiling / running heavy tasks
python collector.py --label compiling --duration 1200

# Collect while browsing / gaming
python collector.py --label gaming --duration 1800
```
Target: **at least 2 hours total** across all labels.

### Step 2 — Preprocess the data
```bash
python preprocess.py
```
Outputs: `data/telemetry_clean.csv`, `data/windows.npz`, `models/scaler.pkl`

### Step 3 — Train the model
```bash
python train.py
```
Outputs: `models/gru_fp32.onnx`, `models/gru_quantized.onnx`, `models/gru_checkpoint.pt`

Training runs for up to 100 epochs with early stopping. Expect 10–30 minutes depending on dataset size and hardware.

### Step 4 — Launch the dashboard
```bash
python main.py
```
The dashboard starts, connects to the pipeline, and begins live monitoring immediately. The AI panel shows "Waiting for data..." for the first 60 seconds while the rolling window fills.

---

## How Everything Connects

```
┌─────────────────────────────────────────────────────────────────┐
│                        BACKGROUND THREAD                        │
│                                                                 │
│  psutil (1 Hz poll)                                             │
│       │                                                         │
│       ▼                                                         │
│  _collect_raw()  →  raw dict {cpu%, mem%, temp, ...}            │
│       │                                                         │
│       ▼                                                         │
│  _scale()  →  MinMaxScaler (models/scaler.pkl)                  │
│       │                                                         │
│       ▼                                                         │
│  deque(maxlen=60)  →  rolling window np.array (60, F)           │
│       │                                                         │
│       ▼                                                         │
│  InferenceEngine  →  gru_quantized.onnx (ONNX Runtime)          │
│       │                                                         │
│       ├──► reg_out  : predicted next-step features              │
│       └──► conf_out : bottleneck probability  0.0 – 1.0         │
│                 │                                               │
│                 ▼                                               │
│          ActionEngine.evaluate()                                │
│                 │   confidence >= 0.80?                         │
│                 ├─ YES ──► select top CPU processes             │
│                 │          check WHITELIST                      │
│                 │          psutil.Process.suspend()             │
│                 │          record in _suspended dict            │
│                 │          Notifier.notify_suspend() ──► Toast  │
│                 └─ NO  ──► pass                                 │
│                 │                                               │
│  PipelineBridge.on_result()  ──► pyqtSignal.emit(result)        │
└─────────────────────────────────────────────────────────────────┘
         │  (Qt signal — thread-safe delivery)
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                         MAIN THREAD (Qt)                        │
│                                                                 │
│  MainWindow._on_result(PipelineResult)                          │
│       │                                                         │
│       ├──► MetricCard.update()      (CPU, MEM, TEMP, SWAP)      │
│       ├──► RollingChart.push()      (3 live charts)             │
│       ├──► ConfidencePanel.update() (% + natural language)      │
│       ├──► NotificationLog.add()    (event log entry)           │
│       └──► Undo button enable/disable                           │
│                                                                 │
│  User presses Undo  ──► pipeline.trigger_undo()                 │
│                          ActionEngine._resume_process()         │
│                          Notifier.notify_resume()               │
│                                                                 │
│  User presses Boost ──► pipeline.trigger_boost()                │
│                          ActionEngine.boost()                   │
│                          gc.collect()                           │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────┐
│  Auto-Resume Watchdog Thread   │
│  Checks every 10s              │
│  Resumes if suspended > 300s   │
└────────────────────────────────┘
```

---

## Project Structure

```
optimizer/
├── main.py              # PyQt6 dashboard — launch this to run the app
├── pipeline.py          # Real-time loop: poll → scale → infer → act
├── action_engine.py     # Process suspend/resume + whitelist + undo state
├── notifier.py          # Windows Toast notifications via plyer
├── gru_model.py         # PyTorch GRU architecture (ResourceGRU class)
├── train.py             # Train, evaluate, quantize, export to ONNX
├── collector.py         # Standalone data collector (producer/consumer)
├── preprocess.py        # Clean → label → scale → window → split
├── config.py            # All constants: paths, thresholds, hyperparams
│
├── data/
│   ├── telemetry_raw.csv    # Raw collector output (appended per session)
│   ├── telemetry_clean.csv  # Cleaned, labelled, scaled CSV
│   └── windows.npz          # Windowed arrays: X_train/val/test + y_*
│
├── models/
│   ├── scaler.pkl           # Fitted MinMaxScaler (MUST exist at runtime)
│   ├── gru_fp32.onnx        # Full-precision exported model
│   ├── gru_quantized.onnx   # INT8 quantized model (loaded by pipeline)
│   └── gru_checkpoint.pt    # PyTorch checkpoint for retraining
│
└── assets/
    └── icon.ico             # System tray icon (optional)
```

---

## How to Retrain the Model

1. **Collect new data** — run `collector.py` with new labels or on new hardware
2. **Re-preprocess** — `python preprocess.py` (refits the scaler on new data)
3. **Retrain** — `python train.py`
4. **The pipeline auto-loads** the new `models/gru_quantized.onnx` on next launch — no code changes needed

> ⚠️ Always re-run `preprocess.py` before `train.py` when adding new data. The scaler must be fitted on the full dataset to avoid distribution mismatch at inference time.

---

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `Temperature shows -1.0 / 0°C` | psutil can't read sensors on this hardware | Install [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) — not required for core functionality |
| `AccessDenied on process suspension` | App not running as Administrator | Right-click → Run as Administrator |
| `Model file not found` — heuristic mode active | train.py not yet run | Complete Steps 1–3 above |
| `Toast notifications not appearing` | plyer not installed | `pip install plyer` |
| `AI panel shows 0% confidence for 60s` | Rolling window filling up | Normal — wait 60 seconds after launch |
| `windows.npz not found` | preprocess.py not run | `python preprocess.py` |
| `ONNX quantization not available` | onnxruntime version missing quantization module | `pip install onnxruntime` (not onnxruntime-gpu) |

---

## Academic Notes for Monograph

| Module | Maps to Monograph Chapter |
|---|---|
| `collector.py` + `preprocess.py` | Chapter 3: Methodology — Dataset Collection |
| `gru_model.py` + `train.py` | Chapter 4: System Design — AI Model |
| `pipeline.py` + `action_engine.py` | Chapter 4: System Design — Data Pipeline |
| `main.py` | Chapter 5: Implementation — User Interface |

All four `.docx` documentation files contain placeholder tables for actual measured values. Fill these in after running the full pipeline on collected data before monograph submission.
