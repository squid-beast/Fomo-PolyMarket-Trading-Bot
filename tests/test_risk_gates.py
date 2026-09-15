"""RISK GATES — every path that must refuse to trade."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from conftest import mk_pair
from datetime import datetime, timezone

EDGE = 9.0        # a comfortably passing edge, so other gates are isolated


def test_blocks_without_a_measured_edge(pf, risk, cm):
    v = risk.evaluate(pf, mk_pair(), cm)
    assert not v.approved and "no_measured_edge" in v.reason_str


def test_blocks_edge_below_friction(pf, risk, cm):
    v = risk.evaluate(pf, mk_pair(), cm, expected_edge_pct=2.0)
    assert not v.approved and "edge_below_friction" in v.reason_str


def test_allows_a_real_edge(pf, risk, cm):
    assert risk.evaluate(pf, mk_pair(), cm, expected_edge_pct=EDGE).approved


def test_cash_floor_is_never_breached(pf, risk, cm):
    pf.cash = pf.starting * 0.10
    v = risk.evaluate(pf, mk_pair(), cm, expected_edge_pct=EDGE)
    assert not v.approved


def test_concurrency_cap(pf, risk, cm):
    for i in range(int(risk.max_concurrent)):
        p = mk_pair(f"C{i}", token=f"T{i}")
        pf.open(p, 20.0, {}, 70.0)
    v = risk.evaluate(pf, mk_pair("X", token="TX"), cm, expected_edge_pct=EDGE)
    assert not v.approved and "max_concurrent" in v.reason_str


def test_no_double_position_in_same_token(pf, risk, cm):
    p = mk_pair("DUP")
    pf.open(p, 20.0, {}, 70.0)
    v = risk.evaluate(pf, mk_pair("DUP"), cm, expected_edge_pct=EDGE)
    assert not v.approved and "already_holding" in v.reason_str


def test_cooldown_after_exit(pf, risk, cm):
    p = mk_pair("CD")
    pf.open(p, 20.0, {}, 70.0)
    pf.close(p.token_address, p.price_usd, p.liquidity_usd, "x")
    v = risk.evaluate(pf, mk_pair("CD"), cm, expected_edge_pct=EDGE)
    assert not v.approved and "cooldown" in v.reason_str


def test_daily_loss_circuit_breaker(pf, risk, cm):
    for i in range(5):
        p = mk_pair(f"L{i}", token=f"TL{i}")
        pf.open(p, 20.0, {}, 70.0)
        pf.close(p.token_address, p.price_usd * 0.4, p.liquidity_usd, "stop", is_stop=True)
    v = risk.evaluate(pf, mk_pair("N", token="TN"), cm, expected_edge_pct=EDGE)
    assert not v.approved and "DAILY_LOSS_LIMIT" in v.reason_str


def test_position_floor_blocks_dust_trades(cfg, cm):
    from ct.portfolio import Portfolio
    from ct.risk import RiskEngine
    import copy
    c = type(cfg)(copy.deepcopy(dict(cfg)))
    c["portfolio"]["starting_equity_usd"] = 25.0
    pf2, r2 = Portfolio(c, cm), RiskEngine(c)
    v = r2.evaluate(pf2, mk_pair(), cm, expected_edge_pct=EDGE)
    assert not v.approved and "below_floor" in v.reason_str


def test_friction_ceiling_blocks_illiquid_entries(pf, risk, cm):
    v = risk.evaluate(pf, mk_pair("ILQ", liq=500.0), cm, expected_edge_pct=50.0)
    assert not v.approved


def test_size_never_exceeds_available_cash(pf, risk, cm):
    pf.cash = 18.0
    v = risk.evaluate(pf, mk_pair(), cm, expected_edge_pct=EDGE)
    assert v.size_usd <= pf.cash
