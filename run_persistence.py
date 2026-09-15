#!/usr/bin/env python3
"""
End-to-end: pull real traders, test whether their edge persists.

  export FOMO_API_KEY=...
  python3 run_persistence.py --traders 100 --calls-per-trader 10

Design note on SELECTION BIAS: traders are chosen from a leaderboard window,
then that window is EXCLUDED from the analysis. Testing persistence inside the
same window you selected on conditions the result on the outcome you are trying
to predict. We select on recent performance and test on everything before it.
"""
from __future__ import annotations
import argparse, sys, time, json
from ct.research import Trader, analyse
from ct.research.fomo_client import FomoClient, BudgetExceeded, trade_return, trade_time

NETS = ("solana", "ethereum", "base", "bsc", "robinhood")
ORDERS = ("recent", "pnl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--traders", type=int, default=100)
    ap.add_argument("--calls-per-trader", type=int, default=10)
    ap.add_argument("--window", default="7d")
    ap.add_argument("--budget", type=int, default=250_000)
    ap.add_argument("--min-trades", type=int, default=20)
    ap.add_argument("--exclude-days", type=float, default=7.0,
                    help="drop trades inside the selection window")
    ap.add_argument("--out", default="persistence_result.json")
    a = ap.parse_args()

    try:
        c = FomoClient(budget_credits=a.budget)
    except RuntimeError as e:
        print(f"\n  {e}\n"); sys.exit(1)

    print(f"  pulling leaderboard ({a.window})...")
    rows = c.leaderboard(a.window, limit=a.traders)
    if not rows:
        print("  leaderboard returned nothing — check the key and the window value")
        print(f"  {c.stats()}"); sys.exit(1)
    print(f"  {len(rows)} traders")

    nets = NETS[:max(1, a.calls_per_trader // len(ORDERS))]
    cutoff = time.time() - a.exclude_days * 86400
    traders, skipped = [], 0

    for i, row in enumerate(rows[:a.traders], 1):
        handle = row.get("handle") or row.get("username") or row.get("displayName")
        if not handle:
            continue
        try:
            raw = c.fetch_trades(handle, ORDERS, nets)
        except BudgetExceeded as e:
            print(f"  stopping at trader {i}: {e}"); break
        rets, ts = [], []
        for t in raw:
            r, tm = trade_return(t), trade_time(t)
            if r is None:
                continue
            if tm and tm > cutoff:      # inside selection window -> exclude
                continue
            rets.append(max(r, -0.99)); ts.append(tm)
        if len(rets) >= a.min_trades:
            traders.append(Trader(handle, rets, ts))
        else:
            skipped += 1
        if i % 10 == 0:
            print(f"    {i}/{len(rows)}  usable={len(traders)}  {c.stats()}")

    print(f"\n  usable traders: {len(traders)}   skipped (too few trades): {skipped}")
    print(f"  {c.stats()}")
    if len(traders) < 8:
        print("\n  Not enough usable traders to run the test.\n"); sys.exit(1)

    rep = analyse(traders, min_trades=a.min_trades)
    print(rep.render())
    with open(a.out, "w") as f:
        json.dump({k: v for k, v in rep.__dict__.items()
                   if not k.startswith("_")}, f, indent=2, default=str)
    print(f"\n  written to {a.out}\n")


if __name__ == "__main__":
    main()
