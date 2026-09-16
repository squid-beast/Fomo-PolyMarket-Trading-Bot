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
from app.wallet import Wallet


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


def _signed_tx_b64(kp):
    """A real, parseable VersionedTransaction so sign_and_send reaches the RPC call."""
    from solders.message import MessageV0
    from solders.hash import Hash
    from solders.transaction import VersionedTransaction
    from solders.signature import Signature
    import base64
    msg = MessageV0.try_compile(kp.pubkey(), [], [], Hash.default())
    return base64.b64encode(bytes(VersionedTransaction.populate(msg, [Signature.default()]))).decode()


def test_send_returning_no_signature_raises_instead_of_empty_string():
    """
    Regression: an RPC 200 carrying neither "error" nor a usable "result" used to
    return an EMPTY signature, which confirm() then reported as confirmed — a send
    nobody can verify, booked as a landed swap. It must fail loudly so the callers
    record it as a failure.
    """
    from solders.keypair import Keypair
    ex = JupiterExecutor(dry_run=False)
    ex._kp = Keypair()
    tx = _signed_tx_b64(ex._kp)
    for body in ({"jsonrpc": "2.0", "id": 1}, {"result": ""}, {"result": None}, {"result": 12}):
        with patch.object(ex.s, "post") as p:
            p.return_value = MagicMock(status_code=200, json=lambda b=body: b)
            with pytest.raises(ExecutionError):
                ex.sign_and_send(tx)


def test_send_with_non_json_body_raises_execution_error():
    from solders.keypair import Keypair
    ex = JupiterExecutor(dry_run=False)
    ex._kp = Keypair()
    tx = _signed_tx_b64(ex._kp)
    with patch.object(ex.s, "post") as p:
        def boom():
            raise ValueError("no json")
        p.return_value = MagicMock(status_code=502, text="<html>bad gateway</html>", json=boom)
        with pytest.raises(ExecutionError):
            ex.sign_and_send(tx)


def test_confirm_refuses_an_empty_signature_in_live():
    """No signature means no way to verify. Unknown is never 'confirmed'."""
    ex = JupiterExecutor(dry_run=False)
    with patch.object(ex.s, "post") as p:
        p.return_value = MagicMock(status_code=200, json=lambda: {"result": {"value": [None]}})
        assert ex.confirm("", tries=2, delay=0.01) is False
        assert ex.confirm(None, tries=2, delay=0.01) is False
        assert ex.confirm("DRY_RUN_NOT_SENT", tries=2, delay=0.01) is False
        assert p.call_count == 0          # nothing to poll, so nothing was polled


def test_dry_run_confirm_still_reports_confirmed():
    """The dry-run path is unchanged: nothing was sent, so there is nothing to fail."""
    ex = JupiterExecutor(dry_run=True)
    assert ex.confirm(ex.sign_and_send("anything")) is True
    assert ex.confirm("") is True


# -- EXIT SIZING: the unit the swap is denominated in -----------------------
#
# Jupiter's `amount` is ATOMIC units. The exit used to pass int(pos.qty), a UI
# amount, which on a 6-decimal token sells a millionth of the position — and 0
# for any qty < 1. The stop reports success and closes nothing.

def _live_service(tmp_path, monkeypatch, wallet_raw, decimals=6, crash_to=0.0002):
    """Live service holding one position that is about to hit its stop."""
    import app.service as svc
    from conftest import mk_pair
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setenv("DB_FILE", str(tmp_path / "p.db"))
    monkeypatch.setenv("LEDGER_FILE", str(tmp_path / "l.db"))
    monkeypatch.setattr(svc, "STATE", tmp_path / "s.json")
    s = svc.Service()
    assert s.live
    pos, _ = s.pf.open(mk_pair("EXIT", price=0.001), 20.0, {}, 70.0)
    monkeypatch.setattr(s.ds, "refresh", lambda pa: mk_pair("EXIT", price=crash_to))
    s.ex = MagicMock()
    s.ex.pubkey = "WALLET"
    s.ex.swap.return_value = {"ok": True, "signature": "sig_confirmed"}
    amounts = None if wallet_raw is None else (
        {} if wallet_raw == "no_account" else
        {pos.token_address: {"raw": wallet_raw, "decimals": decimals, "ui": float(wallet_raw)}})
    monkeypatch.setattr(s.wallet, "token_amounts", lambda pk: amounts)
    return s, pos


def test_exit_sells_atomic_units_not_the_ui_quantity(tmp_path, monkeypatch):
    """The swap amount must be the raw on-chain amount, not int(pos.qty)."""
    s, pos = _live_service(tmp_path, monkeypatch, wallet_raw=10**18)
    tracked_raw = int(pos.qty * 10 ** 6)
    s.manage()
    amount = s.ex.swap.call_args[0][2]
    assert amount == tracked_raw
    assert amount != int(pos.qty)           # the bug: a UI amount in an atomic field
    assert pos.token_address not in s.pf.positions


def test_exit_never_sells_more_than_the_tracked_position(tmp_path, monkeypatch):
    """A surplus is untracked (manual buy / late partial fill) — not ours to sell."""
    s, pos = _live_service(tmp_path, monkeypatch, wallet_raw=10**18)
    s.manage()
    assert s.ex.swap.call_args[0][2] < 10**18


def test_exit_sells_only_what_the_wallet_actually_holds(tmp_path, monkeypatch):
    """Wallet holds less than the model thinks: sell the real balance."""
    s, pos = _live_service(tmp_path, monkeypatch, wallet_raw=1234)
    s.manage()
    assert s.ex.swap.call_args[0][2] == 1234


@pytest.mark.parametrize("wallet_raw", [None, "no_account", 0])
def test_exit_fails_closed_when_raw_balance_is_unknown(tmp_path, monkeypatch, wallet_raw):
    """
    No verifiable size means NO SEND. Guessing a size on a live swap is
    unrecoverable; a recorded failure is not. The position stays open and
    flagged, and nothing waits for a human.
    """
    s, pos = _live_service(tmp_path, monkeypatch, wallet_raw=wallet_raw)
    s.manage()
    s.ex.swap.assert_not_called()
    assert pos.token_address in s.pf.positions          # still held, still managed
    f = s.ledger.failures()
    assert f and f[0]["kind"] == "exit_failed"
    import json
    d = json.loads(f[0]["detail"])
    assert d["position_still_open"] is True
    assert "no_raw_balance" in d["error"]


def test_wallet_raw_amount_stays_an_int(monkeypatch):
    """The RPC sends `amount` as a string. Never route it through a float."""
    from app.wallet import Wallet
    w = Wallet("http://x")
    monkeypatch.setattr(w, "_rpc", lambda m, p: {"value": [{"account": {"data": {"parsed": {"info": {
        "mint": "MINT_A",
        "tokenAmount": {"amount": "123456789012345678", "decimals": 9, "uiAmount": 123456789.012}}}}}}]})
    a = w.token_amounts("pk")["MINT_A"]
    assert a["raw"] == 123456789012345678 and isinstance(a["raw"], int)
    assert a["decimals"] == 9
    assert w.token_balances("pk") == {"MINT_A": 123456789.012}     # unchanged behaviour


def test_wallet_unreachable_is_unknown_not_zero():
    """None means UNKNOWN. Callers about to spend money must be able to tell."""
    assert Wallet("http://127.0.0.1:1").token_amounts("pk") is None


def test_wallet_sums_multiple_accounts_for_one_mint(monkeypatch):
    """One owner can hold several token accounts per mint; the exit needs the total."""
    def acc(amount, ui):
        return {"account": {"data": {"parsed": {"info": {
            "mint": "MINT_A",
            "tokenAmount": {"amount": amount, "decimals": 6, "uiAmount": ui}}}}}}
    w = Wallet("http://x")
    monkeypatch.setattr(w, "_rpc", lambda m, p: {"value": [acc("400", 0.0004), acc("600", 0.0006)]})
    assert w.token_amounts("pk")["MINT_A"]["raw"] == 1000
