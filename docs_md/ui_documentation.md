KWAME NKRUMAH UNIVERSITY OF SCIENCE AND TECHNOLOGY

Faculty of Physical and Computational Sciences

Department of Computer Science

User Interface Design & Implementation

Technical Documentation — Module D

Project: Lightweight AI-Powered System Resource Optimizer

Prepared by:

Lamptey Kwaku Abednego — 3398122

Tugbah Lily Ama Mawuena — 3416522

Korli Larry Addo — 3395922

Date: May 2026

# 1. Introduction

This document describes the design, architecture, and implementation of the Flet-based desktop dashboard for the Lightweight AI-Powered System Resource Optimizer. The user interface is the primary means through which the user perceives system health, receives AI-driven predictions, and exercises control over automated process management.

The dashboard is implemented in `src/main.py` and communicates with the background telemetry pipeline via a thread-safe Queue and Flet's asynchronous event update loop. It was designed to satisfy three core engineering principles: 
1. **Real-time responsiveness** without blocking the monitoring pipeline.
2. **Visual clarity** that communicates AI confidence and system state at a glance.
3. **Minimal cognitive load** so users can make manual decisions quickly.

# 2. Framework Justification: Flet

The user interface framework was selected after evaluating candidate frameworks against the requirements of a Windows desktop application with real-time charting, background execution, and a premium visual presentation.

[TABLE]
Framework | Charting | System Tray | Windows Native | Design Aesthetics | Selected
Flet (Flutter) | Canvas-drawn | Native helper | Excellent | Sleek Glassmorphic | ✅ Yes
Tkinter | Canvas-drawn | None | Poor | Dated & basic | No — unprofessional
Electron | HTML5 / D3 | Yes | Moderate | Modern Web | No — 200MB+ overhead
PyQt6 + PyQtGraph | PyQtGraph | Yes | Excellent | Fully customisable | Backup fallback
[/TABLE]

Table 2.1: UI framework comparison. Flet selected for premium Flutter visual rendering and extremely low execution overhead.

Flet is built on Google's high-performance **Flutter rendering engine**, allowing it to render extremely smooth, modern, and beautiful user interfaces that feel state-of-the-art. Rather than utilizing standard web views (like Electron) which consume massive RAM, or old system widgets (like Tkinter) which look dated, Flet compiles down to native window rendering. It handles three dynamic canvas-painted rolling charts and process tables smoothly at 60 FPS without UI freezing. Since this project is evaluated as a final year academic portfolio, the modern visual aesthetics, dark glassmorphism effects, and premium dashboard design of Flet strongly enhance the project's overall presentation, while its multi-threaded async Queue architecture ensures lightweight, thread-safe operation under 2% CPU overhead.

# 3. UI Component Architecture

The dashboard is structured as a responsive Flet window containing a left-hand navigation sidebar (260px) and a main content area. The main area uses a responsive layout dividing the Circular AI Gauge (top-left) and Metric Tiles (top-right) from the Canvas-based rolling charts (middle) and Running Processes DataTable (bottom). The right control rail houses manual controls, suspended processes, and the event-driven active system log.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│  Sidebar   │  Circular Gauge  │             Metric Tiles (CPU, RAM, Temp, Swap)        │
│  [260px]   │  [ProgressRing]  │             [ft.Container wrappers with ProgressBars]  │
│            ├───────────────────────────────────────────────────────────────────────────┤
│  - Dash    │                                                                           │
│  - Analytics│                         Three Rolling Canvas Charts                       │
│  - Settings│                         [ft.canvas.Canvas drawn lines]                    │
│            ├───────────────────────────────────────────────────────────────────────────┤
│  [App      │       Running Processes Grid      │         Right Control Rail            │
│   Status]  │       [ft.DataTable top 20 list]  │         [Boost, Undo, Auto, Log]      │
└────────────┴───────────────────────────────────────────────────────────────────────────┘
```

[TABLE]
Class / Component | Flet Control | Responsibility
DashboardUI | `ft.Page` wrapper | Main page manager; handles page navigation and transitions.
MetricTile | `ft.Container` | Custom card displaying live metric value, label, and custom colored progress bar.
RollingChart | `ft.canvas.Canvas` | High-performance, custom-painted scrolling line chart (120 samples) with threshold markers.
AI Gauge | `ft.ProgressRing` | Circular ring showing model confidence with severity color transitions and natural language insights.
Event Log | `ft.ListView` | Auto-scrolling, event-driven log displaying system notifications and suspensions in real time.
Queue Signal Bridge | `queue.Queue` | Multi-threaded thread-safe data queue transporting metrics from background thread to UI thread.
[/TABLE]

Table 3.1: Dashboard UI component summary in `src/main.py`.

# 4. Thread-Safe Async Update Architecture

A primary architectural challenge in real-time desktop design is cross-thread synchronization. Flet strictly prohibits background threads from directly modifying UI control values, as doing so would cause race conditions and application crashes. The SRO background telemetry pipeline executes on a dedicated background daemon thread, generating `PipelineResult` objects once every 1.0 second. To ensure complete separation, the system uses a thread-safe Queue-based bridge:

```
[Background Pipeline Thread] ──► Pushes PipelineResult objects at 1Hz
            │
            ▼
     [queue.Queue]
            │
            ▼
[poll_queues Thread (UI Loop)] ──► Constantly polls Queue in background
            │
            ▼
[page.run_task(flush_ui)]    ──► Schedules Flet thread-safe UI update
            │
            ▼
   [Flet Engine Refresh]     ──► Redraws dashboard, canvas lines, and tables
```

1. **Queue Pushing:** The pipeline thread calls its `on_result` callback, placing the `PipelineResult` onto the thread-safe `queue.Queue`.
2. **Polling Thread:** A dedicated background polling thread (`poll_queues`) runs continuously, waiting for new elements in the queue.
3. **Async UI Execution:** When a new result arrives, the polling thread uses Flet's asynchronous task runner `page.run_task(flush_ui, result)` to dispatch the GUI update. Flet's internal engine executes this task on the main rendering loop, ensuring thread-safe, crash-free updates.

## 4.1 Flet Canvas Real-Time Charts

Each `RollingChart` maintains a `collections.deque(maxlen=120)` storing 2 minutes of historical metrics. When the UI updates, a custom Flet `Canvas` control redraws the trendline. The line path is built dynamically using Flet Canvas geometry:
- The line coordinates are translated from scaled floats into local pixel boundaries.
- The path color dynamically shifts based on the metric's severity: Green (#00C896) under 65%, Amber (#F0A500) at 65%–84%, and Red (#E05C5C) at 85%+.
- A horizontal dashed line is drawn on the canvas to represent the bottleneck threshold boundary.

## 4.2 Process Table Decoupled Refresh

Querying process lists is computationally heavy. To prevent Flet interface stutter, the **Running Processes Table** is decoupled from the 1Hz pipeline. A separate background worker thread refreshes the top 20 resource-consuming processes every **3.0 seconds** (sorted by memory usage). This interval prevents visual clutter while guaranteeing the user has access to real-time process statistics.

# 5. Design Token System

The application implements a rigorous dark glassmorphic design token system. All colors, paddings, and font styles are defined as centralized Python constants at the top of `src/main.py`, applied dynamically across all components to guarantee design cohesion.

[TABLE]
Token Name | Hex Color Value | Application & Usage
BG | #0D1117 | Deep dark main window background.
BG_CARD | #161B22 | Glassmorphic card and control rail background.
ACCENT | #00C896 | Emerald green: healthy system state, brand accents, and active lines.
ACCENT_WARN | #F0A500 | Amber yellow: warning state, medium confidence spikes (65–84%).
ACCENT_CRIT | #E05C5C | Crimson red: critical bottlenecks, suspensions, and extreme loads (>= 85%).
TEXT_PRI | #E6EDF3 | Crisp white: primary text headers, metric values, and labels.
TEXT_SEC | #8B949E | Cool grey: secondary descriptions, subtext, and timestamps.
BORDER | #30363D | Steel grey: subtle card borders, dividers, and grid lines.
[/TABLE]

Table 5.1: Dark glassmorphic design token palette.

# 6. Dashboard Control Logic

## 6.1 Confidence Panel (Explainable AI)

The Circular AI Gauge implements the **Explainable AI (XAI)** requirement by displaying the model's confidence output alongside natural-language reasoning:
- **Low Risk (< 55%):** Displays a green circle indicating the system is running optimally under safe baseline loads.
- **Moderate Risk (55%–79%):** Displays an amber circle with specific notifications detailing which resource is rising (e.g. *"Elevated CPU activity predicted in 30s"*).
- **High Risk (>= 80%):** Displays a red flashing ring warning the user that a bottleneck is imminent, showing the forecasted CPU% and memory% values at the 30-second future horizon.

## 6.2 One-Click Boost Manual Override

The green "One-Click Boost" button is permanently available in the control rail. Clicking it bypasses all AI threshold gates, immediately executing a manual system boost. It triggers `Pipeline.trigger_boost()`, which resumes all suspended background tasks, executes Python's garbage collection (`gc.collect()`), and clears physical page allocations. This is immediately logged in the Event Log.

## 6.3 Undo Button State Machine

The "Undo Suspension" button is wired to a strict in-memory state machine to prevent user errors:
- **Disabled State (Default):** The button remains greyed out and inactive on startup.
- **Enabled State:** The moment the Action Engine executes an automated process suspension (or the user clicks Boost), the button transitions to green and becomes active.
- **Auto-Reset State:** If the user clicks Undo, or if the 5-minute safety watchdog auto-resumes the frozen processes, the button automatically disables itself. This prevents confusing, dead no-op clicks when there are no suspended processes in the optimizer's cache.

# 7. System Tray & Window Management

The application is designed to operate as a continuous background daemon, meaning it must not be terminated when the user closes the dashboard window:
1. **Window Close Override:** The close action is intercepted programmatically. Closing the dashboard window simply hides the Flet interface, letting the 1Hz telemetry polling and GRU inference engine continue executing on its background thread.
2. **System Tray Integration:** A system tray icon is initialized. Right-clicking the tray icon exposes a menu containing *"Show Dashboard"* and *"Quit Optimizer"*. 
3. **User Notifications:** On first hide, the app pushes an asynchronous system toast notification confirming: *"System Resource Optimizer is running in the background."* Double-clicking the tray icon restores the window.

# 8. Accessibility & Usability Features

- **Double Coding:** All system severity states are communicated via both color-shifting and natural language descriptions, ensuring full usability for users with color vision deficiencies.
- **DPI Scaling:** All layouts and font sizes are defined using adaptive density offsets, scaling seamlessly on high-DPI displays without visual clipping.
- **Action Verification:** The Undo button is dynamically disabled when no processes are suspended, eliminating visual clutter.
- **Chronological Logs:** The ListView event log contains microsecond-precision timestamps, allowing users to audit exactly when the AI performed process suspensions.

# 9. References

[1] Flet Team. (2024). Flet Reference Guide — High-performance Python desktop apps. https://flet.dev/

[2] Flutter Team. (2024). Flutter Canvas Drawing API. https://flutter.dev/

[3] Nielsen, J. (1994). Usability Engineering. Academic Press.

[4] Python Software Foundation. (2024). queue — A synchronized queue class. Python Standard Library.
