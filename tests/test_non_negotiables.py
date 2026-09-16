"""
NON-NEGOTIABLES — behavioural guards for .claude/rules/non-negotiables.md.

These stand in for the grep-based CI tripwires, which caught 32 of 106
attacks: a commented-out rejection, an extra conjunct on an `if`, a rename, a
`black` reformat, or a threshold moved into a new config key all sailed past
them.

So almost nothing here reads source text. Every behavioural test builds the
real objects from the committed configuration, runs them, and asserts the
OUTCOME. If a rejection stops happening, the assertion fails, whatever the
source looks like.

The exception is deliberate. A behavioural test fires on expected_edge_pct=None
at ANY margin, so it cannot see min_expected_edge_pct being lowered. Pinning a
configured VALUE is the one thing greps did well, and
test_nn2_committed_configs_keep_the_gate_on keeps doing it.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest
from unittest.mock import patch, MagicMock

from conftest import mk_pair
from ct import config
from ct.costs import CostModel
from ct.portfolio import Portfolio
from ct.risk import RiskEngine
from ct.ledger import Ledger, GENESIS
from app.executor import JupiterExecutor

# Every committed profile, discovered rather than listed — a new config file is
# covered the day it lands, which is exactly where a permissive key would hide.
CONFIGS = sorted(p.name for p in ROOT.glob("config*.yaml"))


def _engine(name):
    cfg = config.load(str(ROOT / name))
    cm = CostModel(cfg)
    return cfg, cm, Portfolio(cfg, cm), RiskEngine(cfg)


def test_configs_under_guard_are_all_present():
    """The glob above must never silently match nothing."""
    assert {"config.yaml", "config-small.yaml"} <= set(CONFIGS)


# --- #1 the risk engine is a hard gate / #2 the edge gate ------------------

@pytest.mark.parametrize("cfg_name", CONFIGS)
def test_nn2_committed_configs_keep_the_gate_on(cfg_name):
    """
    Pins the VALUES, because no behavioural test can.

    Every outcome test below refuses on expected_edge_pct=None, and that is true
    whether the margin is 1.5 or 0.05 — so lowering the margin, or disabling the
    gate in one profile, passes all of them. Raising the margin is fine and
    passes here. Lowering it is the documented wrong fix (non-negotiables 2) and
    fails here.
    """
    cfg, _, _, _ = _engine(cfg_name)
    assert cfg.get_path("risk.require_edge_gate") is True, \
        f"{cfg_name} ships with the edge gate disabled"
    margin = cfg.get_path("risk.min_expected_edge_pct")
    assert margin is not None and float(margin) >= 1.5, (
        f"{cfg_name} sets min_expected_edge_pct to {margin}. The fix for a gate "
        "that refuses is a measured out-of-sample edge, not a smaller margin.")


@pytest.mark.parametrize("cfg_name", CONFIGS)
def test_nn1_nn2_no_config_trades_without_a_measured_edge(cfg_name):
    """
    A healthy, fully fundable pair with no supplied edge is REFUSED, in every
    committed profile.

    This is the load-bearing assertion of the whole file. It fails if the edge
    gate is removed, commented out, made conditional, renamed, defanged by a
    new config key, or turned off in a profile nobody reread.
    """
    _, cm, pf, risk = _engine(cfg_name)
    v = risk.evaluate(pf, mk_pair(), cm)
    assert not v.approved, f"{cfg_name} approved an entry with no measured edge"
    assert "no_measured_edge" in v.reason_str, f"{cfg_name}: {v.reason_str}"


@pytest.mark.parametrize("cfg_name", CONFIGS)
def test_nn2_edge_gate_is_a_gate_not_a_wall(cfg_name):
    """
    Refusing everything is only correct because the gate MEASURES. An edge at
    friction is refused; an edge clearly above friction plus margin is allowed.

    Both thresholds come from the live cost model, never from a literal, so a
    widened threshold cannot be hidden behind a hand-picked number here. Running
    it per profile is what catches a margin zeroed in the one config nobody
    rereads: with no margin, an edge that merely equals friction gets through.
    """
    _, cm, pf, risk = _engine(cfg_name)
    pair = mk_pair()

    sized = risk.evaluate(pf, pair, cm, expected_edge_pct=1e6)
    assert sized.approved and sized.size_usd > 0
    friction = cm.round_trip_pct(sized.size_usd, pair.liquidity_usd)

    at_friction = risk.evaluate(pf, pair, cm, expected_edge_pct=friction)
    assert not at_friction.approved, "an edge that only covers friction is not an edge"
    assert "edge_below_friction" in at_friction.reason_str

    sufficient = risk.evaluate(
        pf, pair, cm, expected_edge_pct=friction + risk.min_edge_pct + 1.0)
    assert sufficient.approved, sufficient.reason_str
    assert sufficient.size_usd > 0


def test_nn2_missing_liquidity_data_is_refused_not_waved_through():
    """
    liquidity_usd <= 0 means the friction and edge gates have nothing to work
    with. Missing data is a reason to REFUSE, never a reason to skip the gates.

    ct/safety.py rejects thin pairs upstream, but non-negotiable #1 says the
    risk engine holds on its own — it is the last thing between a proposal and
    an order, and it does not get to assume anything ran before it.
    """
    _, cm, pf, risk = _engine("config.yaml")
    for liq in (0.0, -1.0):
        v = risk.evaluate(pf, mk_pair("NOLIQ", liq=liq), cm, expected_edge_pct=1e6)
        assert not v.approved, f"liquidity_usd={liq} was approved: {v.reason_str}"


# --- #4 the cash floor ------------------------------------------------------

def test_nn4_cash_floor_trims_then_refuses_as_cash_falls():
    """
    The floor is observable in the verdict: size is trimmed to what is spendable
    above the reserve, and once that is too small to trade, the answer is no.

    Cash is solved for a target spendable from the engine's own reserve pct, so
    this keeps testing the floor if the pct changes.
    """
    _, cm, pf, risk = _engine("config.yaml")
    held = 900.0
    p = mk_pair("HELD", token="THELD")
    pos, _f = pf.open(p, 20.0, {}, 70.0)
    pf.mark(p.token_address, held / pos.qty)          # holdings mark at exactly $900
    mcp = risk.min_cash_pct / 100.0
    candidate = mk_pair("NEW", token="TNEW")
    big_edge = 1e6                                    # isolate the floor

    def with_spendable(s):
        pf.cash = (s + mcp * held) / (1.0 - mcp)
        v = risk.evaluate(pf, candidate, cm, expected_edge_pct=big_edge)
        floor = pf.equity() * mcp
        if v.approved:
            # the point of the floor: what is left after the buy is still above it
            assert pf.cash - v.size_usd >= floor - 0.01, v
        return v

    plenty = with_spendable(30.0)
    assert plenty.approved and plenty.size_usd > 20.0

    tight = with_spendable(18.0)
    assert tight.approved, tight.reason_str
    assert tight.size_usd <= 18.01, "size exceeded what was spendable above the reserve"
    assert tight.size_usd < plenty.size_usd, "size was not trimmed as cash fell"

    thin = with_spendable(10.0)
    assert not thin.approved, thin

    empty = with_spendable(0.0)
    assert not empty.approved and "cash_floor" in empty.reason_str


# --- #7 confirmation is never assumed ---------------------------------------

def test_nn7_unconfirmed_send_is_never_reported_as_confirmed():
    """
    An RPC 200 carrying neither "error" nor a usable "result" yields an EMPTY
    signature. There is nothing to look up, so the position is UNKNOWN, and an
    unknown position must never be reported as confirmed.

    The stubbed RPC answers "finalized" to everything, so a True here can only
    come from the code short-circuiting on the signature — the test cannot pass
    by accident because the network was unhappy.
    """
    confirmed = MagicMock(status_code=200, json=lambda: {
        "result": {"value": [{"err": None, "confirmationStatus": "finalized"}]}})

    live = JupiterExecutor(dry_run=False)
    with patch.object(live.s, "post", return_value=confirmed) as post:
        for sig in ("", None, "DRY_RUN_NOT_SENT"):
            assert live.confirm(sig, tries=2, delay=0.01) is False, \
                f"an unverifiable send ({sig!r}) was reported confirmed"
        assert live.confirm("sig123", tries=2, delay=0.01) is True   # a real sig still works
        # nothing can be looked up without a signature, so nothing was looked up
        assert post.call_count == 1

    # The dry-run path is untouched: nothing was ever sent, so nothing can fail.
    dry = JupiterExecutor(dry_run=True)
    with patch.object(dry.s, "post", return_value=confirmed):
        assert dry.confirm(dry.sign_and_send("anything")) is True
        assert dry.confirm("") is True


# --- #8 live and paper are never mixed --------------------------------------

def test_nn8_every_row_carries_the_mode_it_was_written_in(tmp_path):
    """Mode is per-row and set at write time — never inferred later."""
    path = str(tmp_path / "l.db")
    Ledger(path, "paper").record("entry_filled", symbol="P", signature="p" * 60, ok=1)
    live = Ledger(path, "live")
    assert live.mode == "live"
    live.record("entry_filled", symbol="L", signature="l" * 60, ok=1)
    live.record("entry_filled", symbol="X", signature="x" * 60, ok=1, mode="paper")

    rows = {r["symbol"]: r["mode"] for r in live.q("SELECT symbol, mode FROM ledger")}
    assert rows == {"P": "paper", "L": "live", "X": "paper"}
    assert live.live_signatures() == ["l" * 60]     # paper never counted as real money
    assert live.verify()["ok"]


# --- #5 the ledger is append-only -------------------------------------------

def test_nn5_tampering_breaks_verify_full_and_incremental(tmp_path):
    """
    An edited row must break the chain at exactly that row, for a full audit
    AND for the incremental verify a live monitor uses — a checkpointed reader
    must not be the blind spot.
    """
    L = Ledger(str(tmp_path / "l.db"), "live")
    for i in range(5):
        L.record("entry_filled", symbol=f"A{i}", qty=i, net_usd=i * 1.5, ok=1)
    check = L.verify_from(0, GENESIS)
    assert check["ok"] and check["seq"] == 5
    for i in range(5):
        L.record("exit_filled", symbol=f"B{i}", qty=i, net_usd=i * 2.0, ok=1)
    assert L.verify()["ok"] and L.verify_from(check["seq"], check["tip"])["ok"]

    L.db.execute("UPDATE ledger SET net_usd = 9999 WHERE seq = 8")
    L.db.commit()

    full = L.verify()
    assert not full["ok"] and full["broken_at"] == 8
    incr = L.verify_from(check["seq"], check["tip"])
    assert not incr["ok"] and incr["broken_at"] == 8
