#!/usr/bin/env python3
"""Which variable actually dominates the outcome?"""
import os, sys  # PARENT_ON_PATH: these scripts moved into research/ but still
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # import repo-root modules
import montecarlo
from montecarlo import run
orig = montecarlo.path

def patched(rate):
    import math, random
    def path(steps=48, drift=0.0, vol=0.055, seed=None):
        rng = random.Random(seed); p=1.0; out=[]
        for _ in range(steps):
            z = rng.gauss(0,1)
            if rng.random() < 0.04: z *= rng.choice([3.5,-4.5])
            if rng.random() < rate: p *= rng.uniform(0.25,0.6)
            p *= math.exp(drift/steps + vol*z)
            out.append(max(p,1e-9))
        return out
    return path

print(f"\n\033[1m  RUG RATE — the dominant variable  (300 trades, +10% drift, all else fixed)\033[0m")
print(f"  {'rug/step':<11}{'P(rug in hold)':<17}{'gross':>11}{'net':>11}{'win%':>8}")
print("  " + "-" * 58)
for rate in (0.012, 0.008, 0.004, 0.002, 0.0):
    montecarlo.path = patched(rate)
    s = run(300, 0.10, seed=11).stats()
    prug = 1-(1-rate)**48
    print(f"  {rate:<11.3%}{prug:<17.1%}{s['gross_pnl']:>11,.0f}{s['net_pnl']:>11,.0f}{s['win_rate']:>7.1f}%")
montecarlo.path = orig
print()
