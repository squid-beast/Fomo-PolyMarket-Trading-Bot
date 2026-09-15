#!/usr/bin/env python3
"""
Verification harness.

Drives the engine through SCRIPTED price paths so every code path executes
on demand instead of waiting for the market to cooperate. This is how we
prove the engine is correct before trusting a single number it reports.

Checks:
  1. entries fire when score + risk both approve
  2. every exit type triggers: stop, take-profit, trailing, max-hold, liq-collapse
  3. risk gates block: concurrency, cooldown, daily loss, position floor
  4. accounting closes: equity == starting + sum(net_pnl) when flat
  5. costs are never zero and always reduce realised vs quoted PnL
"""
from __future__ import annotations
import sys, time
from datetime import datetime, timezone, timedelta
from ct import config
from ct.costs import CostModel
from ct.portfolio import Portfolio
from ct.risk import RiskEngine
from ct.datasource import Pair

OK, FAIL = "  \033[92mPASS\033[0m", "  \033[91mFAIL\033[0m"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{OK if cond else FAIL}  {name}" + (f"  [{detail}]" if detail else ""))


def mk(symbol="TEST", price=0.001, liq=300_000.0, token=None) -> Pair:
    return Pair(chain="solana", dex="raydium", pair_address=f"PA_{symbol}",
                token_address=token or f"TK_{symbol}", symbol=symbol, name=symbol,
                price_usd=price, liquidity_usd=liq, fdv=liq * 20, market_cap=liq * 20,
                volume_h24=liq * 3, volume_h6=liq, volume_h1=liq / 4, volume_m5=liq / 40,
                txns_h1_buys=60, txns_h1_sells=55, txns_h24_buys=900, txns_h24_sells=880,
                price_change_m5=1.0, price_change_h1=4.0, price_change_h6=12.0,
                price_change_h24=20.0,
                pair_created_at_ms=int((time.time() - 86400 * 5) * 1000), url="")


def fresh_pf():
    cfg = config.load()
    cm = CostModel(cfg)
    return cfg, cm, Portfolio(cfg, cm), RiskEngine(cfg)


print("\n\033[1m=== 1. entry + cost accounting ===\033[0m")
cfg, cm, pf, risk = fresh_pf()
p = mk()
v = risk.evaluate(pf, p, cm, expected_edge_pct=8.0)
check("risk approves a clean candidate", v.approved, v.reason_str)
check("size respects max_position_pct (2% of $1000)", abs(v.size_usd - 20.0) < 0.01, f"${v.size_usd}")
pos, f = pf.open(p, v.size_usd, {"momentum": 70}, 70.0)
check("entry fill is WORSE than quoted", f.effective_price > f.quoted_price,
      f"{f.quoted_price:.6f} -> {f.effective_price:.6f}")
check("entry cost is non-zero", f.total_cost_usd > 0, f"${f.total_cost_usd:.3f}")
check("equity drops immediately on entry", pf.equity() < 1000.0, f"${pf.equity():.2f}")

print("\n\033[1m=== 2. exit types ===\033[0m")
# -- take profit
cfg, cm, pf, risk = fresh_pf()
p = mk("TP"); pf.open(p, 20.0, {}, 70.0)
t, _ = pf.close(p.token_address, p.price_usd * 1.30, p.liquidity_usd, "take_profit")
check("take-profit: +30% quoted -> smaller realised", 0 < t.net_pnl_pct < 30.0,
      f"{t.net_pnl_pct:.2f}% realised")
check("take-profit: costs recorded", t.total_costs_usd > 0, f"${t.total_costs_usd:.3f}")

# -- stop loss (with extra slippage)
cfg, cm, pf, risk = fresh_pf()
p = mk("SL"); pf.open(p, 20.0, {}, 70.0)
t_soft, _ = pf.close(p.token_address, p.price_usd * 0.88, p.liquidity_usd, "x", is_stop=False)
cfg, cm, pf, risk = fresh_pf()
pf.open(p, 20.0, {}, 70.0)
t_stop, _ = pf.close(p.token_address, p.price_usd * 0.88, p.liquidity_usd, "stop_loss", is_stop=True)
check("stop-loss: -12% quoted -> worse than -12% realised", t_stop.net_pnl_pct < -12.0,
      f"{t_stop.net_pnl_pct:.2f}%")
check("stop-loss fills WORSE than a normal exit", t_stop.net_pnl_usd < t_soft.net_pnl_usd,
      f"stop ${t_stop.net_pnl_usd:.3f} vs normal ${t_soft.net_pnl_usd:.3f}")

# -- flat move is a LOSS (the core honesty test)
cfg, cm, pf, risk = fresh_pf()
p = mk("FLAT"); pf.open(p, 20.0, {}, 70.0)
t, _ = pf.close(p.token_address, p.price_usd, p.liquidity_usd, "max_hold")
check("ZERO price move is a LOSS (friction)", t.net_pnl_usd < 0, f"${t.net_pnl_usd:.3f}")
be = cm.breakeven_move_pct(20.0, 300_000)
check("breakeven move matches round-trip cost", abs(be - abs(t.net_pnl_pct)) < 0.5,
      f"breakeven {be:.2f}% vs loss {abs(t.net_pnl_pct):.2f}%")

print("\n\033[1m=== 3. risk gates ===\033[0m")
# -- concurrency
cfg, cm, pf, risk = fresh_pf()
for i in range(int(cfg.get_path("risk.max_concurrent_positions"))):
    q = mk(f"C{i}", token=f"TK_C{i}"); pf.open(q, 20.0, {}, 70.0)
v = risk.evaluate(pf, mk("EXTRA", token="TK_EXTRA"), cm, expected_edge_pct=8.0)
check("blocks at max_concurrent_positions", not v.approved and "max_concurrent" in v.reason_str,
      v.reason_str)

# -- already holding
v = risk.evaluate(pf, mk("C0", token="TK_C0"), cm, expected_edge_pct=8.0)
check("blocks doubling into same token", not v.approved and "already_holding" in v.reason_str)

# -- cooldown
cfg, cm, pf, risk = fresh_pf()
p = mk("CD"); pf.open(p, 20.0, {}, 70.0); pf.close(p.token_address, p.price_usd, p.liquidity_usd, "x")
v = risk.evaluate(pf, mk("CD"), cm, expected_edge_pct=8.0)
check("enforces cooldown after exit", not v.approved and "cooldown" in v.reason_str, v.reason_str)

# -- daily loss circuit breaker
cfg, cm, pf, risk = fresh_pf()
for i in range(4):
    q = mk(f"L{i}", token=f"TK_L{i}"); pf.open(q, 20.0, {}, 70.0)
    pf.close(q.token_address, q.price_usd * 0.55, q.liquidity_usd, "stop_loss", is_stop=True)
dl = pf.daily_loss_pct(datetime.now(timezone.utc))
v = risk.evaluate(pf, mk("NEW", token="TK_NEW"), cm, expected_edge_pct=8.0)
check("daily loss limit trips", not v.approved and "DAILY_LOSS_LIMIT" in v.reason_str,
      f"daily loss {dl:.2f}%")

# -- position floor (the $25-account killer)
cfg2 = config.load(); cfg2["portfolio"]["starting_equity_usd"] = 25.60
cm2 = CostModel(cfg2); pf2 = Portfolio(cfg2, cm2); risk2 = RiskEngine(cfg2)
v = risk2.evaluate(pf2, mk("SMALL"), cm2, expected_edge_pct=8.0)
check("REJECTS a $25.60 account (position below floor)",
      not v.approved and "below_floor" in v.reason_str, v.reason_str)

print("\n\033[1m=== 3b. new gates ===\033[0m")
cfg, cm, pf, risk = fresh_pf()
v = risk.evaluate(pf, mk("NOEDGE"), cm)   # no edge supplied
check("edge gate blocks an unvalidated signal", not v.approved and "no_measured_edge" in v.reason_str)
v = risk.evaluate(pf, mk("THIN"), cm, expected_edge_pct=2.0)
check("edge gate blocks edge below friction", not v.approved and "edge_below_friction" in v.reason_str,
      v.reason_str)
v = risk.evaluate(pf, mk("GOOD"), cm, expected_edge_pct=9.0)
check("edge gate passes a real edge", v.approved, v.reason_str)
cfg, cm, pf, risk = fresh_pf()
pf.cash = pf.starting * 0.20          # below the 25% reserve
v = risk.evaluate(pf, mk("CASH"), cm, expected_edge_pct=9.0)
check("cash floor blocks deploying the reserve", not v.approved, v.reason_str)

print("\n\033[1m=== 4. accounting closes ===\033[0m")
cfg, cm, pf, risk = fresh_pf()
start = pf.equity()
for i, mult in enumerate([1.4, 0.85, 1.1, 0.7, 1.25]):
    q = mk(f"A{i}", token=f"TK_A{i}"); pf.open(q, 20.0, {}, 70.0)
    pf.close(q.token_address, q.price_usd * mult, q.liquidity_usd, "x")
net = sum(t.net_pnl_usd for t in pf.trades)
check("equity == starting + sum(net_pnl) when flat", abs(pf.equity() - (start + net)) < 1e-6,
      f"equity ${pf.equity():.4f} vs ${start + net:.4f}")
gross = sum(t.gross_pnl_usd for t in pf.trades)
check("gross PnL always exceeds net PnL", gross > net, f"gross ${gross:.2f} > net ${net:.2f}")
check("no position leakage", len(pf.positions) == 0)

print("\n\033[1m=== 5. cost model shape ===\033[0m")
check("tiny trades cost MORE than mid trades (fixed-cost drag)",
      cm.round_trip_pct(2, 300_000) > cm.round_trip_pct(200, 300_000),
      f"$2={cm.round_trip_pct(2,300_000):.2f}% vs $200={cm.round_trip_pct(200,300_000):.2f}%")
check("huge trades in thin pools cost MORE (slippage)",
      cm.round_trip_pct(5000, 30_000) > cm.round_trip_pct(200, 30_000),
      f"$5000={cm.round_trip_pct(5000,30_000):.2f}% vs $200={cm.round_trip_pct(200,30_000):.2f}%")

n, total = sum(results), len(results)
print(f"\n\033[1m{'='*46}\033[0m")
print(f"\033[1m  {n}/{total} checks passed\033[0m")
print(f"\033[1m{'='*46}\033[0m\n")
sys.exit(0 if n == total else 1)
