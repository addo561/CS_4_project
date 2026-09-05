# =============================================================================
# history.py — persistent event store for the System Resource Optimizer
#
# Telemetry is high-rate and append-only, so it is written to flat files. The
# mitigation record is different in kind: it is low-rate, relational and needs
# to be *queried* — "which applications caused the most bottlenecks last
# month?" — so it is kept in SQLite, which ships with Python and requires no
# server, driver or configuration.
# =============================================================================

import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta

try:
    from config import LOCAL_SCALER_DIR
except Exception:                                    # pragma: no cover
    LOCAL_SCALER_DIR = os.path.expanduser("~/.sro_optimizer")

DB_NAME = "sro_history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    REAL    NOT NULL,
    ended_at      REAL,
    platform      TEXT,
    app_version   TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER REFERENCES sessions(id),
    ts            REAL    NOT NULL,      -- unix timestamp
    day           TEXT    NOT NULL,      -- YYYY-MM-DD, for grouping
    month         TEXT    NOT NULL,      -- YYYY-MM,    for grouping
    action        TEXT    NOT NULL,      -- suspend | resume | boost | undo
    trigger       TEXT,                  -- autopilot | manual | watchdog
    process_name  TEXT,
    memory_mb     REAL,
    confidence    REAL,
    cpu_percent   REAL,
    mem_percent   REAL
);

CREATE INDEX IF NOT EXISTS idx_events_month   ON events(month);
CREATE INDEX IF NOT EXISTS idx_events_process ON events(process_name);
CREATE INDEX IF NOT EXISTS idx_events_action  ON events(action);
"""

_lock = threading.Lock()


def _db_path():
    d = LOCAL_SCALER_DIR or os.path.expanduser("~/.sro_optimizer")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, DB_NAME)


def _connect():
    conn = sqlite3.connect(_db_path(), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


class History:
    """Thread-safe recorder for mitigation events."""

    def __init__(self, platform="", version=""):
        self.session_id = None
        try:
            with _lock, _connect() as c:
                c.executescript(SCHEMA)
                cur = c.execute(
                    "INSERT INTO sessions (started_at, platform, app_version) VALUES (?,?,?)",
                    (time.time(), platform, version))
                self.session_id = cur.lastrowid
        except Exception:
            self.session_id = None

    # ── recording ─────────────────────────────────────────────────────────
    def record(self, action, process_name=None, memory_mb=None, confidence=None,
               trigger=None, cpu_percent=None, mem_percent=None):
        """Record one mitigation event. Never raises — history must not be able
        to interrupt protection."""
        try:
            now = time.time()
            dt = datetime.fromtimestamp(now)
            with _lock, _connect() as c:
                c.execute(
                    "INSERT INTO events (session_id, ts, day, month, action, trigger, "
                    "process_name, memory_mb, confidence, cpu_percent, mem_percent) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (self.session_id, now, dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m"),
                     action, trigger, process_name, memory_mb, confidence,
                     cpu_percent, mem_percent))
        except Exception:
            pass

    def record_many(self, action, names, confidence=None, trigger=None,
                    cpu_percent=None, mem_percent=None):
        for n in names or []:
            name, mem = _split_name(n)
            self.record(action, process_name=name, memory_mb=mem,
                        confidence=confidence, trigger=trigger,
                        cpu_percent=cpu_percent, mem_percent=mem_percent)

    def close(self):
        try:
            with _lock, _connect() as c:
                c.execute("UPDATE sessions SET ended_at=? WHERE id=?",
                          (time.time(), self.session_id))
        except Exception:
            pass


def _split_name(label):
    """'WhatsApp (301.6MB)' -> ('WhatsApp', 301.6)"""
    if not label:
        return (None, None)
    name, mem = str(label), None
    if "(" in name and name.rstrip().endswith(")"):
        head, _, tail = name.rpartition("(")
        tail = tail.rstrip(")").strip().upper()
        try:
            if tail.endswith("GB"):
                mem = float(tail[:-2]) * 1024
            elif tail.endswith("MB"):
                mem = float(tail[:-2])
            name = head.strip()
        except ValueError:
            pass
    return (name, mem)


# ── queries used by the report generator ──────────────────────────────────
def _rows(sql, params=()):
    try:
        with _lock, _connect() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]
    except Exception:
        return []


def summary(since_days=30):
    cutoff = time.time() - since_days * 86400
    r = _rows("SELECT COUNT(*) n, COUNT(DISTINCT process_name) apps, "
              "COUNT(DISTINCT day) days, AVG(confidence) conf, SUM(memory_mb) mem "
              "FROM events WHERE ts >= ? AND action='suspend'", (cutoff,))
    return r[0] if r else {}


def top_offenders(since_days=30, limit=10):
    cutoff = time.time() - since_days * 86400
    return _rows(
        "SELECT process_name, COUNT(*) events, "
        "ROUND(AVG(memory_mb),1) avg_mb, ROUND(AVG(confidence)*100,1) avg_conf, "
        "MAX(ts) last_seen "
        "FROM events WHERE ts >= ? AND action='suspend' AND process_name IS NOT NULL "
        "GROUP BY process_name ORDER BY events DESC, avg_mb DESC LIMIT ?",
        (cutoff, limit))


def by_day(since_days=30):
    cutoff = time.time() - since_days * 86400
    return _rows("SELECT day, COUNT(*) events FROM events "
                 "WHERE ts >= ? AND action='suspend' GROUP BY day ORDER BY day", (cutoff,))


def by_month(limit=12):
    return _rows("SELECT month, COUNT(*) events, COUNT(DISTINCT process_name) apps "
                 "FROM events WHERE action='suspend' GROUP BY month "
                 "ORDER BY month DESC LIMIT ?", (limit,))


def by_action(since_days=30):
    cutoff = time.time() - since_days * 86400
    return _rows("SELECT action, COUNT(*) n FROM events WHERE ts >= ? "
                 "GROUP BY action ORDER BY n DESC", (cutoff,))


def recent(limit=25):
    return _rows("SELECT ts, action, trigger, process_name, memory_mb, confidence "
                 "FROM events ORDER BY ts DESC LIMIT ?", (limit,))


def sessions(limit=10):
    return _rows("SELECT id, started_at, ended_at, platform, app_version "
                 "FROM sessions ORDER BY id DESC LIMIT ?", (limit,))
