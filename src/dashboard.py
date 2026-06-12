# =============================================================================
# dashboard.py — Next-Gen Flet Dashboard Client for System Resource Optimizer
# Connects to the background optimizer service via a local TCP socket.
# Displays real-time metrics, rolling charts, AI predictions, and logs.
# KNUST Final Year Project — Group 4
# =============================================================================

import os
import sys
import time
import socket
import json
import threading
import asyncio
from queue import Queue, Empty
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

import flet as ft
import flet.canvas as cv
import logging

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

from config import CONFIDENCE_THRESHOLD, CALIBRATION_SECONDS, POLL_INTERVAL_SEC, VERSION, PROFILES, IPC_PORT, BASE_DIR, LOG_DIR
from core.notifier import Notifier

# Configure client-side logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(LOG_DIR, "dashboard.log"), encoding="utf-8")
    ]
)
log = logging.getLogger("dashboard")

notifier = Notifier()

# ── Design tokens ─────────────────────────────────────────────────────────────
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


# ── IPC Client ────────────────────────────────────────────────────────────────

class IPCClient:
    def __init__(self, host="127.0.0.1", port=IPC_PORT):
        self.host = host
        self.port = port
        self.sock = None
        self.connected = False
        self.lock = threading.Lock()
        
    def connect(self) -> bool:
        with self.lock:
            if self.connected:
                return True
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(3.0)
                self.sock.connect((self.host, self.port))
                self.connected = True
                return True
            except Exception:
                self.connected = False
                return False

    def send_request(self, req: dict) -> dict:
        # Check connection first
        if not self.connected:
            if not self.connect():
                return {"connected": False, "status": "error", "message": "Service offline"}
                
        with self.lock:
            try:
                msg = json.dumps(req) + "\n"
                self.sock.sendall(msg.encode("utf-8"))
                
                # Receive line-terminated response
                buffer = b""
                while b"\n" not in buffer:
                    chunk = self.sock.recv(4096)
                    if not chunk:
                        raise ConnectionError("Connection closed by server")
                    buffer += chunk
                
                line = buffer.split(b"\n")[0]
                return json.loads(line.decode("utf-8"))
            except Exception as e:
                self.connected = False
                if self.sock:
                    try:
                        self.sock.close()
                    except Exception:
                        pass
                return {"connected": False, "status": "error", "message": f"IPC error: {e}"}

    def close(self):
        with self.lock:
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.connected = False


# Instance of IPC client
client = IPCClient()


# ── UI helper functions ───────────────────────────────────────────────────────

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

    def push(self, value: float, redraw: bool = True) -> None:
        self.data.append(self._norm(value))
        if redraw:
            self._redraw()

    def redraw(self) -> None:
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
        try:
            self.canvas.update()
        except Exception:
            pass


# ── Metric tile ───────────────────────────────────────────────────────────────
class MetricTile:
    def __init__(self, label: str, unit: str = "%", accent: str = ACCENT, tooltip: Optional[str] = None):
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
            tooltip=tooltip,
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
    last_result: Optional[dict] = None


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
                nav_item("Help & UX Guide", ft.Icons.HELP, "help"),
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
            
            ft.ResponsiveRow(
                [
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

    return ft.Column(
        [
            body_text("Gated Recurrent Unit (GRU) Cell Equations", size=14, weight=ft.FontWeight.BOLD, color=ACCENT),
            ft.Divider(height=1, color=BORDER),
            body_text("SRO's proactive resource prediction is driven by a deep Gated Recurrent Unit (GRU) neural network, chosen for its excellent balance of temporal memory representation and low latency constraints. The hidden state update cycle of the GRU is mathematically defined as follows:", size=12, color=TEXT),
            
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
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )


def build_help_view(page: ft.Page) -> ft.Column:
    return ft.Column(
        [
            body_text("Elite User Experience & Help System", size=14, weight=ft.FontWeight.BOLD, color=ACCENT),
            ft.Divider(height=1, color=BORDER),
            
            # 1. Quick Start Guide
            body_text("Quick Start Guide", size=13, weight=ft.FontWeight.BOLD),
            ft.Container(
                content=ft.Row(
                    [
                        ft.Column([body_text("1. Install", weight=ft.FontWeight.BOLD), body_text("Run installer/setup", size=10, color=MUTED)], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                        ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=MUTED),
                        ft.Column([body_text("2. Launch", weight=ft.FontWeight.BOLD), body_text("Start dashboard/service", size=10, color=MUTED)], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                        ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=MUTED),
                        ft.Column([body_text("3. Wait 60s", weight=ft.FontWeight.BOLD), body_text("Fill GRU history queue", size=10, color=MUTED)], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                        ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=MUTED),
                        ft.Column([body_text("4. Toggle Auto-Pilot", weight=ft.FontWeight.BOLD), body_text("Enable automatic actions", size=10, color=MUTED)], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                        ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=MUTED),
                        ft.Column([body_text("5. Done", weight=ft.FontWeight.BOLD, color=ACCENT), body_text("System runs optimized", size=10, color=ACCENT)], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                    ],
                    spacing=14,
                    alignment=ft.MainAxisAlignment.SPACE_EVENLY,
                ),
                bgcolor=CARD_ALT,
                padding=14,
                border_radius=8,
            ),
            
            # 2. Strategy Table
            body_text("In-App Help & Documentation Strategy", size=13, weight=ft.FontWeight.BOLD),
            ft.DataTable(
                columns=[
                    ft.DataColumn(ft.Container(content=body_text("Format", weight=ft.FontWeight.BOLD), width=150)),
                    ft.DataColumn(ft.Container(content=body_text("Where", weight=ft.FontWeight.BOLD), width=250)),
                    ft.DataColumn(ft.Container(content=body_text("Content", weight=ft.FontWeight.BOLD), width=350)),
                ],
                rows=[
                    ft.DataRow(
                        cells=[
                            ft.DataCell(body_text("Tooltips", size=11)),
                            ft.DataCell(body_text("Hover over any UI element (ⓘ icon)", size=11)),
                            ft.DataCell(body_text("1-sentence explanation", size=11)),
                        ],
                        color=CARD_ALT,
                    ),
                    ft.DataRow(
                        cells=[
                            ft.DataCell(body_text("Quick Start Guide", size=11)),
                            ft.DataCell(body_text("In the installer folder or Help menu", size=11)),
                            ft.DataCell(body_text("5 steps: Install → Launch → Wait 60s → Auto-Pilot", size=11)),
                        ],
                    ),
                    ft.DataRow(
                        cells=[
                            ft.DataCell(body_text("In-app Help Sidebar", size=11)),
                            ft.DataCell(body_text("Click ❓ icon on top of dashboard", size=11)),
                            ft.DataCell(body_text("FAQ regarding suspension, whitelist, recovery", size=11)),
                        ],
                        color=CARD_ALT,
                    ),
                ],
                border=ft.Border.all(1, BORDER),
                border_radius=8,
                heading_row_color=CARD,
            ),
        ],
        spacing=16,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )


def open_help_sidebar(page: ft.Page):
    if not page.end_drawer:
        page.end_drawer = ft.NavigationDrawer(
            controls=[
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.HELP_OUTLINE, color=ACCENT, size=24),
                                    body_text("SRO In-App Help", size=18, weight=ft.FontWeight.BOLD),
                                    ft.Container(expand=True),
                                    ft.IconButton(
                                        icon=ft.Icons.CLOSE,
                                        icon_color=MUTED,
                                        on_click=lambda _: close_help_sidebar(page)
                                    ),
                                ],
                                alignment=ft.MainAxisAlignment.CENTER,
                            ),
                            ft.Divider(color=BORDER),
                            
                            # FAQ section
                            body_text("Frequently Asked Questions", size=14, weight=ft.FontWeight.BOLD, color=ACCENT),
                            
                            ft.Container(
                                content=ft.Column([
                                    body_text("Q: Why suspend instead of kill?", size=12, weight=ft.FontWeight.BOLD, color=TEXT),
                                    body_text("A: Killing a process can cause data loss or crash system services. Suspending safely pauses thread execution to free up CPU time and compositor threads, which can be undone later.", size=11, color=MUTED),
                                ]),
                                bgcolor=CARD_ALT,
                                padding=8,
                                border_radius=6,
                            ),
                            
                            ft.Container(
                                content=ft.Column([
                                    body_text("Q: What does the Whitelist do?", size=12, weight=ft.FontWeight.BOLD, color=TEXT),
                                    body_text("A: It lists crucial apps (e.g. system drivers, antivirus, custom editors) that the SRO will never throttle or suspend.", size=11, color=MUTED),
                                ]),
                                bgcolor=CARD_ALT,
                                padding=8,
                                border_radius=6,
                            ),
                            
                            ft.Container(
                                content=ft.Column([
                                    body_text("Q: How can I restore processes?", size=12, weight=ft.FontWeight.BOLD, color=TEXT),
                                    body_text("A: Click 'Undo' to instantly resume all throttled tasks, or disable the 'Auto-Pilot' mode to handle it manually.", size=11, color=MUTED),
                                ]),
                                bgcolor=CARD_ALT,
                                padding=8,
                                border_radius=6,
                            ),
                        ],
                        spacing=12,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    padding=20,
                    width=320,
                )
            ]
        )
    page.end_drawer.open = True
    page.update()


def close_help_sidebar(page: ft.Page):
    if page.end_drawer:
        page.end_drawer.open = False
        page.update()


def build_settings_view(autopilot_status: ft.Text, page: ft.Page) -> ft.Column:
    resp = client.send_request({"type": "get_full_state"})
    current_profile = resp.get("active_profile", "Balanced") if resp.get("connected") else "Balanced"
    optimizer_active = resp.get("optimizer_active", True) if resp.get("connected") else True

    def on_optimizer_toggle(e):
        val = e.control.value
        client.send_request({"type": "command", "cmd": "toggle_optimizer", "value": val})
        if val:
            notifier.send(title="⚡ SRO: Optimizer Resumed", message="Background optimizer loop has been resumed.")
        else:
            notifier.send(title="⚡ SRO: Optimizer Suspended", message="Background optimizer loop has been suspended. All processes resumed.")
        page.snack_bar = ft.SnackBar(
            ft.Text(f"Background optimizer engine {'resumed' if val else 'suspended'}.", color=TEXT),
            bgcolor=CARD_ALT,
        )
        page.snack_bar.open = True
        page.update()

    optimizer_switch = ft.Switch(
        value=optimizer_active,
        active_color=ACCENT,
        on_change=on_optimizer_toggle,
    )

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
        page.update()

    update_profile_details(current_profile)

    def on_profile_change(e):
        selected_prof = e.control.value
        client.send_request({"type": "command", "cmd": "set_profile", "value": selected_prof})
        update_profile_details(selected_prof)
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
    custom_list_col = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, height=140)

    def refresh_whitelist():
        custom_list_col.controls.clear()
        wl_resp = client.send_request({"type": "command", "cmd": "get_whitelist"})
        whitelist = wl_resp.get("whitelist", []) if wl_resp.get("status") == "ok" else []
        
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
                        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                        bgcolor=CARD,
                        border_radius=6,
                        border=ft.Border.all(1, BORDER),
                    )
                )
        page.update()

    def add_proc(e):
        val = proc_input.value.strip().lower()
        if not val:
            return
        add_resp = client.send_request({"type": "command", "cmd": "add_whitelist", "value": val})
        if add_resp.get("status") == "ok":
            proc_input.value = ""
            refresh_whitelist()
            page.snack_bar = ft.SnackBar(ft.Text(f"Added '{val}' to custom whitelist.", color=ACCENT), bgcolor=CARD_ALT)
            page.snack_bar.open = True
            page.update()
        else:
            page.snack_bar = ft.SnackBar(ft.Text("Error adding process to whitelist", color=CRIT), bgcolor=CARD_ALT)
            page.snack_bar.open = True
            page.update()

    def remove_proc(val):
        rem_resp = client.send_request({"type": "command", "cmd": "remove_whitelist", "value": val})
        if rem_resp.get("status") == "ok":
            refresh_whitelist()
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

    add_button = ft.FilledButton(
        content=body_text("Add", size=12, weight=ft.FontWeight.BOLD, color=BG),
        style=ft.ButtonStyle(
            bgcolor=ACCENT,
            shape=ft.RoundedRectangleBorder(radius=8),
        ),
        on_click=add_proc,
        height=38,
    )

    # 3. Service Control Lifecycle Manager
    def on_shutdown_click(e):
        page.user_shutdown_requested = True
        shutdown_resp = client.send_request({"type": "command", "cmd": "shutdown"})
        if shutdown_resp.get("status") == "ok":
            notifier.send(
                title="⚡ SRO: Service Stopped",
                message="Background optimizer service has been shut down. All suspended processes resumed."
            )
            page.snack_bar = ft.SnackBar(ft.Text("Background service shutdown signal sent. All processes resumed.", color=TEXT), bgcolor=CARD_ALT)
            page.snack_bar.open = True
            page.update()
            def close_window():
                time.sleep(2.0)
                try:
                    page.run_task(page.window.close)
                except Exception:
                    pass
            threading.Thread(target=close_window, daemon=True).start()
        else:
            page.user_shutdown_requested = False
            page.snack_bar = ft.SnackBar(ft.Text("Failed to stop service: " + shutdown_resp.get("message", ""), color=CRIT), bgcolor=CARD_ALT)
            page.snack_bar.open = True
            page.update()

    shutdown_btn = ft.FilledButton(
        content=body_text("Stop Background Service", size=12, weight=ft.FontWeight.BOLD, color=TEXT),
        style=ft.ButtonStyle(
            bgcolor=CRIT,
            shape=ft.RoundedRectangleBorder(radius=8),
        ),
        on_click=on_shutdown_click,
        height=38,
    )

    refresh_whitelist()

    return ft.Column(
        [
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

            ft.ResponsiveRow(
                [
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
                                        # Background Optimizer Engine Toggle
                                        ft.Container(
                                            content=ft.Column(
                                                [
                                                    ft.Row(
                                                        [
                                                            ft.Column(
                                                                [
                                                                    body_text("Background Optimizer Engine", size=14, weight=ft.FontWeight.W_600),
                                                                    body_text("Runs active telemetry & inference loops", size=11, color=MUTED),
                                                                ],
                                                                spacing=1,
                                                            ),
                                                            ft.Container(expand=True),
                                                            optimizer_switch,
                                                        ],
                                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                                    ),
                                                    ft.Divider(height=8, color=BORDER),
                                                    body_text(
                                                        "When turned OFF, SRO suspends all active resource monitoring and AI predictions, "
                                                        "safely resuming all suspended processes. The IPC client remains connected to "
                                                        "allow re-enabling.",
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
                                        # Auto-Pilot Info
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

                    ft.Column(
                        [
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
                                                    ft.Row(
                                                        [
                                                            proc_input,
                                                            add_button,
                                                        ],
                                                        spacing=8,
                                                    ),
                                                    ft.Divider(height=8, color=BORDER),
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
                            
                            card(
                                ft.Column(
                                    [
                                        ft.Row(
                                            [
                                                ft.Icon(ft.Icons.DND_FORWARDSLASH, color=CRIT, size=18),
                                                section_title("SERVICE LIFECYCLE CONTROL"),
                                            ],
                                            spacing=8,
                                        ),
                                        ft.Container(
                                            content=ft.Column(
                                                [
                                                    body_text("Stop the background system optimizer service cleanly (resumes all suspended tasks):", size=12, color=MUTED),
                                                    ft.Divider(height=8, color=BORDER),
                                                    ft.Row([shutdown_btn], alignment=ft.MainAxisAlignment.START),
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
                ],
                spacing=16,
            ),

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
        MetricTile("CPU", "%", ACCENT, tooltip="Current CPU usage (average across all cores)"),
        MetricTile("Memory", "%", WARN, tooltip="Current system RAM consumption"),
        MetricTile("Temp", "°C", CRIT, tooltip="CPU packaging temperature (physical or simulated)"),
        MetricTile("Swap", "%", ACCENT, tooltip="System paging virtual swap file usage"),
    )
    cpu_c = RollingChart("CPU", ACCENT)
    mem_c = RollingChart("Memory", WARN)
    temp_c = RollingChart("CPU Temp", CRIT, value_max=100.0)

    gauge_ring = ft.ProgressRing(
        value=0.0,
        width=112,
        height=112,
        stroke_width=8,
        color=ACCENT,
        bgcolor=CARD_ALT,
        tooltip="AI Bottleneck Risk forecast within the next 30 seconds",
    )
    gauge_value = body_text("—", size=32, weight=ft.FontWeight.BOLD, color=ACCENT)
    gauge_insight = body_text(
        "Connecting to background service…",
        size=12,
        color=MUTED,
        text_align=ft.TextAlign.CENTER,
        max_lines=3,
    )

    ai_gauge_panel = _ai_gauge_panel(gauge_ring, gauge_value, gauge_insight)

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

    top_dashboard_row = ft.Row(
        [ai_gauge_panel, metrics_card],
        spacing=16,
        height=240,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    telemetry_row = ft.Row(
        [cpu_c.control, mem_c.control, temp_c.control],
        spacing=16,
        height=190,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
    )

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

    autopilot = ft.Switch(
        value=False, 
        active_color=ACCENT,
        on_change=lambda e: callbacks["on_autopilot"](e) if "on_autopilot" in callbacks else None,
        tooltip="Enable/disable autonomous AI bottleneck mitigation actions",
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
        tooltip="Manually trigger process resource compression & garbage collection",
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
        tooltip="Restore original scheduling limits and affinities to all suspended processes",
    )

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
            spacing=16,
            expand=True,
        ),
    )

    dashboard_view = ft.Column(
        [
            ft.Row(
                [
                    body_text("Dashboard", size=22, weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.HELP_OUTLINE,
                        icon_color=MUTED,
                        icon_size=20,
                        tooltip="Open Help & FAQ Sidebar",
                        on_click=lambda e: open_help_sidebar(page),
                    ),
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


def _update_analytics_dict(ui: DashboardUI, res: dict) -> None:
    if res is None:
        return
    ui.last_result = res
    calibrating = res.get("calibrating", False)
    if calibrating:
        ui.an_confidence.value = "Confidence: calibrating…"
        ui.an_confidence.color = WARN
        ui.an_predicted_cpu.value = "Predicted CPU: —"
        ui.an_risk_level.value = "Risk level: —"
    else:
        conf = res.get("confidence", 0.0)
        pct = int(conf * 100)
        col = severity(pct)
        ui.an_confidence.value = f"Confidence: {pct}%"
        ui.an_confidence.color = col
        ui.an_predicted_cpu.value = f"Predicted CPU: {res.get('predicted_cpu', 0.0):.1f}%"
        if pct >= 80:
            ui.an_risk_level.value = "Risk level: High"
            ui.an_risk_level.color = CRIT
        elif pct >= 55:
            ui.an_risk_level.value = "Risk level: Moderate"
            ui.an_risk_level.color = WARN
        else:
            ui.an_risk_level.value = "Risk level: Low"
            ui.an_risk_level.color = ACCENT
            
    f = res.get("features", {}) or {}
    cpu = f.get("cpu_percent_raw", f.get("cpu_percent", 0))
    mem = f.get("mem_percent_raw", f.get("mem_percent", 0))
    ui.an_cpu_live.value = f"CPU: {cpu:.1f}%"
    ui.an_mem_live.value = f"Memory: {mem:.1f}%"

    # Dynamic XAI progress bars & labels updates
    attrs = res.get("attributions")
    if attrs is not None and len(attrs) >= 4:
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
    page.title = "System Resource Optimizer Dashboard"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = BG
    page.padding = 0
    page.window.width = 1320
    page.window.height = 840
    page.window.min_width = 1100
    page.window.min_height = 720

    # Set taskbar/window icon
    import platform
    if platform.system() == "Windows":
        icon_ext = "ico"
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("addo561.sro.systemresourceoptimizer.v2")
        except Exception:
            pass
    elif platform.system() == "Darwin":
        icon_ext = "icns"
    else:
        icon_ext = "png"

    icon_path = os.path.join(BASE_DIR, "assets", f"icon.{icon_ext}")
    if os.path.exists(icon_path):
        page.window.icon = icon_path


    # IPC Client action triggers — notifications fired from the foreground client
    # so macOS delivers them instantly (background daemon processes are throttled)
    def on_boost(_e):
        resp = client.send_request({"type": "command", "cmd": "boost"})
        notifier.send(
            title="🚀 One-Click Boost Activated",
            message="Memory freed and background processes suspended."
        )

    def on_undo(_e):
        resp = client.send_request({"type": "command", "cmd": "undo"})
        notifier.send(
            title="↩ Undo: Processes Restored",
            message="All optimizer-suspended processes have been resumed."
        )

    ui_callbacks = {
        "on_boost": on_boost,
        "on_undo": on_undo,
        "on_autopilot": lambda e: client.send_request({
            "type": "command",
            "cmd": "toggle_autopilot",
            "value": e.control.value
        })
    }

    ui, dashboard_view, analytics_view, settings_view, right_rail = build_dashboard_content(page, ui_callbacks)
    help_view = build_help_view(page)
    
    views = {
        "dashboard": dashboard_view,
        "analytics": analytics_view,
        "settings": settings_view,
        "help": help_view,
    }

    for k, view in views.items():
        view.expand = True
        view.top = 0
        view.left = 0
        view.right = 0
        view.bottom = 0
        view.visible = (k == "dashboard")

    views_stack = ft.Stack(
        controls=[dashboard_view, analytics_view, settings_view, help_view],
        expand=True,
    )

    content_host = ft.Container(
        content=views_stack,
        expand=True,
        padding=ft.Padding.only(left=24, top=16, right=16, bottom=16),
    )
    right_rail_host = ft.Container(
        width=300, 
        content=right_rail, 
        visible=True,
        padding=ft.Padding.only(top=16, right=16, bottom=16),
    )
    
    # ── Reconnect Overlay (Offline Page) ──────────────────────────────────────
    overlay = ft.Container(
        content=ft.Column(
            [
                ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=WARN, size=48),
                body_text("Optimizer Service Offline", size=20, weight=ft.FontWeight.BOLD),
                body_text("The background optimizer service (optimizer_service.py) is currently unreachable.", size=12, color=MUTED, text_align=ft.TextAlign.CENTER),
                ft.Row(
                    [
                        ft.ProgressRing(width=16, height=16, stroke_width=2, color=ACCENT),
                        body_text("Waiting for background service to launch...", size=12, color=ACCENT),
                    ],
                    spacing=8,
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=14,
        ),
        bgcolor=CARD,
        border=ft.Border.all(1, BORDER),
        border_radius=16,
        padding=30,
        width=450,
        height=260,
    )
    
    overlay_host = ft.Container(
        content=overlay,
        alignment=ft.Alignment.CENTER,
        visible=True,
        expand=True,
        bgcolor="#0D1117D0",  # Dark semi-transparent blur color
    )

    app_layout = ft.Row(
        [build_sidebar(lambda key: navigate(key), active="dashboard"), content_host, right_rail_host],
        expand=True,
    )

    page.add(
        ft.Stack(
            [
                app_layout,
                overlay_host,
            ],
            expand=True,
        )
    )

    def navigate(key: str) -> None:
        if key not in views:
            return
            
        # Rebuild sidebar selections
        sidebar = app_layout.controls[0]
        for k, item in sidebar.nav_refs.items():
            selected = k == key
            item.bgcolor = CARD_ALT if selected else None
            row = item.content
            row.controls[0].color = ACCENT if selected else MUTED
            row.controls[1].color = TEXT if selected else MUTED

        if key == "settings":
            # Rebuild the settings view to get the latest settings
            views["settings"] = build_settings_view(ui.autopilot_status, page)
            views["settings"].expand = True
            views["settings"].top = 0
            views["settings"].left = 0
            views["settings"].right = 0
            views["settings"].bottom = 0
            
            # Reassign controls array to trigger Flet change detection
            views_stack.controls = [
                views["dashboard"],
                views["analytics"],
                views["settings"],
                views["help"]
            ]

        for k, view in views.items():
            view.visible = (k == key)

        right_rail_host.visible = key == "dashboard"
        if key == "dashboard":
            ui.sync_charts()
        page.update()

    def on_page_resize(_e) -> None:
        ui.sync_charts()
        page.update()

    page.on_resize = on_page_resize

    def launch_background_service():
        import subprocess
        import platform
        try:
            # Check if service is already running by attempting a quick socket connection
            temp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            temp_sock.settimeout(0.5)
            temp_sock.connect(("127.0.0.1", IPC_PORT))
            temp_sock.close()
            return # Already running
        except Exception:
            pass # Not running, proceed to launch
            
        try:
            if getattr(sys, "frozen", False):
                base_dir = os.path.dirname(sys.executable)
                if platform.system() == "Windows":
                    srv_exe = os.path.join(base_dir, "SystemResourceOptimizerService.exe")
                    if os.path.exists(srv_exe):
                        subprocess.Popen([srv_exe], creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
                else:
                    srv_exe = os.path.join(base_dir, "SystemResourceOptimizerService")
                    if os.path.exists(srv_exe):
                        subprocess.Popen([srv_exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                script_path = os.path.join(_DIR, "optimizer_service.py")
                if os.path.exists(script_path):
                    if sys.platform == "win32":
                        pyw = sys.executable.replace("python.exe", "pythonw.exe")
                        subprocess.Popen([pyw, script_path], creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
                    else:
                        subprocess.Popen([sys.executable, script_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"Error launching background service: {e}", flush=True)

    # Background polling and state synchronization thread
    async def poll_service_worker():
        first_connect = True
        service_start_attempted = False
        while True:
            if getattr(page, "user_shutdown_requested", False):
                break  # Exit loop entirely — do NOT continue spinning

            if not client.connected:
                # Show overlay immediately
                overlay_host.visible = True
                try:
                    page.update()
                except Exception:
                    pass
                
                # Attempt to auto-start if not done yet
                if not service_start_attempted and not getattr(page, "user_shutdown_requested", False):
                    launch_background_service()
                    service_start_attempted = True
                    await asyncio.sleep(1.0)
                try:
                    page.update()
                except Exception:
                    pass
                    
                if client.connect():
                    log_resp = client.send_request({"type": "get_full_state"})
                    if log_resp.get("connected"):
                        # Process pending notifications from the background daemon
                        for notif in log_resp.get("pending_notifications", []):
                            try:
                                notifier.send(title=notif["title"], message=notif["message"])
                            except Exception:
                                pass

                        # Reconnected successfully! Populate history charts
                        first_connect = False
                        service_start_attempted = False
                        
                        # Populate metrics history
                        history = log_resp.get("history", [])
                        ui.charts[0].data.clear()
                        ui.charts[1].data.clear()
                        ui.charts[2].data.clear()
                        
                        # Fill deques
                        for i in range(120):
                            ui.charts[0].data.append(0.0)
                            ui.charts[1].data.append(0.0)
                            ui.charts[2].data.append(0.0)
                            
                        for item in history:
                            f = item.get("features", {})
                            cpu = f.get("cpu_percent_raw", f.get("cpu_percent", 0))
                            mem = f.get("mem_percent_raw", f.get("mem_percent", 0))
                            tmp = f.get("cpu_temp_c", 0)
                            ui.charts[0].push(cpu, redraw=False)
                            ui.charts[1].push(mem, redraw=False)
                            if tmp > 0:
                                ui.charts[2].push(tmp, redraw=False)

                        # Set Autopilot UI Switch
                        ui.autopilot.value = log_resp.get("autopilot_enabled", True)
                        ui.autopilot_status.value = f"Status: {'ON' if ui.autopilot.value else 'OFF'}"
                        ui.autopilot_status.color = ACCENT if ui.autopilot.value else MUTED
                        
                        # Set logs
                        ui.log_list.controls.clear()
                        for entry in log_resp.get("logs", []):
                            ts = entry.get("time", "")
                            msg = entry.get("message", "")
                            color = entry.get("color", ACCENT)
                            ui.log_list.controls.append(
                                body_text(f"[{ts}] {msg}", size=11, color=color, font_family="monospace")
                            )
                        
                        # Trigger UI Refresh
                        overlay_host.visible = False
                        ui.sync_charts()
                        try:
                            page.update()
                        except Exception:
                            pass
                else:
                    await asyncio.sleep(2.0)
                    continue

            # Connected - poll updates
            resp = client.send_request({"type": "get_update"})
            if resp.get("connected"):
                # Process pending notifications from the background daemon
                for notif in resp.get("pending_notifications", []):
                    try:
                        notifier.send(title=notif["title"], message=notif["message"])
                    except Exception:
                        pass
                optimizer_active = resp.get("optimizer_active", True)
                if not optimizer_active:
                    ui.gauge_ring.value = 0.0
                    ui.gauge_ring.color = MUTED
                    ui.gauge_value.value = "OFF"
                    ui.gauge_value.color = MUTED
                    ui.gauge_insight.value = "Optimizer engine suspended by user"
                    ui.gauge_insight.color = MUTED
                else:
                    latest = resp.get("latest_result")
                    if latest:
                        f = latest.get("features", {})
                        cpu = f.get("cpu_percent_raw", f.get("cpu_percent", 0))
                        mem = f.get("mem_percent_raw", f.get("mem_percent", 0))
                        tmp = f.get("cpu_temp_c", 0)
                        swap = f.get("swap_percent", 0)
                        
                        ui.metrics[0].update(cpu)
                        ui.metrics[1].update(mem)
                        ui.metrics[2].update(tmp if tmp > 0 else 0)
                        ui.metrics[3].update(swap)
                        
                        ui.charts[0].push(cpu, redraw=True)
                        ui.charts[1].push(mem, redraw=True)
                        if tmp > 0:
                            ui.charts[2].push(tmp, redraw=True)
                            
                        _update_analytics_dict(ui, latest)

                        if latest.get("calibrating"):
                            cal_prog = resp.get("calib_progress", (0, CALIBRATION_SECONDS))
                            elapsed, total = cal_prog
                            if elapsed != -1:
                                pct = min(100, max(1, int((elapsed / total) * 100)))
                                ui.gauge_insight.value = f"Calibrating telemetry ({pct}%)..."
                                ui.gauge_insight.color = WARN
                                ui.gauge_ring.value = elapsed / total
                                ui.gauge_ring.color = WARN
                                ui.gauge_value.value = f"{pct}%"
                                ui.gauge_value.color = WARN
                        else:
                            conf = latest.get("confidence", 0.0)
                            pct = int(conf * 100)
                            col = severity(pct)
                            ui.gauge_ring.value = conf
                            ui.gauge_ring.color = col
                            ui.gauge_value.value = f"{pct}%"
                            ui.gauge_value.color = col
                            if pct >= 80:
                                ui.gauge_insight.value = f"High load risk · CPU ≈ {latest.get('predicted_cpu', 0.0):.0f}%"
                                ui.gauge_insight.color = CRIT
                            elif pct >= 55:
                                ui.gauge_insight.value = f"Moderate risk · CPU ≈ {latest.get('predicted_cpu', 0.0):.0f}%"
                                ui.gauge_insight.color = WARN
                            else:
                                ui.gauge_insight.value = "System healthy"
                                ui.gauge_insight.color = ACCENT
                    else:
                        ui.gauge_ring.value = 0.0
                        ui.gauge_ring.color = MUTED
                        ui.gauge_value.value = "..."
                        ui.gauge_value.color = MUTED
                        ui.gauge_insight.value = "Initializing telemetry..."
                        ui.gauge_insight.color = MUTED

                # Suspended table
                suspended = resp.get("suspended_processes", [])
                rows = []
                for i, sp in enumerate(suspended):
                    rows.append(
                        ft.DataRow(
                            color=CARD_ALT if i % 2 else None,
                            cells=[
                                ft.DataCell(ft.Container(content=body_text(sp["name"], size=11), width=140)),
                                ft.DataCell(ft.Container(content=body_text(time.strftime("%H:%M:%S", time.localtime(sp["suspended_at"])), size=11, color=MUTED), width=80)),
                            ],
                        )
                    )
                ui.susp_table.rows = rows
                ui.undo_btn.disabled = len(rows) == 0

                # Top processes table
                top_procs = resp.get("top_processes", [])
                ui.proc_table.rows = proc_rows(top_procs)

                # Sync Autopilot state
                ui.autopilot.value = resp.get("autopilot_enabled", True)
                ui.autopilot_status.value = f"Status: {'ON' if ui.autopilot.value else 'OFF'}"
                ui.autopilot_status.color = ACCENT if ui.autopilot.value else MUTED

                # Sync logs
                ui.log_list.controls.clear()
                for entry in resp.get("logs", []):
                    ts = entry.get("time", "")
                    msg = entry.get("message", "")
                    color = entry.get("color", ACCENT)
                    ui.log_list.controls.append(
                        body_text(f"[{ts}] {msg}", size=11, color=color, font_family="monospace")
                    )

                overlay_host.visible = False
            else:
                client.connected = False
                overlay_host.visible = True
                
            try:
                page.update()
            except Exception:
                pass
            await asyncio.sleep(0.5)

    # ── Disconnect handler: fires reliably when window is closed ────────────
    # page.on_disconnect is more reliable than window.on_event("close")
    # because it fires when the Flet WebSocket connection drops, which
    # happens immediately when the window is destroyed.
    def on_disconnect(_):
        page.user_shutdown_requested = True
        client.close()

    page.on_disconnect = on_disconnect

    # Also hook window events as a secondary safety net
    def on_window_event(e):
        if e.data in ("close", "destroy"):
            on_disconnect(None)

    page.window.on_event = on_window_event
    page.window.prevent_close = False  # ensure close events fire normally

    page.run_task(poll_service_worker)
    ui.sync_charts()
    page.update()


def main(page: ft.Page) -> None:
    run_app(page)


def _set_windows_taskbar_icon_async() -> None:
    import threading

    def worker():
        import time
        import os
        import ctypes
        from ctypes import wintypes
        import psutil

        # Wait for the Flet child process to start (e.g. sleep 0.5s initially, then poll)
        time.sleep(0.5)

        for _ in range(40):  # 40 * 0.5s = 20s max
            try:
                current_process = psutil.Process(os.getpid())
                child_pids = {p.pid for p in current_process.children(recursive=True)}
            except Exception:
                child_pids = set()
            child_pids.add(os.getpid())

            hwnd_found = [None]

            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def enum_callback(hwnd, lparam):
                if ctypes.windll.user32.IsWindowVisible(hwnd):
                    class_name = ctypes.create_unicode_buffer(256)
                    ctypes.windll.user32.GetClassNameW(hwnd, class_name, 256)
                    if class_name.value == "FLUTTER_RUNNER_WIN32_WINDOW":
                        pid = wintypes.DWORD()
                        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                        if pid.value in child_pids:
                            hwnd_found[0] = hwnd
                            return False
                return True

            cb = WNDENUMPROC(enum_callback)
            ctypes.windll.user32.EnumWindows(cb, 0)

            if hwnd_found[0] is not None:
                hwnd = hwnd_found[0]
                try:
                    class GUID(ctypes.Structure):
                        _fields_ = [
                            ("Data1", ctypes.c_ulong),
                            ("Data2", ctypes.c_ushort),
                            ("Data3", ctypes.c_ushort),
                            ("Data4", ctypes.c_ubyte * 8),
                        ]
                        def __init__(self, l, w1, w2, b1, b2, b3, b4, b5, b6, b7, b8):
                            self.Data1 = l
                            self.Data2 = w1
                            self.Data3 = w2
                            self.Data4 = (ctypes.c_ubyte * 8)(b1, b2, b3, b4, b5, b6, b7, b8)

                    IID_IPropertyStore = GUID(0x886d8eeb, 0x8cf2, 0x4446, 0x8d, 0x02, 0xcd, 0xba, 0x1d, 0xbd, 0xcf, 0x99)
                    shell32 = ctypes.windll.shell32
                    prop_store = ctypes.c_void_p()

                    hr = shell32.SHGetPropertyStoreForWindow(
                        hwnd,
                        ctypes.byref(IID_IPropertyStore),
                        ctypes.byref(prop_store)
                    )

                    if hr >= 0 and prop_store.value:
                        class PROPERTYKEY(ctypes.Structure):
                            _fields_ = [
                                ("fmtid", GUID),
                                ("pid", ctypes.c_ulong),
                            ]
                        PKEY_AppUserModel_ID = PROPERTYKEY(
                            GUID(0x9F4C2855, 0x0379, 0x4D01, 0x87, 0xE5, 0x45, 0xD6, 0xD7, 0x42, 0x46, 0x94),
                            5
                        )
                        class PROPVARIANT(ctypes.Structure):
                            _fields_ = [
                                ("vt", ctypes.c_ushort),
                                ("wReserved1", ctypes.c_ushort),
                                ("wReserved2", ctypes.c_ushort),
                                ("wReserved3", ctypes.c_ushort),
                                ("pwszVal", ctypes.c_wchar_p),
                                ("padding", ctypes.c_ubyte * 8),
                            ]
                        pv = PROPVARIANT()
                        pv.vt = 31
                        pv.pwszVal = "addo561.sro.systemresourceoptimizer.v2"

                        vtable_ptr = ctypes.cast(prop_store, ctypes.POINTER(ctypes.c_void_p))
                        vtable = ctypes.cast(vtable_ptr[0], ctypes.POINTER(ctypes.c_void_p))

                        set_value_proto = ctypes.WINFUNCTYPE(
                            ctypes.c_long,
                            ctypes.c_void_p,
                            ctypes.POINTER(PROPERTYKEY),
                            ctypes.POINTER(PROPVARIANT)
                        )
                        SetValue = set_value_proto(vtable[6])

                        commit_proto = ctypes.WINFUNCTYPE(
                            ctypes.c_long,
                            ctypes.c_void_p
                        )
                        Commit = commit_proto(vtable[7])

                        release_proto = ctypes.WINFUNCTYPE(
                            ctypes.c_ulong,
                            ctypes.c_void_p
                        )
                        Release = release_proto(vtable[2])

                        hr_set = SetValue(prop_store, ctypes.byref(PKEY_AppUserModel_ID), ctypes.byref(pv))
                        hr_commit = Commit(prop_store)
                        Release(prop_store)

                        if hr_set >= 0 and hr_commit >= 0:
                            print(f"[win-icon-patch] Successfully set System.AppUserModel.ID to addo561.sro.systemresourceoptimizer.v2", flush=True)
                            break
                        else:
                            print(f"[win-icon-patch] SetValue (hr={hr_set}) or Commit (hr={hr_commit}) failed", flush=True)
                except Exception as exc:
                    print(f"[win-icon-patch] Error setting property store: {exc}", flush=True)

            time.sleep(0.5)

    t = threading.Thread(target=worker, daemon=True)
    t.start()


def _patch_flet_app_macos() -> None:
    """
    Hides the Flet renderer subprocess from the macOS Dock so only our
    app's custom icon appears.  Done by injecting LSUIElement into
    Flet.app/Contents/Info.plist and replacing its AppIcon with ours —
    both changes survive a re-open of the same Flet client version.
    """
    import glob
    import os
    import plistlib
    import shutil
    import sys

    # Find whichever flet-desktop version is installed (matches both full and light flavors)
    flet_glob = os.path.expanduser(
        "~/.flet/client/flet-desktop-*/Flet.app/Contents"
    )
    matches = sorted(glob.glob(flet_glob), reverse=True)  # newest first
    if not matches:
        return

    contents_dir = matches[0]
    plist_path   = os.path.join(contents_dir, "Info.plist")
    res_dir      = os.path.join(contents_dir, "Resources")

    # ── 1. Patch Info.plist ──
    try:
        with open(plist_path, "rb") as fh:
            plist = plistlib.load(fh)

        changed = False
        is_frozen = getattr(sys, "frozen", False)
        target_lsui = True if is_frozen else False

        if plist.get("LSUIElement") != target_lsui:
            plist["LSUIElement"] = target_lsui
            changed = True

        if plist.get("CFBundleName") != "System Resource Optimizer":
            plist["CFBundleName"] = "System Resource Optimizer"
            changed = True

        if plist.get("CFBundleDisplayName") != "System Resource Optimizer":
            plist["CFBundleDisplayName"] = "System Resource Optimizer"
            changed = True

        if changed:
            with open(plist_path, "wb") as fh:
                plistlib.dump(plist, fh)
            app_dir = os.path.dirname(contents_dir)
            os.utime(app_dir, None)
    except Exception as exc:
        print(f"[icon-patch] plist update skipped: {exc}", flush=True)

    # ── 2. Replace Flet.app's AppIcon.icns with our icon ────────────────
    try:
        our_icns = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "assets", "icon.icns"
        )
        if os.path.exists(our_icns):
            for name in ("AppIcon.icns", "AppIcon"):
                dest = os.path.join(res_dir, name)
                shutil.copy2(our_icns, dest)
    except Exception as exc:
        print(f"[icon-patch] icon replace skipped: {exc}", flush=True)


if __name__ == "__main__":
    try:
        import sys as _sys
        import os as _os

        # ── macOS: hide Flet.app from Dock and give it our icon ──────────
        if _sys.platform == "darwin":
            try:
                import flet_desktop
                flet_desktop.ensure_client_cached()
            except Exception as e:
                print(f"[icon-patch] flet_desktop import/caching failed: {e}", flush=True)
            _patch_flet_app_macos()

        # ── Windows: set AppUserModelID BEFORE ft.run() creates any window
        if _sys.platform == "win32":
            import ctypes as _ct
            try:
                _ct.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "addo561.sro.systemresourceoptimizer.v2"
                )
            except Exception:
                pass
            try:
                _set_windows_taskbar_icon_async()
            except Exception:
                pass

        # Resolve assets dir so Flet renderer picks up our icon.png
        _assets = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "assets")
        ft.run(main, assets_dir=_assets)
    except Exception as e:
        print(f"❌ Flet dashboard runtime error: {e}", flush=True)
        _sys.exit(1)
