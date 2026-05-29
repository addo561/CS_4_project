KWAME NKRUMAH UNIVERSITY OF SCIENCE AND TECHNOLOGY

Faculty of Physical and Computational Sciences

Department of Computer Science

Project Introduction & Final System Capabilities

Technical Documentation — Module E (Introductory Compendium)

Project: Lightweight AI-Powered System Resource Optimizer

Prepared by:

Lamptey Kwaku Abednego — 3398122

Tugbah Lily Ama Mawuena — 3416522

Korli Larry Addo — 3395922

Date: May 2026

# 1. Project Introduction

Modern operating systems, particularly Microsoft Windows, are complex ecosystems supporting a diverse array of background processes, services, and user applications. While hardware capabilities have grown exponentially, software resource efficiency has frequently lagged, leading to common system performance bottlenecks. Inefficiently programmed applications, background updater daemons, page memory leaks, and runaway multi-threaded tasks frequently saturate system CPU cores and exhaust physical memory. 

The consequences of these resource bottlenecks are immediate and highly detrimental to the user experience:
1. **Thermal Throttling:** Sustained CPU utilization peaks core package temperatures, forcing the hardware to dynamically scale down clock frequencies to prevent physical damage, thereby degrading system performance.
2. **Sluggishness & Frame Drops:** Resource starvation in critical threads causes latency spikes, micro-stutters in the desktop environment, and delayed input responses.
3. **Battery Drain:** High CPU and memory bus utilization increases the system's electrical draw, significantly shortening battery life on portable computers.

To address these inefficiencies, our team has designed and implemented the **Lightweight AI-Powered System Resource Optimizer (SRO)**. This project serves as an intelligent, automated, and non-obtrusive utility that runs seamlessly on the user's desktop. Rather than operating purely as a reactive task manager, the System Resource Optimizer utilizes machine learning to **forecast performance bottlenecks before they manifest**, enabling proactive, safe, and fully reversible process optimization.

# 2. High-Level System Architecture

The System Resource Optimizer is engineered around a highly decoupled, modular architecture to guarantee low computational overhead and system stability:

1. **Real-Time Data Ingestion Layer:** Built using a highly optimized, non-blocking polling loop based on the `psutil` system utilities library. This layer samples system-level core metrics, smoothing out instantaneous noise using Exponential Moving Average filters.
2. **AI Predictive Engine:** A custom-trained, lightweight **Gated Recurrent Unit (GRU)** neural network. The model operates entirely in inference mode on a background thread using the highly optimized ONNX Runtime, consuming less than 2% CPU.
3. **Automated Action Engine:** Operates as a safe, thread-safe background optimizer. It evaluates the AI model's confidence scores; once a bottleneck risk exceeds 80%, it performs safe, reversible background process suspensions using system-level kernel calls. It is heavily protected by a hard-coded Windows System Whitelist to prevent OS instability.
4. **Sleek Modern User Interface:** Built using **Flet** (a Python binding for the Flutter rendering engine), providing a GPU-accelerated, dark glassmorphic dashboard with real-time rolling canvas charts, metric cards, active log views, and system tray integration.

# 3. Final Capabilities: What the App Does at the End

Upon the completion of the building and integration phase, the System Resource Optimizer delivers a robust, highly responsive set of desktop optimization capabilities:

### [A] Real-Time Telemetry & Environmental Telemetry
- **1Hz Ingestion Frequency:** The background collector thread gathers comprehensive system metrics precisely once per second, utilizing non-blocking polling to eliminate any pipeline lag.
- **Rich Telemetry Matrix:** The collector reads overall CPU%, current CPU clock frequency, physical memory used, memory available, memory%, swap used, swap%, average CPU package temperature, network upload/download bandwidth, and disk read/write bandwidth.
- **Thermal Sensor Fail-Safe:** To resolve virtual machine abstraction or administrative security restrictions on Windows (which block direct access to thermal registers), the system implements a high-fidelity **ThermalSimulator**. This simulator models CPU heat soak and dissipation mathematically using a first-order lag filter (exponential smoothing) transition:
  $$T_{\text{target}} = 38.0 + (U_{\text{cpu}} \times 0.45) + (U_{\text{mem}} \times 0.1)$$
  $$T_{t+1} = T_t + (T_{\text{target}} - T_t) \times 0.1$$
- **Telemetry Smoothing:** To prevent temporary random system spikes from triggering false alarms, the system applies an Exponential Moving Average (EMA) with an alpha coefficient of 0.3 to CPU and memory streams, producing highly stable input curves.

### [B] AI-Powered Bottleneck Forecasting
- **Quantized GRU Temporal Model:** Uses a dynamic 8-bit quantized ONNX Gated Recurrent Unit model (file size ~0.17 MB) to process a historical sequence of the last 60 consecutive seconds.
- **30-Second Future Horizon:** Every second, the model runs a fast mathematical forward pass (taking less than 1 ms of latency) to predict the system state 30 seconds into the future, outputting:
  1. A **Bottleneck Confidence Score** (0% to 100%) indicating the probability of a system bottleneck.
  2. Predicted **CPU % load** at the 30-second future horizon.
  3. Predicted **Memory % load** at the 30-second future horizon.
- **Heuristic Fallback:** If the neural network files are missing or unreadable, the system automatically falls back to an active heuristic algorithm that tracks CPU trend slopes and registers spikes reactively, ensuring continuous operation.

### [C] Safe Process Autopilot & Action Engine
- **Autonomous Optimization:** When "Auto-Pilot Mode" is toggled ON, the Action Engine automatically triggers a system boost whenever the AI confidence reaches or exceeds 80%. A safety cooldown of 45 seconds prevents redundant back-to-back triggers.
- **Strict System Whitelist:** A hard-coded and user-extensible whitelist protects crucial operating system processes (including `System`, `services.exe`, `explorer.exe`, `dwm.exe`, `taskmgr.exe`, and the optimizer itself) from suspension, ensuring 100% system stability.
- **Reversible Suspension:** Rather than killing processes (which risks losing unsaved user work), the engine calls `psutil.Process(pid).suspend()` to freeze the top 3 resource-consuming, non-whitelisted processes in memory. This stops kernel scheduling for those processes instantly.
- **Safety Watchdog Timer:** An independent background watchdog daemon wakes every 10 seconds and automatically resumes any suspended process after a 5-minute timeout, ensuring no program remains permanently suspended.

### [D] Hardware Domain Adaptation (Local Calibration)
- **Automatic Domain Calibration:** Upon first launch on a new computer, the system executes a silent **90-second hardware calibration**.
- **Local MinMaxScaler Serialization:** By capturing baseline hardware parameters (e.g., core count, RAM capacity, thermal boundaries), the app fits a local `MinMaxScaler` and saves it to `~/.sro_optimizer/scaler_local_v2.pkl`. This ensures out-of-distribution values do not skew AI predictions on different machines.

### [E] Gorgeous Dark Glassmorphic Dashboard
- **GPU-Accelerated Flet UI:** A premium, modern dashboard styled in a dark glassmorphic palette (#0D1117 and #161B22) built with Flet, completely free of PyQt6 signal/slot blocks.
- **Real-Time Rolling Charts:** Three high-performance Canvas charts that render the last 2 minutes (120 samples) of CPU, memory, and temperature history smoothly at 60 FPS.
- **Active Process Table & Control Rail:** Displays the top 20 resource-consuming processes, updated every 3 seconds. The right control rail houses the Auto-Pilot toggle, manual "One-Click Boost" and "Undo" buttons, active suspended processes, and event-driven logging.
