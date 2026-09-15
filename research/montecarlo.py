#!/usr/bin/env python3
"""
Monte Carlo through the REAL engine components.

Not a toy: uses the actual CostModel, Portfolio, RiskEngine and exit rules.
Only the price PATHS are synthetic. Each path is a random walk with fat tails
and the negative skew that characterises this asset class; the engine's own
stop / take-profit / trailing / max-hold logic decides every outcome.

Purpose: measure how much friction costs over a realistic number of trades,
and find out what raw edge is required before the system nets positive.

  python3 montecarlo.py --trades 200 --edge 0.0
"""
from __future__ import annotations
import os, sys  # PARENT_ON_PATH: these scripts moved into research/ but still
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # import repo-root modules
import argparse, math, random
from datetime import datetime, timezone, timedelta
from ct import config
from ct.costs import CostModel
from ct.portfolio import Portfolio
from ct.store import Store
from ct.datasource import Pair


def path(steps=48, drift=0.0, vol=0.055, seed=None):
    """Random walk with fat tails + negative skew (rug risk)."""
    rng = random.Random(seed)
    p, out = 1.0, []
    for _ in range(steps):
        z = rng.gauss(0, 1)
        if rng.random() < 0.04:            # fat tail
            z *= rng.choice([3.5, -4.5])
        if rng.random() < 0.012:           # rug event
            p *= rng.uniform(0.25, 0.6)
        p *= math.exp(drift / steps + vol * z)
        out.append(max(p, 1e-9))
    return out


def mk(sym, price, liq):
    import time
    return Pair(chain="solana", dex="raydium", pair_address=f"PA{sym}", token_address=f"TK{sym}",
                symbol=sym, name=sym, price_usd=price, liquidity_usd=liq, fdv=liq*20,
                market_cap=liq*20, volume_h24=liq*3, volume_h6=liq, volume_h1=liq/4,
                volume_m5=liq/40, txns_h1_buys=60, txns_h1_sells=55, txns_h24_buys=900,
                txns_h24_sells=880, price_change_m5=1.0, price_change_h1=4.0,
                price_change_h6=12.0, price_change_h24=20.0,
                pair_created_at_ms=int((time.time()-86400*5)*1000), url="")


def run(n_trades=200, edge=0.0, seed=7, db_path=None, verbose=True):
    cfg = config.load()
    cm = CostModel(cfg); pf = Portfolio(cfg, cm)
    e = cfg.get_path("exits", {})
    stop, tp = float(e["stop_loss_pct"]), float(e["take_profit_pct"])
    trail, arm = float(e["trailing_stop_pct"]), float(e["trail_arm_pct"])
    size = pf.starting * float(cfg.get_path("risk.max_position_pct")) / 100.0
    store = Store(db_path) if db_path else None
    rng = random.Random(seed)
    t0 = datetime.now(timezone.utc) - timedelta(days=30)

    for i in range(n_trades):
        liq = rng.choice([60_000, 120_000, 300_000, 700_000, 1_200_000])
        entry = rng.uniform(0.0004, 0.02)
        pr = mk(f"S{i:03d}", entry, liq)
        size_i = min(size, pf.cash)
        if size_i < float(cfg.get_path("risk.min_position_usd")):
            break
        pos, fill = pf.open(pr, size_i, {"sim": 70}, 70.0)
        pos.opened_at = t0 + timedelta(hours=i * 3)
        if store: store.log_fill(pr.symbol, pr.token_address, fill)

        series = path(48, drift=edge, seed=rng.randrange(1 << 30))
        reason, px, is_stop = "max_hold", entry * series[-1], False
        hw = entry
        for step, m in enumerate(series):
            cur = entry * m
            hw = max(hw, cur)
            pnl = (cur / entry - 1) * 100
            peak = (hw / entry - 1) * 100
            dfp = (hw - cur) / hw * 100 if hw else 0
            if pnl <= -stop:
                reason, px, is_stop = f"stop_loss({pnl:.1f}%)", cur, True; break
            if peak >= arm and dfp >= trail:
                reason, px = f"trailing_stop(peak+{peak:.0f}%)", cur; break
            if pnl >= tp:
                reason, px = f"take_profit(+{pnl:.1f}%)", cur; break
        t, xf = pf.close(pr.token_address, px, liq, reason, is_stop=is_stop)
        t.opened_at = pos.opened_at
        t.closed_at = pos.opened_at + timedelta(hours=2)
        if store:
            store.log_fill(t.symbol, t.token_address, xf); store.log_trade(t)
            sid = store.start_scan()
            store.finish_scan(sid, universe=rng.randint(240, 320), passed_safety=rng.randint(2, 9),
                              scored=rng.randint(0, 3), entries=1, exits=1, equity=pf.equity())
            store.log_equity(pf)

    if store: store.commit()
    return pf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", type=int, default=200)
    ap.add_argument("--edge", type=float, default=0.0, help="raw drift per path (0 = no edge)")
    ap.add_argument("--db", default=None)
    ap.add_argument("--sweep", action="store_true")
    a = ap.parse_args()

    if a.sweep:
        print(f"\n  {'raw edge':<12}{'gross P&L':>12}{'costs':>11}{'net P&L':>11}"
              f"{'return':>10}{'win rate':>10}")
        print("  " + "-" * 66)
        for edge in (0.0, 0.05, 0.10, 0.15, 0.20, 0.30):
            pf = run(a.trades, edge, seed=7)
            s = pf.stats()
            print(f"  {edge*100:>5.0f}%/path{'':<3}{s['gross_pnl']:>12,.2f}"
                  f"{s['total_costs']:>11,.2f}{s['net_pnl']:>11,.2f}"
                  f"{s['return_pct']:>9.2f}%{s['win_rate']:>9.1f}%")
        print()
        return

    pf = run(a.trades, a.edge, db_path=a.db)
    s = pf.stats()
    print()
    for k, v in s.items():
        print(f"  {k:<24} {v}")
    print()


if __name__ == "__main__":
    main()
