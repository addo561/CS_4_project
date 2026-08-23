#!/usr/bin/env python3
"""
demo_stress.py — Live-demo load generator for the System Resource Optimizer.

Spikes CPU (and optionally RAM) so the SRO's GRU model predicts a bottleneck
and the ActionEngine mitigates it — perfect for a defense walkthrough.

Usage:
    python demo_stress.py                 # CPU spike on all-but-one core for 45s
    python demo_stress.py --secs 60       # run for 60 seconds
    python demo_stress.py --cores 4       # use 4 worker cores
    python demo_stress.py --ram 2         # ALSO hold ~2 GB of RAM
    python demo_stress.py --ram 2 --secs 60

Stop early at any time with Ctrl+C (load is released immediately).
"""
import argparse, multiprocessing as mp, os, time, sys


def _burn(stop_at):
    x = 0.0001
    while time.time() < stop_at:
        # tight floating-point loop = sustained 100% on this core
        for _ in range(500_000):
            x = (x * 1.0000001 + 1.0000001) ** 0.5
    return x


def main():
    ap = argparse.ArgumentParser(description="SRO live-demo load generator")
    ap.add_argument("--secs", type=int, default=45, help="seconds to run (default 45)")
    ap.add_argument("--cores", type=int, default=max(1, (os.cpu_count() or 2) - 1),
                    help="CPU worker processes (default: all-but-one core)")
    ap.add_argument("--ram", type=float, default=0.0,
                    help="gigabytes of RAM to hold during the run (default 0 = none)")
    args = ap.parse_args()

    stop_at = time.time() + args.secs
    print(f"⚡ Stressing {args.cores} core(s) for {args.secs}s"
          + (f"  +  holding ~{args.ram:g} GB RAM" if args.ram else "")
          + " …  (Ctrl+C to stop)")

    # Optional RAM pressure: allocate and touch memory so it is resident.
    ballast = None
    if args.ram > 0:
        try:
            ballast = bytearray(int(args.ram * 1024 * 1024 * 1024))
            for i in range(0, len(ballast), 4096):   # touch each page
                ballast[i] = 1
        except MemoryError:
            print("  (couldn't allocate that much RAM — continuing CPU-only)")
            ballast = None

    procs = [mp.Process(target=_burn, args=(stop_at,)) for _ in range(args.cores)]
    try:
        for p in procs:
            p.start()
        for p in procs:
            p.join()
    except KeyboardInterrupt:
        print("\n⏹  Stopping early — releasing load.")
        for p in procs:
            p.terminate()
    finally:
        ballast = None
        print("✅ Load released. Watch the dashboard recover (Cooldown phase).")


if __name__ == "__main__":
    mp.freeze_support()
    main()
