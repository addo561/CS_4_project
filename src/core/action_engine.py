# =============================================================================
# action_engine.py — Process suspension, whitelist enforcement, undo state machine
# Logic adapted from Proposal/action_manager.py — KNUST Final Year Project Group 4
# =============================================================================

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import psutil

from config import PROCESS_WHITELIST, UNDO_TIMEOUT_SEC, CONFIDENCE_THRESHOLD

log = logging.getLogger("action_engine")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SuspendedProcess:
    """Record of a process that was mitigated by the action engine."""
    pid:               int
    name:              str
    suspended_at:      float          # monotonic timestamp
    reason:            str
    auto_resume:       bool = True    # resume after UNDO_TIMEOUT_SEC if not manually undone
    original_affinity: list = field(default_factory=list)
    original_nice:     Optional[int] = None


@dataclass
class ActionResult:
    """Returned by ActionEngine methods after each action."""
    action_taken:    bool   = False
    action_type:     str    = "none"      # "suspend", "resume", "boost", "none"
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

    Suspension logic: targets highest MEMORY consumers (not CPU) to avoid
    suspending actively-needed foreground processes.  Whitelist covers both
    Windows and macOS system processes as well as root/SYSTEM accounts.

    Thread-safety: all public methods acquire _lock before mutating state.
    """

    # Comprehensive whitelist — Windows + macOS system-critical processes
    _WHITELIST = {
        # ── Windows ─────────────────────────────────────────────────────────
        "explorer.exe", "svchost.exe", "system", "smss.exe", "csrss.exe",
        "wininit.exe", "services.exe", "lsass.exe", "winlogon.exe",
        "taskmgr.exe", "dwm.exe", "spoolsv.exe", "registry", "memory compression",
        # ── macOS ───────────────────────────────────────────────────────────
        "kernel_task", "launchd", "windowserver", "sysmond", "logd",
        "fseventsd", "mds", "mds_stores", "opendirectoryd", "coreservicesuiagent",
        "dock", "finder", "loginwindow", "activity monitor", "systemuiserver",
        "coreaudiod", "configd", "diskarbitrationd", "notificationcenter",
        # ── Python (our own process) ─────────────────────────────────────────
        "python", "pythonw", "python.exe", "pythonw.exe",
        "python3", "python3.11", "python3.12",
        # ── Flet GUI Client and Compiler Whitelist ───────────────────────────
        "flet", "flet-desktop", "flet-desktop-full", "flet.exe", "flet-desktop-full.exe",
        "systemresourceoptimizer", "system resource optimizer", "system_resource_optimizer",
        "systemresourceoptimizer.exe", "optimizer.exe", "optimizer",
    }

    def __init__(self):
        self._lock             = threading.Lock()
        self._suspended: dict[int, SuspendedProcess] = {}   # pid → record
        self._auto_resume_thread: Optional[threading.Thread] = None
        self._stop_event       = threading.Event()
        self._start_auto_resume_watchdog()

    # ── Public API ──────────────────────────────────────────────────────────

    def evaluate(self, confidence: float, predicted_cpu: float, predicted_mem: float) -> ActionResult:
        """
        Called every inference cycle.  Suspends top memory consumers when
        confidence crosses the profile's confidence threshold.
        """
        from config import load_user_settings, PROFILES
        
        settings = load_user_settings()
        profile_name = settings.get("profile", "Balanced")
        profile = PROFILES.get(profile_name, PROFILES["Balanced"])
        current_threshold = profile.get("CONFIDENCE_THRESHOLD", CONFIDENCE_THRESHOLD)

        result = ActionResult(confidence=confidence)

        if confidence < current_threshold:
            result.message = (f"Confidence {confidence:.2f} below {profile_name} threshold "
                              f"{current_threshold}. No action.")
            return result

        reason = (f"Bottleneck predicted [{profile_name}] (conf={confidence:.2f}, "
                  f"CPU={predicted_cpu:.1f}%, MEM={predicted_mem:.1f}%)")
        log.info("Action threshold crossed: %s", reason)

        targets = self._select_targets(max_targets=3)
        if not targets:
            result.message = "No suspendable processes found."
            return result

        suspended_pids, suspended_names = [], []
        for proc in targets:
            if self._suspend_process(proc, reason):
                suspended_pids.append(proc.pid)
                try:
                    suspended_names.append(proc.name())
                except Exception:
                    suspended_names.append(str(proc.pid))

        if suspended_pids:
            result.action_taken   = True
            result.action_type    = "suspend"
            result.affected_pids  = suspended_pids
            result.affected_names = suspended_names
            result.message        = (f"Suspended {len(suspended_pids)} process(es): "
                                     f"{', '.join(suspended_names)}")
            log.info(result.message)

        return result

    def undo(self) -> ActionResult:
        """Immediately resume ALL currently suspended processes (Undo button)."""
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
            message        = (f"Undo: resumed {len(resumed_pids)} process(es): "
                              f"{', '.join(resumed_names)}"),
        )

    def boost(self) -> ActionResult:
        """
        One-Click Boost (from Proposal logic):
        1. Resume any previously suspended processes.
        2. Suspend the top memory consumers right now.
        3. Run Python GC to free memory.
        Always returns action_taken=True so the UI always gets feedback.
        """
        import gc
        gc.collect()

        messages:  list = []
        all_pids:  list = []
        all_names: list = []

        # Step 1: resume whatever was suspended before
        with self._lock:
            prev_suspended = bool(self._suspended)

        if prev_suspended:
            undo_res = self.undo()
            if undo_res.action_taken:
                messages.append(f"Resumed: {', '.join(undo_res.affected_names)}")

        # Step 2: suspend top memory consumers now
        targets = self._select_targets(max_targets=3)
        for proc in targets:
            if self._suspend_process(proc, "Manual One-Click Boost"):
                try:
                    name = proc.name()
                except Exception:
                    name = str(proc.pid)
                all_pids.append(proc.pid)
                all_names.append(name)

        if all_names:
            messages.append(f"Suspended: {', '.join(all_names)}")

        msg = "Boost: " + (" | ".join(messages) if messages
                           else "Memory freed (GC). No suspendable processes found.")
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

    @classmethod
    def _is_system_process(cls, proc: psutil.Process) -> bool:
        """Enhanced system process detection using pre-fetched attributes when available."""
        try:
            # Check pre-fetched status first to skip zombies/dead processes instantly
            status = (proc.info.get("status") if hasattr(proc, "info") and proc.info else None) or proc.status()
            if status in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD, "zombie", "dead"):
                return True
                
            # Check pre-fetched name first (fast path)
            name = (proc.info.get("name") if hasattr(proc, "info") and proc.info else None) or proc.name()
            name = name.lower()
            
            # Check against static whitelist
            if name in cls._WHITELIST:
                return True
            
            # Check against config whitelist
            if name in {w.lower() for w in PROCESS_WHITELIST}:
                return True
            
            # Check UID/GID on Unix systems (like macOS)
            uids = (proc.info.get("uids") if hasattr(proc, "info") and proc.info else None) or (proc.uids() if hasattr(proc, "uids") else None)
            if uids and hasattr(uids, "real") and uids.real == 0:  # Root/SYSTEM
                return True
            
            # Check if parent is system init — PID 1 is launchd/systemd on macOS/Linux only.
            # On Windows there is no PID 1; system processes live under PID 4 (System).
            try:
                ppid = (proc.info.get("ppid") if hasattr(proc, "info") and proc.info else None) or proc.ppid()
                if ppid == 1 and sys.platform != "win32":
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            
            # Additional safety: don't suspend if memory usage is extremely low
            try:
                mem_percent = (proc.info.get("memory_percent") if hasattr(proc, "info") and proc.info else None) or proc.memory_percent()
                if mem_percent < 0.1:  # Less than 0.1% - likely system/idle
                    return True
            except psutil.AccessDenied:
                pass
            
            return False
        except Exception as e:
            # Downgraded to debug to prevent file write blocking under heavy load
            log.debug(f"Error checking if process is system: {e}")
            return True  # Safer to assume system process

    def _is_safe_to_suspend(self, proc: psutil.Process, user_whitelist_lower: set[str] = None) -> bool:
        """
        Checks whitelist AND skips root/SYSTEM account processes.
        """
        try:
            # CRITICAL SAFETY CHECK: Never suspend our own process, parent process, or children (like Flet Client)
            my_pid = os.getpid()
            if proc.pid == my_pid:
                return False
                
            try:
                ppid = (proc.info.get("ppid") if hasattr(proc, "info") and proc.info else None) or proc.ppid()
                if ppid == my_pid:
                    return False
            except Exception:
                pass

            # Skip zombie / dead processes FIRST before running any heavier system checks
            status = (proc.info.get("status") if hasattr(proc, "info") and proc.info else None) or proc.status()
            if status in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD, "zombie", "dead"):
                return False

            name = (proc.info.get("name") if hasattr(proc, "info") and proc.info else None) or proc.name()
            name = name.lower()
            
            # NEVER throttle flet GUI or our own compiler executables by pattern matching name
            if "flet" in name or "optimizer" in name or "systemresource" in name:
                return False
            
            # Check user defined whitelist (cached or loaded on-the-fly)
            if user_whitelist_lower is None:
                from config import load_user_whitelist
                user_whitelist = load_user_whitelist()
                user_whitelist_lower = {w.lower() for w in user_whitelist}
                
            if name in user_whitelist_lower:
                return False

            if self._is_system_process(proc):
                return False

            # Skip system account processes (pre-fetched or fallback)
            username = ""
            try:
                username = (proc.info.get("username") if hasattr(proc, "info") and proc.info else None) or proc.username() or ""
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                return False

            if username.lower() in ("root", "system", "local service",
                                    "network service", "_windowserver", "_spotlight"):
                return False
            # Windows: skip NT AUTHORITY accounts (e.g. 'nt authority\\system', 'nt authority\\network service')
            if sys.platform == "win32" and ("nt authority" in username.lower() or username.lower().endswith("\\system")):
                return False

            # Skip already-suspended/mitigated processes
            with self._lock:
                if proc.pid in self._suspended:
                    return False

            return True

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return False

    def _select_targets(self, max_targets: int = 3) -> list:
        """
        Identify up to max_targets suspendable/mitigatable processes.
        Sorted by MEMORY (RSS) descending — mirrors proposal sort order.
        Reads all required fields efficiently via process_iter pre-fetching.
        """
        candidates = []
        try:
            from config import load_user_whitelist
            user_whitelist = load_user_whitelist()
            user_whitelist_lower = {w.lower() for w in user_whitelist}
            
            # Pre-fetch all fields used during safety filters and selection to prevent N+1 queries
            for proc in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent", 
                                             "username", "status", "memory_percent", "uids", "ppid"]):
                try:
                    if not self._is_safe_to_suspend(proc, user_whitelist_lower):
                        continue
                    mem = proc.info.get("memory_info")
                    rss = mem.rss if mem else 0
                    candidates.append((rss, proc))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as exc:
            log.error("Error iterating processes: %s", exc)
            return []

        # Sort by memory descending (proposal approach)
        candidates.sort(key=lambda x: x[0], reverse=True)
        return [proc for _, proc in candidates[:max_targets]]

    def _suspend_process(self, proc: psutil.Process, reason: str) -> bool:
        """Suspends a process using psutil.Process.suspend() for absolute resource mitigation."""
        try:
            name = proc.name()
            pid = proc.pid
            
            # Whitelist and safety checks
            if not self._is_safe_to_suspend(proc):
                log.warning(f"Refusing to suspend unsafe process: {name} (PID {pid})")
                return False
            
            log.info("Suspending process %d (%s) - Reason: %s", pid, name, reason)
            
            # Perform actual kernel-level suspension
            proc.suspend()
            
            mem  = proc.memory_info().rss / (1024 * 1024)
            mem_str = f"{mem/1024:.1f}GB" if mem > 1024 else f"{mem:.1f}MB"
            
            with self._lock:
                self._suspended[pid] = SuspendedProcess(
                    pid               = pid,
                    name              = f"{name} ({mem_str})",
                    suspended_at      = time.monotonic(),
                    reason            = reason,
                )
            log.info("Successfully suspended PID %d (%s) using %s", pid, name, mem_str)
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            log.warning("Could not suspend PID %d: %s", proc.pid, exc)
            return False

    def _resume_process(self, pid: int) -> bool:
        """Resumes a suspended process using psutil.Process.resume()."""
        try:
            record = self._suspended.get(pid)
            if not record:
                log.warning(f"No suspension record found for PID {pid}")
                return False
                
            proc = psutil.Process(pid)
            name = proc.name()
            log.info(f"Resuming process: {name} (PID {pid})")
            
            # Perform actual kernel-level resumption
            proc.resume()

            self._suspended.pop(pid, None)
            log.info("Successfully resumed PID %d", pid)
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            log.warning("Could not resume PID %d: %s", pid, exc)
            self._suspended.pop(pid, None)
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
        """Auto-resume processes suspended longer than UNDO_TIMEOUT_SEC."""
        while not self._stop_event.is_set():
            now = time.monotonic()
            with self._lock:
                timed_out = [
                    pid for pid, rec in self._suspended.items()
                    if rec.auto_resume and (now - rec.suspended_at) >= UNDO_TIMEOUT_SEC
                ]
            for pid in timed_out:
                log.info("Auto-resuming PID %d (timeout %ds reached)", pid, UNDO_TIMEOUT_SEC)
                with self._lock:
                    self._resume_process(pid)
            time.sleep(10)
