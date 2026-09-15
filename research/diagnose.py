#!/usr/bin/env python3
"""
Every exit rule lost money. Three possible causes — this distinguishes them.

  A. THE ENTRY SIGNAL IS WORTHLESS   -> signal entries perform no better than
                                        random entry bars on the same tokens.
  B. FRICTION EATS A REAL EDGE       -> gross is positive, net is negative.
  C. THE EXITS ARE WRONG             -> gross positive AND net positive with
                                        some rule we haven't found.

Only one of these is fixable by tuning exits.
"""
import json, random, sys
import numpy as np
sys.path.insert(0, ".")
from research.fit_exits import load_paths, entry_bars, run_trade, evaluate
from ct.costs import CostModel
from ct import config

CM_REAL = CostModel(config.load())


class ZeroCost(CostModel):
    """Same walk, no friction at all — isolates the signal from the costs."""
    def fill(self, side, quoted_price, size_usd, liquidity_usd, is_stop=False):
        f = super().fill(side, quoted_price, size_usd, liquidity_usd, is_stop)
        f.effective_price = quoted_price
        f.total_cost_usd = 0.0
        f.total_cost_pct = 0.0
        return f


def with_cost(cm, paths, R, n=None):
    import research.fit_exits as FE
    old = FE.CM
    FE.CM = cm
    try:
        return evaluate(paths, R)
    finally:
        FE.CM = old


def random_entries(p, k, seed):
    rng = random.Random(seed)
    lo, hi = 180, len(p["c"]) - 40
    if hi <= lo:
        return []
    return sorted(rng.sample(range(lo, hi), min(k, hi - lo)))


def main():
    random.seed(11)
    paths = load_paths()
    for p in paths:
        p["_signal"] = entry_bars(p)
    paths = [p for p in paths if p["_signal"]]

    R = {"stop": 18, "tp": 0, "arm": 15, "trail": 8, "max_hold": 60, "ladder": []}

    print(f"\n  {len(paths)} tokens\n")
    print("  " + "=" * 68)
    print("  TEST A — is the entry signal better than picking bars at random?")
    print("  " + "=" * 68)

    for p in paths:
        p["_entries"] = p["_signal"]
    sig = evaluate(paths, R)

    rnd_runs = []
    for s in range(12):
        for p in paths:
            p["_entries"] = random_entries(p, len(p["_signal"]), seed=s * 97 + hash(p["addr"]) % 1000)
        r = evaluate(paths, R)
        if r:
            rnd_runs.append(r["net_per_trade"])

    rnd = np.array(rnd_runs)
    print(f"\n    signal entries   {sig['net_per_trade']:+.3f} /trade   "
          f"({sig['trades']} trades, win {sig['win_rate']:.1f}%)")
    print(f"    random entries   {rnd.mean():+.3f} /trade   "
          f"(mean of {len(rnd)} runs, sd {rnd.std():.3f})")
    z = (sig["net_per_trade"] - rnd.mean()) / (rnd.std() + 1e-9)
    print(f"    signal is {z:+.2f} standard deviations from random")
    verdict_a = ("SIGNAL ADDS NOTHING" if abs(z) < 1.5
                 else ("signal is BETTER than random" if z > 0 else "signal is WORSE than random"))
    print(f"    -> {verdict_a}")

    print("\n  " + "=" * 68)
    print("  TEST B — is it friction, or is the edge simply not there?")
    print("  " + "=" * 68)
    for p in paths:
        p["_entries"] = p["_signal"]
    net = evaluate(paths, R)
    gross = with_cost(ZeroCost(config.load()), paths, R)
    print(f"\n    GROSS (zero friction)   {gross['net_per_trade']:+.3f} /trade   "
          f"win {gross['win_rate']:.1f}%   PF {gross['profit_factor']:.2f}")
    print(f"    NET   (real friction)   {net['net_per_trade']:+.3f} /trade   "
          f"win {net['win_rate']:.1f}%   PF {net['profit_factor']:.2f}")
    print(f"    friction costs          {gross['net_per_trade']-net['net_per_trade']:.3f} /trade")
    if gross["net_per_trade"] > 0 > net["net_per_trade"]:
        verdict_b = "a real edge exists but friction eats it -> need bigger moves or cheaper execution"
    elif gross["net_per_trade"] <= 0:
        verdict_b = "NO EDGE EVEN BEFORE COSTS -> the entry signal is the problem, not the exits"
    else:
        verdict_b = "edge survives costs"
    print(f"    -> {verdict_b}")

    print("\n  " + "=" * 68)
    print("  TEST C — what does the raw forward return distribution look like?")
    print("  " + "=" * 68)
    fwd = {h: [] for h in (15, 30, 60, 120)}
    for p in paths:
        c = p["c"]
        for t in p["_signal"]:
            for h in fwd:
                if t + h < len(c):
                    fwd[h].append(c[t + h] / c[t] - 1)
    print(f"\n    {'horizon':>9}{'n':>7}{'mean':>9}{'median':>9}{'win%':>8}{'p10':>9}{'p90':>9}")
    print("    " + "-" * 60)
    for h, vals in fwd.items():
        a = np.array(vals) * 100
        if len(a) < 5:
            continue
        print(f"    {h:>7}m{len(a):>7}{a.mean():>+9.2f}{np.median(a):>+9.2f}"
              f"{(a>0).mean()*100:>8.1f}{np.percentile(a,10):>+9.2f}{np.percentile(a,90):>+9.2f}")
    print("\n    A positive mean here with a negative median means a few big winners")
    print("    carry everything — and the exits must be built to keep them.")
    print("    A negative mean means entries are simply bad.\n")


if __name__ == "__main__":
    main()
