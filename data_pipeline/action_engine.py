# =============================================================================
# action_engine.py — Process suspension, whitelist enforcement, undo state machine
# KNUST Final Year Project — Group 4
# =============================================================================

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import psutil

_THIS_DIR    = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_THIS_DIR)
_COLLECTOR   = os.path.join(_PROJECT_DIR, "Data_collector")
for _p in (_THIS_DIR, _COLLECTOR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config import PROCESS_WHITELIST, UNDO_TIMEOUT_SEC, CONFIDENCE_THRESHOLD

log = logging.getLogger("action_engine")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SuspendedProcess:
    """Record of a process that was suspended by the action engine."""
    pid:          int
    name:         str
    suspended_at: float          # monotonic timestamp
    reason:       str            # e.g. "CPU bottleneck predicted (conf=0.92)"
    auto_resume:  bool = True    # resume after UNDO_TIMEOUT_SEC if not manually undone


@dataclass
class ActionResult:
    """Returned by ActionEngine.evaluate() after each inference cycle."""
    action_taken:    bool   = False
    action_type:     str    = "none"          # "suspend", "resume", "boost", "none"
    affected_pids:   list   = field(default_factory=list)
    affected_names:  list   = field(default_factory=list)
    message:         str    = ""
    confidence:      float  = 0.0


# ---------------------------------------------------------------------------
# ActionEngine
# ---------------------------------------------------------------------------

class ActionEngine:
    """
    Receives model inference results, decides whether to act, and
    manages the lifecycle of suspended processes.

    Thread-safety: all public methods acquire _lock before mutating state.
    """

    def __init__(self):
        self._lock             = threading.Lock()
        self._suspended: dict[int, SuspendedProcess] = {}   # pid → record
        self._auto_resume_thread: Optional[threading.Thread] = None
        self._stop_event       = threading.Event()
        self._start_auto_resume_watchdog()

    # ── Public API ──────────────────────────────────────────────────────────

    def evaluate(self, confidence: float, predicted_cpu: float, predicted_mem: float) -> ActionResult:
        """
        Called every inference cycle. Decides whether to suspend processes.

        Parameters
        ----------
        confidence    : model's bottleneck probability (0.0 – 1.0)
        predicted_cpu : predicted CPU % at horizon H
        predicted_mem : predicted memory % at horizon H
        """
        result = ActionResult(confidence=confidence)

        if confidence < CONFIDENCE_THRESHOLD:
            result.message = f"Confidence {confidence:.2f} below threshold {CONFIDENCE_THRESHOLD}. No action."
            return result

        reason = (
            f"Bottleneck predicted (conf={confidence:.2f}, "
            f"CPU={predicted_cpu:.1f}%, MEM={predicted_mem:.1f}%)"
        )
        log.info(f"Action threshold crossed: {reason}")

        targets = self._select_targets()
        if not targets:
            result.message = "No suspendable processes found."
            return result

        suspended_pids, suspended_names = [], []
        for proc in targets:
            if self._suspend_process(proc, reason):
                suspended_pids.append(proc.pid)
                suspended_names.append(proc.name())

        if suspended_pids:
            result.action_taken   = True
            result.action_type    = "suspend"
            result.affected_pids  = suspended_pids
            result.affected_names = suspended_names
            result.message        = f"Suspended {len(suspended_pids)} process(es): {', '.join(suspended_names)}"
            log.info(result.message)

        return result

    def undo(self) -> ActionResult:
        """
        Immediately resume ALL currently suspended processes.
        Called by the 'Undo' button in the dashboard.
        """
        with self._lock:
            if not self._suspended:
                return ActionResult(message="Nothing to undo — no processes are suspended.")

            resumed_pids, resumed_names = [], []
            for pid, record in list(self._suspended.items()):
                if self._resume_process(pid):
                    resumed_pids.append(pid)
                    resumed_names.append(record.name)

            return ActionResult(
                action_taken   = True,
                action_type    = "resume",
                affected_pids  = resumed_pids,
                affected_names = resumed_names,
                message        = f"Undo: resumed {len(resumed_pids)} process(es): {', '.join(resumed_names)}",
            )

    def boost(self) -> ActionResult:
        """
        One-Click Boost:
        1. Resume any previously suspended processes.
        2. Suspend the current top CPU consumers (even below the AI threshold).
        3. Run Python GC to free memory.
        """
        import gc
        gc.collect()

        messages = []
        all_pids:  list = []
        all_names: list = []

        # Step 1: resume whatever was suspended before
        with self._lock:
            prev_suspended = bool(self._suspended)

        if prev_suspended:
            undo_res = self.undo()
            if undo_res.action_taken:
                messages.append(f"Resumed: {', '.join(undo_res.affected_names)}")

        # Step 2: suspend top CPU consumers right now
        targets = self._select_targets(max_targets=3)
        suspended_pids, suspended_names = [], []
        for proc in targets:
            if self._suspend_process(proc, "Manual One-Click Boost"):
                suspended_pids.append(proc.pid)
                suspended_names.append(proc.name())

        if suspended_pids:
            all_pids  += suspended_pids
            all_names += suspended_names
            messages.append(f"Suspended: {', '.join(suspended_names)}")

        if not messages:
            msg = "Boost: memory freed (GC). No suspendable processes found."
        else:
            msg = "Boost: " + " | ".join(messages)

        log.info(msg)
        return ActionResult(
            action_taken   = True,
            action_type    = "boost",
            affected_pids  = all_pids,
            affected_names = all_names,
            message        = msg,
        )

    def get_suspended_list(self) -> list[SuspendedProcess]:
        """Return a snapshot of currently suspended processes (for UI display)."""
        with self._lock:
            return list(self._suspended.values())

    def shutdown(self):
        """Stop the watchdog thread; resume all suspended processes."""
        self._stop_event.set()
        self.undo()

    # ── Internal helpers ────────────────────────────────────────────────────

    def _select_targets(self, max_targets: int = 3) -> list:
        """
        Identify up to max_targets suspendable processes.
        Priority: highest CPU consumers that are not whitelisted and not
        already suspended.
        """
        candidates = []
        with self._lock:
            already_suspended = set(self._suspended.keys())

        try:
            for proc in psutil.process_iter(["pid", "name", "cpu_percent", "status"]):
                try:
                    info = proc.info
                    name_lower = (info["name"] or "").lower()

                    # Skip whitelisted processes
                    if name_lower in PROCESS_WHITELIST:
                        continue
                    # Skip already suspended
                    if info["pid"] in already_suspended:
                        continue
                    # Skip zombie / dead processes
                    if info["status"] in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                        continue

                    candidates.append((info["cpu_percent"] or 0.0, proc))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as exc:
            log.error(f"Error iterating processes: {exc}")
            return []

        # Sort descending by CPU usage; return top N
        candidates.sort(key=lambda x: x[0], reverse=True)
        return [proc for _, proc in candidates[:max_targets] if candidates]

    def _suspend_process(self, proc: psutil.Process, reason: str) -> bool:
        try:
            proc.suspend()
            with self._lock:
                self._suspended[proc.pid] = SuspendedProcess(
                    pid          = proc.pid,
                    name         = proc.name(),
                    suspended_at = time.monotonic(),
                    reason       = reason,
                )
            log.debug(f"Suspended PID {proc.pid} ({proc.name()})")
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            log.warning(f"Could not suspend PID {proc.pid}: {exc}")
            return False

    def _resume_process(self, pid: int) -> bool:
        """Resume a process by PID. Must be called with _lock held for state mutation."""
        try:
            proc = psutil.Process(pid)
            proc.resume()
            self._suspended.pop(pid, None)
            log.debug(f"Resumed PID {pid}")
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            log.warning(f"Could not resume PID {pid}: {exc}")
            self._suspended.pop(pid, None)   # remove stale record
            return False

    # ── Auto-resume watchdog ─────────────────────────────────────────────────

    def _start_auto_resume_watchdog(self):
        self._auto_resume_thread = threading.Thread(
            target  = self._watchdog_loop,
            name    = "AutoResumeWatchdog",
            daemon  = True,
        )
        self._auto_resume_thread.start()

    def _watchdog_loop(self):
        """
        Background loop that automatically resumes processes that have been
        suspended longer than UNDO_TIMEOUT_SEC (default: 5 minutes).
        """
        while not self._stop_event.is_set():
            now = time.monotonic()
            with self._lock:
                timed_out = [
                    pid for pid, rec in self._suspended.items()
                    if rec.auto_resume and (now - rec.suspended_at) >= UNDO_TIMEOUT_SEC
                ]
            for pid in timed_out:
                log.info(f"Auto-resuming PID {pid} (timeout {UNDO_TIMEOUT_SEC}s reached)")
                with self._lock:
                    self._resume_process(pid)

            time.sleep(10)   # check every 10 seconds
