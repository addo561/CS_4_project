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
from config import CONFIDENCE_THRESHOLD, CALIBRATION_SECONDS, POLL_INTERVAL_SEC, VERSION

# Background process scanner - prevents UI freezing
_process_cache = {
    "data": [],
    "lock": threading.Lock(),
    "last_update": 0
}

def _background_process_scanner():
    """
    Scan system processes in background thread.
    Never blocks the UI.
    """
    while True:
        try:
            procs = []
            start = time.time()
            
            # Scan all processes (this WILL take time, but it's on background thread!)
            for p in psutil.process_iter(["pid", "name", "memory_percent", "status"]):
                try:
                    mem = p.info.get("memory_percent") or 0
                    if mem > 0.1:
                        procs.append(p.info)
                except (psutil.Error, KeyError):
                    continue
            
            # Sort and cache
            procs.sort(key=lambda x: x["memory_percent"], reverse=True)
            
            scan_time = time.time() - start
            if scan_time > 2.0:
                print(f"⚠️  Slow process scan: {scan_time:.2f}s", flush=True)
            
            # Update cache atomically
            with _process_cache["lock"]:
                _process_cache["data"] = procs[:20]
                _process_cache["last_update"] = time.time()
        
        except Exception as e:
            print(f"Error scanning processes: {e}", flush=True)
        
        time.sleep(3)  # Scan every 3 seconds (on background thread!)

def _is_flet_broken() -> bool:
    """Check if Flet environment is broken or PyQt6 is forced."""
    if os.environ.get("SRO_USE_PYQT", "").lower() in ("true", "1"):
        print("ℹ️  SRO_USE_PYQT env var detected. Forcing PyQt6 dashboard fallback.", flush=True)
        return True
    try:
        import flet as ft
        return False
    except Exception as e:
        print(f"⚠️ Flet check failed: {e}. Falling back to PyQt6.", flush=True)
        return True

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
    an_cpu_bar: Optional[ft.ProgressBar] = None
    an_cpu_weight_text: Optional[ft.Text] = None
    an_mem_bar: Optional[ft.ProgressBar] = None
    an_mem_weight_text: Optional[ft.Text] = None
    an_temp_bar: Optional[ft.ProgressBar] = None
    an_temp_weight_text: Optional[ft.Text] = None
    an_swap_bar: Optional[ft.ProgressBar] = None
    an_swap_weight_text: Optional[ft.Text] = None
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
                nav_item("Science Hub", ft.Icons.SCHOOL, "science_hub"),
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
    # Feature importance visual items (gorgeous dynamic XAI bars)
    ui.an_cpu_weight_text = body_text("42% weight", size=11, color=ACCENT)
    ui.an_cpu_bar = ft.ProgressBar(value=0.42, color=ACCENT, bgcolor=CARD_ALT, height=6, border_radius=3)
    
    ui.an_mem_weight_text = body_text("28% weight", size=11, color=WARN)
    ui.an_mem_bar = ft.ProgressBar(value=0.28, color=WARN, bgcolor=CARD_ALT, height=6, border_radius=3)
    
    ui.an_temp_weight_text = body_text("18% weight", size=11, color=CRIT)
    ui.an_temp_bar = ft.ProgressBar(value=0.18, color=CRIT, bgcolor=CARD_ALT, height=6, border_radius=3)
    
    ui.an_swap_weight_text = body_text("12% weight", size=11, color=ACCENT)
    ui.an_swap_bar = ft.ProgressBar(value=0.12, color=ACCENT, bgcolor=CARD_ALT, height=6, border_radius=3)

    feature_importance = ft.Column(
        [
            ft.Row(
                [
                    body_text("CPU Utilization (raw/percent)", size=12, color=TEXT, weight=ft.FontWeight.W_500),
                    ft.Container(expand=True),
                    ui.an_cpu_weight_text,
                ]
            ),
            ui.an_cpu_bar,
            ft.Row(
                [
                    body_text("Memory Pressure (used/percent)", size=12, color=TEXT, weight=ft.FontWeight.W_500),
                    ft.Container(expand=True),
                    ui.an_mem_weight_text,
                ]
            ),
            ui.an_mem_bar,
            ft.Row(
                [
                    body_text("CPU Thermal Temperature (°C)", size=12, color=TEXT, weight=ft.FontWeight.W_500),
                    ft.Container(expand=True),
                    ui.an_temp_weight_text,
                ]
            ),
            ui.an_temp_bar,
            ft.Row(
                [
                    body_text("Swap space (percent)", size=12, color=TEXT, weight=ft.FontWeight.W_500),
                    ft.Container(expand=True),
                    ui.an_swap_weight_text,
                ]
            ),
            ui.an_swap_bar,
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


def build_science_hub_view() -> ft.Column:
    import os
    assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    
    def get_eq_control(filename: str, fallback_text: str) -> ft.Control:
        path = os.path.join(assets_dir, filename)
        if os.path.isfile(path):
            return ft.Container(
                content=ft.Image(src=path, height=72, fit=ft.BoxFit.CONTAIN),
                padding=ft.Padding.only(left=16, top=6, bottom=6),
            )
        else:
            return body_text(fallback_text, size=13, weight=ft.FontWeight.W_600, color=TEXT, font_family="monospace")

    # Math formulas and academic model equations
    gru_formulas = ft.Column(
        [
            body_text("Gated Recurrent Unit (GRU) Cell Equations", size=14, weight=ft.FontWeight.BOLD, color=ACCENT),
            ft.Divider(height=1, color=BORDER),
            body_text("SRO's proactive resource prediction is driven by a deep Gated Recurrent Unit (GRU) neural network, chosen for its excellent balance of temporal memory representation and low latency constraints. The hidden state update cycle of the GRU is mathematically defined as follows:", size=12, color=TEXT),
            
            # Equation 1: Reset Gate
            ft.Container(
                content=ft.Column([
                    body_text("1. Reset Gate Equation (r_t)", size=12, color=MUTED, weight=ft.FontWeight.BOLD),
                    get_eq_control("eq_reset_gate.png", "   r_t = σ( W_r · x_t  +  U_r · h_{t-1}  +  b_r )"),
                    body_text("   Controls how much of the past hidden state h_{t-1} to forget or keep. σ represents the sigmoid activation function mapping values between 0 and 1.", size=11, color=MUTED),
                ]),
                bgcolor=CARD_ALT,
                padding=10,
                border_radius=8,
            ),
            
            # Equation 2: Update Gate
            ft.Container(
                content=ft.Column([
                    body_text("2. Update Gate Equation (z_t)", size=12, color=MUTED, weight=ft.FontWeight.BOLD),
                    get_eq_control("eq_update_gate.png", "   z_t = σ( W_z · x_t  +  U_z · h_{t-1}  +  b_z )"),
                    body_text("   Decides how much of the new candidate information to carry forward into the next time-step, helping prevent vanishing gradient problems.", size=11, color=MUTED),
                ]),
                bgcolor=CARD_ALT,
                padding=10,
                border_radius=8,
            ),
            
            # Equation 3: Candidate Hidden State
            ft.Container(
                content=ft.Column([
                    body_text("3. Candidate Hidden State Equation (~h_t)", size=12, color=MUTED, weight=ft.FontWeight.BOLD),
                    get_eq_control("eq_candidate_state.png", "   ~h_t = tanh( W_h · x_t  +  U_h · ( r_t ⊙ h_{t-1} )  +  b_h )"),
                    body_text("   Calculates the new candidate memory state. The reset gate r_t acts as a gating filter on the historical hidden state h_{t-1}. ⊙ is the Hadamard (element-wise) product.", size=11, color=MUTED),
                ]),
                bgcolor=CARD_ALT,
                padding=10,
                border_radius=8,
            ),
            
            # Equation 4: Final Hidden State
            ft.Container(
                content=ft.Column([
                    body_text("4. Hidden State Update Equation (h_t)", size=12, color=MUTED, weight=ft.FontWeight.BOLD),
                    get_eq_control("eq_final_state.png", "   h_t = ( 1 - z_t ) ⊙ h_{t-1}  +  z_t ⊙ ~h_t"),
                    body_text("   Synthesizes the final output vector by linearly interpolating between the old state h_{t-1} and the new candidate state ~h_t based on the update gate z_t.", size=11, color=MUTED),
                ]),
                bgcolor=CARD_ALT,
                padding=10,
                border_radius=8,
            ),
        ],
        spacing=12,
    )
    
    quantization_formulas = ft.Column(
        [
            body_text("Dynamic INT8 Post-Training Quantization", size=14, weight=ft.FontWeight.BOLD, color=WARN),
            ft.Divider(height=1, color=BORDER),
            body_text("To achieve a 75% reduction in model size (~0.68 MB to ~0.17 MB) for frictionless background execution on consumer hardware, SRO employs uniform dynamic symmetric INT8 quantization on its weights, mapping float32 weights to 8-bit integers:", size=12, color=TEXT),
            
            ft.Container(
                content=ft.Column([
                    body_text("Linear Quantization Formula (Float → INT8)", size=12, color=MUTED, weight=ft.FontWeight.BOLD),
                    get_eq_control("eq_quantization.png", "   q = round( x / S ) + Z"),
                    body_text("   Where x is the float32 input weight, q is the quantized 8-bit integer weight, S is the Scale factor (float), and Z is the Zero-point shift (integer). For symmetric quantization, Z is set to 0.", size=11, color=MUTED),
                ]),
                bgcolor=CARD_ALT,
                padding=10,
                border_radius=8,
            ),
            ft.Container(
                content=ft.Column([
                    body_text("Dequantization Formula (Inference Phase)", size=12, color=MUTED, weight=ft.FontWeight.BOLD),
                    get_eq_control("eq_dequantization.png", "   x ≈ S · ( q - Z )"),
                    body_text("   During runtime, float values are reconstructed before math operations using the dynamic scale. This yields significant runtime speedups while maintaining high academic forecasting accuracy.", size=11, color=MUTED),
                ]),
                bgcolor=CARD_ALT,
                padding=10,
                border_radius=8,
            ),
        ],
        spacing=12,
    )
    
    thermal_formulas = ft.Column(
        [
            body_text("ACPI Thermal Lag & Fallback Simulator", size=14, weight=ft.FontWeight.BOLD, color=CRIT),
            ft.Divider(height=1, color=BORDER),
            body_text("In virtual machines, cloud instances, or restricted systems where raw hardware sensors are inaccessible, SRO automatically deploys a first-order thermal lag physical simulation derived from Newton's Law of Cooling:", size=12, color=TEXT),
            
            ft.Container(
                content=ft.Column([
                    body_text("First-Order Thermal Lag Equation", size=12, color=MUTED, weight=ft.FontWeight.BOLD),
                    get_eq_control("eq_thermal_lag.png", "   T_t = T_{t-1}  +  k · ( CPU_t - T_{t-1} ) · Δt"),
                    body_text("   Where T_t is the simulated CPU temperature at time t, T_{t-1} is the temperature at the previous second, CPU_t is the raw CPU utilization percentage, k is the dissipation coefficient (constant), and Δt is the poll rate (1.0s). This provides a reliable reactive protection fallback.", size=11, color=MUTED),
                ]),
                bgcolor=CARD_ALT,
                padding=10,
                border_radius=8,
            ),
        ],
        spacing=12,
    )

    # Compile the right-side benchmarking section
    report_md_content = "Loading empirical benchmark metrics..."
    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "benchmark_report.md")
    if not os.path.isfile(report_path):
        report_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "benchmark_report.md")
    if not os.path.isfile(report_path):
        report_path = os.path.join("/Users/user/Desktop/Final_year/src/docs/benchmark_report.md")
    if not os.path.isfile(report_path):
        report_path = os.path.join("/Users/user/Desktop/Final_year/docs/benchmark_report.md")

    if os.path.isfile(report_path):
        try:
            with open(report_path, "r", encoding="utf-8") as file:
                report_md_content = file.read()
        except Exception as e:
            report_md_content = f"Error reading benchmark report: {e}"
            
    # Chart image
    chart_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "benchmark_charts.png")
    if not os.path.isfile(chart_path):
        chart_path = "/Users/user/Desktop/Final_year/src/assets/benchmark_charts.png"
        
    if os.path.isfile(chart_path):
        chart_image = ft.Image(src=chart_path, width=540, fit=ft.BoxFit.CONTAIN, border_radius=8)
    else:
        chart_image = ft.Container(
            content=body_text("No benchmark chart image generated yet. Rerun benchmark suite in settings.", color=MUTED, size=11),
            bgcolor=CARD_ALT,
            padding=20,
            border_radius=8,
            alignment=ft.alignment.center,
        )

    right_rail_content = ft.Column(
        [
            body_text("Empirical Performance Diagnostics", size=14, weight=ft.FontWeight.BOLD, color=ACCENT),
            ft.Divider(height=1, color=BORDER),
            body_text("Comparative performance analysis profiling Accuracy, F1-Score, ROC-AUC, inference latency, and disk memory footprint.", size=12, color=TEXT),
            chart_image,
            ft.Divider(height=1, color=BORDER),
            ft.Markdown(report_md_content, selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB),
        ],
        spacing=12,
    )

    return ft.Column(
        [
            # Header
            ft.Row(
                [
                    ft.Icon(ft.Icons.SCHOOL, color=ACCENT, size=28),
                    ft.Column(
                        [
                            body_text("Science & Academic Hub", size=22, weight=ft.FontWeight.BOLD),
                            body_text("SRO core mathematical equations, quantization models, and comparative evaluation telemetry", size=12, color=MUTED),
                        ],
                        spacing=0,
                    ),
                ],
                spacing=12,
            ),
            ft.Divider(height=1, color=BORDER),
            
            # Scrollable main responsive grid
            ft.Row(
                [
                    ft.Column(
                        [
                            card(gru_formulas, padding=16),
                            card(quantization_formulas, padding=16),
                            card(thermal_formulas, padding=16),
                        ],
                        expand=True,
                        spacing=16,
                    ),
                    ft.Column(
                        [
                            card(right_rail_content, padding=16),
                        ],
                        expand=True,
                        spacing=16,
                    ),
                ],
                expand=True,
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            )
        ],
        spacing=16,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )


def build_settings_view(autopilot_status: ft.Text, page: Optional[ft.Page] = None) -> ft.Column:
    from config import load_user_settings, save_user_settings, PROFILES, load_user_whitelist, save_user_whitelist, POLL_INTERVAL_SEC, CALIBRATION_SECONDS

    # 1. Performance Profiles & Radio Selection
    settings = load_user_settings()
    current_profile = settings.get("profile", "Balanced")

    active_profile_name = ft.Text(f"Current Profile: {current_profile}", color=ACCENT, size=14, weight=ft.FontWeight.W_600)
    profile_details = ft.Text("", color=MUTED, size=12)

    def update_profile_details(prof_name):
        prof = PROFILES.get(prof_name, PROFILES["Balanced"])
        active_profile_name.value = f"Current Profile: {prof_name}"
        profile_details.value = (
            f"Active Strategy Thresholds:\n"
            f"• CPU Bottleneck Threshold: {prof.get('CPU_BOTTLENECK_PCT')}% \n"
            f"• Memory Bottleneck Threshold: {prof.get('MEM_BOTTLENECK_PCT')}% \n"
            f"• Temperature Warning Limit: {prof.get('TEMP_BOTTLENECK_C')}°C \n"
            f"• Prediction Confidence Required: {prof.get('CONFIDENCE_THRESHOLD'):.0%}"
        )
        if page:
            page.update()

    update_profile_details(current_profile)

    def on_profile_change(e):
        selected_prof = e.control.value
        save_user_settings({"profile": selected_prof})
        update_profile_details(selected_prof)
        if page:
            page.snack_bar = ft.SnackBar(
                ft.Text(f"Settings applied: Performance profile switched to {selected_prof}", color=TEXT),
                bgcolor=CARD_ALT,
            )
            page.snack_bar.open = True
            page.update()

    profile_radio = ft.RadioGroup(
        content=ft.Column(
            [
                ft.Radio(value="Eco", label="Eco Mode (Conservation - lower thresholds, higher frequency)", fill_color=ACCENT),
                ft.Radio(value="Balanced", label="Balanced Mode (Default standard optimized strategy)", fill_color=ACCENT),
                ft.Radio(value="Gaming", label="Gaming / Performance Mode (Power - shields gaming, restricts interference)", fill_color=ACCENT),
            ],
            spacing=8,
        ),
        value=current_profile,
        on_change=on_profile_change,
    )

    # 2. Interactive Custom Whitelist Manager
    custom_list_col = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, height=180)

    def refresh_whitelist():
        custom_list_col.controls.clear()
        whitelist = load_user_whitelist()
        if not whitelist:
            custom_list_col.controls.append(
                ft.Text("No custom processes whitelisted yet.", color=MUTED, size=12, italic=True)
            )
        else:
            for proc in sorted(whitelist):
                def make_remove_cb(p=proc):
                    return lambda _: remove_proc(p)
                custom_list_col.controls.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Row(
                                    [
                                        ft.Icon(ft.Icons.SHIELD, color=ACCENT, size=14),
                                        body_text(proc, size=12, weight=ft.FontWeight.W_500),
                                    ],
                                    spacing=8,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.DELETE_OUTLINE,
                                    icon_color=CRIT,
                                    icon_size=16,
                                    on_click=make_remove_cb(),
                                    tooltip="Remove process",
                                )
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        padding=ft.padding.symmetric(horizontal=8, vertical=4),
                        bgcolor=CARD,
                        border_radius=6,
                        border=ft.Border.all(1, BORDER),
                    )
                )
        if page:
            page.update()

    def add_proc(e):
        val = proc_input.value.strip().lower()
        if not val:
            return
        whitelist = load_user_whitelist()
        if val in whitelist:
            if page:
                page.snack_bar = ft.SnackBar(ft.Text(f"'{val}' is already whitelisted.", color=WARN), bgcolor=CARD_ALT)
                page.snack_bar.open = True
                page.update()
            return
        whitelist.add(val)
        save_user_whitelist(whitelist)
        proc_input.value = ""
        refresh_whitelist()
        if page:
            page.snack_bar = ft.SnackBar(ft.Text(f"Added '{val}' to custom whitelist.", color=ACCENT), bgcolor=CARD_ALT)
            page.snack_bar.open = True
            page.update()

    def remove_proc(val):
        whitelist = load_user_whitelist()
        if val in whitelist:
            whitelist.remove(val)
            save_user_whitelist(whitelist)
        refresh_whitelist()
        if page:
            page.snack_bar = ft.SnackBar(ft.Text(f"Removed '{val}' from custom whitelist.", color=WARN), bgcolor=CARD_ALT)
            page.snack_bar.open = True
            page.update()

    proc_input = ft.TextField(
        hint_text="e.g. chrome.exe, spotify",
        hint_style=ft.TextStyle(color=MUTED, size=12),
        text_style=ft.TextStyle(color=TEXT, size=13),
        bgcolor=BG,
        border_color=BORDER,
        border_radius=8,
        height=38,
        expand=True,
        on_submit=add_proc,
    )

    add_button = ft.ElevatedButton(
        content=body_text("Add", size=12, weight=ft.FontWeight.BOLD, color=BG),
        style=ft.ButtonStyle(
            bgcolor=ACCENT,
            shape=ft.RoundedRectangleBorder(radius=8),
        ),
        on_click=add_proc,
        height=38,
    )

    refresh_whitelist()

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
                    # Left column: Auto-Pilot & Controls and Performance Profiles
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
                            
                            # Performance Profiles card
                            card(
                                ft.Column(
                                    [
                                        ft.Row(
                                            [
                                                ft.Icon(ft.Icons.TUNE, color=WARN, size=18),
                                                section_title("PERFORMANCE PROFILES"),
                                            ],
                                            spacing=8,
                                        ),
                                        ft.Container(
                                            content=ft.Column(
                                                [
                                                    body_text("Select an optimization preset to dynamically configure bottleneck thresholds and agent confidence requirements:", size=12, color=MUTED),
                                                    ft.Divider(height=8, color=BORDER),
                                                    profile_radio,
                                                    ft.Divider(height=8, color=BORDER),
                                                    active_profile_name,
                                                    ft.Container(
                                                        content=profile_details,
                                                        bgcolor=BG,
                                                        padding=10,
                                                        border_radius=6,
                                                        border=ft.Border.all(1, BORDER),
                                                    ),
                                                ],
                                                spacing=10,
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

                    # Right column: Whitelist Manager & Static Engine Thresholds
                    ft.Column(
                        [
                            # Whitelist Manager Card
                            card(
                                ft.Column(
                                    [
                                        ft.Row(
                                            [
                                                ft.Icon(ft.Icons.SHIELD, color=ACCENT, size=18),
                                                section_title("CUSTOM PROCESS WHITELIST"),
                                            ],
                                            spacing=8,
                                        ),
                                        ft.Container(
                                            content=ft.Column(
                                                [
                                                    body_text("Processes listed here will NEVER be suspended, even under heavy CPU/memory bottlenecks:", size=12, color=MUTED),
                                                    ft.Divider(height=8, color=BORDER),
                                                    
                                                    # Input row
                                                    ft.Row(
                                                        [
                                                            proc_input,
                                                            add_button,
                                                        ],
                                                        spacing=8,
                                                    ),
                                                    
                                                    ft.Divider(height=8, color=BORDER),
                                                    
                                                    # Custom list column
                                                    custom_list_col,
                                                ],
                                                spacing=10,
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
                            
                            # Engine Settings Card (shows global/static specs)
                            card(
                                ft.Column(
                                    [
                                        ft.Row(
                                            [
                                                ft.Icon(ft.Icons.SETTINGS_SYSTEM_DAYDREAM, color=ACCENT, size=18),
                                                section_title("SYSTEM ENGINE SPECIFICATIONS"),
                                            ],
                                            spacing=8,
                                        ),
                                        ft.Container(
                                            content=ft.Column(
                                                [
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
                                        body_text(VERSION, size=13, weight=ft.FontWeight.W_600, color=ACCENT),
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
    settings_view = build_settings_view(autopilot_status, page)
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

    # Dynamic XAI progress bars & percentage labels updates
    if res is not None and not res.calibrating and getattr(res, "attributions", None) is not None:
        attrs = res.attributions
        if ui.an_cpu_bar:
            ui.an_cpu_bar.value = attrs[0]
            ui.an_cpu_weight_text.value = f"{int(attrs[0] * 100)}% weight"
        if ui.an_mem_bar:
            ui.an_mem_bar.value = attrs[1]
            ui.an_mem_weight_text.value = f"{int(attrs[1] * 100)}% weight"
        if ui.an_temp_bar:
            ui.an_temp_bar.value = attrs[2]
            ui.an_temp_weight_text.value = f"{int(attrs[2] * 100)}% weight"
        if ui.an_swap_bar:
            ui.an_swap_bar.value = attrs[3]
            ui.an_swap_weight_text.value = f"{int(attrs[3] * 100)}% weight"


def run_app(page: ft.Page) -> None:
    page.title = "System Resource Optimizer"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = BG
    page.padding = 0
    page.window.width = 1320
    page.window.height = 840
    page.window.min_width = 1100
    page.window.min_height = 720

    # Set the taskbar/window icon dynamically based on platform
    import os
    import platform
    icon_ext = "icns" if platform.system() == "Darwin" else "png"
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", f"icon.{icon_ext}")
    if os.path.exists(icon_path):
        page.window.icon = icon_path

    ui_callbacks = {}
    ui, dashboard_view, analytics_view, settings_view, right_rail = build_dashboard_content(page, ui_callbacks)

    science_hub_view = build_science_hub_view()
    views = {
        "dashboard": dashboard_view,
        "analytics": analytics_view,
        "science_hub": science_hub_view,
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
        if key == "science_hub":
            views["science_hub"] = build_science_hub_view()
        elif key == "settings":
            views["settings"] = build_settings_view(ui.autopilot_status, page)
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
    calib_stats = {
        "cpu_min": 100.0, "cpu_max": 0.0,
        "mem_min": 100.0, "mem_max": 0.0,
        "temp_min": 200.0, "temp_max": 0.0
    }

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
            calib_stats["cpu_min"] = min(calib_stats["cpu_min"], cpu)
            calib_stats["cpu_max"] = max(calib_stats["cpu_max"], cpu)
            calib_stats["mem_min"] = min(calib_stats["mem_min"], mem)
            calib_stats["mem_max"] = max(calib_stats["mem_max"], mem)
            if tmp > 0:
                calib_stats["temp_min"] = min(calib_stats["temp_min"], tmp)
                calib_stats["temp_max"] = max(calib_stats["temp_max"], tmp)
            # Managed by the calibs loop to display dynamic percentage.
            # Only initialize if we haven't started displaying progress yet.
            if ui.gauge_value.value in ("—", "…"):
                ui.gauge_ring.value = 0.0
                ui.gauge_ring.color = WARN
                ui.gauge_value.value = "0%"
                ui.gauge_value.color = WARN
                ui.gauge_insight.value = "Calibrating sensors..."
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
        with pending_lock:
            calibs = pending_calib[:]
            pending_calib.clear()
            items = pending_items[:]
            pending_items.clear()

        for elapsed, total in calibs:
            if elapsed != -1:
                pct = min(100, max(1, int((elapsed / total) * 100)))
                t_min = calib_stats["temp_min"] if calib_stats["temp_min"] < 200 else 0.0
                t_max = calib_stats["temp_max"]
                ui.gauge_insight.value = (
                    f"Calibrating telemetry...\n"
                    f"CPU: {calib_stats['cpu_min']:.0f}%-{calib_stats['cpu_max']:.0f}% | "
                    f"Mem: {calib_stats['mem_min']:.0f}%-{calib_stats['mem_max']:.0f}%\n"
                    f"Temp: {t_min:.0f}°C-{t_max:.0f}°C"
                )
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
        nonlocal flush_scheduled
        flush_scheduled = False

    def schedule_flush() -> None:
        nonlocal flush_scheduled
        if flush_scheduled:
            return
        flush_scheduled = True
        page.run_task(flush_ui)

    def poll_queues() -> None:
        while True:
            try:
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
            except Exception as exc:
                print(f"Error polling queues: {exc}", flush=True)
            time.sleep(0.1)

    def poll_processes() -> None:
        """
        FIXED VERSION: Never blocks the UI thread.
        Gets cached process data from background scanner.
        """
        while pipeline:
            try:
                # Get cached data (VERY FAST - no blocking!)
                with _process_cache["lock"]:
                    if _process_cache["data"]:
                        # Send cached data to UI
                        result_q.put(("proc_table", _process_cache["data"]))
            except Exception as e:
                print(f"Error in poll_processes: {e}", flush=True)
            
            # Poll frequently but briefly
            time.sleep(0.5)

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

    # START BACKGROUND SCANNER BEFORE PIPELINE
    scanner_thread = threading.Thread(
        target=_background_process_scanner,
        daemon=True,
        name="ProcessScannerThread"
    )
    scanner_thread.start()
    print("✅  Background process scanner started", flush=True)

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
        nonlocal pipeline
        if e.data == "close" and pipeline:
            pipeline.stop()
            pipeline = None

    page.window.on_event = on_window_event


def main(page: ft.Page) -> None:
    run_app(page)


if __name__ == "__main__":
    if _is_flet_broken():
        try:
            from main_pyqt import run_pyqt_ui
            run_pyqt_ui()
        except Exception as e:
            print(f"❌ Failed to fall back to PyQt6: {e}", flush=True)
            sys.exit(1)
    else:
        try:
            ft.run(main)
        except Exception as e:
            print(f"⚠️ Flet runtime error: {e}. Falling back to PyQt6...", flush=True)
            try:
                from main_pyqt import run_pyqt_ui
                run_pyqt_ui()
            except Exception as e2:
                print(f"❌ Failed to fall back to PyQt6: {e2}", flush=True)
                sys.exit(1)