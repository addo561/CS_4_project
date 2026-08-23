# =============================================================================
# notifier.py — Cross-platform desktop notifications
# macOS  → osascript (built-in, zero deps, always works)
# Windows → winotify native toast (falls back silently if unavailable)
# =============================================================================

import logging
import threading
import os
import sys
import platform
import shutil
import subprocess

log = logging.getLogger("notifier")

_OS = platform.system()          # "Darwin" | "Windows" | "Linux"
APP_NAME = "System Resource Optimizer"

# ── Windows: try winotify ─────────────────────────────────────────────────────
_WINOTIFY_OK = False
if _OS == "Windows":
    try:
        from winotify import Notification as WinNotification
        _WINOTIFY_OK = True
    except Exception:
        log.warning("winotify not available — Windows toasts disabled.")


from config import BASE_DIR

def _find_terminal_notifier():
    """Locate the terminal-notifier binary, if installed."""
    tn = shutil.which("terminal-notifier")
    if tn:
        return tn
    for cand in ("/usr/local/bin/terminal-notifier",
                 "/opt/homebrew/bin/terminal-notifier",
                 "/usr/bin/terminal-notifier"):
        if os.path.exists(cand):
            return cand
    return None


_TERMINAL_NOTIFIER = _find_terminal_notifier()

# A single, stable notification group. Every SRO alert reuses it, so macOS keeps
# exactly ONE notification slot for the app (each new alert replaces + re-pops it)
# instead of stacking hundreds of separate notifications — which is what tripped
# macOS's per-app rate limit and made banners "stop working after a while".
_NOTIF_GROUP = "sro-optimizer"

# Lightweight source-side de-duplication to smooth out bursts.
import time as _time_mod
_last_notif = {"key": "", "t": 0.0}


def _send_macos(title: str, message: str):
    """
    Display a native macOS notification.

    Prefers `terminal-notifier` — it ships its own notification identity that
    macOS reliably displays, sidestepping the "Script Editor" attribution and
    per-app throttling that make raw osascript notifications intermittent.
    Falls back to osascript if terminal-notifier is not installed.
    """
    # Drop an identical notification repeated within a few seconds (avoids
    # double-fires and needless posting that contributes to rate limiting).
    now = _time_mod.monotonic()
    key = f"{title}|{message}"
    if key == _last_notif["key"] and (now - _last_notif["t"]) < 4.0:
        return
    _last_notif["key"] = key
    _last_notif["t"] = now

    _uid = int(_time_mod.time() * 1000) % 100000

    # ── Preferred path: terminal-notifier ────────────────────────────────────
    if _TERMINAL_NOTIFIER:
        try:
            proc = subprocess.Popen(
                [_TERMINAL_NOTIFIER,
                 "-title", title,
                 "-message", message,
                 "-sound", "Blow",
                 "-group", _NOTIF_GROUP],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
            # Reap the child so a long session never accumulates zombie processes.
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            log.info(f"terminal-notifier notification sent: {title}")
            return
        except Exception as exc:
            log.warning(f"terminal-notifier failed ({exc}); falling back to osascript.")

    # ── Fallback: osascript (attributed to Script Editor) ────────────────────
    title_s   = title.replace('"', '\\"')
    message_s = f"{message} (#{_uid})".replace('"', '\\"')
    script = (
        f'display notification "{message_s}" '
        f'with title "{title_s}" '
        f'subtitle "Event ID: {_uid}" '
        f'sound name "Blow"'
    )
    try:
        proc = subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True, close_fds=True,
        )
        try:
            _out, err = proc.communicate(timeout=1.0)
            if proc.returncode != 0:
                log.warning(f"osascript notification failed ({proc.returncode}): {err.strip()}")
            else:
                log.info(f"osascript notification sent successfully: {title}")
        except subprocess.TimeoutExpired:
            log.info(f"osascript notification pending background delivery: {title}")
    except Exception as exc:
        log.warning(f"osascript notification execution failed: {exc}")


def _send_windows(title: str, message: str, timeout: int):
    if not _WINOTIFY_OK:
        return
    try:
        icon_path = os.path.join(BASE_DIR, "assets", "icon.ico")
        if not os.path.exists(icon_path):
            icon_path = None
        else:
            icon_path = os.path.abspath(icon_path)
        toast = WinNotification(
            app_id=APP_NAME,
            title=title,
            msg=message,
            icon=icon_path
        )
        toast.show()
    except Exception as exc:
        log.warning(f"Windows native toast failed: {exc}")


class Notifier:
    """
    Non-blocking cross-platform notifications.
    macOS  → native osascript (zero deps).
    Windows → winotify native toast.
    """

    def __init__(self):
        # Callback hook: set by MainWindow to also show a tray bubble
        self.on_notify = None   # Optional[Callable[[str, str], None]]
        self.queue_callback = None  # Optional[Callable[[str, str], None]]

    def send(self, title: str, message: str, timeout: int = 6):
        """Fire notification asynchronously so it never blocks the UI."""
        if self.queue_callback:
            try:
                self.queue_callback(title, message)
            except Exception:
                pass
            return

        # Call the in-app tray bubble immediately (on whatever thread calls this)
        if self.on_notify:
            try:
                self.on_notify(title, message)
            except Exception:
                pass

        # OS notification on a daemon thread
        threading.Thread(
            target=self._fire,
            args=(title, message, timeout),
            daemon=True,
            name="NotifyThread",
        ).start()

    def send_sync(self, title: str, message: str, timeout: int = 6):
        """Fire notification synchronously to ensure it delivers before process exit."""
        if self.queue_callback:
            try:
                self.queue_callback(title, message)
            except Exception:
                pass
            return

        if self.on_notify:
            try:
                self.on_notify(title, message)
            except Exception:
                pass
        try:
            self._fire(title, message, timeout)
        except Exception:
            pass

    def _fire(self, title: str, message: str, timeout: int):
        try:
            if _OS == "Darwin":
                _send_macos(title, message)
            elif _OS == "Windows":
                _send_windows(title, message, timeout)
            # Linux: no-op (could add libnotify later)
        except Exception as exc:
            log.warning(f"Notification failed: {exc}")

    # ── Convenience wrappers ─────────────────────────────────────────────────

    def notify_suspend(self, process_names: list, confidence: float):
        names = ", ".join(process_names[:3])
        extra = f" (+{len(process_names)-3} more)" if len(process_names) > 3 else ""
        self.send(
            title   = "⚡ Optimizer: Processes Suspended",
            message = f"Suspended {names}{extra} — {confidence*100:.0f}% bottleneck confidence. Click Undo to restore.",
        )

    def notify_resume(self, process_names: list):
        names = ", ".join(process_names[:3])
        self.send(
            title   = "✅ Optimizer: Processes Resumed",
            message = f"Restored: {names}",
            timeout = 4,
        )

    def notify_boost(self):
        self.send(
            title   = "🚀 One-Click Boost Activated",
            message = "Memory freed and suspended processes resumed.",
            timeout = 4,
        )

    def notify_undo(self):
        self.send(
            title   = "↩ Undo: Processes Restored",
            message = "All optimizer-suspended processes have been resumed.",
            timeout = 4,
        )

    def notify_autopilot(self, message: str):
        self.send(
            title   = "🤖 AI Auto-Pilot Action",
            message = f"AI Optimizer: {message}",
            timeout = 7,
        )

    def notify_warning(self, confidence: float, cpu: float, mem: float):
        self.send(
            title   = "⚠️ Resource Optimizer: Warning",
            message = f"High load predicted — CPU {cpu:.0f}%, MEM {mem:.0f}% in ~30s ({confidence*100:.0f}% confidence)",
        )
