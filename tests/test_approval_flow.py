"""APPROVAL FLOW — expiry, double-taps, and an unreachable Telegram."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from app.notifier import Notifier, Proposal


def mk(pid="p1"):
    return Proposal(pid, "WIF", "TK", 20.0, 0.001, 70.0, {}, ["because"], 300_000, 4.0)


def test_proposal_expires():
    p = mk(); p.created -= 10_000
    assert p.expired(600)


def test_fresh_proposal_does_not_expire():
    assert not mk().expired(600)


def test_resolved_proposal_never_expires_again():
    p = mk(); p.created -= 10_000; p.status = "approved"
    assert not p.expired(600)


def test_notifier_disabled_is_safe_noop():
    n = Notifier("", "", enabled=False)
    assert n.propose(mk(), 10) is None
    assert n.poll() == []
    n.alert("x", "y")           # must not raise


def test_notifier_network_failure_does_not_raise():
    n = Notifier("tok", "chat", enabled=True)
    n.s = type("S", (), {"post": lambda *a, **k: (_ for _ in ()).throw(OSError("down")),
                         "get": lambda *a, **k: (_ for _ in ()).throw(OSError("down"))})()
    assert n.send("hello") is None
    assert n.poll() == []


def test_callback_parsing():
    n = Notifier("tok", "chat", enabled=True)
    n.s = type("S", (), {"get": lambda *a, **k: type("R", (), {
        "json": lambda self: {"result": [
            {"update_id": 1, "callback_query": {"id": "c1", "data": "y:abc"}},
            {"update_id": 2, "callback_query": {"id": "c2", "data": "n:def"}}]}})()})()
    out = n.poll()
    assert out == [("approve", "abc", "c1"), ("reject", "def", "c2")]
    assert n.offset == 3


def test_malformed_callback_is_ignored():
    n = Notifier("tok", "chat", enabled=True)
    n.s = type("S", (), {"get": lambda *a, **k: type("R", (), {
        "json": lambda self: {"result": [
            {"update_id": 9, "callback_query": {"id": "c", "data": "no-colon"}}]}})()})()
    assert n.poll() == []
