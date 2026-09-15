#!/usr/bin/env python3
"""Parameter sensitivity. This is what paper trading is FOR."""
import os, sys  # PARENT_ON_PATH: these scripts moved into research/ but still
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # import repo-root modules
import copy
from ct import config
from montecarlo import run
from ct.costs import CostModel
from ct.portfolio import Portfolio

base = config.load()

def variant(**over):
    c = config.Config(copy.deepcopy(dict(base)))
    for k, v in over.items():
        sec, key = k.split(".")
        c[sec][key] = v
    return c

import montecarlo
def run_with(cfg, n=300, edge=0.10, seed=11):
    orig = config.load
    config.load = lambda *a, **k: cfg
    montecarlo.config.load = lambda *a, **k: cfg
    try:
        return run(n, edge, seed=seed).stats()
    finally:
        config.load = orig
        montecarlo.config.load = orig

print("\n\033[1m  STOP WIDTH vs VOLATILITY  (300 trades, +10% raw drift)\033[0m")
print(f"  {'stop':<9}{'take profit':<13}{'gross':>11}{'costs':>10}{'net':>11}{'win%':>8}{'trades':>8}")
print("  " + "-" * 70)
for stop, tp in [(12,25),(20,40),(30,60),(45,90),(60,120)]:
    s = run_with(variant(**{"exits.stop_loss_pct":stop,"exits.take_profit_pct":tp,
                            "exits.trail_arm_pct":tp*0.6,"exits.trailing_stop_pct":stop*0.8}))
    print(f"  {stop:>3}%{'':<5}{tp:>4}%{'':<8}{s['gross_pnl']:>11,.0f}{s['total_costs']:>10,.0f}"
          f"{s['net_pnl']:>11,.0f}{s['win_rate']:>7.1f}%{s['trades']:>8}")

print("\n\033[1m  POSITION SIZE vs FIXED-COST DRAG  (300 trades, +10% drift, 30%/60% exits)\033[0m")
print(f"  {'size':<10}{'gross':>11}{'costs':>10}{'cost/trade':>12}{'net':>11}{'drag%':>9}")
print("  " + "-" * 64)
for pct in (0.5, 1.0, 2.0, 5.0, 10.0):
    s = run_with(variant(**{"risk.max_position_pct":pct,"risk.min_position_usd":1.0,
                            "exits.stop_loss_pct":30,"exits.take_profit_pct":60,
                            "exits.trail_arm_pct":36,"exits.trailing_stop_pct":24}))
    cpt = s['total_costs']/s['trades'] if s['trades'] else 0
    drag = s['total_costs']/abs(s['gross_pnl'])*100 if s['gross_pnl'] else 0
    print(f"  {pct:>4.1f}% (${pct*10:.0f}){'':<1}{s['gross_pnl']:>11,.0f}{s['total_costs']:>10,.0f}"
          f"{cpt:>12,.2f}{s['net_pnl']:>11,.0f}{drag:>8.0f}%")
print()
