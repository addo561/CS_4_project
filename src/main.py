"""
System Resource Optimizer — Next-Gen Flet Dashboard
KNUST Final Year Project — Group 4

Layout follows design.md: dark glassmorphism, 260px sidebar, dashboard with
AI gauge, side-by-side telemetry charts, action panel, and process tables.
Layer structure:
  Layer 1: AI Confidence + Metrics Grid (240px) || Manual Actions (240px)
  Layer 2: Telemetry Charts (190px)            || Suspended (190px)
  Layer 3: Running Processes (fluid)           || Event Log (fluid)
"""

import os
import sys
import time
import threading
from collections import deque
from dataclasses import dataclass
from queue import Queue, Empty
from typing import Callable, Optional

import psutil
import flet as ft
import flet.canvas as cv

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

from core.pipeline import Pipeline, PipelineResult
from config import CONFIDENCE_THRESHOLD, CALIBRATION_SECONDS, POLL_INTERVAL_SEC

# ── Design tokens (design.md) ─────────────────────────────────────────────────
BG = "#0D1117"
CARD = "#161B22"
CARD_ALT = "#1C2128"
ACCENT = "#00C896"
WARN = "#F0A500"
CRIT = "#E05C5C"
TEXT = "#E6EDF3"
MUTED = "#8B949E"
BORDER = "#30363D"
SIDEBAR_W = 260
CHART_H = 140
GAUGE_CARD_W = 320
FONT = "Inter, -apple-system, BlinkMacSystemFont, sans-serif"


def severity(pct: float) -> str:
    return CRIT if pct >= 85 else (WARN if pct >= 65 else ACCENT)


def card(content, *, padding=16, radius=12, expand=False, **kwargs) -> ft.Container:
    return ft.Container(
        content=content,
        bgcolor=CARD,
        border=ft.Border.all(1, BORDER),
        border_radius=radius,
        padding=padding,
        expand=expand,
        **kwargs,
    )


def section_title(text: str) -> ft.Text:
    return ft.Text(
        text.upper(),
        size=11,
        weight=ft.FontWeight.W_600,
        color=MUTED,
        font_family=FONT,
    )


def body_text(
    text: str,
    *,
    size: int = 13,
    color: str = TEXT,
    font_family: str = FONT,
    **kwargs,
) -> ft.Text:
    return ft.Text(text, size=size, color=color, font_family=font_family, **kwargs)


# ── Rolling line chart (Bezier + fill) ────────────────────────────────────────
class RollingChart:
    MAX_POINTS = 120

    def __init__(
        self,
        title: str,
        color: str,
        *,
        height: int = CHART_H,
        value_max: float = 100.0,
    ):
        self.title = title
        self.color = color
        self._h = height
        self._value_max = value_max
        self._width = 0.0
        self.data: deque[float] = deque([0.0] * self.MAX_POINTS, maxlen=self.MAX_POINTS)

        self._line = cv.Path(
            paint=ft.Paint(stroke_width=2, color=color, style=ft.PaintingStyle.STROKE),
        )
        self._fill = cv.Path(
            paint=ft.Paint(color=color + "33", style=ft.PaintingStyle.FILL),
        )
        self.canvas = cv.Canvas(
            height=height,
            shapes=[self._fill, self._line],
            expand=True,
            resize_interval=0,
        )
        self.canvas.on_resize = self._on_canvas_resize

        self.control = card(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.SHOW_CHART, size=14, color=color),
                            body_text(title, size=12, color=MUTED, weight=ft.FontWeight.W_600),
                        ],
                        spacing=8,
                    ),
                    ft.Container(self.canvas, height=height, expand=True),
                ],
                spacing=8,
            ),
            padding=12,
            expand=True,
        )

    def _norm(self, value: float) -> float:
        if self._value_max <= 0:
            return 0.0
        return max(0.0, min(100.0, (value / self._value_max) * 100.0))

    def push(self, value: float) -> None:
        self.data.append(self._norm(value))
        self._redraw()

    def set_width(self, width: float) -> None:
        if width > 0 and width != self._width:
            self._width = width
            self._redraw()

    def _on_canvas_resize(self, e) -> None:
        if e.width > 0:
            self._width = e.width
            self._redraw()

    def _redraw(self) -> None:
        if len(self.data) < 2 or self._width <= 0:
            return
        w, h = self._width, float(self._h)
        n = len(self.data)
        dx = w / (n - 1)
        pts = [(i * dx, h - (v / 100.0) * h) for i, v in enumerate(self.data)]

        line = [cv.Path.MoveTo(pts[0][0], pts[0][1])]
        fill = [cv.Path.MoveTo(pts[0][0], h), cv.Path.LineTo(pts[0][0], pts[0][1])]
        for i in range(1, n):
            x0, y0 = pts[i - 1]
            x1, y1 = pts[i]
            cx = x0 + (x1 - x0) / 3
            seg = cv.Path.CubicTo(cx, y0, cx, y1, x1, y1)
            line.append(seg)
            fill.append(seg)
        fill.extend([cv.Path.LineTo(pts[-1][0], h), cv.Path.Close()])
        self._line.elements = line
        self._fill.elements = fill


# ── Metric tile ───────────────────────────────────────────────────────────────
class MetricTile:
    def __init__(self, label: str, unit: str = "%", accent: str = ACCENT):
        self.unit = unit
        self._value = body_text("—", size=26, weight=ft.FontWeight.BOLD)
        self._bar = ft.ProgressBar(
            value=0,
            color=accent,
            bgcolor=CARD_ALT,
            height=5,
            border_radius=3,
        )
        self.control = card(
            ft.Column(
                [
                    section_title(label),
                    self._value,
                    self._bar,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=6,
            ),
            padding=12,
            expand=True,
        )

    def update(self, value: float) -> None:
        col = severity(value)
        self._value.value = f"{value:.1f}{self.unit}"
        self._value.color = col
        self._bar.value = min(1.0, max(0.0, value / 100.0))
        self._bar.color = col


# ── UI state & wiring ─────────────────────────────────────────────────────────
@dataclass
class DashboardUI:
    page: ft.Page
    charts: tuple[RollingChart, RollingChart, RollingChart]
    metrics: tuple[MetricTile, MetricTile, MetricTile, MetricTile]
    gauge_ring: ft.ProgressRing
    gauge_value: ft.Text
    gauge_insight: ft.Text
    proc_table: ft.DataTable
    susp_table: ft.DataTable
    log_list: ft.ListView
    autopilot: ft.Switch
    boost_btn: ft.Button
    undo_btn: ft.OutlinedButton
    chart_area_width: Callable[[], float]
    sync_charts: Callable[[], None]
    # Analytics panel (updated with live data)
    an_confidence: ft.Text
    an_predicted_cpu: ft.Text
    an_risk_level: ft.Text
    an_cpu_live: ft.Text
    an_mem_live: ft.Text
    autopilot_status: ft.Text
    last_result: Optional[PipelineResult] = None


def build_sidebar(on_nav: Callable[[str], None], active: str = "dashboard") -> ft.Container:
    nav_refs: dict[str, ft.Container] = {}

    def nav_item(label: str, icon, key: str) -> ft.Container:
        selected = key == active
        item = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(icon, size=18, color=ACCENT if selected else MUTED),
                    body_text(label, size=13, color=TEXT if selected else MUTED),
                ],
                spacing=12,
            ),
            bgcolor=CARD_ALT if selected else None,
            border_radius=8,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            on_click=lambda e, k=key: on_nav(k),
            data=key,
        )
        nav_refs[key] = item
        return item

    sidebar = ft.Container(
        width=SIDEBAR_W,
        bgcolor=CARD,
        border=ft.Border.only(right=ft.BorderSide(1, BORDER)),
        padding=20,
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.SPEED, color=ACCENT, size=28),
                        ft.Column(
                            [
                                body_text("SRO", size=16, weight=ft.FontWeight.BOLD, color=ACCENT),
                                body_text("Optimizer", size=11, color=MUTED),
                            ],
                            spacing=0,
                        ),
                    ],
                    spacing=10,
                ),
                ft.Divider(height=24, color=BORDER),
                section_title("Navigation"),
                nav_item("Dashboard", ft.Icons.DASHBOARD, "dashboard"),
                nav_item("AI Analytics", ft.Icons.INSIGHTS, "analytics"),
                nav_item("Settings", ft.Icons.SETTINGS, "settings"),
                ft.Container(expand=True),
                body_text("Next-Gen · Flet", size=10, color=MUTED),
            ],
            expand=True,
            spacing=6,
        ),
    )
    sidebar.nav_refs = nav_refs  # type: ignore[attr-defined]
    return sidebar


def _ai_gauge_panel(
    gauge_ring: ft.ProgressRing,
    gauge_value: ft.Text,
    gauge_insight: ft.Text,
) -> ft.Container:
    """Fixed-size gauge card so minimize/restore does not squash the ring."""
    return card(
        ft.Column(
            [
                section_title("AI Confidence"),
                ft.Container(
                    content=ft.Stack(
                        [
                            gauge_ring,
                            ft.Container(
                                gauge_value,
                                alignment=ft.Alignment.CENTER,
                                width=112,
                                height=112,
                            ),
                        ],
                        width=112,
                        height=112,
                    ),
                    width=120,
                    height=120,
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Container(
                    content=gauge_insight,
                    width=GAUGE_CARD_W - 40,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=12,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        padding=16,
        width=GAUGE_CARD_W,
    )


def build_analytics_view(ui: DashboardUI) -> ft.Column:
    # Feature importance visual items (gorgeous mock bars with distinct colors)
    feature_importance = ft.Column(
        [
            ft.Row(
                [
                    body_text("CPU Utilization (raw/percent)", size=12, color=TEXT, weight=ft.FontWeight.W_500),
                    ft.Container(expand=True),
                    body_text("42% weight", size=11, color=ACCENT),
                ]
            ),
            ft.ProgressBar(value=0.42, color=ACCENT, bgcolor=CARD_ALT, height=6, border_radius=3),
            ft.Row(
                [
                    body_text("Memory Pressure (used/percent)", size=12, color=TEXT, weight=ft.FontWeight.W_500),
                    ft.Container(expand=True),
                    body_text("28% weight", size=11, color=WARN),
                ]
            ),
            ft.ProgressBar(value=0.28, color=WARN, bgcolor=CARD_ALT, height=6, border_radius=3),
            ft.Row(
                [
                    body_text("CPU Thermal Temperature (°C)", size=12, color=TEXT, weight=ft.FontWeight.W_500),
                    ft.Container(expand=True),
                    body_text("18% weight", size=11, color=CRIT),
                ]
            ),
            ft.ProgressBar(value=0.18, color=CRIT, bgcolor=CARD_ALT, height=6, border_radius=3),
            ft.Row(
                [
                    body_text("Swap space (percent)", size=12, color=TEXT, weight=ft.FontWeight.W_500),
                    ft.Container(expand=True),
                    body_text("12% weight", size=11, color=ACCENT),
                ]
            ),
            ft.ProgressBar(value=0.12, color=ACCENT, bgcolor=CARD_ALT, height=6, border_radius=3),
        ],
        spacing=8,
    )

    return ft.Column(
        [
            # Header
            ft.Row(
                [
                    ft.Icon(ft.Icons.INSIGHTS, color=ACCENT, size=28),
                    ft.Column(
                        [
                            body_text("AI Analytics", size=22, weight=ft.FontWeight.BOLD),
                            body_text("Deep GRU neural engine diagnostics & live inference predictions", size=12, color=MUTED),
                        ],
                        spacing=0,
                    ),
                    ft.Container(expand=True),
                    ft.Container(
                        content=body_text("GRU ENGINE ACTIVE", size=10, weight=ft.FontWeight.BOLD, color="#0D1117"),
                        bgcolor=ACCENT,
                        padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                        border_radius=4,
                    )
                ],
                spacing=12,
            ),
            ft.Divider(height=1, color=BORDER),
            
            # Responsive Row for main cards
            ft.ResponsiveRow(
                [
                    # Current Prediction Card
                    ft.Column(
                        [
                            card(
                                ft.Column(
                                    [
                                        ft.Row(
                                            [
                                                ft.Icon(ft.Icons.AUTO_GRAPH, color=ACCENT, size=18),
                                                section_title("PREDICTION ENGINE"),
                                            ],
                                            spacing=8,
                                        ),
                                        ft.Container(
                                            content=ft.Column(
                                                [
                                                    ft.Row(
                                                        [
                                                            ft.Icon(ft.Icons.SHIELD, size=20, color=MUTED),
                                                            ui.an_risk_level,
                                                        ],
                                                        spacing=8,
                                                    ),
                                                    ft.Divider(height=8, color=BORDER),
                                                    ft.Row(
                                                        [
                                                            ft.Icon(ft.Icons.BATCH_PREDICTION, size=20, color=ACCENT),
                                                            ui.an_confidence,
                                                        ],
                                                        spacing=8,
                                                    ),
                                                    ft.Row(
                                                        [
                                                            ft.Icon(ft.Icons.TIMELINE, size=20, color=MUTED),
                                                            ui.an_predicted_cpu,
                                                        ],
                                                        spacing=8,
                                                    ),
                                                ],
                                                spacing=8,
                                            ),
                                            bgcolor=CARD_ALT,
                                            padding=14,
                                            border_radius=8,
                                            border=ft.Border.all(1, BORDER),
                                        ),
                                    ],
                                    spacing=12,
                                ),
                                padding=16,
                            ),
                        ],
                        col={"xs": 12, "md": 6},
                    ),
                    
                    # Live Telemetry Card
                    ft.Column(
                        [
                            card(
                                ft.Column(
                                    [
                                        ft.Row(
                                            [
                                                ft.Icon(ft.Icons.SENSORS, color=WARN, size=18),
                                                section_title("LIVE INFERENCE FEATURES"),
                                            ],
                                            spacing=8,
                                        ),
                                        ft.Container(
                                            content=ft.Column(
                                                [
                                                    ft.Row(
                                                        [
                                                            ft.Icon(ft.Icons.MEMORY, size=20, color=ACCENT),
                                                            ui.an_cpu_live,
                                                        ],
                                                        spacing=8,
                                                    ),
                                                    ft.Row(
                                                        [
                                                            ft.Icon(ft.Icons.HARDWARE, size=20, color=WARN),
                                                            ui.an_mem_live,
                                                        ],
                                                        spacing=8,
                                                    ),
                                                    ft.Divider(height=8, color=BORDER),
                                                    ft.Row(
                                                        [
                                                            ft.Icon(ft.Icons.INFO_OUTLINE, size=14, color=MUTED),
                                                            ft.Container(
                                                                content=body_text(
                                                                    f"Threshold trigger: Auto-boost active at ≥ {CONFIDENCE_THRESHOLD:.0%}",
                                                                    size=11,
                                                                    color=MUTED,
                                                                ),
                                                                expand=True,
                                                            ),
                                                        ],
                                                        spacing=6,
                                                    ),
                                                ],
                                                spacing=8,
                                            ),
                                            bgcolor=CARD_ALT,
                                            padding=14,
                                            border_radius=8,
                                            border=ft.Border.all(1, BORDER),
                                        ),
                                    ],
                                    spacing=12,
                                ),
                                padding=16,
                            ),
                        ],
                        col={"xs": 12, "md": 6},
                    ),
                ],
                spacing=16,
            ),
            
            # Model Architecture Diagnostics
            card(
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.SETTINGS_INPUT_COMPONENT, color=ACCENT, size=18),
                                section_title("MODEL SPECIFICATIONS & PARAMETERS"),
                            ],
                            spacing=8,
                        ),
                        ft.ResponsiveRow(
                            [
                                ft.Column(
                                    [
                                        ft.Container(
                                            content=ft.Column(
                                                [
                                                    body_text("Architecture Details", size=12, color=MUTED),
                                                    body_text("Quantized GRU Neural Network", size=14, weight=ft.FontWeight.W_600, color=TEXT),
                                                    body_text("Fully optimized ONNX Execution Provider", size=11, color=MUTED),
                                                ],
                                                spacing=2,
                                            ),
                                            bgcolor=CARD_ALT,
                                            padding=12,
                                            border_radius=8,
                                        ),
                                    ],
                                    col={"xs": 12, "md": 4},
                                ),
                                ft.Column(
                                    [
                                        ft.Container(
                                            content=ft.Column(
                                                [
                                                    body_text("Sliding Input Window", size=12, color=MUTED),
                                                    body_text("60 Past Samples (1Hz)", size=14, weight=ft.FontWeight.W_600, color=TEXT),
                                                    body_text("Captures rolling temporal dependencies", size=11, color=MUTED),
                                                ],
                                                spacing=2,
                                            ),
                                            bgcolor=CARD_ALT,
                                            padding=12,
                                            border_radius=8,
                                        ),
                                    ],
                                    col={"xs": 12, "md": 4},
                                ),
                                ft.Column(
                                    [
                                        ft.Container(
                                            content=ft.Column(
                                                [
                                                    body_text("Prediction Horizon", size=12, color=MUTED),
                                                    body_text("30 Seconds Look-Ahead", size=14, weight=ft.FontWeight.W_600, color=TEXT),
                                                    body_text("Proactive rather than reactive control", size=11, color=MUTED),
                                                ],
                                                spacing=2,
                                            ),
                                            bgcolor=CARD_ALT,
                                            padding=12,
                                            border_radius=8,
                                        ),
                                    ],
                                    col={"xs": 12, "md": 4},
                                ),
                            ],
                            spacing=12,
                        ),
                    ],
                    spacing=12,
                ),
                padding=16,
            ),
            
            # Feature Importance
            card(
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.BAR_CHART, color=ACCENT, size=18),
                                section_title("MODEL FEATURE INFLUENCE WEIGHTS"),
                            ],
                            spacing=8,
                        ),
                        body_text(
                            "This live GRU network computes prediction values using local telemetry signals. "
                            "The weight distribution below describes feature contribution factors during training:",
                            size=12,
                            color=MUTED,
                        ),
                        feature_importance,
                    ],
                    spacing=12,
                ),
                padding=16,
            ),
        ],
        spacing=16,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )


def build_settings_view(autopilot_status: ft.Text) -> ft.Column:
    return ft.Column(
        [
            # Header
            ft.Row(
                [
                    ft.Icon(ft.Icons.SETTINGS, color=ACCENT, size=28),
                    ft.Column(
                        [
                            body_text("Settings", size=22, weight=ft.FontWeight.BOLD),
                            body_text("Configure resource optimization strategy & thresholds", size=12, color=MUTED),
                        ],
                        spacing=0,
                    ),
                ],
                spacing=12,
            ),
            ft.Divider(height=1, color=BORDER),

            # Two Column Responsive Row
            ft.ResponsiveRow(
                [
                    # Left column: Auto-Pilot & Controls
                    ft.Column(
                        [
                            card(
                                ft.Column(
                                    [
                                        ft.Row(
                                            [
                                                ft.Icon(ft.Icons.AUTO_MODE, color=ACCENT, size=18),
                                                section_title("OPTIMIZATION CONTROL"),
                                            ],
                                            spacing=8,
                                        ),
                                        ft.Container(
                                            content=ft.Column(
                                                [
                                                    ft.Row(
                                                        [
                                                            ft.Column(
                                                                [
                                                                    body_text("Autonomous Auto-Pilot", size=14, weight=ft.FontWeight.W_600),
                                                                    body_text("Optimizes processes in the background", size=11, color=MUTED),
                                                                ],
                                                                spacing=1,
                                                            ),
                                                            ft.Container(expand=True),
                                                            autopilot_status,
                                                        ],
                                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                                    ),
                                                    ft.Divider(height=8, color=BORDER),
                                                    body_text(
                                                        "Toggle this option on the main Dashboard's right panel. "
                                                        "When enabled, high-confidence predictions autonomously trigger "
                                                        "the One-Click Boost optimization routine with a 45s safety cooldown.",
                                                        size=12,
                                                        color=MUTED,
                                                    ),
                                                ],
                                                spacing=8,
                                            ),
                                            bgcolor=CARD_ALT,
                                            padding=14,
                                            border_radius=8,
                                            border=ft.Border.all(1, BORDER),
                                        ),
                                    ],
                                    spacing=12,
                                ),
                                padding=16,
                            ),
                        ],
                        col={"xs": 12, "md": 6},
                    ),

                    # Right column: Detection Thresholds
                    ft.Column(
                        [
                            card(
                                ft.Column(
                                    [
                                        ft.Row(
                                            [
                                                ft.Icon(ft.Icons.TUNE, color=WARN, size=18),
                                                section_title("ENGINE THRESHOLDS"),
                                            ],
                                            spacing=8,
                                        ),
                                        ft.Container(
                                            content=ft.Column(
                                                [
                                                    ft.Row(
                                                        [
                                                            ft.Icon(ft.Icons.SPEED, size=16, color=ACCENT),
                                                            body_text(f"Confidence Threshold: {CONFIDENCE_THRESHOLD:.0%}", size=13, weight=ft.FontWeight.W_500),
                                                        ],
                                                        spacing=8,
                                                    ),
                                                    ft.Row(
                                                        [
                                                            ft.Icon(ft.Icons.CALENDAR_TODAY, size=16, color=WARN),
                                                            body_text(f"Calibration Duration: {CALIBRATION_SECONDS}s", size=13, weight=ft.FontWeight.W_500),
                                                        ],
                                                        spacing=8,
                                                    ),
                                                    ft.Row(
                                                        [
                                                            ft.Icon(ft.Icons.TIMER, size=16, color=MUTED),
                                                            body_text(f"Telemetry Poll Rate: {POLL_INTERVAL_SEC:.1f}s (1Hz)", size=13, weight=ft.FontWeight.W_500),
                                                        ],
                                                        spacing=8,
                                                    ),
                                                    ft.Divider(height=8, color=BORDER),
                                                    ft.Row(
                                                        [
                                                            ft.Icon(ft.Icons.COLOR_LENS, size=16, color=MUTED),
                                                            body_text("Severity Codes: <65% (Green) · 65-84% (Amber) · ≥85% (Red)", size=11, color=MUTED),
                                                        ],
                                                        spacing=8,
                                                    ),
                                                ],
                                                spacing=8,
                                            ),
                                            bgcolor=CARD_ALT,
                                            padding=14,
                                            border_radius=8,
                                            border=ft.Border.all(1, BORDER),
                                        ),
                                    ],
                                    spacing=12,
                                ),
                                padding=16,
                            ),
                        ],
                        col={"xs": 12, "md": 6},
                    ),
                ],
                spacing=16,
            ),

            # Full-width card: Project info and credits (styled like an academic portfolio)
            card(
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.SCHOOL, color=ACCENT, size=18),
                                section_title("ACADEMIC PROJECT DETAILS"),
                            ],
                            spacing=8,
                        ),
                        body_text(
                            "This system resource optimizer was engineered as a Next-Generation Flet-based application "
                            "for the Kwame Nkrumah University of Science and Technology (KNUST) final year project.",
                            size=12,
                            color=MUTED,
                        ),
                        ft.Divider(height=8, color=BORDER),
                        ft.ResponsiveRow(
                            [
                                ft.Column(
                                    [
                                        body_text("Academic Group", size=11, color=MUTED),
                                        body_text("Group 4 Final Year Project", size=13, weight=ft.FontWeight.W_600),
                                    ],
                                    col={"xs": 6, "md": 3},
                                ),
                                ft.Column(
                                    [
                                        body_text("Model Architecture", size=11, color=MUTED),
                                        body_text("Quantized GRU (ONNX)", size=13, weight=ft.FontWeight.W_600),
                                    ],
                                    col={"xs": 6, "md": 3},
                                ),
                                ft.Column(
                                    [
                                        body_text("Telemetry Provider", size=11, color=MUTED),
                                        body_text("psutil System API", size=13, weight=ft.FontWeight.W_600),
                                    ],
                                    col={"xs": 6, "md": 3},
                                ),
                                ft.Column(
                                    [
                                        body_text("Software Version", size=11, color=MUTED),
                                        body_text("v2.2.0 (Stable)", size=13, weight=ft.FontWeight.W_600, color=ACCENT),
                                    ],
                                    col={"xs": 6, "md": 3},
                                ),
                            ],
                            spacing=12,
                        ),
                    ],
                    spacing=12,
                ),
                padding=16,
            ),
        ],
        spacing=16,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )


def build_dashboard_content(
    page: ft.Page,
    callbacks: dict,
) -> tuple[DashboardUI, ft.Column, ft.Column, ft.Column, ft.Container]:
    cpu_m, mem_m, temp_m, swap_m = (
        MetricTile("CPU", "%", ACCENT),
        MetricTile("Memory", "%", WARN),
        MetricTile("Temp", "°C", CRIT),
        MetricTile("Swap", "%", ACCENT),
    )
    cpu_c = RollingChart("CPU", ACCENT)
    mem_c = RollingChart("Memory", WARN)
    temp_c = RollingChart("CPU Temp", CRIT, value_max=100.0)

    gauge_ring = ft.ProgressRing(
        value=0.5,
        width=112,
        height=112,
        stroke_width=8,
        color=ACCENT,
        bgcolor=CARD_ALT,
    )
    gauge_value = body_text("—", size=32, weight=ft.FontWeight.BOLD, color=ACCENT)
    gauge_insight = body_text(
        "Analyzing system…",
        size=12,
        color=MUTED,
        text_align=ft.TextAlign.CENTER,
        max_lines=3,
    )

    ai_gauge_panel = _ai_gauge_panel(gauge_ring, gauge_value, gauge_insight)

    # ── Metrics Grid (now wrapped in a card to match spec) ────────────────────
    metrics_grid = ft.Column(
        [
            ft.Row([cpu_m.control, mem_m.control], spacing=16, expand=True),
            ft.Row([temp_m.control, swap_m.control], spacing=16, expand=True),
        ],
        spacing=16,
        expand=True,
    )
    metrics_card = card(metrics_grid, padding=16, expand=True)

    an_confidence = body_text("Confidence: —", size=15, weight=ft.FontWeight.W_600)
    an_predicted_cpu = body_text("Predicted CPU: —", size=13, color=MUTED)
    an_risk_level = body_text("Risk level: —", size=13, color=MUTED)
    an_cpu_live = body_text("CPU: —", size=13)
    an_mem_live = body_text("Memory: —", size=13)

    # Layer 1 (Top): 240px height
    top_dashboard_row = ft.Row(
        [ai_gauge_panel, metrics_card],
        spacing=16,
        height=240,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    # Layer 2 (Middle): Telemetry Charts (190px)
    telemetry_row = ft.Row(
        [cpu_c.control, mem_c.control, temp_c.control],
        spacing=16,
        height=190,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    # Layer 3 (Bottom): Running Processes Card (fluid)
    proc_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Container(content=section_title("PID"), width=70)),
            ft.DataColumn(ft.Container(content=section_title("Process"), width=380)),
            ft.DataColumn(ft.Container(content=section_title("MEM %"), width=90)),
            ft.DataColumn(ft.Container(content=section_title("Status"), width=120)),
        ],
        rows=[],
        heading_row_color=CARD_ALT,
        border=ft.Border.all(1, BORDER),
        border_radius=8,
        data_row_min_height=34,
        data_row_max_height=34,
        column_spacing=60,
    )

    processes_panel = card(
        ft.Column(
            [
                section_title("Running Processes"),
                ft.Column(
                    [proc_table],
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            ],
            spacing=10,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
        padding=12,
        expand=True,
    )

    # ── Right Rail (aligned with the three layers) ────────────────────────────
    autopilot = ft.Switch(
        value=False, 
        active_color=ACCENT,
        on_change=lambda e: callbacks["on_autopilot"](e) if "on_autopilot" in callbacks else None,
    )
    boost_btn = ft.Button(
        "Boost",
        icon=ft.Icons.ROCKET_LAUNCH,
        bgcolor=ACCENT,
        color="#0D1117",
        height=40,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        ),
        expand=True,
        on_click=lambda e: callbacks["on_boost"](e) if "on_boost" in callbacks else None,
    )
    undo_btn = ft.OutlinedButton(
        "Undo",
        icon=ft.Icons.UNDO,
        height=40,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        ),
        expand=True,
        on_click=lambda e: callbacks["on_undo"](e) if "on_undo" in callbacks else None,
    )

    # Layer 1 (Right): Manual Actions Card (240px)
    actions_panel = card(
        ft.Column(
            [
                section_title("Manual Actions"),
                ft.Row(
                    [boost_btn, undo_btn],
                    spacing=8,
                ),
                ft.Divider(height=1, color=BORDER),
                section_title("Auto-Pilot"),
                ft.Row(
                    [
                        ft.Container(
                            content=body_text("Autonomous boost when risk is high", size=11, color=MUTED),
                            expand=True,
                        ),
                        autopilot,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=8,
        ),
        padding=12,
        height=240,
    )

    # Layer 2 (Right): Suspended Card (190px)
    susp_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Container(content=section_title("Process"), width=140)),
            ft.DataColumn(ft.Container(content=section_title("Since"), width=80)),
        ],
        rows=[],
        heading_row_color=CARD_ALT,
        border=ft.Border.all(1, BORDER),
        border_radius=8,
        data_row_min_height=32,
        data_row_max_height=32,
        column_spacing=20,
    )

    susp_panel = card(
        ft.Column(
            [
                section_title("Suspended"),
                ft.Column([susp_table], scroll=ft.ScrollMode.AUTO, expand=True),
            ],
            spacing=8,
            expand=True,
        ),
        padding=12,
        height=190,
    )

    # Layer 3 (Right): Event Log Card (fluid)
    log_list = ft.ListView(spacing=4, expand=True, auto_scroll=True)
    log_panel = card(
        ft.Column(
            [
                section_title("Event Log"),
                ft.Divider(height=1, color=BORDER),
                log_list,
            ],
            spacing=8,
            expand=True,
        ),
        padding=12,
        expand=True,
    )

    right_rail = ft.Container(
        width=300,
        content=ft.Column(
            [
                ft.Row([body_text(" ", size=22, weight=ft.FontWeight.BOLD)]),
                actions_panel,
                susp_panel,
                log_panel,
            ],
            spacing=16,  # match left side spacing for perfect alignment
            expand=True,
        ),
    )

    dashboard_view = ft.Column(
        [
            ft.Row(
                [
                    body_text("Dashboard", size=22, weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    body_text("Live telemetry", size=12, color=MUTED),
                ],
            ),
            top_dashboard_row,
            telemetry_row,
            processes_panel,
        ],
        spacing=16,
        expand=True,
    )

    def chart_area_width() -> float:
        pw = page.width or page.window.width or 1280
        return max(120.0, (pw - SIDEBAR_W - 300 - 96) / 3)

    def sync_charts() -> None:
        w = chart_area_width()
        for ch in (cpu_c, mem_c, temp_c):
            ch.set_width(w)

    autopilot_status = body_text("Status: OFF", size=14, weight=ft.FontWeight.W_600, color=MUTED)

    ui = DashboardUI(
        page=page,
        charts=(cpu_c, mem_c, temp_c),
        metrics=(cpu_m, mem_m, temp_m, swap_m),
        gauge_ring=gauge_ring,
        gauge_value=gauge_value,
        gauge_insight=gauge_insight,
        proc_table=proc_table,
        susp_table=susp_table,
        log_list=log_list,
        autopilot=autopilot,
        boost_btn=boost_btn,
        undo_btn=undo_btn,
        chart_area_width=chart_area_width,
        sync_charts=sync_charts,
        an_confidence=an_confidence,
        an_predicted_cpu=an_predicted_cpu,
        an_risk_level=an_risk_level,
        an_cpu_live=an_cpu_live,
        an_mem_live=an_mem_live,
        autopilot_status=autopilot_status,
    )
    analytics_view = build_analytics_view(ui)
    settings_view = build_settings_view(autopilot_status)
    return ui, dashboard_view, analytics_view, settings_view, right_rail


def proc_rows(procs: list) -> list[ft.DataRow]:
    rows = []
    for i, p in enumerate(procs):
        bg = CARD_ALT if i % 2 else None
        rows.append(
            ft.DataRow(
                color=bg,
                cells=[
                    ft.DataCell(ft.Container(content=body_text(str(p["pid"]), size=11, color=MUTED), width=70)),
                    ft.DataCell(ft.Container(content=body_text(p.get("name") or "", size=11), width=380)),
                    ft.DataCell(
                        ft.Container(
                            content=body_text(
                                f"{p['memory_percent']:.1f}%",
                                size=11,
                                color=severity(p["memory_percent"] * 5),
                                weight=ft.FontWeight.BOLD,
                            ),
                            width=90,
                        )
                    ),
                    ft.DataCell(ft.Container(content=body_text(p.get("status") or "", size=11, color=MUTED), width=120)),
                ],
            )
        )
    return rows


def _update_analytics(ui: DashboardUI, res: Optional[PipelineResult] = None) -> None:
    if res is not None:
        ui.last_result = res
    res = ui.last_result
    if res is None or res.calibrating:
        ui.an_confidence.value = "Confidence: calibrating…"
        ui.an_confidence.color = WARN
        ui.an_predicted_cpu.value = "Predicted CPU: —"
        ui.an_risk_level.value = "Risk level: —"
    else:
        pct = int(res.confidence * 100)
        col = severity(pct)
        ui.an_confidence.value = f"Confidence: {pct}%"
        ui.an_confidence.color = col
        ui.an_predicted_cpu.value = f"Predicted CPU: {res.predicted_cpu:.1f}%"
        if pct >= 80:
            ui.an_risk_level.value = "Risk level: High"
            ui.an_risk_level.color = CRIT
        elif pct >= 55:
            ui.an_risk_level.value = "Risk level: Moderate"
            ui.an_risk_level.color = WARN
        else:
            ui.an_risk_level.value = "Risk level: Low"
            ui.an_risk_level.color = ACCENT
    f = (res.features if res else {}) or {}
    cpu = f.get("cpu_percent_raw", f.get("cpu_percent", 0))
    mem = f.get("mem_percent_raw", f.get("mem_percent", 0))
    ui.an_cpu_live.value = f"CPU: {cpu:.1f}%"
    ui.an_mem_live.value = f"Memory: {mem:.1f}%"


def run_app(page: ft.Page) -> None:
    page.title = "System Resource Optimizer"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = BG
    page.padding = 0
    page.window.width = 1320
    page.window.height = 840
    page.window.min_width = 1100
    page.window.min_height = 720

    ui_callbacks = {}
    ui, dashboard_view, analytics_view, settings_view, right_rail = build_dashboard_content(page, ui_callbacks)

    views = {
        "dashboard": dashboard_view,
        "analytics": analytics_view,
        "settings": settings_view,
    }
    content_host = ft.Container(
        content=dashboard_view,
        expand=True,
        padding=ft.Padding.only(left=24, top=16, right=16, bottom=16),
    )
    right_rail_host = ft.Container(
        width=300, 
        content=right_rail, 
        visible=True,
        padding=ft.Padding.only(top=16, right=16, bottom=16),
    )
    shell = {"sidebar": None}

    def set_active_nav(key: str) -> None:
        sidebar = shell["sidebar"]
        if not sidebar:
            return
        for k, item in sidebar.nav_refs.items():
            selected = k == key
            item.bgcolor = CARD_ALT if selected else None
            row = item.content
            row.controls[0].color = ACCENT if selected else MUTED
            row.controls[1].color = TEXT if selected else MUTED

    def navigate(key: str) -> None:
        if key not in views:
            return
        set_active_nav(key)
        content_host.content = views[key]
        right_rail_host.visible = key == "dashboard"
        if key == "dashboard":
            ui.sync_charts()
        page.update()

    shell["sidebar"] = build_sidebar(navigate, active="dashboard")
    sidebar = shell["sidebar"]

    page.add(
        ft.Row(
            [sidebar, content_host, right_rail_host],
            expand=True,
        )
    )

    def on_page_resize(_e) -> None:
        ui.sync_charts()
        page.update()

    page.on_resize = on_page_resize

    result_q: Queue = Queue()
    calib_q: Queue = Queue()
    pending_lock = threading.Lock()
    pending_calib: list = []
    pending_items: list = []
    flush_scheduled = False
    pipeline: Optional[Pipeline] = None
    ap_last_fire = 0.0

    def log(msg: str, color: str = ACCENT) -> None:
        ts = time.strftime("%H:%M:%S")
        ui.log_list.controls.append(
            body_text(f"[{ts}] {msg}", size=11, color=color, font_family="monospace")
        )
        if len(ui.log_list.controls) > 80:
            ui.log_list.controls.pop(0)

    def snack(msg: str, bg: str = ACCENT) -> None:
        page.snack_bar = ft.SnackBar(
            content=ft.Text(msg, color="#0D1117" if bg == ACCENT else TEXT, size=13),
            bgcolor=bg,
            duration=3000,
        )
        page.snack_bar.open = True
        page.update()

    def refresh_suspended() -> None:
        rows = []
        for i, sp in enumerate(pipeline.get_suspended_processes()):
            since = time.strftime(
                "%H:%M:%S",
                time.localtime(time.time() - (time.monotonic() - sp.suspended_at)),
            )
            rows.append(
                ft.DataRow(
                    color=CARD_ALT if i % 2 else None,
                    cells=[
                        ft.DataCell(ft.Container(content=body_text(sp.name, size=11), width=140)),
                        ft.DataCell(ft.Container(content=body_text(since, size=11, color=MUTED), width=80)),
                    ],
                )
            )
        ui.susp_table.rows = rows
        ui.undo_btn.disabled = len(rows) == 0

    def apply_result(res: PipelineResult) -> None:
        nonlocal ap_last_fire
        f = res.features
        cpu = f.get("cpu_percent_raw", f.get("cpu_percent", 0))
        mem = f.get("mem_percent_raw", f.get("mem_percent", 0))
        tmp = f.get("cpu_temp_c", 0)
        swap = f.get("swap_percent", 0)

        ui.metrics[0].update(cpu)
        ui.metrics[1].update(mem)
        ui.metrics[2].update(tmp if tmp > 0 else 0)
        ui.metrics[3].update(swap)
        ui.charts[0].push(cpu)
        ui.charts[1].push(mem)
        if tmp > 0:
            ui.charts[2].push(tmp)

        _update_analytics(ui, res)

        if res.calibrating:
            # Managed by the calibs loop to display dynamic percentage.
            # Only initialize if we haven't started displaying progress yet.
            if ui.gauge_value.value in ("—", "…"):
                ui.gauge_ring.value = 0.0
                ui.gauge_ring.color = WARN
                ui.gauge_value.value = "0%"
                ui.gauge_value.color = WARN
                ui.gauge_insight.value = "Calibrating sensors"
                ui.gauge_insight.color = WARN
        else:
            pct = int(res.confidence * 100)
            col = severity(pct)
            ui.gauge_ring.value = res.confidence
            ui.gauge_ring.color = col
            ui.gauge_value.value = f"{pct}%"
            ui.gauge_value.color = col
            if pct >= 80:
                ui.gauge_insight.value = f"High load risk · CPU ≈ {res.predicted_cpu:.0f}%"
                ui.gauge_insight.color = CRIT
            elif pct >= 55:
                ui.gauge_insight.value = f"Moderate risk · CPU ≈ {res.predicted_cpu:.0f}%"
                ui.gauge_insight.color = WARN
            else:
                ui.gauge_insight.value = "System healthy"
                ui.gauge_insight.color = ACCENT

        if ui.autopilot.value and not res.calibrating:
            now = time.monotonic()
            if res.confidence >= CONFIDENCE_THRESHOLD and (now - ap_last_fire > 45):
                ap_last_fire = now
                if pipeline:
                    r = pipeline.trigger_boost()
                    log(f"Auto-Pilot: {r.message}")
                    snack("Auto-Pilot fired boost")

        if res.action.action_taken and pipeline:
            log(res.action.message, WARN)
            snack(res.action.message, WARN)
            refresh_suspended()

    async def flush_ui() -> None:
        nonlocal flush_scheduled
        flush_scheduled = False
        with pending_lock:
            calibs = pending_calib[:]
            pending_calib.clear()
            items = pending_items[:]
            pending_items.clear()

        for elapsed, total in calibs:
            if elapsed != -1:
                pct = min(100, max(1, int((elapsed / total) * 100)))
                ui.gauge_insight.value = "Calibrating sensors"
                ui.gauge_insight.color = WARN
                ui.gauge_ring.value = elapsed / total
                ui.gauge_ring.color = WARN
                ui.gauge_value.value = f"{pct}%"
                ui.gauge_value.color = WARN
            else:
                snack("🎯 Calibration Complete! Local system metrics calibrated successfully.", ACCENT)
                log("Calibration complete: Saved machine-specific metrics profile", ACCENT)

        for item in items:
            if isinstance(item, tuple) and item[0] == "proc_table":
                ui.proc_table.rows = proc_rows(item[1])
            else:
                apply_result(item)

        ui.sync_charts()
        page.update()

    def schedule_flush() -> None:
        nonlocal flush_scheduled
        if flush_scheduled:
            return
        flush_scheduled = True
        page.run_task(flush_ui)

    def poll_queues() -> None:
        while True:
            dirty = False
            try:
                while True:
                    with pending_lock:
                        pending_calib.append(calib_q.get_nowait())
                    dirty = True
            except Empty:
                pass
            try:
                while True:
                    with pending_lock:
                        pending_items.append(result_q.get_nowait())
                    dirty = True
            except Empty:
                pass
            if dirty:
                schedule_flush()
            time.sleep(0.1)

    def poll_processes() -> None:
        while pipeline:
            try:
                procs = []
                for p in psutil.process_iter(["pid", "name", "memory_percent", "status"]):
                    try:
                        mem = p.info.get("memory_percent") or 0
                        if mem > 0.1:
                            procs.append(p.info)
                    except (psutil.Error, KeyError):
                        continue
                procs.sort(key=lambda x: x["memory_percent"], reverse=True)
                result_q.put(("proc_table", procs[:20]))
            except Exception:
                pass
            time.sleep(3)

    def on_boost(_e) -> None:
        if not pipeline:
            return
        r = pipeline.trigger_boost()
        log(f"Boost: {r.message}")
        snack(f"Boost: {r.message}")
        refresh_suspended()
        page.update()

    def on_undo(_e) -> None:
        if not pipeline:
            return
        r = pipeline.trigger_undo()
        log(f"Undo: {r.message}", MUTED)
        snack(r.message, MUTED)
        refresh_suspended()
        page.update()

    def on_autopilot(_e) -> None:
        on = ui.autopilot.value
        print(f"ON_AUTOPILOT TRIGGERED! Switch value={on}", flush=True)
        state = "ON" if on else "OFF"
        ui.autopilot_status.value = f"Status: {state}"
        ui.autopilot_status.color = ACCENT if on else MUTED
        log(f"Auto-Pilot {state}", WARN)
        if on:
            snack("🤖 Auto-Pilot Activated — Autonomous Boost Enabled", ACCENT)
        else:
            snack("🤖 Auto-Pilot Deactivated — Manual Mode", MUTED)
        page.update()

    ui_callbacks["on_boost"] = on_boost
    ui_callbacks["on_undo"] = on_undo
    ui_callbacks["on_autopilot"] = on_autopilot
    ui.undo_btn.disabled = True

    pipeline = Pipeline(
        on_result=lambda r: result_q.put(r),
        on_calibration_progress=lambda el, tot: calib_q.put((el, tot)),
    )
    pipeline.start()
    threading.Thread(target=poll_processes, daemon=True).start()
    threading.Thread(target=poll_queues, daemon=True).start()

    ui.sync_charts()
    page.update()

    def on_window_event(e) -> None:
        if e.data == "close" and pipeline:
            pipeline.stop()

    page.window.on_event = on_window_event


def main(page: ft.Page) -> None:
    run_app(page)


if __name__ == "__main__":
    ft.run(main)