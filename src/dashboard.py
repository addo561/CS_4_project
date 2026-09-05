#!/usr/bin/env python3
"""
dashboard.py — desktop UI for the System Resource Optimizer.

A thin client: it connects to the background service over IPC (line-delimited
JSON on 127.0.0.1:5050), renders live telemetry, the model's forecast and its
attributions, and sends commands. It starts the service itself when it isn't
running and reconnects automatically if the connection drops.
"""
import flet as ft
import asyncio
import threading
import subprocess
import platform
import socket
import json
import time
import sys
import os

try:
    from config import VERSION, IPC_PORT as _CFG_PORT
except Exception:
    VERSION, _CFG_PORT = "v4.1.0", 5050
try:
    from core.notifier import Notifier
    notifier = Notifier()
except Exception:
    notifier = None

_DIR = os.path.dirname(os.path.abspath(__file__))

IPC_HOST, IPC_PORT = "127.0.0.1", _CFG_PORT

# ── themes ──────────────────────────────────────────────────────────────────
DARK = {
    "bg": "#0B1220", "bg2": "#0C1A30", "card": "#16233F", "panel": "#111F38",
    "text": "#FFFFFF", "text2": "#CADCFC", "muted": "#7C8AA5", "line": "#CADCFC",
    "cyan": "#2AC6D9", "mint": "#3DDC97", "amber": "#F4A259",
    "red": "#F96167", "violet": "#9B8CFF", "teal": "#4FA8C7",
    "line_op": 0.10, "track_op": 0.09, "soft_op": 0.06,
}
LIGHT = {
    "bg": "#F5F8FC", "bg2": "#E9F0F9", "card": "#FFFFFF", "panel": "#EEF3FA",
    "text": "#0B1220", "text2": "#22314B", "muted": "#5F6E85", "line": "#3B5878",
    "cyan": "#0E7C8C", "mint": "#17875A", "amber": "#A85E18",
    "red": "#B4302A", "violet": "#5A49BE", "teal": "#155E75",
    "line_op": 0.20, "track_op": 0.12, "soft_op": 0.08,
}

st = {"light": False, "running": False, "live": False, "want_live": True,
      "autopilot": True, "profile": "Balanced", "thr": 80,
      "prevented": 0, "protected": 0, "status_token": "mint",
      "log_seen": set(), "last_action": ""}
T = dict(DARK)

REG = []
def reg(ctrl, attr, resolver):
    REG.append((ctrl, attr, resolver)); return ctrl


# ── IPC client ──────────────────────────────────────────────────────────────
class Backend:
    """Line-delimited JSON over the service's TCP loopback socket."""
    def __init__(self, host=IPC_HOST, port=IPC_PORT):
        self.host, self.port = host, port
        self.sock = None
        self._buf = b""
        self._lock = threading.Lock()

    def connect(self, timeout=2.0):
        s = socket.create_connection((self.host, self.port), timeout=timeout)
        s.settimeout(6.0)
        with self._lock:
            self.sock, self._buf = s, b""
        return True

    def request(self, obj):
        with self._lock:
            if not self.sock:
                raise ConnectionError("not connected")
            self.sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))
            while b"\n" not in self._buf:
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise ConnectionError("service closed the connection")
                self._buf += chunk
            line, self._buf = self._buf.split(b"\n", 1)
            return json.loads(line.decode("utf-8"))

    def close(self):
        with self._lock:
            try:
                if self.sock: self.sock.close()
            except Exception:
                pass
            self.sock, self._buf = None, b""


backend = Backend()


def main(page: ft.Page):
    page.title = "System Resource Optimizer"
    page.padding = 0
    page.window.width = 1280
    page.window.height = 820
    page.window.min_width = 1040
    page.window.min_height = 660
    page.scroll = ft.ScrollMode.AUTO

    def upd():
        try: page.update()
        except Exception: pass

    # ── building blocks ─────────────────────────────────────────────────────
    def card(content, pad=16, radius=16):
        c = ft.Container(content=content, padding=pad, border_radius=radius, bgcolor=T["card"],
                         border=ft.Border.all(1, ft.Colors.with_opacity(T["line_op"], T["line"])))
        reg(c, "bgcolor", lambda t: t["card"])
        reg(c, "border", lambda t: ft.Border.all(1, ft.Colors.with_opacity(t["line_op"], t["line"])))
        return c

    def txt(value, size=12, token="text2", weight=None):
        c = ft.Text(value, size=size, color=T[token], weight=weight)
        reg(c, "color", lambda t, k=token: t[k]); return c

    def icon(name, token="cyan", size=18):
        c = ft.Icon(name, color=T[token], size=size)
        reg(c, "color", lambda t, k=token: t[k]); return c

    def title_row(icon_name, label, token="cyan"):
        return ft.Row([icon(icon_name, token, 17),
                       txt(label, 12.5, "text2", ft.FontWeight.W_600)], spacing=8)

    # ── top bar ─────────────────────────────────────────────────────────────
    mode_txt = ft.Text("OFFLINE", size=10.5, color=T["muted"], weight=ft.FontWeight.W_700)
    reg(mode_txt, "color", lambda t: t["mint"] if st["live"] else t["muted"])
    mode_chip = ft.Container(content=mode_txt, padding=ft.Padding(10, 6, 10, 6), border_radius=8,
                             bgcolor=ft.Colors.with_opacity(T["soft_op"], T["line"]))
    reg(mode_chip, "bgcolor", lambda t: (ft.Colors.with_opacity(0.16, t["mint"]) if st["live"]
                                         else ft.Colors.with_opacity(t["soft_op"], t["line"])))

    status_dot = reg(ft.Container(width=9, height=9, border_radius=9, bgcolor=T["mint"]),
                     "bgcolor", lambda t: t[st["status_token"]])
    status_txt = reg(ft.Text("ALL CLEAR", size=11.5, color=T["mint"], weight=ft.FontWeight.W_700),
                     "color", lambda t: t[st["status_token"]])
    status_pill = ft.Container(content=ft.Row([status_dot, status_txt], spacing=8, tight=True),
                               padding=ft.Padding(13, 8, 13, 8), border_radius=20, bgcolor=T["panel"],
                               border=ft.Border.all(1, ft.Colors.with_opacity(T["line_op"], T["line"])))
    reg(status_pill, "bgcolor", lambda t: t["panel"])
    reg(status_pill, "border", lambda t: ft.Border.all(1, ft.Colors.with_opacity(t["line_op"], t["line"])))

    theme_btn = ft.IconButton(icon=ft.Icons.LIGHT_MODE_ROUNDED, icon_color=T["muted"],
                              icon_size=19, tooltip="Switch to light mode")
    reg(theme_btn, "icon_color", lambda t: t["muted"])

    def styled(btn, token, filled=True, pad=(15, 12)):
        def mk(t):
            if filled:
                return ft.ButtonStyle(bgcolor=t[token], color=t["bg"],
                                      shape=ft.RoundedRectangleBorder(radius=11),
                                      padding=ft.Padding(pad[0], pad[1], pad[0], pad[1]))
            return ft.ButtonStyle(bgcolor=ft.Colors.with_opacity(0.16, t[token]), color=t[token],
                                  shape=ft.RoundedRectangleBorder(radius=11),
                                  padding=ft.Padding(pad[0], pad[1], pad[0], pad[1]))
        btn.style = mk(T); reg(btn, "style", mk); return btn

    conn_btn = styled(ft.FilledButton("  Connect", icon=ft.Icons.SENSORS_ROUNDED), "mint", False)
    tour_btn = styled(ft.FilledButton("  Tour", icon=ft.Icons.TIPS_AND_UPDATES_ROUNDED), "cyan", True)

    topbar = ft.Row([
        ft.Row([icon(ft.Icons.BOLT_ROUNDED, "cyan", 25),
                ft.Column([txt("System Resource Optimizer", 17, "text", ft.FontWeight.BOLD),
                           txt(f"Predictive bottleneck forecasting  ·  {VERSION}", 10.5, "muted")], spacing=1)],
               spacing=10),
        ft.Row([mode_chip, status_pill, theme_btn, conn_btn, tour_btn], spacing=8),
    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
       vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # ── banner ──────────────────────────────────────────────────────────────
    banner_title = ft.Text("Bottleneck predicted in ~30 seconds", size=13.5,
                           weight=ft.FontWeight.BOLD, color="#0B1220")
    forecast_txt = ft.Text("Forecast confidence —", size=11, color="#20303A")
    banner = ft.Container(
        content=ft.Row([ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color="#0B1220", size=21),
                        ft.Column([banner_title, forecast_txt], spacing=1)], spacing=12),
        padding=ft.Padding(16, 11, 16, 11), border_radius=13, bgcolor=T["amber"],
        visible=False, animate_opacity=ft.Animation(300, ft.AnimationCurve.EASE_OUT))
    reg(banner, "bgcolor", lambda t: t["amber"])

    # ── gauges ──────────────────────────────────────────────────────────────
    def gauge(label, token):
        ring = ft.ProgressRing(value=0.0, width=104, height=104, stroke_width=10, color=T[token],
                               bgcolor=ft.Colors.with_opacity(T["track_op"], T["line"]))
        reg(ring, "bgcolor", lambda t: ft.Colors.with_opacity(t["track_op"], t["line"]))
        val = txt("0", 30, "text", ft.FontWeight.BOLD)
        stack = ft.Stack([ring, ft.Container(
            content=ft.Row([val, txt("%", 12, "muted")], alignment=ft.MainAxisAlignment.CENTER,
                           vertical_alignment=ft.CrossAxisAlignment.END, spacing=1, tight=True),
            width=104, height=104, alignment=ft.Alignment.CENTER)], width=104, height=104)
        c = card(ft.Column([stack, ft.Container(height=5),
                            txt(label, 11.5, "muted", ft.FontWeight.W_600)],
                           horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2))
        return ft.Container(c, expand=True), ring, val

    cpu_card, cpu_ring, cpu_val = gauge("CPU LOAD", "cyan")
    mem_card, mem_ring, mem_val = gauge("MEMORY", "teal")
    risk_card, risk_ring, risk_val = gauge("BOTTLENECK RISK", "amber")
    gauges = ft.Row([cpu_card, mem_card, risk_card], spacing=12)

    # ── environment tiles ───────────────────────────────────────────────────
    def tile(icon_name, label, token):
        v = txt("—", 16, "text", ft.FontWeight.BOLD)
        c = card(ft.Row([icon(icon_name, token, 19),
                         ft.Column([v, txt(label, 10, "muted")], spacing=0)], spacing=9),
                 pad=12, radius=13)
        return ft.Container(c, expand=True), v

    temp_c, temp_v = tile(ft.Icons.THERMOSTAT_ROUNDED, "TEMP °C", "amber")
    swap_c, swap_v = tile(ft.Icons.SWAP_HORIZ_ROUNDED, "SWAP", "teal")
    freq_c, freq_v = tile(ft.Icons.SPEED_ROUNDED, "CPU FREQ MHz", "cyan")
    tiles = ft.Row([temp_c, swap_c, freq_c], spacing=12)

    # ── sparkline ───────────────────────────────────────────────────────────
    N = 48
    hist = [8] * N
    bars = [ft.Container(width=8, height=8, border_radius=3,
                         bgcolor=ft.Colors.with_opacity(0.55, T["cyan"]),
                         animate=ft.Animation(280, ft.AnimationCurve.EASE_OUT),
                         alignment=ft.Alignment.BOTTOM_CENTER) for _ in range(N)]
    spark = ft.Row(bars, alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                   vertical_alignment=ft.CrossAxisAlignment.END, height=112)
    chart_sub = txt("last 48 s", 10.5, "muted")
    chart_card = card(ft.Column([
        ft.Row([title_row(ft.Icons.TIMELINE_ROUNDED, "Live telemetry (CPU)"), chart_sub],
               alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Container(height=10), spark], spacing=2))

    # ── XAI ─────────────────────────────────────────────────────────────────
    def attr(label, token):
        bar = ft.ProgressBar(value=0.0, color=T[token],
                             bgcolor=ft.Colors.with_opacity(T["track_op"], T["line"]),
                             height=9, border_radius=6, expand=True)
        reg(bar, "color", lambda t, k=token: t[k])
        reg(bar, "bgcolor", lambda t: ft.Colors.with_opacity(t["track_op"], t["line"]))
        pv = ft.Text("0%", size=10.5, color=T["muted"], width=34, text_align=ft.TextAlign.RIGHT)
        reg(pv, "color", lambda t: t["muted"])
        lab = txt(label, 11, "text2", ft.FontWeight.W_500); lab.width = 52
        return ft.Row([lab, bar, pv], spacing=10,
                      vertical_alignment=ft.CrossAxisAlignment.CENTER), bar, pv

    a_cpu, b_cpu, p_cpu = attr("CPU", "cyan")
    a_mem, b_mem, p_mem = attr("Memory", "teal")
    a_tmp, b_tmp, p_tmp = attr("Temp", "amber")
    a_swp, b_swp, p_swp = attr("Swap", "violet")
    xai_card = card(ft.Column([
        title_row(ft.Icons.INSIGHTS_ROUNDED, "Why? — Explainable AI attributions"),
        txt("Which signals drove the forecast (occlusion sensitivity)", 10.5, "muted"),
        ft.Container(height=11), a_cpu, ft.Container(height=9), a_mem,
        ft.Container(height=9), a_tmp, ft.Container(height=9), a_swp], spacing=2))

    def set_attr(c, m, tp, s):
        for bar, pv, v in [(b_cpu,p_cpu,c),(b_mem,p_mem,m),(b_tmp,p_tmp,tp),(b_swp,p_swp,s)]:
            bar.value = max(0.0, min(1.0, v)); pv.value = f"{int(max(0.0,min(1.0,v))*100)}%"

    # ── counters ────────────────────────────────────────────────────────────
    def counter(label, token, icon_name):
        v = txt("0", 24, "text", ft.FontWeight.BOLD)
        return ft.Container(ft.Column([
            ft.Row([icon(icon_name, token, 15), txt(label, 10, "muted")], spacing=6), v],
            spacing=2), expand=True), v

    prev_c, prev_v = counter("BOTTLENECKS PREVENTED", "mint", ft.Icons.SHIELD_ROUNDED)
    prot_c, prot_v = counter("PROCESSES HANDLED", "violet", ft.Icons.LOCK_ROUNDED)
    session_card = card(ft.Row([prev_c, prot_c], spacing=10))

    # ── processes ───────────────────────────────────────────────────────────
    proc_list = ft.Column(spacing=7)
    proc_rows = []                      # dicts with row refs + state

    def make_proc_row(name, mem, state="running"):
        dot = ft.Icon(ft.Icons.CIRCLE, color=T["mint"], size=10)
        chip_txt = ft.Text("RUNNING", size=10, color=T["mint"], weight=ft.FontWeight.W_700)
        chip = ft.Container(content=chip_txt, padding=ft.Padding(8, 3, 8, 3), border_radius=9,
                            bgcolor=ft.Colors.with_opacity(0.15, T["mint"]))
        name_t = ft.Text(name, size=12.5, color=T["text"], weight=ft.FontWeight.W_600,
                         no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, expand=True)
        mem_t = ft.Text(mem, size=11, color=T["muted"])
        row = ft.Container(content=ft.Row([
                ft.Row([dot, name_t], spacing=9, expand=True),
                ft.Row([mem_t, chip], spacing=10)],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            padding=ft.Padding(12, 9, 12, 9), border_radius=11, bgcolor=T["panel"],
            animate=ft.Animation(250, ft.AnimationCurve.EASE_OUT))
        p = {"row": row, "dot": dot, "chip": chip, "chip_txt": chip_txt,
             "name_t": name_t, "mem_t": mem_t, "state": state, "name": name}
        paint_proc(p)
        return p

    def paint_proc(p):
        tok = "amber" if p["state"] == "suspended" else "mint"
        p["dot"].color = T[tok]
        p["chip_txt"].value = "SUSPENDED" if p["state"] == "suspended" else "RUNNING"
        p["chip_txt"].color = T[tok]
        p["chip"].bgcolor = ft.Colors.with_opacity(0.15, T[tok])
        p["name_t"].color = T["text"]; p["mem_t"].color = T["muted"]
        p["row"].bgcolor = (ft.Colors.with_opacity(0.12, T["amber"])
                            if p["state"] == "suspended" else T["panel"])

    def set_proc(p, state):
        p["state"] = state; paint_proc(p)

    def set_proc_list(items):
        """items: list of (name, mem_str, state).

        Rebuilds rows only when the set of process names changes; otherwise it
        updates the existing rows in place. Rebuilding every poll made
        page.update() progressively slower and eventually tripped the service's
        15-second read timeout.
        """
        names = [i[0] for i in items]
        if len(proc_rows) == len(items) and [p["name"] for p in proc_rows] == names:
            for p, (name, mem, state) in zip(proc_rows, items):
                if p["mem_t"].value != mem:
                    p["mem_t"].value = mem
                if p["state"] != state:
                    p["state"] = state; paint_proc(p)
            return
        proc_rows.clear(); proc_list.controls.clear()
        for name, mem, state in items:
            p = make_proc_row(name, mem, state)
            proc_rows.append(p); proc_list.controls.append(p["row"])

    proc_title = txt("Top processes", 12.5, "text2", ft.FontWeight.W_600)
    proc_card = card(ft.Column([
        ft.Row([icon(ft.Icons.SPEED_ROUNDED, "violet", 17), proc_title], spacing=8),
        ft.Container(height=9), proc_list], spacing=0))

    OFFLINE_PROCS = [("Waiting for the background service…", "—", "running")]

    # ── event feed ──────────────────────────────────────────────────────────
    log = ft.ListView(spacing=7, auto_scroll=True, expand=True)
    log_items = []
    def add_log(msg, token="text2", icon_name=ft.Icons.CHEVRON_RIGHT_ROUNDED):
        i = ft.Icon(icon_name, size=13.5, color=T[token])
        t = ft.Text(msg, size=11.5, color=T[token], no_wrap=False, expand=True)
        log_items.append((i, t, token))
        log.controls.append(ft.Row([i, t], spacing=8,
                                   vertical_alignment=ft.CrossAxisAlignment.START))
        if len(log.controls) > 120:
            log.controls.pop(0); log_items.pop(0)

    add_log("Starting up — connecting to the background service…",
            "muted", ft.Icons.SENSORS_ROUNDED)
    log_card = card(ft.Column([
        title_row(ft.Icons.TIMELINE_ROUNDED, "Event feed", "mint"),
        ft.Container(height=7), ft.Container(content=log, height=142)], spacing=2))

    # ── controls ────────────────────────────────────────────────────────────
    thr_txt = txt("Acts at 80% confidence", 10.5, "muted")
    pills = {}
    def make_pill(name, thr, token):
        label = ft.Text(name, size=11, weight=ft.FontWeight.W_700,
                        color=(T["bg"] if st["profile"] == name else T["muted"]))
        c = ft.Container(content=label, padding=ft.Padding(13, 7, 13, 7), border_radius=10,
                         bgcolor=(T[token] if st["profile"] == name
                                  else ft.Colors.with_opacity(T["soft_op"], T["line"])),
                         animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT))
        def pick(e):
            st["profile"] = name; st["thr"] = thr
            thr_txt.value = f"Acts at {thr}% confidence"
            paint_pills()
            if st["live"]:
                send_cmd("set_profile", value=name)
            add_log(f"Profile → {name} (acts at {thr}% confidence).", token, ft.Icons.TUNE_ROUNDED)
            upd()
        c.on_click = pick
        pills[name] = (c, label, token)
        return c

    def paint_pills():
        for nm, (c, label, token) in pills.items():
            active = st["profile"] == nm
            label.color = T["bg"] if active else T["muted"]
            c.bgcolor = T[token] if active else ft.Colors.with_opacity(T["soft_op"], T["line"])

    profiles = ft.Row([make_pill("Eco", 70, "mint"), make_pill("Balanced", 80, "cyan"),
                       make_pill("Gaming", 90, "violet")], spacing=8)

    auto_sw = ft.Switch(value=True, active_color=T["mint"], scale=0.85)
    reg(auto_sw, "active_color", lambda t: t["mint"])
    def on_auto(e):
        st["autopilot"] = auto_sw.value
        if st["live"]:
            send_cmd("toggle_autopilot", value=auto_sw.value)
        add_log(f"Auto-Pilot {'enabled' if auto_sw.value else 'disabled'}.",
                "mint" if auto_sw.value else "amber", ft.Icons.SMART_TOY_ROUNDED)
        upd()
    auto_sw.on_change = on_auto

    svc_sw = ft.Switch(value=True, active_color=T["cyan"], scale=0.85)
    reg(svc_sw, "active_color", lambda t: t["cyan"])
    def on_service(e):
        if svc_sw.value:
            st["want_live"] = True
            launch_service()
            add_log("Starting the background service…", "cyan",
                    ft.Icons.POWER_SETTINGS_NEW_ROUNDED)
        else:
            send_cmd("shutdown")
            st["want_live"] = False
            add_log("Background service stopped. Your system is no longer protected.",
                    "amber", ft.Icons.POWER_SETTINGS_NEW_ROUNDED)
            go_offline("")
        upd()
    svc_sw.on_change = on_service

    boost_btn = styled(ft.FilledButton("  Boost", icon=ft.Icons.ROCKET_LAUNCH_ROUNDED),
                       "cyan", False, (13, 11))
    undo_btn = styled(ft.FilledButton("  Undo", icon=ft.Icons.REPLAY_ROUNDED),
                      "mint", False, (13, 11))

    controls_card = card(ft.Column([
        title_row(ft.Icons.TUNE_ROUNDED, "Controls", "cyan"),
        ft.Container(height=11),
        txt("Performance profile", 11, "text2", ft.FontWeight.W_600),
        ft.Container(height=7), profiles, ft.Container(height=5), thr_txt,
        ft.Container(height=13),
        ft.Row([ft.Row([icon(ft.Icons.SMART_TOY_ROUNDED, "mint", 17),
                        txt("Auto-Pilot", 11.5, "text2", ft.FontWeight.W_600)], spacing=8),
                auto_sw], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Container(height=7),
        ft.Row([ft.Row([icon(ft.Icons.POWER_SETTINGS_NEW_ROUNDED, "cyan", 17),
                        txt("Background service", 11.5, "text2", ft.FontWeight.W_600)], spacing=8),
                svc_sw], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Container(height=11),
        ft.Row([ft.Container(boost_btn, expand=True),
                ft.Container(undo_btn, expand=True)], spacing=9),
        ft.Container(height=8),
        ft.Row([ft.Container(report_btn, expand=True)])], spacing=2))

    # ── protected processes (user whitelist) ────────────────────────────────
    wl_list = ft.Row(wrap=True, spacing=8, run_spacing=8)
    WL_H = 44
    wl_input = ft.TextField(
        hint_text="Add an app to protect — e.g. obs64",
        text_size=12.5, height=WL_H, expand=True,
        border_radius=11, border_width=1, focused_border_width=1.6,
        content_padding=ft.Padding(14, 0, 14, 0),
        border_color=ft.Colors.with_opacity(T["line_op"] + 0.08, T["line"]),
        focused_border_color=T["violet"],
        cursor_color=T["violet"],
        bgcolor=T["panel"], filled=True,
        text_style=ft.TextStyle(color=T["text"]),
        hint_style=ft.TextStyle(color=T["muted"], size=12))
    reg(wl_input, "border_color", lambda t: ft.Colors.with_opacity(t["line_op"] + 0.08, t["line"]))
    reg(wl_input, "focused_border_color", lambda t: t["violet"])
    reg(wl_input, "cursor_color", lambda t: t["violet"])
    reg(wl_input, "bgcolor", lambda t: t["panel"])
    reg(wl_input, "text_style", lambda t: ft.TextStyle(color=t["text"]))
    reg(wl_input, "hint_style", lambda t: ft.TextStyle(color=t["muted"], size=12))

    wl_add = ft.FilledButton("  Add", icon=ft.Icons.ADD_ROUNDED, height=WL_H,
                             tooltip="Protect this app")
    styled(wl_add, "violet", False, (16, 0))

    # Running-app picker: an arrow beside the field opens a list of the apps
    # currently running, so a process can be protected without typing its name.
    proc_choices = []                       # refreshed from every live poll
    picker_list = ft.Column(spacing=5, scroll=ft.ScrollMode.AUTO, height=168)
    picker_panel = ft.Container(visible=False, content=picker_list,
                                padding=ft.Padding(0, 8, 0, 0))

    def build_picker():
        picker_list.controls.clear()
        wl_now = set()
        r = send_cmd("get_whitelist") or {}
        wl_now = {x.lower() for x in (r.get("whitelist") or [])}
        if not proc_choices:
            picker_list.controls.append(
                txt("Connect to the service to see running apps.", 11, "muted"))
            return
        for name in proc_choices:
            already = name.lower() in wl_now
            def pick(e, n=name):
                send_cmd("add_whitelist", value=n)
                add_log(f"\u201c{n}\u201d is now protected from throttling.",
                        "violet", ft.Icons.LOCK_ROUNDED)
                close_picker()
                refresh_whitelist(); upd()
            picker_list.controls.append(ft.Container(
                content=ft.Row([
                    ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED if already
                            else ft.Icons.ADD_CIRCLE_OUTLINE_ROUNDED,
                            size=14, color=T["mint"] if already else T["muted"]),
                    ft.Text(name, size=12, color=T["text"] if not already else T["muted"],
                            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                    ft.Text("protected" if already else "", size=9.5, color=T["mint"]),
                ], spacing=8),
                padding=ft.Padding(11, 7, 11, 7), border_radius=9,
                bgcolor=T["panel"], on_click=None if already else pick))

    def close_picker():
        picker_panel.visible = False
        picker_arrow.icon = ft.Icons.ARROW_DROP_DOWN_ROUNDED

    def toggle_picker(e):
        picker_panel.visible = not picker_panel.visible
        picker_arrow.icon = (ft.Icons.ARROW_DROP_UP_ROUNDED if picker_panel.visible
                             else ft.Icons.ARROW_DROP_DOWN_ROUNDED)
        if picker_panel.visible:
            build_picker()
        upd()

    picker_arrow = ft.IconButton(ft.Icons.ARROW_DROP_DOWN_ROUNDED, icon_size=26,
                                 icon_color=T["violet"], height=WL_H,
                                 tooltip="Choose from running apps",
                                 on_click=toggle_picker)
    reg(picker_arrow, "icon_color", lambda t: t["violet"])

    def wl_row(name):
        """One protected app, rendered as a compact chip so entries flow
        side by side and wrap, instead of stacking full-width."""
        def do_rm(e, n=name):
            send_cmd("remove_whitelist", value=n)
            add_log(f"Removed “{n}” from protected apps.", "muted", ft.Icons.LOCK_ROUNDED)
            refresh_whitelist(); upd()
        x = ft.Container(content=ft.Icon(ft.Icons.CLOSE_ROUNDED, size=13, color=T["muted"]),
                         padding=ft.Padding(3, 3, 3, 3), border_radius=9,
                         tooltip="Stop protecting", on_click=do_rm)
        return ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.LOCK_ROUNDED, size=12, color=T["violet"]),
                ft.Text(name, size=11.5, color=T["text"], weight=ft.FontWeight.W_500),
                x], spacing=6, tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding(10, 4, 5, 4), border_radius=16,
            bgcolor=ft.Colors.with_opacity(0.13, T["violet"]),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.30, T["violet"])))

    def refresh_whitelist():
        wl_list.controls.clear()
        if not st["live"]:
            wl_list.controls.append(txt("Connect to manage protected apps.", 11, "muted"))
            return
        r = send_cmd("get_whitelist") or {}
        items = r.get("whitelist") or []
        if not items:
            wl_list.controls.append(txt("No custom apps protected yet.", 11, "muted"))
        for n in items:
            wl_list.controls.append(wl_row(n))

    def add_wl(e):
        v = (wl_input.value or "").strip()
        if not v:
            return
        send_cmd("add_whitelist", value=v)
        add_log(f"“{v}” is now protected from throttling.", "violet", ft.Icons.LOCK_ROUNDED)
        wl_input.value = ""
        refresh_whitelist(); upd()
    wl_add.on_click = add_wl
    wl_input.on_submit = add_wl

    wl_card = card(ft.Column([
        title_row(ft.Icons.LOCK_ROUNDED, "Protected processes", "violet"),
        txt("These apps are never suspended — system processes are always protected too.",
            10.5, "muted"),
        ft.Container(height=9),
        ft.Row([wl_input, picker_arrow, wl_add], spacing=6,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        picker_panel,
        ft.Container(height=8), wl_list], spacing=2))

    # ── footer ──────────────────────────────────────────────────────────────
    def spec(k, v, token="text2"):
        return ft.Row([txt(k, 10, "muted"), txt(v, 11.5, token, ft.FontWeight.W_700)], spacing=6)
    footer = card(ft.Row([
        ft.Row([icon(ft.Icons.MEMORY_ROUNDED, "violet", 17),
                txt("Under the hood", 11.5, "text2", ft.FontWeight.W_600)], spacing=8),
        spec("MODEL", "2-layer GRU"), spec("PARAMS", "44,525"),
        spec("RUNTIME", "8-bit ONNX", "mint"), spec("INFERENCE", "< 2.8 ms", "mint"),
        spec("OVERHEAD", "< 1.8% CPU", "mint"), spec("WINDOW", "60 s × 12 signals"),
    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
       vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True, spacing=14, run_spacing=8),
        pad=14, radius=14)

    # ── shell ───────────────────────────────────────────────────────────────
    TOURABLES = []

    def tourable(content):
        """Wrap a section so the tour can ring it — and frost it.

        Each card carries its own scrim laid exactly over itself, so the tour
        never has to guess pixel rectangles: during a step every card frosts
        except the one being explained.
        """
        scrim = ft.Container(left=0, top=0, right=0, bottom=0, visible=False,
                             border_radius=17,
                             bgcolor=ft.Colors.with_opacity(0.20, "#0B1220"),
                             blur=ft.Blur(7, 7, ft.BlurTileMode.MIRROR))
        w = ft.Container(content=ft.Stack([content, scrim]),
                         border_radius=19, padding=3,
                         border=ft.Border.all(2, ft.Colors.TRANSPARENT),
                         animate=ft.Animation(250, ft.AnimationCurve.EASE_OUT))
        TOURABLES.append((w, scrim))
        return w

    w_bar      = tourable(ft.Row([mode_chip, status_pill, theme_btn, conn_btn, tour_btn], spacing=8))
    w_gauges   = tourable(gauges)
    w_tiles    = tourable(tiles)
    w_chart    = tourable(chart_card)
    w_xai      = tourable(xai_card)
    w_wl       = tourable(wl_card)
    w_session  = tourable(session_card)
    w_procs    = tourable(proc_card)
    w_controls = tourable(controls_card)
    w_log      = tourable(log_card)
    w_footer   = tourable(footer)

    topbar.controls[1] = w_bar   # swap the plain button row for the tourable one

    shell = ft.Container(content=ft.Column([
        topbar, ft.Container(height=11), banner,
        ft.Row([
            ft.Column([w_gauges, ft.Container(height=9), w_tiles, ft.Container(height=9),
                       w_chart, ft.Container(height=9), w_xai,
                       ft.Container(height=9), w_wl], expand=7, spacing=0),
            ft.Column([w_session, ft.Container(height=9), w_procs, ft.Container(height=9),
                       w_controls, ft.Container(height=9), w_log], expand=4, spacing=0),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ft.Container(height=9), w_footer], spacing=0),
        padding=24,
        gradient=ft.LinearGradient(begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT,
                                   colors=[T["bg"], T["bg2"]]))
    reg(shell, "gradient", lambda t: ft.LinearGradient(
        begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT, colors=[t["bg"], t["bg2"]]))
    page.add(shell)

    # ── rendering ───────────────────────────────────────────────────────────
    last = [12, 42, 6, 44, 8, 2400]
    def render(cpu, mem, risk, temp, swap, freq, push=True):
        last[:] = [cpu, mem, risk, temp, swap, freq]
        cpu_ring.value = max(0.0, min(1.0, cpu/100.0)); cpu_val.value = str(int(cpu))
        mem_ring.value = max(0.0, min(1.0, mem/100.0)); mem_val.value = str(int(mem))
        risk_ring.value = max(0.0, min(1.0, risk/100.0)); risk_val.value = str(int(risk))
        cpu_ring.color = T["cyan"]; mem_ring.color = T["teal"]
        risk_ring.color = T["red"] if risk >= 80 else (T["amber"] if risk >= 45 else T["cyan"])
        temp_v.value = ("—" if temp is None or temp < 0 else str(int(temp)))
        swap_v.value = f"{int(swap)}%"
        freq_v.value = ("—" if not freq else f"{int(freq)}")
        hist.append(cpu); hist.pop(0)
        for b, v in zip(bars, hist):
            b.height = max(6, min(112, v*1.0))
            tok = "red" if v >= 85 else ("amber" if v >= 55 else "cyan")
            b.bgcolor = ft.Colors.with_opacity(0.85, T[tok])
        if push:
            upd()

    def set_status(text, token):
        st["status_token"] = token
        status_txt.value = text; status_txt.color = T[token]; status_dot.bgcolor = T[token]

    # ── theming ─────────────────────────────────────────────────────────────
    def apply_theme():
        T.clear(); T.update(LIGHT if st["light"] else DARK)
        page.bgcolor = T["bg"]
        page.theme_mode = ft.ThemeMode.LIGHT if st["light"] else ft.ThemeMode.DARK
        for ctrl, attr_, resolver in REG:
            try: setattr(ctrl, attr_, resolver(T))
            except Exception: pass
        for i, t_, token in log_items:
            i.color = T[token]; t_.color = T[token]
        for p in proc_rows: paint_proc(p)
        paint_pills()
        theme_btn.icon = ft.Icons.DARK_MODE_ROUNDED if st["light"] else ft.Icons.LIGHT_MODE_ROUNDED
        theme_btn.tooltip = "Switch to dark mode" if st["light"] else "Switch to light mode"
        render(*last)

    def toggle_theme(e):
        st["light"] = not st["light"]; apply_theme(); upd()
    theme_btn.on_click = toggle_theme

    # ── LIVE mode ───────────────────────────────────────────────────────────
    def send_cmd(cmd, **kw):
        if not st["live"]: return None
        try:
            payload = {"type": "command", "cmd": cmd}
            payload.update(kw)
            return backend.request(payload)
        except Exception as ex:
            add_log(f"Command '{cmd}' failed: {ex}", "red", ft.Icons.CLOUD_OFF_ROUNDED)
            go_offline(f"connection lost during '{cmd}'")
            return None

    def ingest_logs(entries, initial=False):
        new = []
        for e in entries or []:
            sig = f"{e.get('time','')}|{e.get('message','')}"
            if sig in st["log_seen"]:
                continue
            st["log_seen"].add(sig)
            new.append(e)
        if initial:
            new = new[-5:]
        for e in new:
            msg = e.get("message", "")
            low = msg.lower()
            if "suspend" in low or "throttl" in low:
                token, ic = "amber", ft.Icons.PAUSE_CIRCLE_FILLED_ROUNDED
            elif "resum" in low or "undo" in low:
                token, ic = "mint", ft.Icons.PLAY_CIRCLE_FILLED_ROUNDED
            elif "boost" in low:
                token, ic = "cyan", ft.Icons.ROCKET_LAUNCH_ROUNDED
            else:
                token, ic = "text2", ft.Icons.CHEVRON_RIGHT_ROUNDED
            add_log(f"{e.get('time','')}  {msg}", token, ic)

    def apply_live(resp, initial=False):
        latest = resp.get("latest_result") or {}
        f = latest.get("features") or {}
        cpu = f.get("cpu_percent_raw", f.get("cpu_percent", 0)) or 0
        mem = f.get("mem_percent_raw", f.get("mem_percent", 0)) or 0
        conf = latest.get("confidence", 0) or 0
        risk = conf * 100.0
        temp = f.get("cpu_temp_c", -1)
        swap = f.get("swap_percent", 0) or 0
        freq = f.get("cpu_freq_mhz", 0) or 0

        # calibration state
        if resp.get("calibrating"):
            done, total = (resp.get("calib_progress") or (0, 90))
            set_status(f"CALIBRATING {int(done)}/{int(total)}s", "violet")
            chart_sub.value = "calibrating — learning this machine"
        else:
            thr = st["thr"]
            if risk >= thr:
                set_status("PREDICTING", "amber")
            elif risk >= 45:
                set_status("WATCHING", "cyan")
            else:
                set_status("ALL CLEAR", "mint")
            chart_sub.value = "live · 1 Hz"

        render(cpu, mem, risk, temp, swap, freq, push=False)   # one update at the end

        # attributions
        att = latest.get("attributions") or []
        if len(att) >= 4:
            set_attr(att[0], att[1], att[2], att[3])

        # forecast banner
        if not resp.get("calibrating") and risk >= st["thr"]:
            banner.visible = True; banner.opacity = 1
            forecast_txt.value = (f"CPU → {latest.get('predicted_cpu',0):.0f}%, "
                                  f"MEM → {latest.get('predicted_mem',0):.0f}% in ~30 s  ·  "
                                  f"{risk:.0f}% confidence (acts at {st['thr']}%)")
        else:
            banner.visible = False

        # sync controls with the service
        prof = resp.get("active_profile")
        if prof and prof != st["profile"] and prof in pills:
            st["profile"] = prof
            st["thr"] = {"Eco": 70, "Balanced": 80, "Gaming": 90}.get(prof, 80)
            thr_txt.value = f"Acts at {st['thr']}% confidence"
            paint_pills()
        ap = resp.get("autopilot_enabled")
        if ap is not None and ap != auto_sw.value:
            auto_sw.value = ap; st["autopilot"] = ap

        # processes + suspended
        susp = {(s.get("display_name") or s.get("name") or "").split(" (")[0]
                for s in (resp.get("suspended_processes") or [])}
        st["protected"] = len(susp)
        prot_v.value = str(len(susp))
        proc_choices[:] = sorted({
            (p.get("display_name") or p.get("name") or "").split(" (")[0].strip()
            for p in (resp.get("top_processes") or [])
            if (p.get("display_name") or p.get("name"))
        })
        items = []
        for p in (resp.get("top_processes") or [])[:5]:
            nm = p.get("display_name") or p.get("name") or "?"
            mp = p.get("memory_percent")
            mem_s = f"{mp:.1f}%" if isinstance(mp, (int, float)) else "—"
            items.append((nm, mem_s, "suspended" if nm.split(" (")[0] in susp else "running"))
        for s in (resp.get("suspended_processes") or []):
            nm = (s.get("display_name") or s.get("name") or "?")
            if not any(nm.split(" (")[0] == i[0].split(" (")[0] for i in items):
                items.append((nm, "—", "suspended"))
        if items:
            set_proc_list(items[:5])   # fixed count keeps the card height stable

        ingest_logs(resp.get("logs"), initial=initial)
        upd()

    def launch_service():
        """Start the background service — handles frozen (PyInstaller) builds,
        Windows detached launch, and running from source."""
        try:
            if getattr(sys, "frozen", False):
                base_dir = os.path.dirname(sys.executable)
                if platform.system() == "Windows":
                    srv = os.path.join(base_dir, "SystemResourceOptimizerService.exe")
                    if os.path.exists(srv):
                        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                                 | getattr(subprocess, "DETACHED_PROCESS", 0x00000008))
                        subprocess.Popen([srv], creationflags=flags)
                        return True
                else:
                    srv = os.path.join(base_dir, "SystemResourceOptimizerService")
                    if os.path.exists(srv):
                        subprocess.Popen([srv], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
                        return True
                return False

            for script in (os.path.join(_DIR, "optimizer_service.py"),
                           os.path.join(_DIR, "src", "optimizer_service.py")):
                if os.path.exists(script):
                    if sys.platform == "win32":
                        import re
                        pyw = re.sub(r"python\.exe$", "pythonw.exe", sys.executable,
                                     flags=re.IGNORECASE)
                        py_cmd = pyw if os.path.exists(pyw) else sys.executable
                        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                                 | getattr(subprocess, "DETACHED_PROCESS", 0x00000008))
                        subprocess.Popen([py_cmd, script], creationflags=flags)
                    else:
                        subprocess.Popen([sys.executable, script], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
                    return True
            return False
        except Exception:
            return False

    def enter_live(full):
        st["live"] = True
        st["log_seen"].clear()
        mode_txt.value = "LIVE"
        conn_btn.text = "  Disconnect"; conn_btn.icon = ft.Icons.LINK_OFF_ROUNDED
        add_log(f"Connected to the background service at {IPC_HOST}:{IPC_PORT}.",
                "mint", ft.Icons.SENSORS_ROUNDED)
        svc_sw.value = True
        apply_theme()
        apply_live(full, initial=True)
        refresh_whitelist()

    def go_offline(reason=""):
        was_live = st["live"]
        st["live"] = False
        backend.close()
        mode_txt.value = "OFFLINE"
        conn_btn.text = "  Connect"; conn_btn.icon = ft.Icons.SENSORS_ROUNDED
        chart_sub.value = "last 48 s"
        if was_live:
            add_log(f"Disconnected from the background service. {reason}".strip(),
                    "amber", ft.Icons.CLOUD_OFF_ROUNDED)
            set_status("ALL CLEAR", "mint")
            banner.visible = False
            set_proc_list(OFFLINE_PROCS)
            set_attr(0, 0, 0, 0)
            refresh_whitelist()
        apply_theme(); upd()

    # Async poll loop — same pattern the production dashboard uses. Blocking
    # socket I/O is pushed to a worker thread with asyncio.to_thread so the UI
    # event loop keeps repainting (a raw thread calling page.update() freezes it).
    async def poll_service_worker():
        try:
            await _poll_loop()
        except Exception:
            import traceback
            print("POLL WORKER DIED:", traceback.format_exc(), file=sys.stderr, flush=True)

    async def _poll_loop():
        launched = False
        while True:
            try:
                if not st["want_live"]:
                    if st["live"]:
                        go_offline("")
                    await asyncio.sleep(0.5)
                    continue

                if not st["live"]:
                    mode_txt.value = "CONNECTING"
                    upd()
                    try:
                        await asyncio.to_thread(backend.connect)
                        full = await asyncio.to_thread(backend.request,
                                                       {"type": "get_full_state"})
                        enter_live(full)
                        launched = False   # allow a relaunch if it ever dies later
                        upd()
                    except Exception:
                        backend.close()
                        if not launched:
                            launched = True
                            if launch_service():
                                add_log("No service found — starting it now…",
                                        "muted", ft.Icons.POWER_SETTINGS_NEW_ROUNDED)
                                upd()
                                await asyncio.sleep(4.0)
                            else:
                                add_log("Could not locate optimizer_service.py — running offline.",
                                        "amber", ft.Icons.CLOUD_OFF_ROUNDED)
                                st["want_live"] = False
                                go_offline("")
                        else:
                            mode_txt.value = "OFFLINE"
                            upd()
                            await asyncio.sleep(2.0)
                        continue
                else:
                    resp = await asyncio.to_thread(backend.request, {"type": "get_update"})
                    apply_live(resp)
                    upd()

            except Exception as err:
                # Connection dropped — keep the UI alive and retry on the next tick.
                backend.close()
                if st["live"]:
                    st["live"] = False
                    mode_txt.value = "RECONNECTING"
                    add_log(f"Connection lost — reconnecting… ({err})",
                            "amber", ft.Icons.CLOUD_OFF_ROUNDED)
                    upd()
            await asyncio.sleep(1.0)

    def toggle_conn(e):
        st["want_live"] = not st["want_live"]
        if not st["want_live"]:
            go_offline("")
        else:
            mode_txt.value = "CONNECTING"; upd()
    conn_btn.on_click = toggle_conn

    # ── actions ─────────────────────────────────────────────────────────────
    def do_boost(e):
        if st["running"]: return
        if st["live"]:
            r = send_cmd("boost")
            if r: add_log(r.get("message", "Boost sent."), "cyan", ft.Icons.ROCKET_LAUNCH_ROUNDED)
            upd(); return
        acted = False
        for p in proc_rows:
            if p["state"] == "running" and p["name"] != "Code Editor":
                set_proc(p, "suspended"); acted = True
                st["protected"] += 1; prot_v.value = str(st["protected"])
        add_log("One-Click Boost — trimmed memory & suspended heavy apps."
                if acted else "Boost: nothing heavy to suspend right now.",
                "cyan" if acted else "muted", ft.Icons.ROCKET_LAUNCH_ROUNDED)
        upd()

    def do_undo(e):
        if st["running"]: return
        if st["live"]:
            r = send_cmd("undo")
            if r: add_log(r.get("message", "Undo sent."), "mint", ft.Icons.REPLAY_ROUNDED)
            upd(); return
        n = sum(1 for p in proc_rows if p["state"] == "suspended")
        for p in proc_rows:
            if p["state"] == "suspended": set_proc(p, "running")
        add_log(f"Undo — resumed {n} process(es). No work lost." if n else "Nothing to undo.",
                "mint" if n else "muted", ft.Icons.REPLAY_ROUNDED)
        upd()

    def do_report(e):
        if not st["live"]:
            add_log("Connect to the service to generate a report.", "muted")
            upd(); return
        add_log("Generating analytics report…", "violet", ft.Icons.ASSESSMENT_ROUNDED)
        upd()
        r = send_cmd("generate_report", days=30)
        if r and r.get("status") == "ok":
            add_log(f"Report saved to {r.get('path')}", "mint", ft.Icons.ASSESSMENT_ROUNDED)
        elif r:
            add_log(f"Report failed: {r.get('message')}", "red", ft.Icons.CLOUD_OFF_ROUNDED)
        upd()

    report_btn = styled(ft.FilledButton("  Report", icon=ft.Icons.ASSESSMENT_ROUNDED),
                        "violet", False, (13, 11))
    report_btn.on_click = do_report

    boost_btn.on_click = do_boost; undo_btn.on_click = do_undo

    # ── window close: never kill the service; exit this process cleanly ─────
    def on_window_event(e):
        evt = getattr(getattr(e, "type", None), "value", None) or getattr(e, "data", None)
        if evt in ("close", "destroy", "quit", "exit", "window_close"):
            if getattr(page, "_closing_in_progress", False):
                return
            page._closing_in_progress = True
            st["live"] = False; st["want_live"] = False
            try: backend.close()
            except Exception: pass
            try:
                page.window.prevent_close = False
                page.window.destroy()
            except Exception: pass

            def finalize_exit():
                try:
                    if notifier:
                        notifier.send_sync(
                            title="⚡ SRO Dashboard Closed",
                            message="The optimizer service is still running in the background "
                                    "to keep your system fast.",
                            timeout=1)
                except Exception:
                    pass
                time.sleep(0.15)
                os._exit(0)

            threading.Thread(target=finalize_exit, daemon=True).start()

    page.window.on_event = on_window_event
    page.window.prevent_close = True

    # ── guided tour ─────────────────────────────────────────────────────────
    UP, DOWN = ft.Icons.ARROW_UPWARD_ROUNDED, ft.Icons.ARROW_DOWNWARD_ROUNDED
    LEFTA, RIGHTA = ft.Icons.ARROW_BACK_ROUNDED, ft.Icons.ARROW_FORWARD_ROUNDED

    # (target, arrow, callout-left, callout-top, title, body)
    STEPS = [
        (w_bar, UP, 660, 96, "Connection, theme & this tour",
         "LIVE means the background service is connected and streaming real data. "
         "Connect/Disconnect, switch light or dark, or replay this tour any time."),
        (w_gauges, LEFTA, 826, 120, "The three live gauges",
         "CPU load and Memory are measured now. BOTTLENECK RISK is the AI's confidence that a "
         "slowdown is coming in the next 30 seconds — it turns amber, then red."),
        (w_tiles, LEFTA, 826, 250, "Supporting hardware signals",
         "Package temperature, swap pressure and CPU frequency. These feed the model too — "
         "12 signals are sampled every single second."),
        (w_chart, LEFTA, 826, 360, "Live telemetry",
         "The last 48 seconds of CPU load. The model reads a rolling 60-second window of all "
         "12 signals — it looks at the trend, not just this instant."),
        (w_xai, LEFTA, 826, 545, "Explainable AI — the 'why'",
         "The model doesn't just say a bottleneck is coming, it says which signal caused that "
         "belief. Each bar is measured by blanking one signal and seeing how much confidence drops."),
        (w_wl, LEFTA, 826, 520, "Protected processes",
         "Apps you never want touched. Critical system processes are protected automatically; "
         "add your own here and the optimizer will always skip them."),
        (w_session, RIGHTA, 420, 88, "Session impact",
         "How many bottlenecks have been prevented, and how many processes the optimizer has "
         "acted on since it started."),
        (w_procs, RIGHTA, 420, 200, "Top processes",
         "Your real running apps, ranked by memory. When one is throttled it turns amber and "
         "reads SUSPENDED — frozen, never closed, so no work is lost."),
        (w_controls, RIGHTA, 420, 500, "Controls",
         "Eco / Balanced / Gaming set how confident the AI must be before it acts (70/80/90%). "
         "Auto-Pilot lets it act on its own. Boost frees resources now; Undo restores everything."),
        (w_log, RIGHTA, 420, 700, "Event feed",
         "A running record of every decision: forecasts, suspensions, resumes and your commands — "
         "with timestamps, straight from the service."),
        (w_footer, DOWN, 420, 470, "Under the hood",
         "A 2-layer GRU with 44,525 parameters, exported to 8-bit ONNX. One prediction takes under "
         "2.8 ms and the whole optimizer costs under 1.8% CPU."),
    ]

    tour = {"i": 0, "on": False}

    # A long dashed shaft whose dashes ride a wave, ending in an arrow head.
    WAVE = [0, 4, 8, 10, 8, 4, 0, 4]

    def build_dashed_arrow(direction):
        col = T["cyan"]
        horiz = direction in (LEFTA, RIGHTA)
        dashes = []
        for i in range(len(WAVE)):
            w = WAVE[i] if direction in (RIGHTA, DOWN) else WAVE[len(WAVE) - 1 - i]
            dashes.append(ft.Container(
                width=10 if horiz else 3, height=3 if horiz else 10,
                border_radius=2, bgcolor=col,
                margin=ft.Margin(0, w, 0, 0) if horiz else ft.Margin(w, 0, 0, 0)))
        head = ft.Icon(direction, size=26, color=col)
        parts = ([head] + dashes) if direction in (LEFTA, UP) else (dashes + [head])
        if horiz:
            return ft.Row(parts, spacing=5, tight=True,
                          vertical_alignment=ft.CrossAxisAlignment.CENTER)
        return ft.Column(parts, spacing=5, tight=True,
                         horizontal_alignment=ft.CrossAxisAlignment.CENTER)

    tour_arrow = ft.Container(content=build_dashed_arrow(LEFTA),
                              offset=ft.Offset(0, 0),
                              animate_offset=ft.Animation(430, ft.AnimationCurve.EASE_IN_OUT))

    async def wiggle_arrow():
        """Nudge the arrow back and forth along its pointing axis while the
        tour is open, so the eye follows it to the highlighted card."""
        out = False
        while tour["on"]:
            d = tour.get("dir", LEFTA)
            a = 0.18
            if not out:
                tour_arrow.offset = ft.Offset(0, 0)
            elif d == LEFTA:
                tour_arrow.offset = ft.Offset(-a, 0)
            elif d == RIGHTA:
                tour_arrow.offset = ft.Offset(a, 0)
            elif d == UP:
                tour_arrow.offset = ft.Offset(0, -a)
            else:
                tour_arrow.offset = ft.Offset(0, a)
            out = not out
            # Repaint just the arrow — a full page.update() twice a second is
            # far more work than this animation is worth.
            try:
                tour_arrow.update()
            except Exception:
                pass
            await asyncio.sleep(0.62)
        tour_arrow.offset = ft.Offset(0, 0)
        try:
            tour_arrow.update()
        except Exception:
            pass
    tour_step = ft.Text("", size=9.5, color=T["muted"], weight=ft.FontWeight.W_700)
    reg(tour_step, "color", lambda t: t["muted"])
    tour_title = ft.Text("", size=14, weight=ft.FontWeight.BOLD, color=T["text"])
    reg(tour_title, "color", lambda t: t["text"])
    tour_body = ft.Text("", size=11.5, color=T["text2"], no_wrap=False)
    reg(tour_body, "color", lambda t: t["text2"])

    tour_skip = ft.TextButton("Skip", style=ft.ButtonStyle(color=T["muted"]))
    reg(tour_skip, "style", lambda t: ft.ButtonStyle(color=t["muted"]))
    tour_back = ft.TextButton("Back", style=ft.ButtonStyle(color=T["text2"]))
    reg(tour_back, "style", lambda t: ft.ButtonStyle(color=t["text2"]))
    tour_next = styled(ft.FilledButton("Next"), "cyan", True, (16, 10))

    tour_card = ft.Container(
        width=360, padding=16, border_radius=15,
        bgcolor=ft.Colors.with_opacity(0.92, T["card"]),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.35, T["cyan"])),
        shadow=ft.BoxShadow(blur_radius=34, spread_radius=2,
                            color=ft.Colors.with_opacity(0.5, "#000000"),
                            offset=ft.Offset(0, 10)),
        blur=ft.Blur(9, 9, ft.BlurTileMode.MIRROR),
        content=ft.Column([tour_step, tour_title, ft.Container(height=3), tour_body,
                           ft.Container(height=10),
                           ft.Row([tour_skip, ft.Row([tour_back, tour_next], spacing=4)],
                                  alignment=ft.MainAxisAlignment.SPACE_BETWEEN)],
                          spacing=2))
    reg(tour_card, "bgcolor", lambda t: ft.Colors.with_opacity(0.92, t["card"]))
    reg(tour_card, "border", lambda t: ft.Border.all(1, ft.Colors.with_opacity(0.35, t["cyan"])))

    tour_layer = ft.Stack([tour_arrow, tour_card], expand=True, visible=False)
    page.overlay.append(tour_layer)

    def clear_rings():
        for target, *_ in STEPS:
            target.border = ft.Border.all(2, ft.Colors.TRANSPARENT)
            target.shadow = None
        for _w, sc in TOURABLES:
            sc.visible = False

    def show_step(i):
        clear_rings()
        target, arrow, left, top, title, bodytext = STEPS[i]
        # frost every card except the one this step is about
        for w, sc in TOURABLES:
            sc.visible = (w is not target)
        target.border = ft.Border.all(2, T["cyan"])
        target.shadow = ft.BoxShadow(blur_radius=26, spread_radius=1,
                                     color=ft.Colors.with_opacity(0.5, T["cyan"]))
        tour["dir"] = arrow
        tour_arrow.content = build_dashed_arrow(arrow)
        # Keep the callout fully on screen — the lower steps were placing it
        # past the bottom edge, putting Skip/Next out of reach.
        win_h = page.window.height or 820
        win_w = page.window.width or 1280
        top = max(14, min(top, win_h - 230))
        left = max(14, min(left, win_w - 380))
        tour_card.left, tour_card.top = left, top
        if arrow == LEFTA:
            tour_arrow.left, tour_arrow.top = left - 142, top + 30
        elif arrow == RIGHTA:
            tour_arrow.left, tour_arrow.top = left + 372, top + 30
        elif arrow == UP:
            tour_arrow.left, tour_arrow.top = left + 150, max(4, top - 108)
        else:
            tour_arrow.left, tour_arrow.top = left + 150, min(win_h - 120, top + 168)
        tour_step.value = f"STEP {i+1} OF {len(STEPS)}"
        tour_title.value = title
        tour_body.value = bodytext
        tour_back.visible = i > 0
        tour_next.text = "Done" if i == len(STEPS) - 1 else "Next"
        upd()

    def start_tour(e=None):
        if tour["on"]:
            return
        tour["i"] = 0; tour["on"] = True
        tour_layer.visible = True
        show_step(0)
        page.run_task(wiggle_arrow)

    def end_tour(e=None):
        tour["on"] = False
        tour_layer.visible = False
        clear_rings()
        upd()

    def next_step(e=None):
        if tour["i"] >= len(STEPS) - 1:
            end_tour(); return
        tour["i"] += 1; show_step(tour["i"])

    def prev_step(e=None):
        if tour["i"] > 0:
            tour["i"] -= 1; show_step(tour["i"])

    tour_next.on_click = next_step
    tour_back.on_click = prev_step
    tour_skip.on_click = end_tour
    tour_btn.on_click = start_tour

    set_proc_list(OFFLINE_PROCS)
    apply_theme()
    render(12, 42, 6, 44, 8, 2400)

    # Connect to the background service (starting it if needed) and keep the
    # live telemetry flowing, exactly like the production dashboard.
    page.run_task(poll_service_worker)

    # Show the guided tour on every launch, once the window has settled.
    async def autostart_tour():
        await asyncio.sleep(1.4)
        start_tour()
    page.run_task(autostart_tour)


if __name__ == "__main__":
    ft.run(main)
