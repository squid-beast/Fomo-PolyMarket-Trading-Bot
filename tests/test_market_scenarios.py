"""MARKET SCENARIOS — what the market does to an open position."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from conftest import mk_pair


def test_rug_liquidity_to_zero_does_not_crash(cm, pf):
    p = mk_pair("RUG", liq=200_000)
    pf.open(p, 20.0, {}, 70.0)
    t, f = pf.close(p.token_address, p.price_usd * 0.05, 0.0, "liquidity_collapse", is_stop=True)
    assert t.net_pnl_usd < 0
    assert f.effective_price >= 0          # never negative, never NaN


def test_price_to_zero_is_total_loss_not_an_exception(cm, pf):
    p = mk_pair("DEAD")
    pf.open(p, 20.0, {}, 70.0)
    t, _ = pf.close(p.token_address, 1e-18, 1000.0, "worthless")
    assert -20.01 <= t.net_pnl_usd <= -19.0


def test_zero_liquidity_slippage_is_capped_at_total(cm):
    assert cm.slippage_frac(100.0, 0.0) == 1.0


def test_negative_liquidity_does_not_produce_negative_cost(cm):
    f = cm.fill("exit", 1.0, 100.0, -5000.0)
    assert f.total_cost_usd >= 0 and f.effective_price >= 0


def test_huge_order_in_tiny_pool_is_flagged_not_silently_filled(cm):
    rt = cm.round_trip_pct(50_000, 10_000)
    assert rt > 50, "a 5x-pool-size order must show catastrophic friction"


def test_safety_rejects_liquidity_collapse_candidate(safety):
    assert not safety.check(mk_pair("THIN", liq=1_000)).passed


def test_safety_rejects_wash_trading(safety):
    p = mk_pair("WASH", liq=50_000, vol24=50_000 * 200)
    r = safety.check(p)
    assert not r.passed and any("wash" in x for x in r.reasons)


def test_safety_rejects_one_sided_selling(safety):
    p = mk_pair("DUMP", h1_buys=10, h1_sells=200)
    r = safety.check(p)
    assert not r.passed


def test_safety_rejects_brand_new_pair(safety):
    assert not safety.check(mk_pair("NEW", age_days=0.001)).passed


def test_stale_or_absent_price_rejected(safety):
    assert not safety.check(mk_pair("NOPX", price=0.0)).passed


def test_rolling_over_after_a_spike_is_rejected(safety):
    """Strong 1h run now fading on the 5m — the JACKCAT pattern."""
    p = mk_pair("FADE", h1=45.0, m5=-8.0)
    r = safety.check(p)
    assert not r.passed and any("rolling_over" in x for x in r.reasons)


def test_gain_then_full_round_trip_nets_less_than_gross(cm, pf):
    p = mk_pair("G")
    pf.open(p, 20.0, {}, 70.0)
    t, _ = pf.close(p.token_address, p.price_usd * 2.0, p.liquidity_usd, "tp")
    assert t.net_pnl_usd < t.gross_pnl_usd
