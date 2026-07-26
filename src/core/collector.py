# =============================================================================
# collector.py — Real-time system telemetry collector
# KNUST Final Year Project — Group 4
#
# Usage:
#   python collector.py                    # runs until Ctrl+C
#   python collector.py --duration 3600    # collect for 1 hour then stop
#   python collector.py --label gaming     # tag all rows with a system-state label
#
# Output:
#   data/telemetry_raw.csv  (appended on each run, never overwritten)
# =============================================================================

import argparse
import csv
import logging
import os
import queue
import signal
import sys
import threading
import time
from datetime import datetime

import psutil

# ---------------------------------------------------------------------------
# Ensure project root is on path when run directly
# ---------------------------------------------------------------------------
_PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from config import (
    DATA_DIR, RAW_CSV, POLL_INTERVAL_SEC,
    QUEUE_MAX_SIZE, FLUSH_EVERY_N, TEMP_FALLBACK,
    FEATURE_COLS,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("collector")

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
_stop_event = threading.Event()   # set this to stop all threads cleanly
_sample_queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
_core_count: int = psutil.cpu_count(logical=True) or 1


class ThermalSimulator:
    def __init__(self):
        self.current_temp = 38.0
        self.heat_soak = 0.0

    def get_simulated_temp(self, cpu_usage, mem_usage):
        target = 38.0 + (cpu_usage * 0.45) + (mem_usage * 0.1)
        diff = target - self.current_temp
        self.current_temp += diff * 0.1
        return round(self.current_temp, 1)

_thermal_sim = ThermalSimulator()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_thermal_warnings_logged = set()  # Track what we've warned about

def _get_cpu_temp() -> float:
    """
    Return the average package CPU temperature in °C.
    Returns TEMP_FALLBACK (-1.0) when sensors are unavailable
    (common on VMs, some laptops, and Windows without Open Hardware Monitor).
    """
    if not hasattr(psutil, "sensors_temperatures"):
        return TEMP_FALLBACK
    try:
        temps = psutil.sensors_temperatures()
        if not temps:
            if "no_sensors" not in _thermal_warnings_logged:
                log.warning("⚠️  No temperature sensors detected")
                log.warning("   Thermal data unavailable (common on VMs, MacBooks)")
                log.warning("   Using simulated temperature values for model inputs")
                _thermal_warnings_logged.add("no_sensors")
            return TEMP_FALLBACK

        # Try common sensor keys in priority order
        for key in ("coretemp", "k10temp", "acpitz", "cpu_thermal", "cpu-thermal"):
            if key in temps:
                readings = [t.current for t in temps[key] if t.current is not None]
                if readings:
                    return round(sum(readings) / len(readings), 2)

        # Fallback: average across ALL available sensors
        all_readings = [
            t.current
            for entries in temps.values()
            for t in entries
            if t.current is not None
        ]
        if all_readings:
            return round(sum(all_readings) / len(all_readings), 2)

    except Exception as e:
        if "sensor_error" not in _thermal_warnings_logged:
            log.warning(f"⚠️  Could not read temperature sensors: {e}")
            _thermal_warnings_logged.add("sensor_error")

    return TEMP_FALLBACK


def _collect_sample(label: str) -> dict:
    """
    Poll psutil once and return a flat dict of telemetry features.
    This function must complete well within POLL_INTERVAL_SEC.
    """
    timestamp = datetime.utcnow().isoformat(timespec="milliseconds")

    # ── CPU ───────────────────────────────────────────────────────────────────
    try:
        cpu_pct = psutil.cpu_percent(interval=None)          # non-blocking (needs prior call)
    except Exception as exc:
        log.warning("Could not read CPU percent; using 0.0%%: %s", exc)
        cpu_pct = 0.0
    try:
        per_core = psutil.cpu_percent(interval=None, percpu=True)
    except Exception as exc:
        log.warning("Could not read per-core CPU percent: %s", exc)
        per_core = []
    try:
        freq_info = psutil.cpu_freq()
    except Exception as exc:
        log.warning("Could not read CPU frequency; using 0.0 MHz: %s", exc)
        freq_info = None
    cpu_freq    = round(freq_info.current, 1) if freq_info else 0.0

    # ── Memory ───────────────────────────────────────────────────────────────
    try:
        mem = psutil.virtual_memory()
        mem_used_mb = round(mem.used / 1_048_576, 2)
        mem_available_mb = round(mem.available / 1_048_576, 2)
        mem_percent = float(mem.percent)
    except Exception as exc:
        log.warning("Could not read memory stats; using zeros: %s", exc)
        mem_used_mb = 0.0
        mem_available_mb = 0.0
        mem_percent = 0.0
    try:
        swap = psutil.swap_memory()
        swap_used_mb = round(swap.used / 1_048_576, 2)
        swap_percent = round(swap.percent, 2)
    except Exception as exc:
        log.warning("Could not read swap stats; using zeros: %s", exc)
        swap_used_mb = 0.0
        swap_percent = 0.0

    # ── Temperature ──────────────────────────────────────────────────────────
    real_temp   = _get_cpu_temp()
    if real_temp == TEMP_FALLBACK:
        cpu_temp = _thermal_sim.get_simulated_temp(cpu_pct, mem_percent)
    else:
        cpu_temp = real_temp

    # ── Assemble row ─────────────────────────────────────────────────────────
    row = {
        "timestamp":        timestamp,
        "label":            label,
        "cpu_percent":      round(cpu_pct, 2),
        "cpu_freq_mhz":     cpu_freq,
        "mem_used_mb":      mem_used_mb,
        "mem_available_mb": mem_available_mb,
        "mem_percent":      round(mem_percent, 2),
        "swap_used_mb":     swap_used_mb,
        "swap_percent":     swap_percent,
        "cpu_temp_c":       cpu_temp,
    }

    # ── Per-core CPU (dynamic column count) ──────────────────────────────────
    for i, pct in enumerate(per_core):
        row[f"cpu_core_{i}"] = round(pct, 2)

    return row


def _build_header(sample: dict) -> list:
    """Derive the CSV column order from the first sample collected."""
    fixed = ["timestamp", "label"] + FEATURE_COLS
    core_cols = sorted(
        [k for k in sample if k.startswith("cpu_core_")],
        key=lambda c: int(c.split("_")[-1])
    )
    return fixed + core_cols


# ---------------------------------------------------------------------------
# Producer thread — polls psutil at POLL_INTERVAL_SEC
# ---------------------------------------------------------------------------

def _producer_thread(label: str, duration_sec: float | None):
    """
    Continuously samples telemetry and pushes rows onto _sample_queue.
    Stops when _stop_event is set or duration_sec elapses.
    """
    log.info(f"Producer started | interval={POLL_INTERVAL_SEC}s | label='{label}'")

    # Warm up cpu_percent (first call always returns 0.0)
    psutil.cpu_percent(interval=None)
    psutil.cpu_percent(interval=None, percpu=True)
    time.sleep(POLL_INTERVAL_SEC)

    start_time = time.monotonic()
    sample_count = 0

    while not _stop_event.is_set():
        # Duration guard
        if duration_sec and (time.monotonic() - start_time) >= duration_sec:
            log.info("Collection duration reached. Stopping producer.")
            _stop_event.set()
            break

        tick = time.monotonic()
        try:
            sample = _collect_sample(label)
            _sample_queue.put(sample, block=True, timeout=2.0)
            sample_count += 1

            if sample_count % 60 == 0:
                log.info(
                    f"  {sample_count} samples collected | "
                    f"CPU={sample['cpu_percent']}% | "
                    f"MEM={sample['mem_percent']}% | "
                    f"TEMP={sample['cpu_temp_c']}°C"
                )
        except queue.Full:
            log.warning("Sample queue full — consumer may be lagging. Dropping sample.")
        except Exception as exc:
            log.error(f"Producer error: {exc}", exc_info=True)

        # Sleep for the remainder of the interval (drift-corrected)
        elapsed = time.monotonic() - tick
        sleep_time = max(0.0, POLL_INTERVAL_SEC - elapsed)
        time.sleep(sleep_time)

    log.info(f"Producer stopped after {sample_count} samples.")


# ---------------------------------------------------------------------------
# Consumer thread — drains queue and flushes to CSV
# ---------------------------------------------------------------------------

def _consumer_thread(csv_path: str):
    """
    Drains _sample_queue and writes rows to CSV.
    Flushes every FLUSH_EVERY_N rows for durability.
    Guards operations with robust try-catch blocks for restricted environments.
    """
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception as exc:
        log.error(f"❌  Failed to create directory {DATA_DIR}: {exc}")
        return

    file_exists = os.path.isfile(csv_path)

    header: list | None = None
    buffer: list = []
    total_written = 0

    log.info(f"Consumer started | output={csv_path}")

    try:
        with open(csv_path, mode="a", newline="", encoding="utf-8") as f:
            writer = None  # initialised after first sample (to know full column set)

            while not (_stop_event.is_set() and _sample_queue.empty()):
                try:
                    row = _sample_queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                # Initialise writer on first row
                if writer is None:
                    header = _build_header(row)
                    writer = csv.DictWriter(
                        f, fieldnames=header, extrasaction="ignore"
                    )
                    if not file_exists:
                        try:
                            writer.writeheader()
                            log.info(f"CSV created with {len(header)} columns.")
                        except (PermissionError, IOError) as write_exc:
                            log.error(f"❌  Failed to write CSV header: {write_exc}")
                            raise write_exc

                buffer.append(row)

                if len(buffer) >= FLUSH_EVERY_N:
                    try:
                        writer.writerows(buffer)
                        f.flush()
                        total_written += len(buffer)
                        log.info(f"Flushed {len(buffer)} rows | total={total_written}")
                        buffer.clear()
                    except (PermissionError, IOError) as write_exc:
                        log.error(f"❌  Failed to flush {len(buffer)} rows to CSV: {write_exc}")
                        raise write_exc

            # Final flush
            if writer and buffer:
                try:
                    writer.writerows(buffer)
                    f.flush()
                    total_written += len(buffer)
                    buffer.clear()
                except (PermissionError, IOError) as write_exc:
                    log.error(f"❌  Failed to perform final CSV flush: {write_exc}")
                    raise write_exc

    except PermissionError:
        log.error(f"❌  Permission denied writing to {csv_path}")
        log.error("   Check file/directory permissions or if the file is locked by another process.")
    except IOError as e:
        log.error(f"❌  I/O error writing telemetry to {csv_path}: {e}")
    except Exception as e:
        log.error(f"❌  Unexpected error flushing CSV to {csv_path}: {e}")

    log.info(f"Consumer stopped | total rows written={total_written}")


# ---------------------------------------------------------------------------
# Signal handlers for graceful Ctrl+C / SIGTERM shutdown
# ---------------------------------------------------------------------------

def _handle_signal(signum, frame):
    log.info(f"Signal {signum} received. Shutting down gracefully...")
    _stop_event.set()


signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Collect system telemetry to CSV for GRU model training."
    )
    parser.add_argument(
        "--duration", type=float, default=None,
        help="Stop after this many seconds (omit for indefinite collection)."
    )
    parser.add_argument(
        "--label", type=str, default="untagged",
        choices=["idle", "browsing", "compiling", "gaming", "untagged"],
        help="System-state label to tag all rows in this session."
    )
    parser.add_argument(
        "--output", type=str, default=RAW_CSV,
        help=f"Path to output CSV file (default: {RAW_CSV})."
    )
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("  System Resource Optimizer — Data Collector")
    log.info(f"  Output : {args.output}")
    log.info(f"  Label  : {args.label}")
    log.info(f"  Duration: {'unlimited' if args.duration is None else f'{args.duration}s'}")
    log.info("  Press Ctrl+C to stop cleanly.")
    log.info("=" * 60)

    producer = threading.Thread(
        target=_producer_thread,
        args=(args.label, args.duration),
        name="TelemetryProducer",
        daemon=True,
    )
    consumer = threading.Thread(
        target=_consumer_thread,
        args=(args.output,),
        name="CSVConsumer",
        daemon=True,
    )

    consumer.start()
    producer.start()

    # Block main thread until both finish
    producer.join()
    consumer.join()

    log.info("Collection complete. Exiting.")


if __name__ == "__main__":
    main()
