"""
FOMO CLIENT — offline contract tests. No network, no API key, no credits.

fomoapi.io is a read-only analytics provider: it documents no order-placement
endpoint, and nothing here exercises one. These tests pin two things that cost
real money to get wrong — the research cache contract (run_persistence.py
depends on it) and the credit ceiling.
"""
import os
import time

import pytest

from ct.research import fomo_client
from ct.research.fomo_client import BudgetExceeded


class UnexpectedNetworkCall(BaseException):
    """Not an Exception on purpose: _get swallows Exception, and a test that
    silently turned into a network call would then look like a pass."""


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    """Stands in for requests.Session. Records calls; never opens a socket."""

    def __init__(self):
        self.headers = {}
        self.requests = []
        self.responses = []

    def get(self, url, params=None, timeout=None):
        self.requests.append((url, dict(params or {})))
        if not self.responses:
            raise UnexpectedNetworkCall(f"unqueued call to {url} {params}")
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Every socket in this module is sealed, and no key is ever read."""
    monkeypatch.delenv("FOMO_API_KEY", raising=False)
    monkeypatch.setattr(fomo_client.requests, "Session", FakeSession)
    monkeypatch.setattr(fomo_client.requests, "get", _boom)
    monkeypatch.setattr(fomo_client.time, "sleep", lambda s: None)


def _boom(*a, **k):
    raise UnexpectedNetworkCall("module-level requests.get is not allowed here")


@pytest.fixture
def make_client(tmp_path):
    made = []

    def _make(budget_credits=250_000):
        # a cache dir per client, so one test's cache cannot hide another's charge
        c = fomo_client.FomoClient(api_key="test-key-never-real",
                                   budget_credits=budget_credits,
                                   cache_dir=str(tmp_path / f"cache{len(made)}"))
        made.append(c)
        return c

    return _make


# -- fixtures modelled on the documented response shapes -----------------

BALANCES = {
    "holdings": [
        {"token": {"symbol": "WIF", "address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
                   "networkId": "solana"},
         "chain": "solana", "amount": 12345.678, "priceUsd": 0.842,
         "valueUsd": 10395.06, "change24h": -4.2},
        {"token": {"symbol": "ETH", "address": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                   "networkId": "eth"},
         "chain": "eth", "amount": 1.5, "priceUsd": 3210.0,
         "valueUsd": 4815.0, "change24h": 1.1},
    ],
    "totalValueUsd": 15210.06,
    "byChain": {"solana": 10395.06, "eth": 4815.0},
    "livePerpPnl": -812.34,
    "hyperliquidPerps": [],
}

OPEN_ROW = {
    "tradeId": "t-open-1",
    "token": {"symbol": "WIF", "address": "EKpQGSJt", "networkId": "solana"},
    "chain": "solana", "status": "open", "amount": 12345.678,
    "avgEntryPrice": 0.61, "avgExitPrice": None,
    "realizedPnlUsd": 0.0, "unrealizedPnlUsd": 2863.4,
    "createdAt": "2026-09-01T12:00:00Z", "closedAt": None,
    "closedTotalOnFomo": 25, "complete": False,
}

CLOSED_ROW = {
    "tradeId": "t-closed-1",
    "token": {"symbol": "BONK", "address": "DezXAZ8z", "networkId": "solana"},
    "chain": "solana", "status": "closed", "amount": 900000.0,
    "avgEntryPrice": 0.000021, "avgExitPrice": 0.000018,
    "realizedPnlUsd": -2.7, "unrealizedPnlUsd": 0.0,
    "createdAt": "2026-08-30T09:15:00Z", "closedAt": "2026-08-31T04:02:00Z",
    "closedTotalOnFomo": 25, "complete": False,
}


def envelope(kind, rows):
    """The provider's list key varies; fetch_trades already tolerates all of these."""
    if kind == "list":
        return rows
    return {kind: rows, "complete": True}   # a True here must never be believed


# -- the cache contract the research path depends on ---------------------

def test_get_without_max_age_never_refetches(make_client):
    c = make_client()
    c.s.responses.append(FakeResponse({"v": 1}))
    assert c._get("/v2/leaderboard/7d", "leaderboard", limit=100) == {"v": 1}

    # run_persistence.py passes no max_age and must keep costing zero on a rerun
    assert c._get("/v2/leaderboard/7d", "leaderboard", limit=100) == {"v": 1}
    assert len(c.s.requests) == 1
    assert c.spent == 250 and c.cache_hits == 1


def test_max_age_refetches_a_stale_file_but_keeps_a_fresh_one(make_client):
    c = make_client()
    c.s.responses.extend([FakeResponse({"v": 1}), FakeResponse({"v": 2})])
    c._get("/v2/users/whale/balances", "balances")
    ck = c._ck("/v2/users/whale/balances", {})
    assert ck.exists()

    stale = time.time() - 3600
    os.utime(ck, (stale, stale))
    assert c._get("/v2/users/whale/balances", "balances", max_age=0) == {"v": 2}
    assert len(c.s.requests) == 2

    # the refetch refreshed the file, so an age window now covers it again
    assert c._get("/v2/users/whale/balances", "balances", max_age=600) == {"v": 2}
    assert len(c.s.requests) == 2


def test_fetch_trades_still_fans_out_and_dedupes(make_client):
    """Behaviourally unchanged: run_persistence.py's only source of trades."""
    c = make_client()
    row = {"id": "x1", "tokenAddress": "TK", "closedAt": 1}
    c.s.responses.extend([FakeResponse({"data": [row]}),
                          FakeResponse({"data": [row, {"id": "x2"}]})])
    out = c.fetch_trades("whale")
    assert [t["id"] for t in out] == ["x1", "x2"]
    assert {r[1]["orderBy"] for r in c.s.requests} == {"recent", "pnl"}
    assert c.spent == 500


# -- balances ------------------------------------------------------------

def test_balances_normalises_the_documented_response(make_client):
    c = make_client()
    c.s.responses.append(FakeResponse(BALANCES))
    b = c.balances("whale", chain="solana")

    url, params = c.s.requests[0]
    assert url.endswith("/v2/users/whale/balances")
    assert params.get("chain") == "solana"

    assert b["holdings"][0] == {
        "symbol": "WIF",
        "address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
        "network_id": "solana", "chain": "solana", "amount": 12345.678,
        "price_usd": 0.842, "value_usd": 10395.06, "change_24h": -4.2,
    }
    assert [h["symbol"] for h in b["holdings"]] == ["WIF", "ETH"]
    assert b["total_value_usd"] == 15210.06
    assert b["by_chain"] == {"solana": 10395.06, "eth": 4815.0}
    assert b["live_perp_pnl"] == -812.34
    assert b["error"] is None


def test_balances_reports_an_http_error_in_shape_instead_of_raising(make_client):
    c = make_client()
    c.s.responses.append(FakeResponse(status_code=500, text="upstream exploded"))
    b = c.balances("whale")   # a UI renders this; it must not raise

    assert set(b) == {"holdings", "total_value_usd", "by_chain",
                      "live_perp_pnl", "error"}
    assert b["holdings"] == [] and b["total_value_usd"] is None
    assert isinstance(b["by_chain"], dict)
    assert isinstance(b["error"], str) and "500" in b["error"]


# -- account_trades ------------------------------------------------------

@pytest.mark.parametrize("kind", ["list", "data", "trades", "results"])
def test_account_trades_splits_open_from_closed(make_client, kind):
    c = make_client()
    c.s.responses.append(FakeResponse(envelope(kind, [OPEN_ROW, CLOSED_ROW])))
    t = c.account_trades("whale", deep=5)

    # one call against /trades?deep=N, not the 10-call research fan-out
    assert len(c.s.requests) == 1
    url, params = c.s.requests[0]
    assert url.endswith("/v2/users/whale/trades")
    assert params.get("deep") == 5

    assert t["error"] is None
    assert [r["trade_id"] for r in t["open"]] == ["t-open-1"]
    assert [r["trade_id"] for r in t["closed"]] == ["t-closed-1"]

    o = t["open"][0]
    assert o["symbol"] == "WIF" and o["chain"] == "solana" and o["status"] == "open"
    assert o["amount"] == 12345.678
    assert o["avg_entry_price"] == 0.61 and o["avg_exit_price"] is None
    assert o["unrealized_pnl_usd"] == 2863.4
    assert o["created_at"] == "2026-09-01T12:00:00Z" and o["closed_at"] is None

    x = t["closed"][0]
    assert x["symbol"] == "BONK" and x["status"] == "closed"
    assert x["avg_exit_price"] == 0.000018 and x["realized_pnl_usd"] == -2.7
    assert x["closed_at"] == "2026-08-31T04:02:00Z"


@pytest.mark.parametrize("kind", ["list", "data", "trades", "results"])
def test_account_trades_is_never_complete(make_client, kind):
    """25 closed rows per chain, no cursor. The provider cannot say otherwise."""
    c = make_client()
    c.s.responses.append(FakeResponse(envelope(kind, [OPEN_ROW, CLOSED_ROW])))
    assert c.account_trades("whale", deep=5)["complete"] is False


def test_account_trades_reports_an_error_in_shape(make_client):
    c = make_client()
    c.s.responses.append(FakeResponse(status_code=503, text="provider down"))
    t = c.account_trades("whale")

    assert set(t) == {"open", "closed", "complete", "error"}
    assert t["open"] == [] and t["closed"] == [] and t["complete"] is False
    assert isinstance(t["error"], str) and "503" in t["error"]


# -- the credit ceiling --------------------------------------------------

def test_budget_guard_refuses_before_spending_over_the_ceiling(make_client):
    c = make_client(budget_credits=250)          # exactly one plain call
    c.s.responses.append(FakeResponse({"ok": True}))
    c._get("/v2/users/a/balances", "balances")
    assert c.spent == 250 and c.remaining() == 0

    with pytest.raises(BudgetExceeded):
        c._get("/v2/users/b/balances", "balances")
    assert len(c.s.requests) == 1, "refused after the call, not before it"
    assert c.spent <= c.budget


def test_deep_is_charged_for_every_upstream_call(make_client):
    c = make_client()
    c.s.responses.append(FakeResponse(envelope("data", [OPEN_ROW, CLOSED_ROW])))
    c.account_trades("whale", deep=5)
    assert c.spent >= 250 + 5, (
        f"deep=5 buys 5 upstream calls at 1 credit each; charged {c.spent}. "
        "Under-counting locally is how a run dies on a surprise 402.")


def test_the_deep_surcharge_cannot_push_spend_past_the_budget(make_client):
    c = make_client(budget_credits=250)          # no room for the surcharge
    c.s.responses.append(FakeResponse(envelope("data", [])))
    try:
        c.account_trades("whale", deep=5)
    except BudgetExceeded:
        pass
    assert c.spent <= c.budget


# --- trade_return must read the shape the provider actually sends -----------
# This bug was invisible to every other test in this file: CLOSED_ROW existed in
# the documented shape and was exercised through fetch_trades, but nothing ever
# put it through trade_return. run_persistence.py keeps a trader only when >=20
# rows parse, so returning None for all of them silently dropped EVERY trader —
# after spending the credits — and reported it as "not enough usable traders".

def test_trade_return_reads_the_documented_provider_shape():
    r = fomo_client.trade_return(CLOSED_ROW)
    assert r is not None, "documented row did not parse; every trader would be dropped"
    expected = CLOSED_ROW["avgExitPrice"] / CLOSED_ROW["avgEntryPrice"] - 1.0
    assert r == pytest.approx(expected, rel=1e-9)
    assert r < 0, "this fixture is a loss; a parser that reports a gain is worse than one that fails"


def test_trade_return_still_reads_the_older_spellings():
    """Additive fix: the legacy keys some responses use must keep working."""
    assert fomo_client.trade_return(
        {"entryPrice": 0.000012, "exitPrice": 0.000015}) == pytest.approx(0.25)
    assert fomo_client.trade_return(
        {"realizedPnlUsd": 3.0, "costUsd": 12.0}) == pytest.approx(0.25)


def test_trade_return_stays_none_when_it_genuinely_cannot_tell():
    """Inventing a return is far worse than discarding the row."""
    for junk in ({}, {"foo": 1}, {"avgEntryPrice": 0}, {"realizedPnlUsd": 3.0},
                 {"amount": 0, "avgEntryPrice": 0.001}):
        assert fomo_client.trade_return(junk) is None, junk


def test_a_full_page_of_documented_rows_clears_the_min_trades_gate():
    """run_persistence.py:66 needs >=20 parsed rows before it keeps a trader."""
    rows = [dict(CLOSED_ROW, tradeId=f"t{i}") for i in range(25)]
    parsed = [x for x in (fomo_client.trade_return(r) for r in rows) if x is not None]
    assert len(parsed) == 25, f"only {len(parsed)}/25 parsed — the trader is dropped"
