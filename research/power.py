#!/usr/bin/env python3
"""
What can the free API tier actually detect? Answer this BEFORE spending credits.

Budget: 250,000 credits/month = ~1,000 API calls.
  1 call  -> leaderboard (a page of traders)
  1-3     -> each trader's trade history (pagination)
So ~100-300 traders is the realistic sample.
"""
import os, sys  # PARENT_ON_PATH: these scripts moved into research/ but still
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # import repo-root modules
import numpy as np
from validate_persistence import population, detects
import sys; sys.path.insert(0, '.')

def rate(runs, k, n, skill_sd, vol=0.40):
    return sum(detects(population(k, n, skill_sd, vol,
               np.random.default_rng(2000+s)), s)[0] for s in range(runs)) / runs

print("\n" + "="*70)
print("  MINIMUM DETECTABLE SKILL  (100 traders x 100 trades = ~200 API calls)")
print("="*70)
print(f"  {'true skill SD':<18}{'detection rate':<18}{'usable?'}")
print("  " + "-"*52)
for s in (0.01, 0.02, 0.03, 0.04, 0.05):
    p = rate(40, 100, 100, s)
    tag = "yes" if p >= 0.8 else ("marginal" if p >= 0.5 else "NO - blind here")
    print(f"  {s*100:>4.0f}% per trade{'':<5}{p*100:>6.1f}%{'':<10}{tag}")

print("\n" + "="*70)
print("  DOES A BIGGER SAMPLE RESCUE A SMALL EDGE?  (skill SD = 2%/trade)")
print("="*70)
print(f"  {'traders':<12}{'trades ea':<13}{'~API calls':<14}{'detection'}")
print("  " + "-"*52)
for k, n in [(100,100),(200,100),(300,150),(300,400),(500,400)]:
    p = rate(30, k, n, 0.02)
    calls = k*2 + 1
    flag = "" if p>=0.8 else ("  <- still blind" if p<0.5 else "")
    print(f"  {k:<12}{n:<13}{calls:<14}{p*100:>5.1f}%{flag}")
print()
