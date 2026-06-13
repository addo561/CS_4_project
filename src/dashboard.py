"""
dashboard_glass.py — System Resource Optimizer (glassmorphic client).

Production Flet client wired to the background service (`optimizer_service.py`)
over local TCP IPC. Modern glass UI with 4 tabs (Dashboard, AI Analytics,
Settings, Help) and a first-run guided tour. Lightweight: a single 1 Hz IPC
poll drives everything; no local model, no file logging from the UI.
"""

import asyncio
import json
import logging
import math
import os
import socket
import sys
import threading
import time
from collections import deque

import flet as ft
import flet.canvas as cv

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

from config import CALIBRATION_SECONDS, VERSION, PROFILES, IPC_PORT, BASE_DIR
from core.notifier import Notifier

log = logging.getLogger("sro_dashboard")
log.addHandler(logging.NullHandler())

# ── Palette ───────────────────────────────────────────────────────────────────
BG_TOP   = "#0A0E14"
BG_BOT   = "#0F1A1F"
ACCENT   = "#00E0A8"
ACCENT_2 = "#3DA9FC"
VIOLET   = "#9B6CFF"
WARN     = "#F7B955"
CRIT     = "#FF6B6B"
TEXT     = "#EAF2F0"
MUTED    = "#8FA3A0"
GLASS    = "#FFFFFF"

HIST_LEN = 46


# ── Helpers ───────────────────────────────────────────────────────────────────
def glass(content, *, padding=20, radius=22, expand=False, width=None, height=None,
          glow=ACCENT, glow_strength=0.18):
    return ft.Container(
        content=content,
        padding=ft.Padding(padding, padding, padding, padding),
        border_radius=radius, width=width, height=height, expand=expand,
        bgcolor=ft.Colors.with_opacity(0.05, GLASS),
        blur=ft.Blur(22, 22, ft.BlurTileMode.CLAMP),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.10, GLASS)),
        gradient=ft.LinearGradient(
            begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT,
            colors=[ft.Colors.with_opacity(0.09, GLASS), ft.Colors.with_opacity(0.015, GLASS)]),
        shadow=ft.BoxShadow(blur_radius=34, spread_radius=-6,
                            color=ft.Colors.with_opacity(glow_strength, glow),
                            offset=ft.Offset(0, 14)),
    )


def blob(color, size, left, top):
    return ft.Container(width=size, height=size, left=left, top=top, border_radius=size,
                        bgcolor=ft.Colors.with_opacity(0.55, color),
                        blur=ft.Blur(140, 140, ft.BlurTileMode.CLAMP))


def pct_color(p):
    return ACCENT if p < 60 else (WARN if p < 85 else CRIT)


def lbl(txt, size=12, color=MUTED, weight=ft.FontWeight.W_500):
    return ft.Text(txt, size=size, color=color, weight=weight)


def card_title(icon, text, glow):
    return ft.Row([ft.Icon(icon, size=18, color=glow),
                   lbl(text, size=12, color=TEXT, weight=ft.FontWeight.W_600)], spacing=8)


# ── IPC client (ported from the legacy dashboard) ─────────────────────────────
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
        if not self.connected:
            if not self.connect():
                return {"connected": False, "status": "error", "message": "Service offline"}
        with self.lock:
            try:
                self.sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
                buffer = b""
                while b"\n" not in buffer:
                    chunk = self.sock.recv(4096)
                    if not chunk:
                        raise ConnectionError("Connection closed by server")
                    buffer += chunk
                return json.loads(buffer.split(b"\n")[0].decode("utf-8"))
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


client = IPCClient()
notifier = Notifier()


def main(page: ft.Page):
    page.title = "System Resource Optimizer"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = BG_TOP
    page.padding = 0
    page.window.width = 1240
    page.window.height = 840
    page.window.min_width = 1040
    page.window.min_height = 720

    # window/dock icon (macOS .icns / Windows .ico / Linux .png)
    import platform
    icon_ext = {"Windows": "ico", "Darwin": "icns"}.get(platform.system(), "png")
    icon_path = os.path.join(BASE_DIR, "assets", f"icon.{icon_ext}")
    if os.path.exists(icon_path):
        page.window.icon = icon_path

    state = {"view": "dashboard", "optimizer": True, "autopilot": True,
             "notify": True, "profile": "Balanced",
             "service_stopped": False, "announced": False}
    undo_state = {"enabled": False}

    # ── toast popup (Flet 0.85 has no page.snack_bar; use our own glass toast) ─
    toast_icon = ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, size=18, color=ACCENT)
    toast_text = ft.Text("", size=13, color=TEXT, weight=ft.FontWeight.W_600)
    toast = ft.Container(
        content=glass(ft.Row([toast_icon, toast_text], spacing=10, tight=True),
                      padding=14, radius=14, glow=ACCENT, glow_strength=0.28),
        right=26, top=22, visible=False,
        animate_opacity=ft.Animation(250, ft.AnimationCurve.EASE_OUT))

    def show_toast(message, icon=ft.Icons.CHECK_CIRCLE_ROUNDED, color=ACCENT):
        toast_icon.icon = icon
        toast_icon.color = color
        toast_text.value = message
        toast.visible = True
        try:
            page.update()
        except Exception:
            pass

        def _hide():
            toast.visible = False
            try:
                page.update()
            except Exception:
                pass

        threading.Timer(3.4, _hide).start()

    def notify(title, message):
        if state["notify"]:
            try:
                notifier.send(title=title, message=message)
            except Exception:
                pass

    # ── live controls ────────────────────────────────────────────────────────
    def ring(color):
        return ft.ProgressRing(value=0, stroke_width=11, color=color,
                               bgcolor=ft.Colors.with_opacity(0.08, GLASS), width=150, height=150)

    cpu_ring, mem_ring, risk_ring = ring(ACCENT), ring(ACCENT_2), ring(VIOLET)
    cpu_val = ft.Text("0", size=40, weight=ft.FontWeight.BOLD, color=TEXT)
    mem_val = ft.Text("0", size=40, weight=ft.FontWeight.BOLD, color=TEXT)
    risk_val = ft.Text("0", size=40, weight=ft.FontWeight.BOLD, color=TEXT)

    swap_v = ft.Text("0%", size=22, weight=ft.FontWeight.BOLD, color=TEXT)
    disk_v = ft.Text("0.0", size=22, weight=ft.FontWeight.BOLD, color=TEXT)
    net_up = ft.Text("0.0", size=22, weight=ft.FontWeight.BOLD, color=TEXT)
    net_dn = ft.Text("0.0", size=22, weight=ft.FontWeight.BOLD, color=TEXT)
    temp_v = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=TEXT)
    proc_n = ft.Text("0", size=22, weight=ft.FontWeight.BOLD, color=TEXT)
    risk_desc = ft.Text("Connecting…", size=12, color=MUTED, weight=ft.FontWeight.W_500)
    an_pred = ft.Text("Predicted CPU ≈ —", size=13, color=TEXT, weight=ft.FontWeight.W_500)
    an_thresh = ft.Text("Confidence required: —", size=12, color=MUTED)

    status_dot = ft.Container(width=9, height=9, border_radius=9, bgcolor=WARN)
    status_txt = lbl("CONNECTING…", size=11, color=WARN, weight=ft.FontWeight.W_600)
    clock = ft.Text("", size=12, color=MUTED, weight=ft.FontWeight.W_500)
    susp_txt = ft.Text("0 suspended", size=11, color=MUTED)

    hist = deque([0] * HIST_LEN, maxlen=HIST_LEN)
    spark = ft.Row(spacing=3, alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                   vertical_alignment=ft.CrossAxisAlignment.END, height=90)
    proc_list = ft.Column(spacing=10, scroll=ft.ScrollMode.HIDDEN, expand=True)

    # attribution bars (model order: CPU, Memory, Temp, Swap)
    attr = {}
    for nm in ("CPU", "Memory", "Temp", "Swap"):
        fill = ft.Container(width=0, height=8, border_radius=8, bgcolor=ACCENT,
                            animate=ft.Animation(350, ft.AnimationCurve.EASE_OUT))
        val = ft.Text("0%", size=12, color=MUTED, width=46, text_align=ft.TextAlign.RIGHT)
        attr[nm] = (fill, val)

    # ── settings controls (created up front so the poll loop can sync them) ───
    def build_switch(key, glow, on_extra=None):
        sw = ft.Switch(value=state[key], active_color=glow,
                       active_track_color=ft.Colors.with_opacity(0.4, glow),
                       inactive_track_color=ft.Colors.with_opacity(0.10, GLASS))

        def changed(e):
            state[key] = e.control.value
            if on_extra:
                on_extra(e.control.value)
            page.update()

        sw.on_change = changed
        return sw

    def on_optimizer_change(val):
        client.send_request({"type": "command", "cmd": "toggle_optimizer", "value": val})
        if val:
            notify("⚡ SRO: Optimizer Resumed", "Background optimizer loop has been resumed.")
        else:
            notify("⚡ SRO: Optimizer Suspended", "Background optimizer loop has been suspended. All processes resumed.")
        refresh_status()

    def on_autopilot_change(val):
        client.send_request({"type": "command", "cmd": "toggle_autopilot", "value": val})

    opt_switch = build_switch("optimizer", ACCENT, on_optimizer_change)
    auto_switch = build_switch("autopilot", ACCENT_2, on_autopilot_change)
    notify_switch = build_switch("notify", VIOLET)

    # Master switch: completely STOP (kill) or START the background service process.
    service_switch = ft.Switch(value=True, active_color=ACCENT,
                               active_track_color=ft.Colors.with_opacity(0.4, ACCENT),
                               inactive_track_color=ft.Colors.with_opacity(0.10, GLASS))

    def set_service(running):
        if running:
            state["service_stopped"] = False
            state["announced"] = False
            launch_background_service()
            show_toast("Starting background service…", ft.Icons.PLAY_ARROW_ROUNDED, ACCENT)
        else:
            try:
                client.send_request({"type": "command", "cmd": "shutdown"})
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass
            state["service_stopped"] = True
            show_toast("Background service stopped", ft.Icons.STOP_ROUNDED, WARN)
        refresh_status()
        page.update()

    service_switch.on_change = lambda e: set_service(e.control.value)

    profile_pills_row = ft.Row(spacing=10)
    set_thresh_txt = ft.Text("", size=12, color=MUTED)

    def update_threshold_text():
        thr = PROFILES.get(state["profile"], PROFILES["Balanced"]).get("CONFIDENCE_THRESHOLD", 0.8)
        set_thresh_txt.value = f"Confidence required to act: {int(thr * 100)}%  (set by profile)"
        an_thresh.value = f"Confidence required: {int(thr * 100)}%  ·  Profile: {state['profile']}"

    def build_profile_pills():
        profile_pills_row.controls.clear()
        for p, glow in (("Eco", ACCENT), ("Balanced", ACCENT_2), ("Gaming", VIOLET)):
            active = state["profile"] == p

            def pick(e, name=p):
                state["profile"] = name
                client.send_request({"type": "command", "cmd": "set_profile", "value": name})
                build_profile_pills()
                update_threshold_text()
                page.update()

            profile_pills_row.controls.append(
                ft.Container(content=ft.Text(p, size=12, weight=ft.FontWeight.W_600,
                                             color=(BG_TOP if active else MUTED)),
                             padding=ft.Padding(18, 9, 18, 9), border_radius=12, on_click=pick,
                             bgcolor=(glow if active else ft.Colors.with_opacity(0.05, GLASS)),
                             border=ft.Border.all(1, ft.Colors.with_opacity(0.12, GLASS))))

    # whitelist (protected processes)
    wl_input = ft.TextField(hint_text="process name, e.g. spotify", expand=True, height=46,
                            text_size=13, color=TEXT, border_color=ft.Colors.with_opacity(0.15, GLASS),
                            focused_border_color=ACCENT, content_padding=ft.Padding(12, 8, 12, 8))
    wl_list = ft.Column(spacing=8)

    def wl_chip(name):
        return ft.Container(
            content=ft.Row([ft.Icon(ft.Icons.SHIELD_ROUNDED, size=14, color=ACCENT),
                            ft.Text(name, size=12, color=TEXT, expand=True),
                            ft.Container(content=ft.Icon(ft.Icons.CLOSE_ROUNDED, size=15, color=MUTED),
                                         on_click=lambda e, n=name: wl_remove(n))], spacing=10),
            padding=ft.Padding(12, 8, 12, 8), border_radius=10,
            bgcolor=ft.Colors.with_opacity(0.05, GLASS),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.08, GLASS)))

    def refresh_whitelist():
        resp = client.send_request({"type": "command", "cmd": "get_whitelist"})
        wl = resp.get("whitelist", []) if resp.get("status") == "ok" else []
        wl_list.controls = ([wl_chip(n) for n in wl] if wl
                            else [ft.Text("No custom processes whitelisted.", size=12,
                                          color=MUTED, italic=True)])

    def wl_add(e=None):
        v = (wl_input.value or "").strip().lower()
        if not v:
            return
        client.send_request({"type": "command", "cmd": "add_whitelist", "value": v})
        wl_input.value = ""
        refresh_whitelist()
        page.update()

    def wl_remove(name):
        client.send_request({"type": "command", "cmd": "remove_whitelist", "value": name})
        refresh_whitelist()
        page.update()

    # ── actions: boost / undo ────────────────────────────────────────────────
    def on_boost(_e=None):
        client.send_request({"type": "command", "cmd": "boost"})
        notify("🚀 One-Click Boost Activated", "Memory freed and background processes suspended.")

    def on_undo(_e=None):
        if not undo_state["enabled"]:
            return
        client.send_request({"type": "command", "cmd": "undo"})
        notify("↩ Undo: Processes Restored", "All optimizer-suspended processes have been resumed.")

    def action_pill(label_txt, icon, glow, handler):
        return ft.Container(
            content=ft.Row([ft.Icon(icon, size=15, color=glow),
                            ft.Text(label_txt, size=12, color=TEXT, weight=ft.FontWeight.W_600)],
                           spacing=7, tight=True),
            padding=ft.Padding(15, 9, 15, 9), border_radius=11, on_click=handler,
            bgcolor=ft.Colors.with_opacity(0.08, glow),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.20, glow)))

    boost_pill = action_pill("Boost", ft.Icons.BOLT_ROUNDED, ACCENT, on_boost)
    undo_pill = action_pill("Undo", ft.Icons.UNDO_ROUNDED, ACCENT_2, on_undo)
    undo_pill.opacity = 0.45

    # ── reusable pieces ──────────────────────────────────────────────────────
    def gauge(rng, val, sub_text, glow):
        center = ft.Container(
            content=ft.Row([val, ft.Text("%", size=18, weight=ft.FontWeight.W_600, color=MUTED)],
                           alignment=ft.MainAxisAlignment.CENTER,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=2, tight=True),
            width=150, height=150, alignment=ft.Alignment.CENTER)
        return glass(
            ft.Column([ft.Stack([rng, center], alignment=ft.Alignment.CENTER, width=150, height=150),
                       lbl(sub_text, size=11, color=MUTED, weight=ft.FontWeight.W_600)],
                      horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                      alignment=ft.MainAxisAlignment.CENTER, spacing=12),
            glow=glow, expand=True, height=210)

    def mini(icon, value_ctrl, name, glow):
        return glass(
            ft.Column([ft.Row([ft.Icon(icon, size=18, color=glow),
                               lbl(name, size=11, color=MUTED, weight=ft.FontWeight.W_600)],
                              spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                       ft.Container(height=4), value_ctrl], spacing=2),
            glow=glow, glow_strength=0.12, expand=True, height=104, padding=16, radius=18)

    def attr_row(name, glow):
        fill, val = attr[name]
        fill.bgcolor = glow
        track = ft.Container(content=fill, height=8, border_radius=8, expand=True,
                             bgcolor=ft.Colors.with_opacity(0.07, GLASS),
                             alignment=ft.Alignment.CENTER_LEFT)
        return ft.Row([lbl(name, size=12, color=TEXT, weight=ft.FontWeight.W_600),
                       ft.Container(content=track, expand=True), val],
                      spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # ── First-run guided tour ────────────────────────────────────────────────
    TOUR_FLAG = os.path.join(os.path.expanduser("~"), ".sro_ui_tour_seen")
    tour_canvas = cv.Canvas(shapes=[], expand=True)
    tour_step_lbl = ft.Text("", size=11, color=ACCENT, weight=ft.FontWeight.W_700)
    tour_title = ft.Text("", size=16, weight=ft.FontWeight.BOLD, color=TEXT)
    tour_body = ft.Text("", size=12.5, color=MUTED)
    tour_next_txt = ft.Text("Next", size=12, weight=ft.FontWeight.W_700, color=BG_TOP)

    STEPS = [
        dict(title="Navigation", t_left=330, t_top=150,
             sx=330, sy=212, c1x=300, c1y=150, c2x=272, c2y=214, tx=246, ty=212,
             body="Switch between Dashboard, AI Analytics, Settings and Help from this sidebar."),
        dict(title="CPU & Memory", t_left=470, t_top=250,
             sx=500, sy=250, c1x=460, c1y=215, c2x=420, c2y=200, tx=398, ty=170,
             body="Live system load from the optimizer service. Green is healthy, red means pressure."),
        dict(title="Quick stats", t_left=600, t_top=250,
             sx=770, sy=250, c1x=860, c1y=215, c2x=935, c2y=190, tx=950, ty=150,
             body="Swap, temperature, disk I/O and live network speed — all at a glance."),
        dict(title="CPU activity", t_left=470, t_top=500,
             sx=640, sy=500, c1x=710, c1y=470, c2x=755, c2y=455, tx=765, ty=435,
             body="A rolling history of how hard your CPU is working."),
        dict(title="Boost & processes", t_left=430, t_top=300,
             sx=560, sy=360, c1x=620, c1y=440, c2x=680, c2y=485, tx=700, ty=508,
             body="Top memory hogs. Use Boost to free memory now, Undo to resume. You're all set!"),
    ]
    tour_idx = {"i": 0}

    def arrow_shapes(s):
        stroke = ft.Paint(color=ACCENT, stroke_width=2.5, style=ft.PaintingStyle.STROKE,
                          stroke_dash_pattern=[9, 7], stroke_cap=ft.StrokeCap.ROUND)
        line = cv.Path([cv.Path.MoveTo(s["sx"], s["sy"]),
                        cv.Path.CubicTo(s["c1x"], s["c1y"], s["c2x"], s["c2y"], s["tx"], s["ty"])],
                       paint=stroke)
        ang = math.atan2(s["ty"] - s["c2y"], s["tx"] - s["c2x"])
        L, W = 17, 8.5
        bx, by = s["tx"] - L * math.cos(ang), s["ty"] - L * math.sin(ang)
        perp = ang + math.pi / 2
        p1 = (bx + W * math.cos(perp), by + W * math.sin(perp))
        p2 = (bx - W * math.cos(perp), by - W * math.sin(perp))
        head = cv.Path([cv.Path.MoveTo(s["tx"], s["ty"]), cv.Path.LineTo(*p1),
                        cv.Path.LineTo(*p2), cv.Path.Close()],
                       paint=ft.Paint(color=ACCENT, style=ft.PaintingStyle.FILL))
        return [line, head]

    def tour_apply():
        s = STEPS[tour_idx["i"]]
        tour_canvas.shapes = arrow_shapes(s)
        tour_card.left, tour_card.top = s["t_left"], s["t_top"]
        tour_title.value = s["title"]
        tour_body.value = s["body"]
        tour_step_lbl.value = f"STEP {tour_idx['i'] + 1} / {len(STEPS)}"
        tour_next_txt.value = "Done" if tour_idx["i"] == len(STEPS) - 1 else "Next"

    def tour_advance(_e=None):
        if tour_idx["i"] >= len(STEPS) - 1:
            tour_finish()
            return
        tour_idx["i"] += 1
        tour_apply()
        page.update()

    def tour_finish(_e=None):
        overlay.visible = False
        try:
            with open(TOUR_FLAG, "w") as f:
                f.write("1")
        except Exception:
            pass
        page.update()

    def start_tour(_e=None):
        tour_idx["i"] = 0
        tour_apply()
        overlay.visible = True
        page.update()

    tour_card = ft.Container(
        content=glass(ft.Column([
            tour_step_lbl, ft.Container(height=4), tour_title, ft.Container(height=6), tour_body,
            ft.Container(height=16),
            ft.Row([
                ft.Container(content=ft.Text("Skip tour", size=12, color=MUTED),
                             padding=ft.Padding(10, 9, 10, 9), on_click=tour_finish),
                ft.Container(expand=True),
                ft.Container(content=tour_next_txt, padding=ft.Padding(22, 9, 22, 9),
                             border_radius=12, bgcolor=ACCENT, on_click=tour_advance),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=0), glow=ACCENT, width=320),
        left=300, top=176, width=320,
        animate_position=ft.Animation(320, ft.AnimationCurve.EASE_OUT))

    tour_skip_pill = ft.Container(
        content=ft.Row([ft.Text("Skip tour", size=12.5, color=TEXT, weight=ft.FontWeight.W_700),
                        ft.Icon(ft.Icons.CLOSE_ROUNDED, size=16, color=TEXT)], spacing=8, tight=True),
        padding=ft.Padding(16, 10, 16, 10), border_radius=14, right=30, top=26,
        bgcolor=ft.Colors.with_opacity(0.12, GLASS),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.20, GLASS)),
        on_click=tour_finish)

    overlay = ft.Stack([
        ft.Container(expand=True, bgcolor=ft.Colors.with_opacity(0.74, "#05080B"), on_click=tour_advance),
        ft.Container(content=tour_canvas, expand=True),
        tour_card, tour_skip_pill,
    ], expand=True, visible=False)

    # ── VIEW: Dashboard ──────────────────────────────────────────────────────
    def view_dashboard():
        return ft.Column([
            ft.Row([
                gauge(cpu_ring, cpu_val, "CPU LOAD", ACCENT),
                gauge(mem_ring, mem_val, "MEMORY", ACCENT_2),
                ft.Column([
                    ft.Row([mini(ft.Icons.SWAP_HORIZ_ROUNDED, swap_v, "SWAP", VIOLET),
                            mini(ft.Icons.THERMOSTAT_ROUNDED, temp_v, "TEMP °C", WARN)], spacing=16),
                    ft.Row([mini(ft.Icons.UPLOAD_ROUNDED, net_up, "NET ↑ MB/s", ACCENT),
                            mini(ft.Icons.DOWNLOAD_ROUNDED, net_dn, "NET ↓ MB/s", ACCENT_2)], spacing=16),
                ], spacing=16, expand=True),
            ], spacing=16),
            ft.Container(height=16),
            ft.Row([
                ft.Column([mini(ft.Icons.STORAGE_ROUNDED, disk_v, "DISK I/O MB/s", ACCENT_2),
                           mini(ft.Icons.LAYERS_ROUNDED, proc_n, "PROCESSES", VIOLET)],
                          spacing=16, width=250),
                glass(ft.Column([
                    ft.Row([card_title(ft.Icons.SHOW_CHART, "CPU ACTIVITY", ACCENT),
                            ft.Container(expand=True), lbl("live", size=11)]),
                    ft.Container(height=10), ft.Container(content=spark, expand=True)],
                    spacing=0), glow=ACCENT, expand=True, height=190),
            ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.START),
            ft.Container(height=16),
            glass(ft.Column([
                ft.Row([card_title(ft.Icons.MEMORY_ROUNDED, "TOP PROCESSES BY MEMORY", VIOLET),
                        ft.Container(expand=True), susp_txt, boost_pill, undo_pill],
                       spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=12), proc_list], spacing=0, expand=True),
                  glow=VIOLET, expand=True),
        ], spacing=0, expand=True)

    # ── VIEW: AI Analytics ───────────────────────────────────────────────────
    def view_analytics():
        return ft.Column([
            ft.Row([
                gauge(risk_ring, risk_val, "BOTTLENECK CONFIDENCE", VIOLET),
                glass(ft.Column([
                    card_title(ft.Icons.INSIGHTS, "PREDICTION", VIOLET),
                    ft.Container(height=10), risk_desc,
                    ft.Container(height=12), an_pred,
                    ft.Container(height=6), an_thresh,
                    ft.Container(height=14),
                    lbl("Live output from the on-device model running in the background service.",
                        size=11, color=MUTED),
                ], spacing=0, expand=True), glow=VIOLET, expand=True, height=210),
            ], spacing=16),
            ft.Container(height=16),
            glass(ft.Column([
                card_title(ft.Icons.AUTO_GRAPH, "FEATURE ATTRIBUTION", ACCENT),
                ft.Container(height=16),
                attr_row("CPU", ACCENT), ft.Container(height=14),
                attr_row("Memory", ACCENT_2), ft.Container(height=14),
                attr_row("Temp", WARN), ft.Container(height=14),
                attr_row("Swap", VIOLET),
                ft.Container(height=18),
                lbl("How much each signal contributes to the model's current prediction.",
                    size=11, color=MUTED),
            ], spacing=0), glow=ACCENT, expand=True),
        ], spacing=0, expand=True)

    # ── VIEW: Settings ───────────────────────────────────────────────────────
    def setting_row(icon, title, desc, control, glow):
        return ft.Row([
            ft.Container(content=ft.Icon(icon, size=20, color=glow), width=42, height=42,
                         border_radius=12, alignment=ft.Alignment.CENTER,
                         bgcolor=ft.Colors.with_opacity(0.08, glow)),
            ft.Column([ft.Text(title, size=14, color=TEXT, weight=ft.FontWeight.W_600),
                       ft.Text(desc, size=11, color=MUTED)], spacing=2, expand=True),
            control,
        ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def view_settings():
        build_profile_pills()
        update_threshold_text()
        refresh_whitelist()
        return ft.Column([
            glass(ft.Column([
                card_title(ft.Icons.POWER_SETTINGS_NEW_ROUNDED, "BACKGROUND SERVICE", ACCENT),
                ft.Container(height=16),
                setting_row(ft.Icons.POWER_ROUNDED, "Run Background Service",
                            "Turn off to completely stop (kill) the service; turn on to start it again",
                            service_switch, ACCENT),
            ], spacing=0), glow=ACCENT),
            ft.Container(height=16),
            glass(ft.Column([
                card_title(ft.Icons.TUNE_ROUNDED, "ENGINE", ACCENT),
                ft.Container(height=16),
                setting_row(ft.Icons.BOLT_ROUNDED, "Background Optimizer Engine",
                            "Monitor and mitigate bottlenecks", opt_switch, ACCENT),
                ft.Divider(color=ft.Colors.with_opacity(0.06, GLASS), height=24),
                setting_row(ft.Icons.AUTO_MODE, "Auto-Pilot",
                            "Act automatically when risk is high", auto_switch, ACCENT_2),
                ft.Divider(color=ft.Colors.with_opacity(0.06, GLASS), height=24),
                setting_row(ft.Icons.NOTIFICATIONS_ACTIVE_ROUNDED, "Native Notifications",
                            "OS banners on optimizer actions", notify_switch, VIOLET),
            ], spacing=0), glow=ACCENT),
            ft.Container(height=16),
            glass(ft.Column([
                card_title(ft.Icons.SPEED_ROUNDED, "PERFORMANCE PROFILE", ACCENT_2),
                ft.Container(height=14), profile_pills_row,
                ft.Container(height=18), set_thresh_txt,
            ], spacing=0), glow=ACCENT_2),
            ft.Container(height=16),
            glass(ft.Column([
                card_title(ft.Icons.SHIELD_ROUNDED, "PROTECTED PROCESSES", VIOLET),
                ft.Container(height=6),
                lbl("These apps are never suspended by the optimizer.", size=11, color=MUTED),
                ft.Container(height=12),
                ft.Row([wl_input,
                        ft.Container(content=ft.Text("Add", size=12, color=BG_TOP, weight=ft.FontWeight.W_700),
                                     padding=ft.Padding(18, 12, 18, 12), border_radius=12,
                                     bgcolor=ACCENT, on_click=wl_add)],
                       spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=14), wl_list,
            ], spacing=0), glow=VIOLET),
        ], spacing=0, scroll=ft.ScrollMode.HIDDEN, expand=True)

    # ── VIEW: Help ───────────────────────────────────────────────────────────
    def help_card(icon, title, body, glow):
        return glass(ft.Column([card_title(icon, title, glow), ft.Container(height=8),
                                ft.Text(body, size=13, color=MUTED)], spacing=0),
                     glow=glow, glow_strength=0.12)

    def view_help():
        return ft.Column([
            ft.Row([ft.Container(expand=True),
                    ft.Container(content=ft.Row([ft.Icon(ft.Icons.REPLAY_ROUNDED, size=16, color=ACCENT),
                                                 ft.Text("Replay tour", size=12, color=ACCENT,
                                                         weight=ft.FontWeight.W_600)], spacing=8),
                                 padding=ft.Padding(14, 9, 14, 9), border_radius=12, on_click=start_tour,
                                 bgcolor=ft.Colors.with_opacity(0.06, GLASS),
                                 border=ft.Border.all(1, ft.Colors.with_opacity(0.12, ACCENT)))]),
            ft.Container(height=14),
            help_card(ft.Icons.SPEED, "What is SRO?",
                      "System Resource Optimizer watches your CPU, memory, swap and I/O in real time "
                      "and gently suspends low-priority background processes when it predicts a "
                      "bottleneck — keeping your active apps responsive.", ACCENT),
            ft.Container(height=14),
            help_card(ft.Icons.DONUT_LARGE, "Reading the gauges",
                      "The rings show live CPU and Memory load. Green is healthy, amber is busy, "
                      "red means pressure. The sparkline tracks recent CPU activity.", ACCENT_2),
            ft.Container(height=14),
            help_card(ft.Icons.INSIGHTS, "AI Analytics",
                      "The Analytics tab shows the background model's bottleneck confidence and which "
                      "signal (CPU, memory, temp, swap) is driving the prediction.", VIOLET),
            ft.Container(height=14),
            help_card(ft.Icons.SHIELD_ROUNDED, "What's protected",
                      "Your foreground app, developer tools, browsers and the optimizer itself are "
                      "never suspended. Add your own apps under Settings → Protected Processes.", WARN),
        ], spacing=0, scroll=ft.ScrollMode.HIDDEN, expand=True)

    views = {"dashboard": view_dashboard(), "analytics": view_analytics(),
             "settings": view_settings(), "help": view_help()}

    # ── sidebar / nav ────────────────────────────────────────────────────────
    nav_items = {}

    def make_nav(name, icon, key):
        ic = ft.Icon(icon, size=20, color=MUTED)
        tx = ft.Text(name, size=13, weight=ft.FontWeight.W_600, color=MUTED)
        cont = ft.Container(content=ft.Row([ic, tx], spacing=14,
                                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            padding=ft.Padding(14, 11, 14, 11), border_radius=14,
                            on_click=lambda e: set_view(key))
        nav_items[key] = (cont, ic, tx)
        return cont

    def restyle_nav():
        for k, (cont, ic, tx) in nav_items.items():
            active = (k == state["view"])
            cont.bgcolor = ft.Colors.with_opacity(0.10, GLASS) if active else None
            cont.border = ft.Border.all(1, ft.Colors.with_opacity(0.10, GLASS)) if active else None
            ic.color = ACCENT if active else MUTED
            tx.color = TEXT if active else MUTED

    def set_view(key):
        state["view"] = key
        body.content = views[key]
        restyle_nav()
        page.update()

    def refresh_status():
        if state["service_stopped"]:
            status_dot.bgcolor = ft.Colors.with_opacity(0.5, CRIT)
            status_txt.value = "SERVICE STOPPED"
            status_txt.color = MUTED
            return
        if not client.connected:
            status_dot.bgcolor = ft.Colors.with_opacity(0.6, WARN)
            status_txt.value = "CONNECTING…"
            status_txt.color = WARN
            return
        on = state["optimizer"]
        status_dot.bgcolor = ACCENT if on else ft.Colors.with_opacity(0.5, MUTED)
        status_txt.value = "OPTIMIZER ACTIVE" if on else "OPTIMIZER PAUSED"
        status_txt.color = TEXT if on else MUTED

    sidebar = glass(ft.Column([
        ft.Row([ft.Container(content=ft.Icon(ft.Icons.BOLT_ROUNDED, color=BG_TOP, size=22),
                             width=42, height=42, border_radius=13, alignment=ft.Alignment.CENTER,
                             gradient=ft.LinearGradient(begin=ft.Alignment.TOP_LEFT,
                                                        end=ft.Alignment.BOTTOM_RIGHT,
                                                        colors=[ACCENT, ACCENT_2])),
                ft.Column([ft.Text("SRO", size=16, weight=ft.FontWeight.BOLD, color=TEXT),
                           ft.Text("Resource Optimizer", size=10, color=MUTED)], spacing=0)],
               spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=26),
        make_nav("Dashboard", ft.Icons.DASHBOARD_ROUNDED, "dashboard"),
        ft.Container(height=6),
        make_nav("AI Analytics", ft.Icons.INSIGHTS, "analytics"),
        ft.Container(height=6),
        make_nav("Settings", ft.Icons.SETTINGS_ROUNDED, "settings"),
        ft.Container(height=6),
        make_nav("Help", ft.Icons.HELP_ROUNDED, "help"),
        ft.Container(expand=True),
        ft.Row([status_dot, status_txt], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=8),
        ft.Row([clock, ft.Container(expand=True), ft.Text(str(VERSION), size=10, color=MUTED)]),
    ], spacing=0, expand=True), width=232, glow=ACCENT, glow_strength=0.10, radius=24)

    body = ft.Container(content=views["dashboard"], expand=True, padding=ft.Padding(8, 0, 0, 0))
    restyle_nav()

    page.add(ft.Stack([
        ft.Container(expand=True, gradient=ft.LinearGradient(
            begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER, colors=[BG_TOP, BG_BOT])),
        blob(ACCENT, 520, -160, -180), blob(ACCENT_2, 460, 880, -120), blob(VIOLET, 420, 420, 560),
        ft.Container(content=ft.Row([sidebar, body], spacing=18, expand=True),
                     padding=ft.Padding(22, 22, 22, 22), expand=True),
        toast,
        overlay,
    ], expand=True))

    tour_apply()
    overlay.visible = not os.path.exists(TOUR_FLAG)

    def bar(v):
        h = max(4, (v / 100.0) * 82)
        c = pct_color(v)
        return ft.Container(width=12, height=h, border_radius=6,
                            gradient=ft.LinearGradient(begin=ft.Alignment.BOTTOM_CENTER,
                                                       end=ft.Alignment.TOP_CENTER,
                                                       colors=[ft.Colors.with_opacity(0.35, c), c]))

    def proc_row(name, m):
        return ft.Row([
            ft.Container(width=8, height=8, border_radius=8, bgcolor=pct_color(m * 6)),
            ft.Text(name[:34], size=13, color=TEXT, weight=ft.FontWeight.W_500, expand=True, no_wrap=True),
            ft.Container(content=ft.Container(width=max(6, min(120, m * 6)), height=6, border_radius=6,
                                              bgcolor=ACCENT),
                         width=120, height=6, border_radius=6,
                         bgcolor=ft.Colors.with_opacity(0.08, GLASS), alignment=ft.Alignment.CENTER_LEFT),
            ft.Text(f"{m:.1f}%", size=12, color=MUTED, width=52, text_align=ft.TextAlign.RIGHT),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # ── background service auto-launch (ported) ──────────────────────────────
    def launch_background_service():
        import subprocess
        try:
            tmp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tmp.settimeout(0.5)
            tmp.connect(("127.0.0.1", IPC_PORT))
            tmp.close()
            return  # already running
        except Exception:
            pass
        try:
            if getattr(sys, "frozen", False):
                base_dir = os.path.dirname(sys.executable)
                if platform.system() == "Windows":
                    srv = os.path.join(base_dir, "SystemResourceOptimizerService.exe")
                    if os.path.exists(srv):
                        subprocess.Popen([srv], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                else:
                    srv = os.path.join(base_dir, "SystemResourceOptimizerService")
                    if os.path.exists(srv):
                        subprocess.Popen([srv], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         start_new_session=True)
            else:
                script = os.path.join(_DIR, "optimizer_service.py")
                if os.path.exists(script):
                    if sys.platform == "win32":
                        pyw = sys.executable.replace("python.exe", "pythonw.exe")
                        subprocess.Popen([pyw, script],
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | 0x00000008)
                    else:
                        subprocess.Popen([sys.executable, script], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
        except Exception as e:
            log.warning("Failed to launch background service: %s", e)

    # ── apply a service snapshot to the UI ───────────────────────────────────
    def apply_update(resp):
        for n in resp.get("pending_notifications", []):
            notify(n.get("title", ""), n.get("message", ""))

        state["optimizer"] = resp.get("optimizer_active", True)
        state["autopilot"] = resp.get("autopilot_enabled", True)
        state["profile"] = resp.get("active_profile", state["profile"])
        opt_switch.value = state["optimizer"]
        auto_switch.value = state["autopilot"]
        refresh_status()
        update_threshold_text()

        latest = resp.get("latest_result")
        if latest:
            f = latest.get("features", {}) or {}
            cpu = f.get("cpu_percent_raw", f.get("cpu_percent", 0)) or 0
            mem = f.get("mem_percent_raw", f.get("mem_percent", 0)) or 0
            swap = f.get("swap_percent", 0) or 0
            temp = f.get("cpu_temp_c", 0) or 0
            up = f.get("net_sent_mbps", 0) or 0
            dn = f.get("net_recv_mbps", 0) or 0
            dio = (f.get("disk_read_mbps", 0) or 0) + (f.get("disk_write_mbps", 0) or 0)
            pcount = f.get("process_count", 0) or 0

            cpu_ring.value, cpu_ring.color, cpu_val.value = cpu / 100, pct_color(cpu), f"{cpu:.0f}"
            mem_ring.value, mem_ring.color, mem_val.value = mem / 100, pct_color(mem), f"{mem:.0f}"
            swap_v.value = f"{swap:.0f}%"
            if temp > 0:
                temp_v.value, temp_v.color = f"{temp:.0f}", pct_color(temp)
            else:
                temp_v.value, temp_v.color = "—", MUTED
            net_up.value, net_dn.value = f"{up:.1f}", f"{dn:.1f}"
            disk_v.value = f"{dio:.1f}"
            proc_n.value = f"{pcount}"
            hist.append(cpu)
            spark.controls = [bar(v) for v in hist]

            if latest.get("calibrating"):
                elapsed, total = resp.get("calib_progress", (0, CALIBRATION_SECONDS))
                cpct = min(100, max(1, int((elapsed / total) * 100))) if total else 0
                risk_ring.value, risk_ring.color = (elapsed / total if total else 0), WARN
                risk_val.value = f"{cpct}"
                risk_desc.value = f"Calibrating telemetry… {cpct}%"
                an_pred.value = "Predicted CPU ≈ —"
            else:
                conf = latest.get("confidence", 0.0) or 0
                cpct = int(conf * 100)
                risk_ring.value, risk_ring.color = conf, pct_color(cpct)
                risk_val.value = f"{cpct}"
                risk_desc.value = ("High risk — bottleneck likely imminent." if cpct >= 80
                                   else "Elevated risk — optimizer may act soon." if cpct >= 55
                                   else "Low risk — system has headroom.")
                an_pred.value = f"Predicted CPU ≈ {latest.get('predicted_cpu', 0.0):.0f}%"

            attrs = latest.get("attributions")
            if attrs and len(attrs) >= 4:
                for i, nm in enumerate(("CPU", "Memory", "Temp", "Swap")):
                    fill, val = attr[nm]
                    w = max(0.0, min(1.0, attrs[i] or 0))
                    fill.width = w * 360
                    val.value = f"{int(w * 100)}%"

        susp = resp.get("suspended_processes", [])
        undo_state["enabled"] = len(susp) > 0
        undo_pill.opacity = 1.0 if undo_state["enabled"] else 0.45
        susp_txt.value = f"{len(susp)} suspended"

        top = resp.get("top_processes", [])
        proc_list.controls = [proc_row(p.get("name", "—"), p.get("memory_percent", 0) or 0)
                              for p in top[:8]]
        clock.value = time.strftime("%H:%M:%S")

    # ── poll loop ────────────────────────────────────────────────────────────
    async def poll_service_worker():
        started = False
        while True:
            if getattr(page, "user_shutdown_requested", False):
                break
            try:
                if state["service_stopped"]:
                    # User killed the service — reflect it and do NOT relaunch.
                    service_switch.value = False
                    started = False
                    refresh_status()
                    try:
                        page.update()
                    except Exception:
                        pass
                    await asyncio.sleep(1.0)
                    continue
                if not client.connected:
                    refresh_status()
                    if not started:
                        launch_background_service()
                        started = True
                        await asyncio.sleep(1.0)
                    if not client.connect():
                        try:
                            page.update()
                        except Exception:
                            pass
                        await asyncio.sleep(1.5)
                        continue
                    started = False
                resp = await asyncio.to_thread(client.send_request, {"type": "get_update"})
                if resp.get("connected"):
                    service_switch.value = True
                    if not state["announced"]:
                        state["announced"] = True
                        show_toast("Background service initialized", ft.Icons.CHECK_CIRCLE_ROUNDED, ACCENT)
                    apply_update(resp)
                else:
                    client.connected = False
                    refresh_status()
                try:
                    page.update()
                except Exception:
                    pass
            except Exception as err:
                log.warning("poll loop error: %s", err)
            await asyncio.sleep(1.0)

    # ── window close: never kill the service; force-exit this process ────────
    def on_window_event(e):
        evt = getattr(getattr(e, "type", None), "value", None) or getattr(e, "data", None)
        if evt in ("close", "destroy", "quit", "exit", "window_close"):
            if getattr(page, "_closing_in_progress", False):
                return
            page._closing_in_progress = True
            page.user_shutdown_requested = True
            try:
                client.close()
            except Exception:
                pass
            try:
                page.window.prevent_close = False
                page.window.destroy()
            except Exception:
                pass

            def finalize_exit():
                try:
                    notifier.send_sync(
                        title="⚡ SRO Dashboard Closed",
                        message="Optimizer service is still running in the background to keep your system fast.",
                        timeout=1)
                except Exception:
                    pass
                time.sleep(0.15)
                os._exit(0)

            threading.Thread(target=finalize_exit, daemon=True).start()

    page.window.on_event = on_window_event
    page.window.prevent_close = True

    page.run_task(poll_service_worker)


# ── Windows taskbar icon patch (ported verbatim; win32-only, lazy psutil) ─────
def _set_windows_taskbar_icon_async() -> None:
    def worker():
        import time as _t
        import os as _o
        import ctypes
        from ctypes import wintypes
        import psutil

        _t.sleep(0.5)
        for _ in range(40):
            try:
                cur = psutil.Process(_o.getpid())
                child_pids = {p.pid for p in cur.children(recursive=True)}
            except Exception:
                child_pids = set()
            child_pids.add(_o.getpid())
            hwnd_found = [None]
            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def enum_callback(hwnd, lparam):
                if ctypes.windll.user32.IsWindowVisible(hwnd):
                    cn = ctypes.create_unicode_buffer(256)
                    ctypes.windll.user32.GetClassNameW(hwnd, cn, 256)
                    if cn.value == "FLUTTER_RUNNER_WIN32_WINDOW":
                        pid = wintypes.DWORD()
                        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                        if pid.value in child_pids:
                            hwnd_found[0] = hwnd
                            return False
                return True

            ctypes.windll.user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
            if hwnd_found[0] is not None:
                hwnd = hwnd_found[0]
                try:
                    class GUID(ctypes.Structure):
                        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                                    ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

                        def __init__(self, l, w1, w2, b1, b2, b3, b4, b5, b6, b7, b8):
                            self.Data1, self.Data2, self.Data3 = l, w1, w2
                            self.Data4 = (ctypes.c_ubyte * 8)(b1, b2, b3, b4, b5, b6, b7, b8)

                    IID = GUID(0x886d8eeb, 0x8cf2, 0x4446, 0x8d, 0x02, 0xcd, 0xba, 0x1d, 0xbd, 0xcf, 0x99)
                    shell32 = ctypes.windll.shell32
                    ps = ctypes.c_void_p()
                    hr = shell32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(IID), ctypes.byref(ps))
                    if hr >= 0 and ps.value:
                        class PROPERTYKEY(ctypes.Structure):
                            _fields_ = [("fmtid", GUID), ("pid", ctypes.c_ulong)]

                        PKEY = PROPERTYKEY(GUID(0x9F4C2855, 0x0379, 0x4D01, 0x87, 0xE5, 0x45, 0xD6,
                                                0xD7, 0x42, 0x46, 0x94), 5)

                        class PROPVARIANT(ctypes.Structure):
                            _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort),
                                        ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                                        ("pwszVal", ctypes.c_wchar_p), ("pad", ctypes.c_ubyte * 8)]

                        pv = PROPVARIANT()
                        pv.vt = 31
                        pv.pwszVal = "addo561.sro.systemresourceoptimizer.v2"
                        vtp = ctypes.cast(ps, ctypes.POINTER(ctypes.c_void_p))
                        vt = ctypes.cast(vtp[0], ctypes.POINTER(ctypes.c_void_p))
                        SetValue = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                                      ctypes.POINTER(PROPERTYKEY),
                                                      ctypes.POINTER(PROPVARIANT))(vt[6])
                        Commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vt[7])
                        Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])
                        hs = SetValue(ps, ctypes.byref(PKEY), ctypes.byref(pv))
                        hc = Commit(ps)
                        Release(ps)
                        if hs >= 0 and hc >= 0:
                            break
                except Exception:
                    pass
            _t.sleep(0.5)

    threading.Thread(target=worker, daemon=True).start()


def _patch_flet_app_macos() -> None:
    """Make our SRO icon win the macOS Dock slot.

    Flet renders the window via a separate bundled "Flet.app" (a Flutter
    runner). By default that app shows the generic Flet logo in the Dock. We:
      • when FROZEN  → set LSUIElement=True so the Flet renderer is hidden from
        the Dock entirely, leaving only our real .app bundle (which carries
        icon.icns from the PyInstaller spec) visible.
      • when SOURCE  → keep it visible (LSUIElement=False) but replace its
        AppIcon.icns with ours, so `python dashboard.py` still shows our icon.
    Also rename it to "System Resource Optimizer" in the title bar.
    """
    import glob
    import plistlib
    import shutil

    try:
        import flet_desktop
        flet_desktop.ensure_client_cached()
    except Exception as e:
        print(f"[icon-patch] ensure_client_cached failed: {e}", flush=True)

    matches = sorted(glob.glob(os.path.expanduser(
        "~/.flet/client/flet-desktop-*/Flet.app/Contents")), reverse=True)
    if not matches:
        return
    contents = matches[0]
    plist_path = os.path.join(contents, "Info.plist")
    res_dir = os.path.join(contents, "Resources")
    try:
        with open(plist_path, "rb") as fh:
            plist = plistlib.load(fh)
        target_lsui = bool(getattr(sys, "frozen", False))
        changed = False
        if plist.get("LSUIElement") != target_lsui:
            plist["LSUIElement"] = target_lsui
            changed = True
        for key in ("CFBundleName", "CFBundleDisplayName"):
            if plist.get(key) != "System Resource Optimizer":
                plist[key] = "System Resource Optimizer"
                changed = True
        if changed:
            with open(plist_path, "wb") as fh:
                plistlib.dump(plist, fh)
            os.utime(os.path.dirname(contents), None)  # nudge LaunchServices
    except Exception as e:
        print(f"[icon-patch] plist skipped: {e}", flush=True)
    try:
        our_icns = os.path.join(BASE_DIR, "assets", "icon.icns")
        if os.path.exists(our_icns):
            for name in ("AppIcon.icns", "AppIcon"):
                shutil.copy2(our_icns, os.path.join(res_dir, name))
    except Exception as e:
        print(f"[icon-patch] icon skipped: {e}", flush=True)


if __name__ == "__main__":
    try:
        if sys.platform == "darwin":
            try:
                _patch_flet_app_macos()
            except Exception as e:
                print(f"[icon-patch] macOS patch failed: {e}", flush=True)
        if sys.platform == "win32":
            import ctypes as _ct
            try:
                _ct.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "addo561.sro.systemresourceoptimizer.v2")
            except Exception:
                pass
            try:
                _set_windows_taskbar_icon_async()
            except Exception:
                pass
        _assets = os.path.join(BASE_DIR, "assets")
        ft.run(main, assets_dir=_assets)
    except Exception as e:
        print(f"❌ Flet dashboard runtime error: {e}", flush=True)
        sys.exit(1)
