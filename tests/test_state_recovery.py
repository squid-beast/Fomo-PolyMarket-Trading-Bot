"""STATE AND RECOVERY — the system restarting, and disagreeing with reality."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from conftest import mk_pair
from ct.ledger import Ledger
from app.wallet import Wallet


def test_open_position_survives_a_restart(tmp_path, monkeypatch, cfg):
    import app.service as svc
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setenv("DB_FILE", str(tmp_path / "p.db"))
    monkeypatch.setenv("LEDGER_FILE", str(tmp_path / "l.db"))
    monkeypatch.setattr(svc, "STATE", tmp_path / "s.json")
    s = svc.Service()
    p = mk_pair("KEEP")
    s.pf.open(p, 20.0, {}, 70.0)
    s._save()
    s2 = svc.Service()
    assert len(s2.pf.positions) == 1
    assert s2.pf.positions[p.token_address].symbol == "KEEP"


def test_corrupt_state_file_starts_clean_rather_than_crashing(tmp_path, monkeypatch):
    import app.service as svc
    f = tmp_path / "s.json"
    f.write_text("{ this is not json")
    monkeypatch.setenv("STATE_FILE", str(f))
    monkeypatch.setenv("DB_FILE", str(tmp_path / "p.db"))
    monkeypatch.setenv("LEDGER_FILE", str(tmp_path / "l.db"))
    monkeypatch.setattr(svc, "STATE", f)
    s = svc.Service()            # must not raise
    assert s.pf.positions == {}


def test_wallet_drift_detects_missing_holdings():
    w = Wallet("http://127.0.0.1:1")          # unreachable on purpose
    issues = w.reconcile({"MINT_A": 10.0}, "pubkey")
    assert issues and "NONE" in issues[0]


def test_wallet_drift_detects_untracked_holdings(monkeypatch):
    w = Wallet("http://x")
    monkeypatch.setattr(w, "token_balances", lambda pk: {"MINT_B": 5.0})
    issues = w.reconcile({}, "pubkey")
    assert any("UNTRACKED" in i for i in issues)


def test_ledger_rebuilds_open_positions_independently(tmp_path):
    L = Ledger(str(tmp_path / "l.db"), "live")
    L.record("entry_filled", symbol="A", token="TA", qty=100, ok=1)
    L.record("entry_filled", symbol="A", token="TA", qty=50, ok=1)
    L.record("exit_filled", symbol="A", token="TA", qty=120, ok=1)
    rows = L.q("SELECT kind, qty FROM ledger WHERE ok=1")
    held = sum(r["qty"] if r["kind"] == "entry_filled" else -r["qty"] for r in rows)
    assert abs(held - 30.0) < 1e-9


def test_ledger_survives_process_restart(tmp_path):
    p = str(tmp_path / "l.db")
    Ledger(p, "live").record("entry_filled", symbol="X", ok=1)
    L2 = Ledger(p, "live")
    L2.record("exit_filled", symbol="X", ok=1)
    assert L2.verify()["ok"] and L2.verify()["entries"] == 2
