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
from core.notifier import Notifier
from core.process_names import friendly_name
from core.history import History
from config import (
    CONFIDENCE_THRESHOLD, CALIBRATION_SECONDS, PROFILES,
    load_user_settings, save_user_settings,
    load_user_whitelist, save_user_whitelist,
    LOG_DIR, IPC_PORT, VERSION
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
        
        # Pending notifications queue for client delivery
        self.pending_notifications = []

        # Tracks when a dashboard client last polled. While a client is active
        # we hand notifications to it (the foreground process delivers them
        # reliably on macOS). When no client is connected, the service fires the
        # OS notification itself so the user still gets alerts in the background.
        self.last_client_poll = 0.0
        self.CLIENT_ACTIVE_WINDOW = 6.0  # seconds since last poll = "dashboard open"
        self._direct_notifier = Notifier()  # no queue_callback -> fires OS notification
        # Persistent, queryable record of every mitigation (SQLite).
        # NOTE: self.history is the in-memory telemetry ring buffer; the event
        # store is deliberately a separate attribute.
        try:
            import platform as _pf
            self.event_store = History(platform=_pf.system(), version=VERSION)
        except Exception:
            self.event_store = None

        # Add startup log entry
        self.add_log("Optimizer background service initialized", ACCENT)

    def _names_with_mem(self, names):
        """The action result carries plain names; the engine's suspended records
        carry the memory footprint. Match them up so the event store captures
        how much memory each mitigation actually relieved."""
        try:
            lookup = {}
            for sp in (self.pipeline.get_suspended_processes() if self.pipeline else []):
                label = getattr(sp, "display_name", "") or getattr(sp, "name", "")
                base = label.split(" (")[0].strip().lower()
                if base:
                    lookup[base] = label
            return [lookup.get(str(n).split(" (")[0].strip().lower(), n) for n in (names or [])]
        except Exception:
            return names or []

    def queue_notification(self, title: str, message: str) -> None:
        # Single, reliable source for NATIVE OS notifications — fired directly
        # from the service whether the dashboard is open or closed. Previously
        # the service handed delivery to the GUI while a client was polling,
        # which was subject to macOS per-app throttling and only worked at
        # startup. Firing here every time gives consistent system notifications.
        try:
            self._direct_notifier.send(title, message)
        except Exception as e:
            log.warning(f"Native notification failed: {e}")

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
                    if self.event_store and r.action_taken:
                        self.event_store.record_many(
                            "suspend", self._names_with_mem(r.affected_names), confidence=res.confidence,
                            trigger="autopilot",
                            cpu_percent=(res.features or {}).get("cpu_percent"),
                            mem_percent=(res.features or {}).get("mem_percent"))

        # Log action events if mitigation occurs
        if res.action and res.action.action_taken:
            self.add_log(res.action.message, WARN)
            if self.event_store:
                self.event_store.record_many(
                    res.action.action_type or "suspend", self._names_with_mem(res.action.affected_names),
                    confidence=res.confidence, trigger="pipeline",
                    cpu_percent=(res.features or {}).get("cpu_percent"),
                    mem_percent=(res.features or {}).get("mem_percent"))

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
                total_mem = float(psutil.virtual_memory().total or 1)
                for p in psutil.process_iter(["pid", "name", "memory_info", "status"]):
                    try:
                        info = p.info
                        mem_info = info.get("memory_info")
                        if mem_info:
                            mem_pct = (mem_info.rss / total_mem) * 100.0
                            if mem_pct > 0.1:
                                item = info.copy()
                                item["memory_percent"] = mem_pct
                                procs.append(item)
                    except (psutil.Error, KeyError, AttributeError):
                        continue

                procs.sort(key=lambda x: x["memory_percent"], reverse=True)
                top = procs[:20]
                # Resolve friendly display names only for the top 20 (keeps cost low,
                # leaves raw "name" intact for whitelist matching).
                for info in top:
                    info["display_name"] = friendly_name(
                        info.get("pid"), info.get("name"))
                with self.lock:
                    self.top_processes = top
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

        # Any polling request from the dashboard means a client is connected.
        if req_type in ("get_full_state", "get_update"):
            self.last_client_poll = time.monotonic()

        if req_type == "get_full_state":
            with self.lock:
                suspended = []
                if self.pipeline:
                    for sp in self.pipeline.get_suspended_processes():
                        suspended.append({
                            "name": sp.name,
                            "display_name": getattr(sp, "display_name", "")
                                            or friendly_name(sp.pid, sp.name),
                            "suspended_at": sp.suspended_at
                        })
                optimizer_active = bool(self.pipeline and self.pipeline._thread and self.pipeline._thread.is_alive())
                
                # Fetch and clear pending notifications
                notifs = list(self.pending_notifications)
                self.pending_notifications.clear()
                
                return {
                    "connected": True,
                    "calibrating": self.calibrating,
                    "calib_progress": (self.calib_elapsed, self.calib_total),
                    "autopilot_enabled": self.autopilot_enabled,
                    "active_profile": self.active_profile,
                    "optimizer_active": optimizer_active,
                    "logs": list(self.recent_logs),
                    "history": list(self.history),
                    "suspended_processes": suspended,
                    "top_processes": self.top_processes,
                    "pending_notifications": notifs
                }
                
        elif req_type == "get_update":
            with self.lock:
                suspended = []
                if self.pipeline:
                    for sp in self.pipeline.get_suspended_processes():
                        suspended.append({
                            "name": sp.name,
                            "display_name": getattr(sp, "display_name", "")
                                            or friendly_name(sp.pid, sp.name),
                            "suspended_at": sp.suspended_at
                        })
                optimizer_active = bool(self.pipeline and self.pipeline._thread and self.pipeline._thread.is_alive())
                
                # Fetch and clear pending notifications
                notifs = list(self.pending_notifications)
                self.pending_notifications.clear()
                
                return {
                    "connected": True,
                    "calibrating": self.calibrating,
                    "calib_progress": (self.calib_elapsed, self.calib_total),
                    "autopilot_enabled": self.autopilot_enabled,
                    "active_profile": self.active_profile,
                    "optimizer_active": optimizer_active,
                    "logs": list(self.recent_logs),
                    "latest_result": self.last_result,
                    "suspended_processes": suspended,
                    "top_processes": self.top_processes,
                    "pending_notifications": notifs
                }
                
        elif req_type == "command":
            cmd = req.get("cmd")
            if cmd == "boost":
                if self.pipeline:
                    r = self.pipeline.trigger_boost()
                    self.add_log(r.message, ACCENT)
                    if self.event_store and r.action_taken:
                        self.event_store.record_many("suspend", self._names_with_mem(r.affected_names),
                                                     trigger="manual")
                    return {"status": "ok", "message": r.message}
                return {"status": "error", "message": "Pipeline not running"}
                
            elif cmd == "undo":
                if self.pipeline:
                    r = self.pipeline.trigger_undo()
                    self.add_log(r.message, MUTED)
                    if self.event_store:
                        self.event_store.record_many("resume", r.affected_names,
                                                     trigger="manual")
                    return {"status": "ok", "message": r.message}
                return {"status": "error", "message": "Pipeline not running"}
                
            elif cmd == "toggle_autopilot":
                val = req.get("value", True)
                self.autopilot_enabled = val
                settings = load_user_settings()
                settings["autopilot"] = val
                save_user_settings(settings)
                self.add_log(f"Auto-Pilot {'Activated' if val else 'Deactivated'}", WARN if val else MUTED)
                self.queue_notification(
                    title=f"🤖 SRO: Auto-Pilot {'Enabled' if val else 'Disabled'}",
                    message=("Optimizer will now act automatically on predicted bottlenecks."
                             if val else "Automatic mitigation is off — you're in manual control."),
                )
                return {"status": "ok"}
                
            elif cmd == "toggle_optimizer":
                val = req.get("value", True)
                if val:
                    if self.pipeline:
                        if not self.pipeline._thread or not self.pipeline._thread.is_alive():
                            self.pipeline.start()
                            self.add_log("Optimizer engine resumed", ACCENT)
                            self.queue_notification(
                                title="⚡ SRO: Optimizer Resumed",
                                message="Background optimizer loop has been resumed."
                            )
                    return {"status": "ok", "optimizer_active": True}
                else:
                    if self.pipeline:
                        if self.pipeline._thread and self.pipeline._thread.is_alive():
                            self.queue_notification(
                                title="⚡ SRO: Optimizer Suspended",
                                message="Background optimizer loop has been suspended. All processes resumed."
                            )
                            self.pipeline.stop()
                            self.add_log("Optimizer engine suspended", MUTED)
                    return {"status": "ok", "optimizer_active": False}
                
            elif cmd == "set_profile":
                val = req.get("value", "Balanced")
                self.active_profile = val
                settings = load_user_settings()
                settings["profile"] = val
                save_user_settings(settings)
                self.add_log(f"Performance Profile set to: {val}", ACCENT)
                thr = PROFILES.get(val, PROFILES["Balanced"]).get("CONFIDENCE_THRESHOLD", 0.8)
                self.queue_notification(
                    title=f"🎛 SRO: {val} Profile",
                    message=f"Switched to {val} — acts at {int(thr * 100)}% bottleneck confidence.",
                )
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
                
            elif cmd == "generate_report":
                try:
                    from reporting.report import build
                    days = int(req.get("days", 30))
                    out = req.get("path") or os.path.join(
                        os.path.expanduser("~"), "Desktop", "SRO_Analytics_Report.pdf")
                    path = build(out, days)
                    self.add_log(f"Analytics report generated: {path}", ACCENT)
                    return {"status": "ok", "path": path}
                except Exception as e:
                    log.warning("Report generation failed: %s", e)
                    return {"status": "error", "message": str(e)}

            elif cmd == "shutdown":
                self.add_log("Service shutdown command received", WARN)
                # Signal main loop to stop, which will run shutdown on the main thread
                self.stop_event.set()
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
        if hasattr(self.pipeline, "_notifier") and self.pipeline._notifier:
            self.pipeline._notifier.queue_callback = self.queue_notification
        self.pipeline.start()
        
        # Start socket server
        self.start_ipc_server()
        
        # Main service loop
        try:
            while not self.stop_event.is_set():
                try:
                    time.sleep(1)
                except KeyboardInterrupt:
                    log.info("Received KeyboardInterrupt signal")
                    break
        except Exception as e:
            log.error(f"Error in main service loop: {e}")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        log.info("Shutting down background service...")
        self.stop_event.set()

        # Stamp the session as closed. A session left with ended_at NULL is
        # therefore meaningful: the service was killed rather than stopped
        # cleanly, and any process it had suspended may not have been resumed.
        if getattr(self, "event_store", None):
            try:
                self.event_store.close()
            except Exception:
                pass
        
        # Stop pipeline first (this will handle process resumption)
        if self.pipeline:
            try:
                if hasattr(self.pipeline, "_notifier") and self.pipeline._notifier:
                    try:
                        self.pipeline._notifier.send_sync(
                            title="⚡ SRO: Optimizer Suspended",
                            message="Background optimizer has been suspended. All processes resumed."
                        )
                        time.sleep(0.5)  # Wait for subprocess/notification engine to launch
                    except Exception as e:
                        log.error(f"Failed to send shutdown notification: {e}")
                
                # Stop pipeline and wait for threads to finish
                self.pipeline.stop()
                time.sleep(0.3)  # Brief wait for pipeline cleanup
            except Exception as e:
                log.error(f"Error stopping pipeline: {e}")
            finally:
                self.pipeline = None
        
        # Wait for scanner thread to finish
        if self.scanner_thread and self.scanner_thread.is_alive():
            try:
                self.scanner_thread.join(timeout=2.0)
            except Exception as e:
                log.error(f"Error joining scanner thread: {e}")
        
        # Close server socket
        if self.server_socket:
            try:
                self.server_socket.shutdown(socket.SHUT_RDWR)
                self.server_socket.close()
            except Exception:
                pass
        
        # Wait for server thread to finish
        if self.server_thread and self.server_thread.is_alive():
            try:
                self.server_thread.join(timeout=2.0)
            except Exception as e:
                log.error(f"Error joining server thread: {e}")
        
        log.info("Background service terminated cleanly.")
        sys.exit(0)


if __name__ == "__main__":
    import signal as _signal

    service = OptimizerService()

    # core.collector registers its own SIGTERM/SIGINT handler at import time,
    # which only stops the collector loop — the service's shutdown() never ran,
    # so suspended processes were not resumed and the session was never closed.
    # Registering here (after that import) takes precedence.
    def _graceful(signum, frame):
        log.info("Signal %s received — shutting down gracefully.", signum)
        try:
            service.shutdown()
        except SystemExit:
            raise
        except Exception as exc:
            log.error("Error during graceful shutdown: %s", exc)
            sys.exit(1)

    for _sig in (_signal.SIGTERM, _signal.SIGINT):
        try:
            _signal.signal(_sig, _graceful)
        except Exception:
            pass

    service.start()
