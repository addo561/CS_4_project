# =============================================================================
# optimizer_service.py — Background Service / Daemon for System Resource Optimizer
# Runs silently, monitors metrics, executes AI predictions, manages processes,
# and serves real-time status data over a local TCP socket.
# KNUST Final Year Project — Group 4
# =============================================================================

import collections
import json
import logging
import os
import socket
import sys
import threading
import time
from typing import Optional

import psutil

# Add src to python path if not already there
_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

from core.pipeline import Pipeline, PipelineResult
from config import (
    CONFIDENCE_THRESHOLD, CALIBRATION_SECONDS, PROFILES,
    load_user_settings, save_user_settings,
    load_user_whitelist, save_user_whitelist,
    LOG_DIR, IPC_PORT
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(LOG_DIR, "optimizer_service.log"), encoding="utf-8")
    ]
)
log = logging.getLogger("service")

ACCENT = "#00C896"
WARN = "#F0A500"
MUTED = "#8B949E"


class OptimizerService:
    def __init__(self, host="127.0.0.1", port=IPC_PORT):
        self.host = host
        self.port = port
        self.pipeline: Optional[Pipeline] = None
        self.lock = threading.Lock()
        
        # In-memory history and logs
        self.history = collections.deque(maxlen=120)
        self.recent_logs = collections.deque(maxlen=100)
        self.top_processes = []
        
        # Load persisted settings
        settings = load_user_settings()
        self.autopilot_enabled = settings.get("autopilot", True)
        self.active_profile = settings.get("profile", "Balanced")
        
        # Calibration state
        self.calibrating = True
        self.calib_elapsed = 0
        self.calib_total = CALIBRATION_SECONDS
        
        # Autopilot pacing
        self.last_autopilot_boost = 0.0
        self.last_result: Optional[dict] = None
        
        # Threading events
        self.stop_event = threading.Event()
        self.scanner_thread: Optional[threading.Thread] = None
        self.server_socket = None
        self.server_thread = None

        # Add startup log entry
        self.add_log("Optimizer background service initialized", ACCENT)

    def add_log(self, message: str, color: str = ACCENT) -> None:
        ts = time.strftime("%H:%M:%S")
        entry = {"time": ts, "message": message, "color": color}
        with self.lock:
            self.recent_logs.append(entry)
        log.info(f"Log: {message}")

    def serialize_result(self, res: PipelineResult) -> dict:
        return {
            "timestamp": res.timestamp,
            "features": res.features,
            "confidence": res.confidence,
            "predicted_cpu": res.predicted_cpu,
            "predicted_mem": res.predicted_mem,
            "warning_active": res.warning_active,
            "calibrating": res.calibrating,
            "attributions": res.attributions,
            "action": {
                "action_taken": res.action.action_taken,
                "action_type": res.action.action_type,
                "affected_pids": res.action.affected_pids,
                "affected_names": res.action.affected_names,
                "message": res.action.message,
                "confidence": res.action.confidence
            } if res.action else None
        }

    # ── Telemetry & Calibration Callback ─────────────────────────────────────

    def on_pipeline_result(self, res: PipelineResult) -> None:
        serialized = self.serialize_result(res)
        with self.lock:
            self.history.append(serialized)
            self.last_result = serialized
            self.calibrating = res.calibrating

        # Auto-Pilot Execution Loop (Autonomous)
        if self.autopilot_enabled and not res.calibrating:
            profile = PROFILES.get(self.active_profile, PROFILES["Balanced"])
            threshold = profile.get("CONFIDENCE_THRESHOLD", CONFIDENCE_THRESHOLD)
            
            now = time.monotonic()
            if res.confidence >= threshold and (now - self.last_autopilot_boost > 45):
                self.last_autopilot_boost = now
                if self.pipeline:
                    r = self.pipeline.trigger_boost()
                    self.add_log(f"Auto-Pilot: {r.message}", ACCENT)

        # Log action events if mitigation occurs
        if res.action and res.action.action_taken:
            self.add_log(res.action.message, WARN)

    def on_calibration_progress(self, elapsed: int, total: int) -> None:
        with self.lock:
            self.calib_elapsed = elapsed
            self.calib_total = total
            
        if elapsed == -1:
            self.add_log("Calibration Complete! Local system metrics calibrated successfully.", ACCENT)

    # ── Background Process Scanner ───────────────────────────────────────────

    def _process_scanner(self) -> None:
        """Scan processes periodically in background without blocking telemetry."""
        while not self.stop_event.is_set():
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
                with self.lock:
                    self.top_processes = procs[:20]
            except Exception as e:
                log.error(f"Error scanning processes: {e}")
            
            # Wait 3 seconds
            time.sleep(3)

    # ── TCP Socket Server ───────────────────────────────────────────────────

    def start_ipc_server(self) -> None:
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(5)
            log.info(f"IPC TCP Server running on {self.host}:{self.port}")
            
            self.server_thread = threading.Thread(
                target=self._accept_connections,
                daemon=True,
                name="IPCServerAccept"
            )
            self.server_thread.start()
        except Exception as e:
            log.error(f"Failed to start IPC Server: {e}")
            sys.exit(1)

    def _accept_connections(self) -> None:
        while not self.stop_event.is_set():
            try:
                client_sock, client_addr = self.server_socket.accept()
                t = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock,),
                    daemon=True,
                    name=f"ClientHandler-{client_addr[1]}"
                )
                t.start()
            except Exception:
                break

    def _handle_client(self, client_sock: socket.socket) -> None:
        buffer = ""
        client_sock.settimeout(15.0)
        while not self.stop_event.is_set():
            try:
                data = client_sock.recv(4096)
                if not data:
                    break
                buffer += data.decode("utf-8")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line.strip():
                        continue
                    req = json.loads(line)
                    resp = self.handle_request(req)
                    client_sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            except Exception as e:
                break
        client_sock.close()

    def handle_request(self, req: dict) -> dict:
        req_type = req.get("type")
        
        if req_type == "get_full_state":
            with self.lock:
                suspended = []
                if self.pipeline:
                    for sp in self.pipeline.get_suspended_processes():
                        suspended.append({
                            "name": sp.name,
                            "suspended_at": sp.suspended_at
                        })
                return {
                    "connected": True,
                    "calibrating": self.calibrating,
                    "calib_progress": (self.calib_elapsed, self.calib_total),
                    "autopilot_enabled": self.autopilot_enabled,
                    "active_profile": self.active_profile,
                    "logs": list(self.recent_logs),
                    "history": list(self.history),
                    "suspended_processes": suspended,
                    "top_processes": self.top_processes
                }
                
        elif req_type == "get_update":
            with self.lock:
                suspended = []
                if self.pipeline:
                    for sp in self.pipeline.get_suspended_processes():
                        suspended.append({
                            "name": sp.name,
                            "suspended_at": sp.suspended_at
                        })
                return {
                    "connected": True,
                    "calibrating": self.calibrating,
                    "calib_progress": (self.calib_elapsed, self.calib_total),
                    "autopilot_enabled": self.autopilot_enabled,
                    "active_profile": self.active_profile,
                    "logs": list(self.recent_logs),
                    "latest_result": self.last_result,
                    "suspended_processes": suspended,
                    "top_processes": self.top_processes
                }
                
        elif req_type == "command":
            cmd = req.get("cmd")
            if cmd == "boost":
                if self.pipeline:
                    r = self.pipeline.trigger_boost()
                    self.add_log(r.message, ACCENT)
                    return {"status": "ok", "message": r.message}
                return {"status": "error", "message": "Pipeline not running"}
                
            elif cmd == "undo":
                if self.pipeline:
                    r = self.pipeline.trigger_undo()
                    self.add_log(r.message, MUTED)
                    return {"status": "ok", "message": r.message}
                return {"status": "error", "message": "Pipeline not running"}
                
            elif cmd == "toggle_autopilot":
                val = req.get("value", True)
                self.autopilot_enabled = val
                settings = load_user_settings()
                settings["autopilot"] = val
                save_user_settings(settings)
                self.add_log(f"Auto-Pilot {'Activated' if val else 'Deactivated'}", WARN if val else MUTED)
                return {"status": "ok"}
                
            elif cmd == "set_profile":
                val = req.get("value", "Balanced")
                self.active_profile = val
                settings = load_user_settings()
                settings["profile"] = val
                save_user_settings(settings)
                self.add_log(f"Performance Profile set to: {val}", ACCENT)
                return {"status": "ok"}
                
            elif cmd == "get_whitelist":
                return {"status": "ok", "whitelist": list(sorted(load_user_whitelist()))}
                
            elif cmd == "add_whitelist":
                val = req.get("value", "").strip().lower()
                if val:
                    wl = load_user_whitelist()
                    wl.add(val)
                    save_user_whitelist(wl)
                    self.add_log(f"Added to Whitelist: {val}", ACCENT)
                    return {"status": "ok"}
                return {"status": "error", "message": "Invalid value"}
                
            elif cmd == "remove_whitelist":
                val = req.get("value", "").strip().lower()
                if val:
                    wl = load_user_whitelist()
                    wl.discard(val)
                    save_user_whitelist(wl)
                    self.add_log(f"Removed from Whitelist: {val}", MUTED)
                    return {"status": "ok"}
                return {"status": "error", "message": "Invalid value"}
                
            elif cmd == "shutdown":
                self.add_log("Service shutdown command received", WARN)
                # Terminate service asynchronously
                threading.Thread(target=self.shutdown, daemon=True).start()
                return {"status": "ok", "message": "Service is shutting down"}
                
        return {"status": "error", "message": "Unknown request type"}

    # ── Service Lifecycle ───────────────────────────────────────────────────

    def start(self) -> None:
        log.info("Starting System Resource Optimizer background engine...")
        
        # Start background scanner
        self.stop_event.clear()
        self.scanner_thread = threading.Thread(
            target=self._process_scanner,
            daemon=True,
            name="ProcessScannerThread"
        )
        self.scanner_thread.start()
        
        # Start pipeline
        self.pipeline = Pipeline(
            on_result=self.on_pipeline_result,
            on_calibration_progress=self.on_calibration_progress
        )
        self.pipeline.start()
        
        # Start socket server
        self.start_ipc_server()
        
        # Main service loop
        while not self.stop_event.is_set():
            try:
                time.sleep(1)
            except KeyboardInterrupt:
                break
                
        self.shutdown()

    def shutdown(self) -> None:
        log.info("Shutting down background service...")
        self.stop_event.set()
        
        # Close socket
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
                
        # Stop pipeline
        if self.pipeline:
            self.pipeline.stop()
            self.pipeline = None
            
        log.info("Background service terminated cleanly.")
        sys.exit(0)


if __name__ == "__main__":
    service = OptimizerService()
    service.start()
