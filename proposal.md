
# KWAME NKRUMAH UNIVERSITY OF SCIENCE AND TECHNOLOGY
### Faculty of Physical and Computational Sciences
### Department of Computer Science

---

# Project Proposal: 
# Lightweight AI-Powered System Resource Optimizer
## (GROUP 4)

**Prepared by:**
* Lamptey Kwaku Abednego - 3398122
* Tugbah Lily Ama Mawuena - 3416522
* Korli Larry Addo - 3395922

**Date:** March 18, 2026

---

## 1. Introduction
Modern Windows applications and background processes frequently consume system resources inefficiently, leading to thermal throttling, increased battery drain, and sluggish overall performance. Our team proposes the development of a lightweight, AI-driven desktop application designed to actively monitor, predict, and optimize CPU and memory usage.

Our goal is to build a tool that runs unobtrusively in the background, utilizing minimal overhead to forecast resource spikes. By providing predictive analytics, transparent automated process management, and a sleek real-time dashboard, this application will help users maintain optimal system health without getting in their way.

## 2. System Architecture
We have designed the application around three core, highly decoupled modules to ensure stability and low overhead on Windows:

* **Data Ingestion Layer (`psutil`):** A continuous monitoring script that collects real-time system telemetry, specifically CPU load, memory allocation, and core temperatures.
* **AI Prediction Engine:** A highly optimized time-series forecasting model that analyzes recent telemetry windows to predict imminent resource bottlenecks or thermal events.
* **User Interface (Flet):** A Windows desktop dashboard built entirely in Python that visualizes current metrics, AI-driven predictions, and user controls.

## 3. AI Model Selection: The Quantized GRU
A fundamental requirement for this project is that the optimizer itself must not become a resource burden. Deploying massive, heavy neural networks would contribute to the exact problem we are trying to solve. Therefore, our team has selected a **Quantized Gated Recurrent Unit (GRU)** as our predictive engine.

![Internal architecture of a Gated Recurrent Unit (GRU)](https://towardsdatascience.com/wp-content/uploads/2022/02/13a8HnDUlzhhKcSpQzOyiCQ.png)
> *Figure :  Architecture of a Gated Recurrent Unit (GRU). The simplified gating mechanism allows for lower computational overhead compared to traditional LSTMs, making it ideal for real-time system monitoring.*

Here is why this approach is ideal for our constraints:
* **Architectural Efficiency:** A GRU is a streamlined variant of a Recurrent Neural Network (RNN) designed specifically for sequential, time-series data. Compared to other standard models like LSTMs, GRUs have fewer internal gating mechanisms. This means fewer tensor operations, translating to faster inference times and lower CPU utilization.
* **Quantization for Minimal Footprint:** To make the model truly lightweight, we will apply post-training quantization. This process converts the model's internal weights from standard 32-bit floating-point numbers to 8-bit integers. This drastically reduces the physical memory footprint of the model and accelerates computation on standard desktop CPUs without requiring a dedicated GPU.

## 4. Advanced Features & User Experience
To elevate this project beyond a basic utility and demonstrate a strong focus on both academic rigor and practical usability, our team is integrating three standout features:

* **Explainable AI (XAI) & Confidence Metrics:** The dashboard will display the GRU model's real-time Confidence Score (e.g., "85% probability of a thermal throttle in 2 minutes") so the system's decision-making is fully transparent.
* **Native Windows Notifications:** The application will push sleek, non-intrusive Windows Toast alerts (via `win10toast` or `plyer`) to keep the user informed when background optimization occurs, removing the need to constantly monitor the dashboard.
* **Manual Override & Fail-Safe "Undo":** Users will have a "One-Click Boost" to instantly bypass the AI and clear RAM manually, alongside an "Undo" fail-safe to instantly resume any process the AI has suspended.

## 5. Implementation Plan
Our team will execute this project across four distinct phases:

* **Phase 1: Data Collection & Profiling**
    * Develop a Python background process (running invisibly via `pythonw.exe` or registered as a Windows Service) using `psutil` to log CPU usage per core, memory usage, and hardware temperatures to a local dataset.
    * Capture telemetry under various system states (idle, heavy browsing, gaming) to build a robust training dataset.
* **Phase 2: Model Training, Quantization & XAI**
    * Preprocess the collected data into sliding time-series windows.
    * Train the base GRU model to accurately predict future resource states and output **probability/confidence scores** alongside its predictions.
    * Apply INT8 quantization and export the model to an optimized runtime format (e.g., TFLite or ONNX).
* **Phase 3: Flet Dashboard & User Controls**
    * Construct the Flet application shell and integrate real-time telemetry graphs.
    * Build the physical UI controls for the **Explainable AI Confidence display**, the **"One-Click Boost"**, and the **"Undo" button**.
    * Implement asynchronous background threading to ensure the UI updates smoothly alongside data collection.
* **Phase 4: Action Logic, OS Integration & Stress Testing**
    * Program the backend action layer using `psutil.Process().suspend()` and `.resume()` to handle automated process management and the manual "Undo" feature.
    * Wire up the **Native Windows Notifications** to trigger alongside process suspensions.
    * Conduct rigorous profiling to guarantee the optimizer application consumes less than 2% of total CPU and minimal RAM during active operation.

## 6. Feasibility & Risk Assessment
Our team assesses the feasibility of this project at an 8/10. It strikes a strong balance between advanced AI implementation and practical software engineering.

* **Strengths:** Data ingestion and process management (suspending/resuming tasks) are natively and efficiently handled by Python's `psutil` library. Additionally, Flet's async-first architecture ensures that the UI and the monitoring loop can run concurrently without freezing.
* **Risk Mitigation (The Whitelist):** The primary risk is the AI inadvertently suspending critical system processes (e.g., `explorer.exe` or `svchost.exe`). To mitigate this, Phase 4 will include the development of a strict "Windows System Whitelist." The action logic will verify target process IDs against this whitelist before executing any suspensions, ensuring the tool remains completely safe for everyday use.