"""AUDIT INTEGRITY — the proof layer must be unforgeable and never lie."""
import sys, json, sqlite3
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from ct.ledger import Ledger, KINDS


def test_clean_chain_verifies(tmp_path):
    L = Ledger(str(tmp_path / "l.db"), "live")
    for i in range(20):
        L.record("entry_filled", symbol=f"S{i}", qty=i, size_usd=i * 1.5, ok=1)
    assert L.verify()["ok"]


def test_edit_breaks_chain_at_exact_row(tmp_path):
    p = str(tmp_path / "l.db")
    L = Ledger(p, "live")
    for i in range(10):
        L.record("entry_filled", symbol=f"S{i}", ok=1)
    L.db.execute("UPDATE ledger SET net_usd=9999 WHERE seq=6"); L.db.commit()
    v = L.verify()
    assert not v["ok"] and v["broken_at"] == 6


def test_delete_breaks_chain(tmp_path):
    p = str(tmp_path / "l.db")
    L = Ledger(p, "live")
    for i in range(10):
        L.record("entry_filled", symbol=f"S{i}", ok=1)
    L.db.execute("DELETE FROM ledger WHERE seq=4"); L.db.commit()
    assert not L.verify()["ok"]


def test_int_vs_float_does_not_false_positive(tmp_path):
    """Regression: ints in REAL columns used to break the chain on clean data."""
    L = Ledger(str(tmp_path / "l.db"), "live")
    L.record("entry_filled", symbol="A", qty=100, size_usd=20, price=1, net_usd=0, ok=1)
    L.record("exit_filled", symbol="A", qty=100, size_usd=20, price=2, net_usd=20, ok=1)
    assert L.verify()["ok"]


def test_live_and_paper_are_never_mixed(tmp_path):
    p = str(tmp_path / "l.db")
    Ledger(p, "paper").record("entry_filled", symbol="P", ok=1)
    Ledger(p, "live").record("entry_filled", symbol="L", ok=1)
    L = Ledger(p)
    modes = {r["mode"] for r in L.q("SELECT mode FROM ledger")}
    assert modes == {"paper", "live"}
    assert len(L.q("SELECT 1 FROM ledger WHERE mode='live'")) == 1


def test_failures_are_queryable(tmp_path):
    L = Ledger(str(tmp_path / "l.db"), "live")
    L.record("entry_filled", symbol="OK", ok=1)
    L.record("exit_failed", symbol="BAD", ok=0, error="boom")
    assert len(L.failures()) == 1


def test_every_declared_kind_is_writable(tmp_path):
    L = Ledger(str(tmp_path / "l.db"), "paper")
    for k in KINDS:
        L.record(k, symbol="X")
    assert L.verify()["ok"]


def test_unregistered_kind_is_flagged_not_silently_accepted(tmp_path):
    L = Ledger(str(tmp_path / "l.db"), "paper")
    L.record("totally_made_up", symbol="X")
    row = L.q("SELECT detail FROM ledger")[0]
    assert "_unregistered_kind" in json.loads(row["detail"])


def test_ledger_is_append_only_in_practice(tmp_path):
    L = Ledger(str(tmp_path / "l.db"), "live")
    L.record("entry_filled", symbol="A", ok=1)
    before = L.verify()["tip"]
    L.record("exit_filled", symbol="A", ok=1)
    assert L.verify()["tip"] != before and L.verify()["ok"]


def test_live_signature_extraction(tmp_path):
    L = Ledger(str(tmp_path / "l.db"), "live")
    L.record("entry_filled", symbol="A", signature="x" * 60, ok=1)
    L.record("entry_filled", symbol="B", signature="short", ok=1)
    sigs = L.live_signatures()
    assert len(sigs) == 1 and len(sigs[0]) == 60
