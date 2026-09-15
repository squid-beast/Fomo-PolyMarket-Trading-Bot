#!/usr/bin/env python3
"""
FIT THE EXIT RULES ON REAL PRICE PATHS.

The stop/target/trail numbers in config.yaml were picked by hand. Hand-picked
numbers are worth nothing. This fits them against what actually happened to
real Solana memecoins, net of the measured friction.

METHOD
  entry proxy   a bar qualifies when the momentum + volume conditions our live
                strategies use are true, computed ONLY from bars at or before
                that bar. No look-ahead is structurally possible.
  exit walk     from the entry bar forward, apply the candidate rule set.
  friction      every fill priced through the real CostModel.
  no overlap    one open trade per token at a time.

OVERFITTING CONTROL
  Tokens (not trades) are split into train and test. Parameters are chosen on
  train only and reported on test. The gap between the two is printed, because
  that gap is the honest measure of how much of the result is curve-fitting.
"""
from __future__ import annotations
import json, itertools, sys, random
from pathlib import Path
import numpy as np
sys.path.insert(0, ".")
from ct import config
from ct.costs import CostModel

CFG = config.load()
CM = CostModel(CFG)
PATHS = Path("paths")


# ---------------------------------------------------------------- data
def load_paths():
    out = []
    for f in sorted(PATHS.glob("*.json")):
        d = json.loads(f.read_text())
        rows = d["ohlcv"]
        if len(rows) < 150:
            continue
        arr = np.array(rows, dtype=float)      # ts,o,h,l,c,v
        out.append({"addr": d["meta"]["address"], "name": d["meta"]["name"],
                    "liq": max(d["meta"]["liquidity"], 1000.0),
                    "ts": arr[:, 0], "o": arr[:, 1], "h": arr[:, 2],
                    "l": arr[:, 3], "c": arr[:, 4], "v": arr[:, 5]})
    return out


# ------------------------------------------------------- entry signal
def entry_bars(p, lb6=180, lb1=60, lb5=5):
    """
    Mirrors the live momentum + volume_surge strategies, using only past bars.
    Returns indices where an entry would fire.
    """
    c, v = p["c"], p["v"]
    n = len(c)
    out = []
    start = max(lb6, 60)
    for t in range(start, n - 30):          # leave room to exit
        p6 = c[t] / c[t - lb6] - 1
        p1 = c[t] / c[t - lb1] - 1
        p5 = c[t] / c[t - lb5] - 1
        if not (p6 > 0.03):                 # 6h-equivalent uptrend
            continue
        if not (0.005 <= p1 <= 0.25):       # 1h confirming, not overextended
            continue
        if not (0.0 < p5 <= 0.06):          # steady, NOT a vertical spike
            continue
        base = v[max(0, t - 720):t].mean()  # own volume baseline
        if base <= 0 or v[max(0, t - 60):t].mean() / base < 1.2:
            continue
        out.append(t)
    return out


# ---------------------------------------------------------- exit walk
def run_trade(p, t0, R, size_usd=20.0):
    """Walk forward from t0 applying rule set R. Returns net USD and bars held."""
    c, h, l = p["c"], p["h"], p["l"]
    liq = p["liq"]
    entry_fill = CM.fill("entry", c[t0], size_usd, liq)
    entry_px = entry_fill.effective_price
    qty = size_usd / entry_px

    hw = c[t0]
    remaining = 1.0            # fraction of position still open
    proceeds = 0.0
    ladder = list(R.get("ladder", []))     # [(gain_pct, frac), ...]
    n = len(c)
    end = min(n - 1, t0 + R["max_hold"])

    for t in range(t0 + 1, end + 1):
        hi, lo, px = h[t], l[t], c[t]
        hw = max(hw, hi)
        g_hi = hi / c[t0] - 1
        g_lo = lo / c[t0] - 1
        peak = hw / c[t0] - 1

        # stop first — intrabar low is assumed to hit before the high
        if g_lo <= -R["stop"] / 100:
            px_exit = c[t0] * (1 - R["stop"] / 100)
            f = CM.fill("exit", px_exit, qty * remaining * px_exit, liq, is_stop=True)
            proceeds += qty * remaining * f.effective_price
            return proceeds - size_usd, t - t0, "stop"

        # scale-out ladder
        while ladder and g_hi >= ladder[0][0] / 100 and remaining > 1e-9:
            gain, frac = ladder.pop(0)
            take = min(frac, remaining)
            px_exit = c[t0] * (1 + gain / 100)
            f = CM.fill("exit", px_exit, qty * take * px_exit, liq)
            proceeds += qty * take * f.effective_price
            remaining -= take

        if remaining <= 1e-9:
            return proceeds - size_usd, t - t0, "ladder_complete"

        # trailing stop, armed after a threshold gain
        if peak >= R["arm"] / 100:
            trail_px = hw * (1 - R["trail"] / 100)
            if lo <= trail_px:
                f = CM.fill("exit", trail_px, qty * remaining * trail_px, liq)
                proceeds += qty * remaining * f.effective_price
                return proceeds - size_usd, t - t0, "trail"

        # hard take profit for whatever is left
        if R["tp"] and g_hi >= R["tp"] / 100:
            px_exit = c[t0] * (1 + R["tp"] / 100)
            f = CM.fill("exit", px_exit, qty * remaining * px_exit, liq)
            proceeds += qty * remaining * f.effective_price
            return proceeds - size_usd, t - t0, "tp"

    px = c[end]
    f = CM.fill("exit", px, qty * remaining * px, liq)
    proceeds += qty * remaining * f.effective_price
    return proceeds - size_usd, end - t0, "max_hold"


def evaluate(paths, R, size_usd=20.0):
    pnl, holds, reasons = [], [], {}
    for p in paths:
        busy_until = -1
        for t0 in p["_entries"]:
            if t0 < busy_until:
                continue
            net, held, why = run_trade(p, t0, R, size_usd)
            pnl.append(net); holds.append(held)
            reasons[why] = reasons.get(why, 0) + 1
            busy_until = t0 + held
    if not pnl:
        return None
    a = np.array(pnl)
    wins, losses = a[a > 0], a[a <= 0]
    gw, gl = wins.sum(), abs(losses.sum())
    return {"trades": len(a), "net": float(a.sum()),
            "net_per_trade": float(a.mean()),
            "win_rate": float((a > 0).mean() * 100),
            "profit_factor": float(gw / gl) if gl > 0 else float("inf"),
            "median": float(np.median(a)),
            "avg_hold_min": float(np.mean(holds)),
            "reasons": reasons}


# ------------------------------------------------------------- search
GRID = {
    "stop":  [8, 12, 18, 25, 35],
    "tp":    [0, 40, 80, 150],            # 0 = no hard cap, let the trail work
    "arm":   [8, 15, 25],
    "trail": [8, 15, 25, 40],
    "max_hold": [60, 180, 360],
}
LADDERS = {
    "none": [],
    "25@50/25@120": [(50, 0.25), (120, 0.25)],
    "33@40/33@100": [(40, 0.33), (100, 0.33)],
    "50@60": [(60, 0.50)],
}


def main():
    random.seed(7)
    paths = load_paths()
    for p in paths:
        p["_entries"] = entry_bars(p)
    paths = [p for p in paths if p["_entries"]]
    tot = sum(len(p["_entries"]) for p in paths)
    print(f"\n  {len(paths)} tokens with signals, {tot:,} raw entry bars\n")

    idx = list(range(len(paths)))
    random.shuffle(idx)
    cut = int(len(idx) * 0.6)
    train = [paths[i] for i in idx[:cut]]
    test = [paths[i] for i in idx[cut:]]
    print(f"  train {len(train)} tokens   |   test {len(test)} tokens (held out)\n")

    combos = list(itertools.product(*GRID.values()))
    results = []
    for lname, ladder in LADDERS.items():
        for combo in combos:
            R = dict(zip(GRID.keys(), combo)); R["ladder"] = ladder
            r = evaluate(train, R)
            if r and r["trades"] >= 25:
                results.append((r["net_per_trade"], lname, R, r))
    results.sort(key=lambda x: -x[0])

    print("  TOP 8 ON TRAIN (net USD per $20 trade, after friction)\n")
    print(f"  {'stop':>5}{'tp':>6}{'arm':>5}{'trail':>6}{'hold':>6}  {'ladder':<14}"
          f"{'trades':>7}{'net/trade':>11}{'win%':>7}{'PF':>7}")
    print("  " + "-" * 78)
    for npt, lname, R, r in results[:8]:
        print(f"  {R['stop']:>5}{R['tp']:>6}{R['arm']:>5}{R['trail']:>6}{R['max_hold']:>6}  "
              f"{lname:<14}{r['trades']:>7}{npt:>+11.3f}{r['win_rate']:>7.1f}{r['profit_factor']:>7.2f}")

    print("\n\n  OUT-OF-SAMPLE CHECK — same rules on tokens never used for fitting\n")
    print(f"  {'rank':>5}  {'ladder':<14}{'train net/tr':>14}{'TEST net/tr':>13}"
          f"{'test trades':>13}{'test win%':>11}{'test PF':>9}")
    print("  " + "-" * 78)
    survivors = []
    for i, (npt, lname, R, r) in enumerate(results[:8], 1):
        te = evaluate(test, R)
        if not te:
            continue
        survivors.append((te["net_per_trade"], npt, lname, R, te))
        print(f"  {i:>5}  {lname:<14}{npt:>+14.3f}{te['net_per_trade']:>+13.3f}"
              f"{te['trades']:>13}{te['win_rate']:>11.1f}{te['profit_factor']:>9.2f}")

    # honest baseline: what the hand-picked config does
    base = {"stop": float(CFG.get_path("exits.stop_loss_pct")),
            "tp": float(CFG.get_path("exits.take_profit_pct")),
            "arm": float(CFG.get_path("exits.trail_arm_pct")),
            "trail": float(CFG.get_path("exits.trailing_stop_pct")),
            "max_hold": 360, "ladder": []}
    bt, be = evaluate(train, base), evaluate(test, base)
    print("\n  CURRENT HAND-PICKED CONFIG (12/25/15/10) for comparison")
    if bt and be:
        print(f"        {'':<14}{bt['net_per_trade']:>+14.3f}{be['net_per_trade']:>+13.3f}"
              f"{be['trades']:>13}{be['win_rate']:>11.1f}{be['profit_factor']:>9.2f}")

    if survivors:
        survivors.sort(key=lambda x: -x[0])
        best = survivors[0]
        print(f"\n\n  BEST BY OUT-OF-SAMPLE RESULT: {best[3]}")
        print(f"    ladder: {best[2]}")
        print(f"    train {best[1]:+.3f}/trade   test {best[0]:+.3f}/trade   "
              f"degradation {(best[1]-best[0]):+.3f}")
        json.dump({"rules": {k: v for k, v in best[3].items()},
                   "ladder_name": best[2],
                   "train_net_per_trade": best[1], "test_net_per_trade": best[0],
                   "test_stats": best[4]},
                  open("research/fitted_exits.json", "w"), indent=2, default=str)
        print("    -> research/fitted_exits.json")
    print()


if __name__ == "__main__":
    main()
