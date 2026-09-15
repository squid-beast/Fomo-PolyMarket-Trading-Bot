#!/usr/bin/env python3
"""
Free tier = 250,000 credits. A trades call is 250 credits and returns at most
25 trades (paging params are silently ignored; you fan out over orderBy/chain).

  K traders x C calls each,  K*C <= 1,000  and  N = 25*C
  =>  K * N <= 25,000

So: many traders shallow, or few traders deep? Same cost. Not the same power.
"""
import os, sys  # PARENT_ON_PATH: these scripts moved into research/ but still
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # import repo-root modules
import numpy as np, sys
sys.path.insert(0, '.')
from validate_persistence import population
from ct.research import analyse

def detect_rate(runs, k, n, skill_sd, vol=0.40):
    hits = 0
    for s in range(runs):
        pop = population(k, n, skill_sd, vol, np.random.default_rng(7000+s))
        r = analyse(pop, n_sims=700, n_boot=350, seed=s)
        hits += ("NO EVIDENCE" not in r.verdict)
    return hits/runs

ALLOC = [(1000,25),(500,50),(250,100),(125,200),(100,250)]
print("\n" + "="*72)
print("  BUDGET ALLOCATION — all rows cost the SAME 250,000 credits")
print("="*72)
print(f"  {'traders':<10}{'trades ea':<12}{'calls/trader':<15}{'detect 3%':<13}{'detect 2%'}")
print("  " + "-"*62)
best=None
for k,n in ALLOC:
    c = n//25
    d3 = detect_rate(25, k, n, 0.03)
    d2 = detect_rate(25, k, n, 0.02)
    print(f"  {k:<10}{n:<12}{c:<15}{d3*100:>5.1f}%{'':<7}{d2*100:>5.1f}%")
    if best is None or (d2+d3) > best[0]: best=(d2+d3,k,n)
print(f"\n  BEST ALLOCATION: {best[1]} traders x {best[2]} trades each\n")
