"""Shared helper for turning cryptic OS process names into friendly app names.

Used by both the background service (process list / suspended list serialization)
and the action engine (notification + log messages) so the whole UI stays
consistent. The raw process name is always kept for whitelist matching; this
helper only affects what the user *sees*.
"""
import re

import psutil

# Known cryptic OS process names → friendly app names (lowercase keys).
_FRIENDLY_MAP = {
    "com.apple.webkit.webcontent": "Safari",
    "com.apple.webkit.networking": "Safari",
    "com.apple.webkit.gpu": "Safari",
    "safari": "Safari",
    "google chrome helper": "Chrome",
    "google chrome": "Chrome",
    "chrome": "Chrome",
    "firefox": "Firefox",
    "code helper": "VS Code",
    "code": "VS Code",
    "cursor helper": "Cursor",
    "cursor": "Cursor",
    "electron": "Electron",
    "windowserver": "WindowServer",
    "mds_stores": "Spotlight",
    "mdworker_shared": "Spotlight",
    "spotify": "Spotify",
    "iterm2": "iTerm",
}


def friendly_name(pid, raw: str) -> str:
    """Best-effort mapping from a raw process name to a recognisable app name."""
    name = (raw or "").strip()
    if not name:
        return "—"
    low = name.lower()
    base = low.split(" (")[0].strip()            # drop "(Renderer)" etc.
    if low in _FRIENDLY_MAP:
        return _FRIENDLY_MAP[low]
    if base in _FRIENDLY_MAP:
        return _FRIENDLY_MAP[base]
    # Derive from the .app bundle in the executable path (macOS GUI apps).
    try:
        exe = psutil.Process(pid).exe() or "" if pid else ""
    except (psutil.Error, OSError, Exception):
        exe = ""
    if ".app/" in exe:
        app = exe.split(".app/")[0].split("/")[-1].strip()
        if app:
            return app.title() if app.islower() else app
    # Fallback: strip extension/helper suffixes and tidy up the binary name.
    cleaned = re.sub(r"\.(exe|app|bin)$", "", name, flags=re.I)
    cleaned = re.sub(r"\s*\(.*?\)\s*$", "", cleaned)            # "(GPU)"
    cleaned = re.sub(r"\s*helper.*$", "", cleaned, flags=re.I)  # "Helper ..."
    cleaned = cleaned.strip() or name
    if cleaned.islower():
        cleaned = cleaned.replace("_", " ").replace("-", " ").title()
    return cleaned
