# =============================================================================
# main.py — PyQt6 Dashboard for the System Resource Optimizer
# KNUST Final Year Project — Group 4
#
# Usage:  python main.py
# Requires: pip install PyQt6 pyqtgraph psutil onnxruntime plyer
# =============================================================================

import sys
import os
import time
import collections

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QFrame,
    QSystemTrayIcon, QMenu, QSplitter, QScrollArea, QHeaderView,
    QProgressBar, QGraphicsDropShadowEffect, QDialog, QTextBrowser,
)
from PyQt6.QtCore import (
    Qt, QTimer, pyqtSignal, QObject, QThread, QSize,
)
from PyQt6.QtGui import QIcon, QColor, QFont, QPalette, QAction

import pyqtgraph as pg

# Add src to python path so submodules can find each other
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from core.pipeline import Pipeline, PipelineResult
from config import CONFIDENCE_THRESHOLD, CALIBRATION_SECONDS

# ── Colour constants ──────────────────────────────────────────────────────────
BG          = "#0D1117"
BG_CARD     = "#161B22"
BG_CARD2    = "#1C2128"
ACCENT      = "#00C896"
ACCENT_WARN = "#F0A500"
ACCENT_CRIT = "#E05C5C"
TEXT_PRI    = "#E6EDF3"
TEXT_SEC    = "#8B949E"
BORDER      = "#30363D"

# ── Global stylesheet ─────────────────────────────────────────────────────────
QSS = f"""
QMainWindow, QWidget {{
    background-color: {BG};
    color: {TEXT_PRI};
    font-family: Arial, Helvetica;
    font-size: 13px;
}}
QFrame#card {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#sidebar {{
    background-color: {BG_CARD};
    border-right: 1px solid {BORDER};
}}
QLabel#title {{
    font-size: 22px;
    font-weight: bold;
    color: {ACCENT};
}}
QLabel#sectionTitle {{
    font-size: 11px;
    font-weight: bold;
    color: {TEXT_SEC};
    letter-spacing: 1px;
}}
QLabel#metricValue {{
    font-size: 28px;
    font-weight: bold;
    color: {TEXT_PRI};
}}
QLabel#metricLabel {{
    font-size: 11px;
    color: {TEXT_SEC};
}}
QLabel#confValue {{
    font-size: 36px;
    font-weight: bold;
}}
QPushButton#boostBtn {{
    background-color: {ACCENT};
    color: #0D1117;
    border: none;
    border-radius: 6px;
    padding: 10px 20px;
    font-weight: bold;
    font-size: 13px;
}}
QPushButton#boostBtn:hover {{ background-color: #00E5AD; }}
QPushButton#boostBtn:pressed {{ background-color: #00A87C; }}
QPushButton#undoBtn {{
    background-color: {BG_CARD2};
    color: {TEXT_PRI};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 10px 20px;
    font-size: 13px;
}}
QPushButton#undoBtn:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton#undoBtn:disabled {{ color: {TEXT_SEC}; border-color: {BORDER}; }}
QPushButton#autopilotOff {{
    background-color: {BG_CARD2};
    color: {TEXT_SEC};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 10px 20px;
    font-weight: bold;
    font-size: 13px;
}}
QPushButton#autopilotOff:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton#autopilotOn {{
    background-color: #0a3d2b;
    color: {ACCENT};
    border: 2px solid {ACCENT};
    border-radius: 6px;
    padding: 10px 20px;
    font-weight: bold;
    font-size: 13px;
}}
QPushButton#autopilotOn:hover {{ background-color: #0d4f38; }}
QTableWidget {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    gridline-color: {BORDER};
    color: {TEXT_PRI};
}}
QTableWidget::item:selected {{ background-color: #1F6FEB22; }}
QHeaderView::section {{
    background-color: {BG_CARD2};
    color: {TEXT_SEC};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 10px;
    font-size: 11px;
    font-weight: bold;
}}
QScrollBar:vertical {{
    background: {BG_CARD};
    width: 6px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 3px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QProgressBar {{
    background-color: {BG_CARD2};
    border: 1px solid {BORDER};
    border-radius: 4px;
    height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{ border-radius: 4px; }}
QSplitter::handle {{ background-color: {BORDER}; width: 1px; }}
"""

# ── Help / Instructions Dialog ────────────────────────────────────────────────
class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI Resource Optimizer Guide")
        self.setFixedSize(540, 600)
        self.setStyleSheet(QSS + f" QDialog {{ background-color: {BG}; border: 1px solid {BORDER}; }} ")
        
        lay = QVBoxLayout(self)
        lay.setContentsMargins(30, 30, 30, 30)
        lay.setSpacing(20)
        
        title = QLabel("AI System Resource Optimizer")
        title.setObjectName("title")
        title.setStyleSheet(f"color: {ACCENT}; font-size: 24px; font-weight: bold;")
        
        # Use QTextBrowser for proper rich-text/HTML rendering
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setStyleSheet(f"""
            QTextBrowser {{
                background-color: transparent;
                border: none;
                color: {TEXT_PRI};
                font-size: 14px;
                line-height: 1.6;
            }}
        """)
        
        html_content = f"""
        <div style="color: {TEXT_PRI}; font-family: Arial, sans-serif;">
            <p style="font-size: 15px;">This application uses a <b>Quantized GRU Machine Learning</b> model to 
            monitor system telemetry and predict resource bottlenecks before they impact performance.</p>

            <h3 style="color: {ACCENT}; margin-top: 20px;">🔬 First Launch: Calibration</h3>
            <p>The app collects baseline telemetry for <b>90 seconds</b>. This allows the AI to learn 
            your specific hardware characteristics. <i>Predictions are paused during this phase.</i></p>

            <h3 style="color: {ACCENT}; margin-top: 20px;">🚀 Key Features</h3>
            <ul>
                <li><b>Live Telemetry:</b> Real-time charts of CPU, Memory, and Thermals.</li>
                <li><b>AI Prediction:</b> Forecasts system load 30s into the future. High Confidence (>80%) triggers alerts.</li>
                <li><b>One-Click Boost:</b> Instantly suspends non-critical, high-load processes.</li>
                <li><b>AI Auto-Pilot:</b> Automatically executes a Boost if a bottleneck is predicted.</li>
                <li><b>Safe List:</b> System-critical processes (Kernel, WindowServer, etc.) are whitelisted and protected.</li>
            </ul>

            <h3 style="color: {ACCENT}; margin-top: 20px;">🛠 Management</h3>
            <p>Use the <b>Suspended Processes</b> table to see what has been paused, and use 
            the <b>Undo</b> button to resume them at any time.</p>
        </div>
        """
        self.browser.setHtml(html_content)
        
        btn = QPushButton("Start Optimizing")
        btn.setFixedHeight(45)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT};
                color: #0D1117;
                border-radius: 6px;
                font-weight: bold;
                font-size: 15px;
            }}
            QPushButton:hover {{ background-color: #00E5AD; }}
        """)
        btn.clicked.connect(self.accept)
        
        lay.addWidget(title)
        lay.addWidget(self.browser)
        lay.addWidget(btn)

# ── Thread-safe bridge from Pipeline thread to Qt main thread ─────────────────
class PipelineBridge(QObject):
    result_ready          = pyqtSignal(object)
    calibration_progress  = pyqtSignal(int, int)   # (elapsed_s, total_s); -1 = done

    def on_result(self, result):
        self.result_ready.emit(result)

    def on_cal_prog(self, elapsed: int, total: int):
        self.calibration_progress.emit(elapsed, total)


# ── Metric card widget ────────────────────────────────────────────────────────
class MetricCard(QFrame):
    def __init__(self, label: str, unit: str = "%", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumWidth(130)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(4)

        self._lbl = QLabel(label.upper())
        self._lbl.setObjectName("sectionTitle")

        self._val = QLabel("—")
        self._val.setObjectName("metricValue")

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(5)

        self._unit = unit
        lay.addWidget(self._lbl)
        lay.addWidget(self._val)
        lay.addWidget(self._bar)
        self._set_bar_color(0)

    def update(self, value: float):
        pct = int(value)
        display = f"{value:.1f}{self._unit}"
        self._val.setText(display)
        self._bar.setValue(min(100, pct))
        self._set_bar_color(pct)
        # Colour the value text by severity
        if pct >= 90:
            self._val.setStyleSheet(f"color: {ACCENT_CRIT}; font-size: 28px; font-weight: bold;")
        elif pct >= 70:
            self._val.setStyleSheet(f"color: {ACCENT_WARN}; font-size: 28px; font-weight: bold;")
        else:
            self._val.setStyleSheet(f"color: {TEXT_PRI}; font-size: 28px; font-weight: bold;")

    def _set_bar_color(self, pct: int):
        color = ACCENT_CRIT if pct >= 90 else ACCENT_WARN if pct >= 70 else ACCENT
        self._bar.setStyleSheet(f"QProgressBar::chunk {{ background-color: {color}; border-radius: 4px; }}")


# ── Real-time chart widget ────────────────────────────────────────────────────
class RollingChart(pg.PlotWidget):
    MAX_POINTS = 120   # 2 minutes of history

    def __init__(self, title: str, unit: str = "%", parent=None):
        super().__init__(parent, background=BG_CARD)
        self.setTitle(title, color=TEXT_SEC, size="11pt")
        self.showGrid(x=False, y=True, alpha=0.15)
        self.setYRange(0, 100)
        self.getAxis("left").setTextPen(pg.mkPen(TEXT_SEC))
        self.getAxis("bottom").setTextPen(pg.mkPen(TEXT_SEC))
        self.getAxis("left").setLabel(unit, color=TEXT_SEC)
        self.setMinimumHeight(160)
        self.setMaximumHeight(200)

        pen = pg.mkPen(color=ACCENT, width=2)
        self._curve = self.plot(pen=pen)
        self._data: collections.deque = collections.deque(maxlen=self.MAX_POINTS)
        self._warn_line = pg.InfiniteLine(pos=90, angle=0,
                                          pen=pg.mkPen(color=ACCENT_CRIT, width=1, style=Qt.PenStyle.DashLine))
        self.addItem(self._warn_line)

    def push(self, value: float):
        self._data.append(value)
        self._curve.setData(list(self._data))
        # Colour the line based on latest value
        color = ACCENT_CRIT if value >= 90 else ACCENT_WARN if value >= 70 else ACCENT
        self._curve.setPen(pg.mkPen(color=color, width=2))


# ── Confidence gauge ──────────────────────────────────────────────────────────
class ConfidencePanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("AI CONFIDENCE")
        hdr.setObjectName("sectionTitle")

        self._pct = QLabel("—")
        self._pct.setObjectName("confValue")
        self._pct.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pct.setStyleSheet(f"color: {ACCENT}; font-size: 36px; font-weight: bold;")

        self._desc = QLabel("Waiting for data...")
        self._desc.setWordWrap(True)
        self._desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._desc.setStyleSheet(f"color: {TEXT_SEC}; font-size: 12px;")

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)

        lay.addWidget(hdr)
        lay.addSpacing(8)
        lay.addWidget(self._pct)
        lay.addWidget(self._desc)
        lay.addSpacing(8)
        lay.addWidget(self._bar)

    def update(self, confidence: float, pred_cpu: float, pred_mem: float, calibrating: bool = False):
        if calibrating:
            self._pct.setText("🔬")
            self._pct.setStyleSheet(f"color: {ACCENT_WARN}; font-size: 36px; font-weight: bold;")
            self._desc.setText("Calibrating to hardware...")
            self._bar.setStyleSheet(f"QProgressBar::chunk {{ background-color: {ACCENT_WARN}; border-radius: 4px; }}")
            return

        pct = int(confidence * 100)
        self._pct.setText(f"{pct}%")
        self._bar.setValue(pct)

        if pct >= 80:
            color = ACCENT_CRIT
            if pred_cpu > 0 or pred_mem > 0:
                self._desc.setText(
                    f"⚠ High load predicted in ~30s\n"
                    f"CPU ≈ {pred_cpu:.0f}%  |  MEM ≈ {pred_mem:.0f}%"
                )
            else:
                self._desc.setText("⚠ Bottleneck likely — taking action")
        elif pct >= 55:
            color = ACCENT_WARN
            self._desc.setText(f"Moderate risk — monitoring closely\nCPU ≈ {pred_cpu:.0f}%")
        else:
            color = ACCENT
            self._desc.setText("System healthy — scanning for risks...")

        self._pct.setStyleSheet(f"color: {color}; font-size: 36px; font-weight: bold;")
        self._bar.setStyleSheet(f"QProgressBar::chunk {{ background-color: {color}; border-radius: 4px; }}")


# ── Notification log ──────────────────────────────────────────────────────────
class NotificationLog(QFrame):
    MAX_ENTRIES = 50

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        hdr = QLabel("EVENT LOG")
        hdr.setObjectName("sectionTitle")
        lay.addWidget(hdr)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(f"background: transparent;")

        self._container = QWidget()
        self._vlay = QVBoxLayout(self._container)
        self._vlay.setContentsMargins(0, 0, 0, 0)
        self._vlay.setSpacing(4)
        self._vlay.addStretch()

        self._scroll.setWidget(self._container)
        lay.addWidget(self._scroll)
        self._count = 0

    def add(self, message: str, level: str = "info"):
        if self._count >= self.MAX_ENTRIES:
            # Remove oldest
            item = self._vlay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        ts = time.strftime("%H:%M:%S")
        color = {"info": TEXT_SEC, "warn": ACCENT_WARN, "action": ACCENT, "error": ACCENT_CRIT}.get(level, TEXT_SEC)

        lbl = QLabel(f"<span style='color:{TEXT_SEC};'>[{ts}]</span> "
                     f"<span style='color:{color};'>{message}</span>")
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("font-size: 11px;")

        self._vlay.insertWidget(self._vlay.count() - 1, lbl)
        self._scroll.verticalScrollBar().setValue(self._scroll.verticalScrollBar().maximum())
        self._count += 1


# ── Main window ───────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("System Resource Optimizer  —  KNUST Group 4")
        self.setMinimumSize(1100, 700)
        self.resize(1280, 780)

        pg.setConfigOptions(antialias=True)

        self._autopilot_enabled   = False
        self._autopilot_last_fire = 0.0
        self._cycle_count         = 0

        self._pipeline_bridge = PipelineBridge()
        self._pipeline_bridge.result_ready.connect(self._on_result)
        self._pipeline_bridge.calibration_progress.connect(self._on_calibration_progress)
        self._pipeline = Pipeline(
            on_result=self._pipeline_bridge.on_result,
            on_calibration_progress=self._pipeline_bridge.on_cal_prog,
        )

        self._build_ui()
        self._setup_tray()

        # ── Notifier: use tray bubble as the in-app channel ──────────────────
        from core.notifier import Notifier
        self._notifier = Notifier()
        self._notifier.on_notify = self._show_tray_message

        self._pipeline.start()
        self._log.add("Pipeline started — collecting telemetry", "info")

        # Show calibration banner immediately if pipeline starts in calibration mode
        if self._pipeline._calibrating:
            QTimer.singleShot(500, lambda: self._on_calibration_progress(0, CALIBRATION_SECONDS))
            # Also auto-show instructions on first use
            QTimer.singleShot(1500, self._show_help)

    def _show_help(self):
        dlg = HelpDialog(self)
        dlg.exec()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ── Calibration banner (hidden by default) ────────────────────────────
        self._cal_banner = QFrame()
        self._cal_banner.setFixedHeight(36)
        self._cal_banner.setStyleSheet(
            "background:#0a3d2b; border-bottom:1px solid #00C896;"
        )
        cal_lay = QHBoxLayout(self._cal_banner)
        cal_lay.setContentsMargins(16, 0, 16, 0)
        self._cal_icon  = QLabel("🔬")
        self._cal_icon.setStyleSheet("font-size:16px;")
        self._cal_text  = QLabel("Calibrating to your hardware — please wait...")
        self._cal_text.setStyleSheet(f"color:#00C896; font-size:12px; font-weight:bold;")
        self._cal_bar   = QProgressBar()
        self._cal_bar.setRange(0, 90)
        self._cal_bar.setValue(0)
        self._cal_bar.setFixedWidth(200)
        self._cal_bar.setStyleSheet(
            "QProgressBar{background:#0D1117;border:1px solid #00C896;border-radius:4px;height:12px;}"
            "QProgressBar::chunk{background:#00C896;border-radius:3px;}"
        )
        cal_lay.addWidget(self._cal_icon)
        cal_lay.addWidget(self._cal_text)
        cal_lay.addStretch()
        cal_lay.addWidget(self._cal_bar)
        self._cal_banner.setVisible(False)   # shown only during calibration
        root_lay.addWidget(self._cal_banner)

        # ── Header bar ──────────────────────────────────────────────────────
        header = QFrame()
        header.setFixedHeight(54)
        header.setStyleSheet(f"background-color: {BG_CARD}; border-bottom: 1px solid {BORDER};")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(20, 0, 20, 0)

        title = QLabel("⚡  System Resource Optimizer")
        title.setObjectName("title")
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color: {ACCENT}; font-size: 16px;")
        self._status_lbl = QLabel("Live")
        self._status_lbl.setStyleSheet(f"color: {TEXT_SEC}; font-size: 12px;")

        help_btn = QPushButton("ℹ️ Help")
        help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        help_btn.setStyleSheet(f"background: transparent; border: 1px solid {BORDER}; border-radius: 4px; padding: 4px 8px;")
        help_btn.clicked.connect(self._show_help)

        h_lay.addWidget(title)
        h_lay.addStretch()
        h_lay.addWidget(help_btn)
        h_lay.addSpacing(10)
        h_lay.addWidget(self._status_dot)
        h_lay.addWidget(self._status_lbl)
        root_lay.addWidget(header)

        # ── Body splitter ────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root_lay.addWidget(splitter)

        # LEFT: charts + process table
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(16, 16, 8, 16)
        left_lay.setSpacing(10)

        # Metric summary row
        metrics_row = QHBoxLayout()
        self._card_cpu  = MetricCard("CPU",  "%")
        self._card_mem  = MetricCard("Memory", "%")
        self._card_temp = MetricCard("Temp",  " °C")
        self._card_swap = MetricCard("Swap",  "%")
        for c in (self._card_cpu, self._card_mem, self._card_temp, self._card_swap):
            metrics_row.addWidget(c)
        left_lay.addLayout(metrics_row)

        # Charts
        self._chart_cpu  = RollingChart("CPU Utilisation", "%")
        self._chart_mem  = RollingChart("Memory Utilisation", "%")
        self._chart_temp = RollingChart("CPU Temperature", "°C")
        for ch in (self._chart_cpu, self._chart_mem, self._chart_temp):
            left_lay.addWidget(ch)

        # Process table
        proc_hdr = QLabel("RUNNING PROCESSES")
        proc_hdr.setObjectName("sectionTitle")
        proc_hdr.setContentsMargins(0, 8, 0, 4)
        left_lay.addWidget(proc_hdr)

        self._proc_table = QTableWidget(0, 4)
        self._proc_table.setHorizontalHeaderLabels(["PID", "Process Name", "MEM %", "Status"])
        self._proc_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._proc_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._proc_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._proc_table.setFixedHeight(180)
        self._proc_table.verticalHeader().setVisible(False)
        left_lay.addWidget(self._proc_table)

        splitter.addWidget(left)

        # RIGHT: sidebar
        right = QFrame()
        right.setObjectName("sidebar")
        right.setFixedWidth(280)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(16, 20, 16, 20)
        right_lay.setSpacing(12)

        ai_lbl = QLabel("AI PREDICTION ENGINE")
        ai_lbl.setObjectName("sectionTitle")
        right_lay.addWidget(ai_lbl)

        self._conf_panel = ConfidencePanel()
        right_lay.addWidget(self._conf_panel)

        # ── Auto-Pilot toggle ────────────────────────────────────────────────
        autopilot_lbl = QLabel("AUTO-PILOT")
        autopilot_lbl.setObjectName("sectionTitle")
        right_lay.addWidget(autopilot_lbl)

        self._autopilot_btn = QPushButton("🤖  Auto-Pilot  OFF")
        self._autopilot_btn.setObjectName("autopilotOff")
        self._autopilot_btn.setFixedHeight(46)
        self._autopilot_btn.setCheckable(True)
        self._autopilot_btn.setToolTip(
            "When ON, the optimizer automatically boosts your system\n"
            "whenever the AI confidence exceeds the action threshold.\n"
            "No manual clicking required."
        )
        self._autopilot_btn.clicked.connect(self._on_autopilot_toggle)
        right_lay.addWidget(self._autopilot_btn)

        # ── Manual action buttons ────────────────────────────────────────────
        actions_lbl = QLabel("MANUAL ACTIONS")
        actions_lbl.setObjectName("sectionTitle")
        right_lay.addWidget(actions_lbl)

        self._boost_btn = QPushButton("🚀  One-Click Boost")
        self._boost_btn.setObjectName("boostBtn")
        self._boost_btn.setFixedHeight(42)
        self._boost_btn.clicked.connect(self._on_boost)

        self._undo_btn = QPushButton("↩  Undo Last Action")
        self._undo_btn.setObjectName("undoBtn")
        self._undo_btn.setFixedHeight(42)
        self._undo_btn.setEnabled(False)
        self._undo_btn.clicked.connect(self._on_undo)

        right_lay.addWidget(self._boost_btn)
        right_lay.addWidget(self._undo_btn)

        # Suspended processes
        susp_lbl = QLabel("SUSPENDED BY OPTIMIZER")
        susp_lbl.setObjectName("sectionTitle")
        right_lay.addWidget(susp_lbl)

        self._susp_table = QTableWidget(0, 2)
        self._susp_table.setHorizontalHeaderLabels(["Process", "Since"])
        self._susp_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._susp_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._susp_table.setFixedHeight(130)
        self._susp_table.verticalHeader().setVisible(False)
        right_lay.addWidget(self._susp_table)

        # Event log
        log_lbl = QLabel("EVENT LOG")
        log_lbl.setObjectName("sectionTitle")
        right_lay.addWidget(log_lbl)

        self._log = NotificationLog()
        right_lay.addWidget(self._log)

        splitter.addWidget(right)
        splitter.setSizes([820, 280])

        # ── Refresh timer for process table ─────────────────────────────────
        self._proc_timer = QTimer(self)
        self._proc_timer.timeout.connect(self._refresh_proc_table)
        self._proc_timer.start(3000)

    # ── System tray ──────────────────────────────────────────────────────────

    def _setup_tray(self):
        self._tray = QSystemTrayIcon(self)
        self._tray.setToolTip("System Resource Optimizer")

        # Handle PyInstaller path resolution
        import sys
        if getattr(sys, 'frozen', False):
            _root = sys._MEIPASS
        else:
            _root = os.path.dirname(os.path.abspath(__file__))

        _icon_paths = [
            os.path.join(_root, "assets", "icon_proper.png"),
            os.path.join(_root, "assets", "icon.png"),
        ]
        for _p in _icon_paths:
            if os.path.isfile(_p):
                self._tray.setIcon(QIcon(_p))
                self.setWindowIcon(QIcon(_p)) # Also set main window icon
                break
        else:
            self._tray.setIcon(self.style().standardIcon(
                self.style().StandardPixmap.SP_ComputerIcon))

        tray_menu = QMenu()
        show_action = QAction("Show Dashboard", self)
        show_action.triggered.connect(self.show)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit)
        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)
        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(
            lambda r: self.show()
            if r == QSystemTrayIcon.ActivationReason.DoubleClick else None
        )
        self._tray.show()

    # ── Pipeline result handler (runs on main thread via signal) ─────────────

    def _on_result(self, result: PipelineResult):
        f = result.features

        # Metric cards — use raw (unsmoothed) values for display accuracy
        self._card_cpu.update(f.get("cpu_percent_raw", f.get("cpu_percent", 0)))
        self._card_mem.update(f.get("mem_percent_raw", f.get("mem_percent", 0)))
        temp = f.get("cpu_temp_c", -1)
        self._card_temp.update(temp if temp > 0 else 0)
        self._card_swap.update(f.get("swap_percent", 0))

        # Charts — use raw values for accuracy
        self._chart_cpu.push(f.get("cpu_percent_raw", f.get("cpu_percent", 0)))
        self._chart_mem.push(f.get("mem_percent_raw", f.get("mem_percent", 0)))
        if temp > 0:
            self._chart_temp.push(min(temp, 100))

        # AI panel
        self._conf_panel.update(result.confidence, result.predicted_cpu, result.predicted_mem, result.calibrating)
        if result.calibrating:
            self._cal_banner.setVisible(True)

        # Extended Telemetry logging (every ~1 min)
        self._cycle_count += 1
        if self._cycle_count % 30 == 0:
            uptime_hrs = f.get('uptime_sec', 0) / 3600
            self._log.add(
                f"Health Check: {f.get('process_count', 0)} processes, "
                f"Up: {uptime_hrs:.1f}h. "
                f"Net: {f.get('net_sent_mbps', 0):.1f}↑ {f.get('net_recv_mbps', 0):.1f}↓ MB/s. "
                f"Disk: {f.get('disk_read_mbps', 0):.1f}R {f.get('disk_write_mbps', 0):.1f}W MB/s.",
                "info"
            )

        # Status dot colour
        if result.confidence >= CONFIDENCE_THRESHOLD:
            self._status_dot.setStyleSheet(f"color: {ACCENT_CRIT}; font-size: 16px;")
            self._status_lbl.setText("Action Mode")
        elif result.warning_active:
            self._status_dot.setStyleSheet(f"color: {ACCENT_WARN}; font-size: 16px;")
            self._status_lbl.setText("Warning")
        else:
            self._status_dot.setStyleSheet(f"color: {ACCENT}; font-size: 16px;")
            self._status_lbl.setText("Live")

        # ── Auto-Pilot logic ─────────────────────────────────────────────────
        if self._autopilot_enabled:
            now = time.monotonic()
            if (result.confidence >= CONFIDENCE_THRESHOLD
                    and now - self._autopilot_last_fire > 45):
                self._autopilot_last_fire = now
                ap_result = self._pipeline.trigger_boost()
                self._log.add(f"[Auto-Pilot] {ap_result.message}", "action")
                self._notifier.notify_autopilot(ap_result.message)
                self._undo_btn.setEnabled(True)
                self._refresh_susp_table()

        # Log action events (from pipeline's own evaluate() call)
        if result.action.action_taken:
            self._log.add(result.action.message, "action")
            self._undo_btn.setEnabled(True)
            self._refresh_susp_table()
        
        # Periodic sync check: Ensure Undo button is active if any processes are suspended
        if self._pipeline.get_suspended_processes():
            if not self._undo_btn.isEnabled():
                self._undo_btn.setEnabled(True)

    def _on_calibration_progress(self, elapsed: int, total: int):
        """Called every second during calibration. elapsed=-1 means done."""
        if 0 <= elapsed <= 1:
            # First tick — show the banner
            self._cal_banner.setVisible(True)
            self._cal_bar.setRange(0, total)
            self._log.add("🔬 Calibrating to your hardware — 90s, please wait...", "info")
        elif elapsed == -1:
            # Done
            self._cal_text.setText("✅  Calibrated to your hardware! AI mode active.")
            self._cal_bar.setValue(total)
            self._log.add("✅ Calibration complete — AI predictions are now personalised to this machine.", "action")
            self._notifier.send("✅ Calibration Complete",
                                "The AI model is now calibrated to your hardware.", timeout=5)
            QTimer.singleShot(3500, lambda: self._cal_banner.setVisible(False))
        else:
            remaining = total - elapsed
            self._cal_text.setText(
                f"🔬  Calibrating to your hardware — {remaining}s remaining..."
            )
            self._cal_bar.setValue(elapsed)

    # ── Button handlers ──────────────────────────────────────────────────────

    def _show_tray_message(self, title: str, message: str):
        """Show a tray bubble — cross-platform, always available."""
        if hasattr(self, "_tray") and self._tray.isSystemTrayAvailable():
            self._tray.showMessage(title, message,
                QSystemTrayIcon.MessageIcon.Information, 5000)

    def _on_autopilot_toggle(self, checked: bool):
        self._autopilot_enabled = checked
        if checked:
            self._autopilot_btn.setObjectName("autopilotOn")
            self._autopilot_btn.setText("🤖  Auto-Pilot  ON")
            msg = "Auto-Pilot ON — optimizer will act automatically."
            self._log.add(msg, "action")
            self._notifier.send("🤖 Auto-Pilot Enabled", msg, timeout=4)
        else:
            self._autopilot_btn.setObjectName("autopilotOff")
            self._autopilot_btn.setText("🤖  Auto-Pilot  OFF")
            msg = "Auto-Pilot OFF — manual control restored."
            self._log.add(msg, "info")
            self._notifier.send("🤖 Auto-Pilot Disabled", msg, timeout=4)
        self._autopilot_btn.style().unpolish(self._autopilot_btn)
        self._autopilot_btn.style().polish(self._autopilot_btn)

    def _on_boost(self):
        result = self._pipeline.trigger_boost()
        self._log.add(result.message, "action")
        self._notifier.notify_boost()
        self._undo_btn.setEnabled(True)
        self._refresh_susp_table()
        self._refresh_proc_table()
        self._refresh_susp_table()

    def _on_undo(self):
        result = self._pipeline.trigger_undo()
        self._log.add(result.message, "action")
        self._notifier.notify_undo()
        self._undo_btn.setEnabled(False)
        self._refresh_susp_table()

    # ── Table refresh ────────────────────────────────────────────────────────

    def _refresh_proc_table(self):
        if not self.isVisible():
            return
        import psutil
        try:
            procs = sorted(
                psutil.process_iter(["pid", "name", "status", "memory_percent"]),
                key=lambda p: p.info.get("memory_percent") or 0,
                reverse=True
            )[:20]

            self._proc_table.setRowCount(len(procs))
            for row, proc in enumerate(procs):
                try:
                    info = proc.info
                    status = info.get("status", "")
                    color = QColor(ACCENT_CRIT) if status == "stopped" else QColor(TEXT_PRI)

                    items = [
                        str(info.get("pid", "")),
                        info.get("name", ""),
                        f"{info.get('memory_percent') or 0:.1f}",
                        status,
                    ]
                    for col, text in enumerate(items):
                        item = QTableWidgetItem(text)
                        item.setForeground(color)
                        self._proc_table.setItem(row, col, item)
                except:
                    pass
        except:
            pass

    def _refresh_susp_table(self):
        suspended = self._pipeline.get_suspended_processes()
        self._susp_table.setRowCount(len(suspended))
        now = time.monotonic()
        for row, rec in enumerate(suspended):
            elapsed = int(now - rec.suspended_at)
            self._susp_table.setItem(row, 0, QTableWidgetItem(rec.name))
            self._susp_table.setItem(row, 1, QTableWidgetItem(f"{elapsed}s ago"))
        if not suspended:
            self._undo_btn.setEnabled(False)

    # ── Window events ────────────────────────────────────────────────────────

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self._tray.showMessage("Still running", "Optimizer is active in the tray.", QSystemTrayIcon.MessageIcon.Information, 2000)

    def _quit(self):
        self._pipeline.stop()
        QApplication.quit()


# ── Entry point ───────────────────────────────────────────────────────────────
def run_pyqt_ui():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)

    # Dark palette fallback
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_PRI))
    palette.setColor(QPalette.ColorRole.Base, QColor(BG_CARD))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(BG_CARD2))
    app.setPalette(palette)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


def main():
    run_pyqt_ui()


if __name__ == "__main__":
    main()
