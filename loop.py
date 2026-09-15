#!/usr/bin/env python3
"""
Continuous paper-trading loop.

  python3 loop.py            # run forever
  python3 loop.py --hours 8  # run for 8 hours then stop

Position management runs on a faster cadence than discovery, because exits
are time-critical and discovery is not.
"""
import argparse, time, traceback
from datetime import datetime, timezone, timedelta
from ct.engine import Engine


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=None)
    ap.add_argument("--db", default="paper.db")
    args = ap.parse_args()

    eng = Engine(db_path=args.db)
    scan_every = int(eng.cfg.get_path("run.scan_interval_sec", 300))
    manage_every = int(eng.cfg.get_path("run.manage_interval_sec", 60))
    deadline = (datetime.now(timezone.utc) + timedelta(hours=args.hours)) if args.hours else None

    print(f"paper loop started  scan={scan_every}s manage={manage_every}s "
          f"equity=${eng.pf.equity():.2f}"
          + (f"  until {deadline:%Y-%m-%d %H:%M} UTC" if deadline else "  (ctrl-c to stop)"))

    last_scan = 0.0
    try:
        while True:
            if deadline and datetime.now(timezone.utc) >= deadline:
                print("\ndeadline reached"); break
            now = time.time()
            try:
                if now - last_scan >= scan_every:
                    eng.scan(); last_scan = now
                else:
                    n = eng.manage()
                    if n:
                        eng.store.log_equity(eng.pf); eng.store.commit()
            except Exception:
                traceback.print_exc()
            time.sleep(manage_every)
    except KeyboardInterrupt:
        print("\nstopped by user")

    st = eng.pf.stats()
    print("\n=== final ===")
    for k, v in st.items():
        print(f"  {k:<24} {v}")


if __name__ == "__main__":
    main()
