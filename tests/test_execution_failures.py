"""
EXECUTION FAILURE SCENARIOS.

The dangerous cases are not "the swap failed". They are the cases where the
system's belief about its position and the actual chain state diverge, because
every later decision is then made on a false premise.
"""
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from unittest.mock import patch, MagicMock
from app.executor import JupiterExecutor, ExecutionError, Quote, SOL_MINT
from ct.ledger import Ledger


def _q(out=1_000_000, impact=0.0):
    return Quote(SOL_MINT, "TK", 100_000_000, out, impact, 100,
                 {"inAmount": "100000000", "outAmount": str(out)})


def test_quote_http_error_raises_not_silently_passes():
    ex = JupiterExecutor(dry_run=True)
    with patch.object(ex.s, "get") as g:
        g.return_value = MagicMock(status_code=500, text="upstream boom")
        with pytest.raises(ExecutionError):
            ex.quote(SOL_MINT, "TK", 1000)


def test_malformed_quote_is_rejected():
    """A quote missing fields must raise, never be coerced into a trade."""
    ex = JupiterExecutor(dry_run=True)
    with patch.object(ex.s, "get") as g:
        g.return_value = MagicMock(status_code=200, json=lambda: {"garbage": True})
        with pytest.raises(ExecutionError):
            ex.quote(SOL_MINT, "TK", 1000)


def test_price_impact_gate_blocks_before_signing():
    ex = JupiterExecutor(dry_run=True)
    q = _q(impact=0.15)                       # 15%
    problems = ex.check_quote(q, max_impact_pct=3.0)
    assert problems and "price_impact" in problems[0]


def test_zero_output_quote_is_caught():
    ex = JupiterExecutor(dry_run=True)
    assert ex.check_quote(_q(out=0), 99.0)


def test_min_out_floor_enforced():
    ex = JupiterExecutor(dry_run=True)
    assert ex.check_quote(_q(out=500), 99.0, min_out=1000)


def test_swap_build_failure_raises():
    ex = JupiterExecutor(dry_run=True)
    with patch.object(ex.s, "post") as p:
        p.return_value = MagicMock(status_code=502, text="bad gateway")
        with pytest.raises(ExecutionError):
            ex.build_swap(_q(), "SomePubkey")


def test_swap_response_without_transaction_raises():
    ex = JupiterExecutor(dry_run=True)
    with patch.object(ex.s, "post") as p:
        p.return_value = MagicMock(status_code=200, json=lambda: {})
        with pytest.raises(ExecutionError):
            ex.build_swap(_q(), "SomePubkey")


def test_confirmation_timeout_reports_false_not_true():
    """
    THE MOST DANGEROUS CASE. A send whose confirmation never arrives must
    report NOT confirmed. Reporting success here would leave the system
    believing it holds a position it may not hold.
    """
    ex = JupiterExecutor(dry_run=False)
    with patch.object(ex.s, "post") as p:
        p.return_value = MagicMock(status_code=200,
                                   json=lambda: {"result": {"value": [None]}})
        assert ex.confirm("sig123", tries=2, delay=0.01) is False


def test_transaction_confirmed_with_onchain_error_is_failure():
    ex = JupiterExecutor(dry_run=False)
    with patch.object(ex.s, "post") as p:
        p.return_value = MagicMock(status_code=200, json=lambda: {
            "result": {"value": [{"err": {"InstructionError": [0, "Custom"]}}]}})
        assert ex.confirm("sig123", tries=2, delay=0.01) is False


def test_malformed_transaction_raises_execution_error_not_valueerror():
    """
    Regression: a corrupt swapTransaction used to escape as a raw ValueError,
    bypassing the service's ExecutionError handling and leaving a proposal in
    limbo rather than cleanly failed.
    """
    ex = JupiterExecutor(dry_run=False)
    ex._kp = MagicMock()
    with pytest.raises(ExecutionError):
        ex.sign_and_send("aGVsbG8=")      # valid base64, not a transaction


def test_dry_run_never_sends():
    ex = JupiterExecutor(dry_run=True)
    assert ex.sign_and_send("anything") == "DRY_RUN_NOT_SENT"


def test_missing_key_refuses_rather_than_using_a_default():
    os.environ.pop("SOLANA_PRIVATE_KEY", None)
    ex = JupiterExecutor(dry_run=False)
    ex._kp = None
    with pytest.raises(ExecutionError):
        _ = ex.keypair


def test_failed_exit_is_recorded_with_position_still_open(tmp_path):
    """A failed exit must leave a durable record flagging that we STILL HOLD."""
    L = Ledger(str(tmp_path / "l.db"), "live")
    L.record("exit_failed", symbol="WIF", token="TK", qty=100, ok=0,
             error="impact 14% > cap", position_still_open=True)
    f = L.failures()
    assert len(f) == 1
    import json
    assert json.loads(f[0]["detail"])["position_still_open"] is True
