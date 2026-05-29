KWAME NKRUMAH UNIVERSITY OF SCIENCE AND TECHNOLOGY

Faculty of Physical and Computational Sciences

Department of Computer Science

Cross-Platform Resource Mitigation Strategy

Technical Documentation — Sub-Module Upgrade

Project: Lightweight AI-Powered System Resource Optimizer

Prepared by:

Lamptey Kwaku Abednego — 3398122

Tugbah Lily Ama Mawuena — 3416522

Korli Larry Addo — 3395922

Date: May 2026

# 1. Introduction

The existing INT8 quantized GRU successfully forecasts imminent system bottlenecks ($P(B_{t+1}) > \tau$). However, the legacy mitigation strategy of abruptly suspending background tasks often exacerbates latency due to locked OS resources and context-switching overhead. 

To solve this, the optimizer architecture has been upgraded with a cross-platform, deterministic intervention sub-module. This upgrade explicitly replaces naive task suspension with dynamic process scheduling and memory allocation. By throttling heavy tasks *before* the bottleneck occurs, the operating system remains highly responsive without triggering system-wide freezes.

# 2. Methodology: Operating System Specific Mitigations

## 2.1. Windows Architecture (Hardware & Affinity Mapping)

Windows allows granular, low-level control over hardware resources. The optimizer mitigates heavy processes through a three-step sequence:

1. **CPU Affinity Isolation:** The offending heavy process is dynamically stripped of its access to Core 0 and Core 1. By isolating the heavy task strictly to the remaining logical processors, the OS interrupt routines and the graphical user interface remain completely responsive, eliminating UI lag.
2. **Priority Throttling:** The process's scheduling priority is downgraded to the lowest idle state (`IDLE_PRIORITY_CLASS` or 64). This forces the Windows scheduler to yield CPU time to active foreground windows automatically.
3. **Pre-emptive Memory Trimming:** If the GRU predicts a memory ceiling collision, the working set of the heavy process is aggressively flushed to disk smoothly before the bottleneck hits using Win32 API kernel calls (`EmptyWorkingSet`), avoiding violent hard page faults.

## 2.2. macOS Architecture (Intent-Based Quality of Service)

macOS, particularly on Apple Silicon with asymmetric P-Core (Performance) and E-Core (Efficiency) architectures, restricts direct CPU affinity manipulation. The optimizer adapts to this via the Darwin scheduler:

1. **Quality of Service (QoS) Downgrade:** The optimizer drops the POSIX execution priority of the offending process to the lowest background state (nice value 19). 
2. **Core Migration:** Upon receiving this signal, the Darwin scheduler seamlessly migrates the intensive task away from the P-Cores and hands it off entirely to the E-Cores. This protects the P-Cores for immediate user interactions without requiring unsafe memory page flushing.

# 3. Reversal Protocol

The optimizer continuously monitors the system state. Once the GRU predicts that the bottleneck threat has passed and system resources have stabilized, the mitigation is reversed. Processes have their original CPU affinities restored (on Windows) and their execution priorities returned to normal (restoring original nice class / value).

# 4. Evaluation Metrics for Validation (Tracking Progress)

To empirically validate the effectiveness of this new mitigation strategy against the old suspension method, the following telemetry will be tracked to visualize progress:

* **Context Switch Rate ($\Delta CS / \text{sec}$):** Monitored to prove that the dynamic priority shifting prevents context-switching storms. Progress is visualized as a flattened curve compared to the baseline.
* **Hard Page Faults per Second:** Evaluates the success of the pre-emptive memory trimming mechanism.
* **Core Utilization Spikes:** Demonstrating exactly 0% utilization on Core 0/1 during a throttled background heavy load, proving that UI and interrupt cores were successfully isolated.

# 5. Project Deliverables

To fulfill the final-year engineering requirements and demonstrate the system's enhanced capabilities, the following assets will be submitted:

* **Advanced Mitigation Sub-Module:** The OS-agnostic Python component integrated into the existing desktop application, officially replacing the legacy "task suspension" logic.
* **Telemetry & Logging Integration:** Upgraded application monitoring that actively tracks and exports Context Switch Rates and Hard Page Faults to visualize the system's progress during mitigation.
* **Comparative Evaluation Report:** A comprehensive data analysis document containing graphs that chart the performance delta between the baseline (no optimizer), the legacy version (task suspension), and the final version (dynamic throttling).
* **Live System Demonstration:** A practical presentation showcasing the updated desktop app running in real-time. This will involve deliberately triggering heavy workloads to visually prove the elimination of OS-level UI lag using the new core-isolation techniques.
