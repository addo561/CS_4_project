# =============================================================================
# notifier.py — Cross-platform desktop notifications
# macOS  → osascript (built-in, zero deps, always works)
# Windows → plyer toast (falls back silently if unavailable)
# =============================================================================

import logging
import threading
import os
import sys
import platform
import subprocess

log = logging.getLogger("notifier")

_OS = platform.system()          # "Darwin" | "Windows" | "Linux"
APP_NAME = "System Resource Optimizer"

# ── Windows: try plyer ────────────────────────────────────────────────────────
_PLYER_OK = False
if _OS == "Windows":
    try:
        from plyer import notification as _plyer_notification
        _PLYER_OK = True
    except ImportError:
        log.warning("plyer not installed — Windows toasts disabled.")


from config import BASE_DIR

def _send_macos(title: str, message: str):
    """Use macOS built-in osascript to display notifications instantly."""
    import time as _time
    # Escape double-quotes for AppleScript string literals
    title   = title.replace('"', '\\"')
    message = message.replace('"', '\\"')
    
    # Add a unique microsecond tag to prevent macOS notification coalescing
    # (macOS silently replaces notifications with identical title+message)
    _uid = int(_time.time() * 1000) % 100000
    
    script = (
        f'display notification "{message}" '
        f'with title "{title}" '
        f'subtitle "Event ID: {_uid}" '
        f'sound name "Blow"'
    )
    
    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,   # fully detach from parent process
            close_fds=True,
        )
    except Exception as exc:
        log.warning(f"osascript notification failed: {exc}")


def _send_windows(title: str, message: str, timeout: int):
    if not _PLYER_OK:
        return
    try:
        icon_path = os.path.join(BASE_DIR, "assets", "icon.ico")
        if not os.path.exists(icon_path):
            icon_path = None
        _plyer_notification.notify(
            app_name=APP_NAME,
            title=title,
            message=message,
            timeout=timeout,
            app_icon=icon_path,
        )
    except Exception as exc:
        log.warning(f"Windows toast failed: {exc}")


class Notifier:
    """
    Non-blocking cross-platform notifications.
    macOS  → native osascript (zero deps).
    Windows → plyer toast.
    """

    def __init__(self):
        # Callback hook: set by MainWindow to also show a tray bubble
        self.on_notify = None   # Optional[Callable[[str, str], None]]

    def send(self, title: str, message: str, timeout: int = 6):
        """Fire notification asynchronously so it never blocks the UI."""
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
