#!/usr/bin/env python3
"""
VALIDATE THE TEST ITSELF, on populations where the truth is known.

A persistence test you haven't calibrated can tell you anything. Before this
touches real trader data it has to clear two bars:

  - Under PURE LUCK it must say "no skill" (false positives near 5%).
  - Under REAL SKILL it must say "skill" often enough to be worth running.

Anything that fails these is not measuring what it claims to measure.
"""
from __future__ import annotations
import numpy as np
from ct.research import Trader, analyse

RNG = np.random.default_rng(42)


def population(k_traders, n_trades, skill_sd, vol=0.40, rng=RNG):
    """k traders. skill_sd=0 means every trader is identical (pure luck)."""
    true_edges = rng.normal(0.0, skill_sd, k_traders)
    out = []
    for i, mu in enumerate(true_edges):
        n = max(8, int(rng.normal(n_trades, n_trades * 0.25)))
        logs = rng.normal(mu, vol, n)
        out.append(Trader(f"t{i}", (np.expm1(logs)).tolist(),
                          list(range(n))))
    return out


def detects(traders, seed):
    r = analyse(traders, n_sims=800, n_boot=400, seed=seed)
    return ("NO EVIDENCE" not in r.verdict), r


def rate(runs, k, n, skill_sd, vol=0.40):
    hits = 0
    for s in range(runs):
        pop = population(k, n, skill_sd, vol, np.random.default_rng(1000 + s))
        d, _ = detects(pop, s)
        hits += d
    return hits / runs


def main():
    print("\n" + "=" * 68)
    print("  VALIDATION 1 - FALSE POSITIVES  (truth: every trader identical)")
    print("=" * 68)
    print(f"  {'traders':<10}{'trades ea':<12}{'flagged as skilled':<22}{'verdict'}")
    print("  " + "-" * 62)
    for k, n in [(30, 60), (60, 60), (100, 100), (150, 200)]:
        fp = rate(40, k, n, skill_sd=0.0)
        ok = "OK" if fp <= 0.15 else "TOO HIGH"
        print(f"  {k:<10}{n:<12}{fp*100:>6.1f}%{'':<15}{ok}")

    print("\n" + "=" * 68)
    print("  VALIDATION 2 - POWER  (truth: real skill dispersion exists)")
    print("=" * 68)
    print(f"  {'skill SD':<12}{'traders':<10}{'trades ea':<12}{'detected':<12}{'note'}")
    print("  " + "-" * 62)
    for skill, k, n in [(0.02, 100, 100), (0.05, 100, 100), (0.10, 100, 100),
                        (0.05, 100, 300), (0.05, 300, 100), (0.10, 300, 300)]:
        p = rate(40, k, n, skill_sd=skill)
        note = "usable" if p >= 0.8 else ("weak" if p >= 0.4 else "BLIND")
        print(f"  {skill*100:>4.0f}%/trade{'':<3}{k:<10}{n:<12}{p*100:>5.1f}%{'':<6}{note}")
    print()


if __name__ == "__main__":
    main()
