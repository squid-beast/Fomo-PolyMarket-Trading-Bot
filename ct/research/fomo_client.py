"""
fomo API client, built around the free tier's real constraints.

Two constraints shape everything here:

  1. CREDITS. 250,000/month free. A trades/balances/search call is 250 credits;
     a wallet resolution (/v2/users/{handle}) is 2,500. The budget is tracked
     and the client REFUSES to exceed it rather than failing mid-run.

  2. THE 25-TRADE CAP. /v2/users/{handle}/trades returns at most 25 rows and
     silently ignores offset/cursor/page/lastId. The only way deeper is to fan
     out over the parameters that return disjoint sets - orderBy and networkId -
     and dedupe. That is what fetch_trades does.

Every response is cached to disk, so re-running an analysis costs zero credits.
The cache is infinite by default; only a caller that explicitly passes
max_age=<seconds> (the account view, not research) can trigger a refetch.
"""
from __future__ import annotations
import json, math, os, time, hashlib
from pathlib import Path
import requests

BASE = "https://api.fomoapi.io"
COST = {"trades": 250, "balances": 250, "search": 250, "leaderboard": 250,
        "user": 2500, "following": 250, "default": 250}


class BudgetExceeded(RuntimeError):
    pass


# A failure is never written to the disk cache, so without this a UI asking for a
# handle the provider 404s pays the full 505 credits on EVERY page load. Keyed on
# the same cache path and held at module level on purpose: the web server builds a
# fresh FomoClient per request, so a per-instance dict would never see a second
# hit. TTL is short enough that a transient outage self-heals on its own.
_FAILED: dict[str, tuple[float, dict]] = {}
_FAIL_TTL = 60.0


def _remember_fail(key: str, payload: dict) -> dict:
    now = time.time()
    if len(_FAILED) > 512:          # bounded; drop what has already expired
        for k, (t, _) in list(_FAILED.items()):
            if now - t > _FAIL_TTL:
                _FAILED.pop(k, None)
    _FAILED[key] = (now, payload)
    return payload


class FomoClient:
    def __init__(self, api_key: str | None = None, budget_credits: int = 250_000,
                 cache_dir: str = ".fomo_cache", timeout: int = 25):
        self.key = api_key or os.environ.get("FOMO_API_KEY", "")
        if not self.key:
            raise RuntimeError(
                "No API key. Get a free one at https://fomoapi.io/docs and either "
                "export FOMO_API_KEY=... or pass api_key=")
        self.budget = budget_credits
        self.spent = 0
        self.cache = Path(cache_dir); self.cache.mkdir(exist_ok=True)
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {self.key}",
                               "Accept": "application/json"})
        self.calls = 0
        self.cache_hits = 0

    # -- plumbing -------------------------------------------------------
    def _ck(self, path, params):
        raw = path + "?" + json.dumps(params or {}, sort_keys=True)
        return self.cache / (hashlib.sha256(raw.encode()).hexdigest()[:24] + ".json")

    def remaining(self) -> int:
        return self.budget - self.spent

    def _get(self, path: str, kind: str = "default", max_age: float | None = None,
             _retries: int = 0, **params):
        ck = self._ck(path, params)
        # max_age=None is the original contract: cache forever, so re-running an
        # analysis costs zero credits. Research callers never pass it and so can
        # never be surprised by a refetch. A caller that needs fresh data (the
        # account view a human is staring at) passes an age in seconds.
        if ck.exists() and (max_age is None or
                            time.time() - ck.stat().st_mtime <= max_age):
            self.cache_hits += 1
            return json.loads(ck.read_text())
        # ?deep=N adds 1 credit per extra upstream call on top of the base 250.
        # Charged here rather than in each endpoint so no deep call can under-pay.
        fk = str(ck)
        fail = _FAILED.get(fk)
        if fail and time.time() - fail[0] <= _FAIL_TTL:
            self.cache_hits += 1
            return dict(fail[1])
        cost = COST.get(kind, 250) + _deep_surcharge(params.get("deep"))
        if self.spent + cost > self.budget:
            raise BudgetExceeded(
                f"would spend {cost}, only {self.remaining()} credits left "
                f"(spent {self.spent:,}/{self.budget:,})")
        self.calls += 1          # an ATTEMPT. A throw below still cost us a socket.
        try:
            r = self.s.get(f"{BASE}{path}", params=params or None, timeout=self.timeout)
        except Exception as e:
            return _remember_fail(fk, {"_error": str(e)})
        self.spent += cost
        if r.status_code == 429:
            # Bounded. An unbounded recursion here is a 3s-per-level hang during
            # a rate-limit storm, which looks exactly like the process wedging.
            self.spent -= cost
            if _retries >= 3:
                return _remember_fail(fk, {"_error": "http 429: retries exhausted"})
            time.sleep(3)
            return self._get(path, kind, max_age=max_age, _retries=_retries + 1, **params)
        if r.status_code != 200:
            body = r.text[:200]
            if r.status_code == 402:
                raise BudgetExceeded(f"API reports credits exhausted: {body}")
            return _remember_fail(fk, {"_error": f"http {r.status_code}", "_body": body})
        data = r.json()
        ck.write_text(json.dumps(data))
        _FAILED.pop(fk, None)    # a success clears the back-off
        return data

    # -- endpoints -------------------------------------------------------
    def leaderboard(self, window: str = "7d", limit: int = 100):
        d = self._get(f"/v2/leaderboard/{window}", "leaderboard", limit=limit)
        if isinstance(d, dict) and "_error" in d:
            return []
        rows = d if isinstance(d, list) else (
            d.get("data") or d.get("traders") or d.get("leaderboard") or d.get("results") or [])
        return rows if isinstance(rows, list) else []

    def fetch_trades(self, handle: str, order_bys=("recent", "pnl"),
                     networks=("solana",), limit: int = 25) -> list[dict]:
        """Fan out over the disjoint-set params to beat the 25-row cap."""
        seen, out = set(), []
        for net in networks:
            for ob in order_bys:
                params = {"limit": limit, "orderBy": ob, "networkId": net}
                d = self._get(f"/v2/users/{handle}/trades", "trades", **params)
                if isinstance(d, dict) and "_error" in d:
                    continue
                rows = d if isinstance(d, list) else (
                    d.get("data") or d.get("trades") or d.get("results") or [])
                if not isinstance(rows, list):
                    continue
                for t in rows:
                    tid = str(t.get("id") or t.get("tradeId") or
                              f"{t.get('tokenAddress')}|{t.get('timestamp') or t.get('closedAt')}")
                    if tid not in seen:
                        seen.add(tid); out.append(t)
        return out

    def _safe_get(self, path: str, kind: str, max_age: float | None, **params):
        """
        _get already turns network errors into {"_error": ...}, but a malformed
        body (r.json()) or an unwritable cache dir still throws. The account
        endpoints are rendered in a UI, so they must degrade to an error string
        rather than take the request down. BudgetExceeded is the one exception
        that propagates: it means stop, not retry.
        """
        try:
            return self._get(path, kind, max_age=max_age, **params)
        except BudgetExceeded:
            raise
        except Exception as e:
            return {"_error": str(e)}

    def balances(self, handle: str, chain: str | None = None,
                 max_age: float | None = None) -> dict:
        """Current holdings for one handle, normalised to snake_case."""
        params = {"chain": chain} if chain else {}
        d = self._safe_get(f"/v2/users/{handle}/balances", "balances", max_age, **params)
        err = _error_of(d)
        by_chain = _dig(d, "byChain", "by_chain")
        return {
            "holdings": [] if err else [_norm_holding(h) for h in _rows(
                d, "holdings", "balances", "tokens", "assets")],
            "total_value_usd": None if err else _num(
                _dig(d, "totalValueUsd", "total_value_usd", "totalUsd")),
            "by_chain": by_chain if isinstance(by_chain, dict) else {},
            "live_perp_pnl": None if err else _num(
                _dig(d, "livePerpPnl", "live_perp_pnl")),
            "error": err,
        }

    def account_trades(self, handle: str, deep: int = 5,
                       max_age: float | None = None) -> dict:
        """
        Open positions + the most recent closed trades for one handle.

        Deliberately NOT fetch_trades: that fans out over orderBy/networkId to beat
        the 25-row cap, which is right for a research pull and wrong for an account
        view. One call, ?deep=N, and the result is knowingly incomplete - the
        provider caps closed trades at 25 per chain and ships no cursor, so
        "complete" is a constant False, not something the caller can fix.
        """
        deep = _deep_surcharge(deep)   # coerces nan/inf/str without raising
        d = self._safe_get(f"/v2/users/{handle}/trades", "trades", max_age, deep=deep)
        err = _error_of(d)
        open_rows = [] if err else _rows(d, "open", "openPositions", "openTrades", "positions")
        closed_rows = [] if err else _rows(
            d, "closed", "closedTrades", "recentTrades", "history")
        if not err and not open_rows and not closed_rows:
            # flat shape: one list, split on each row's own status field
            flat = ([r for r in d if isinstance(r, dict)] if isinstance(d, list)
                    else _rows(d, "trades", "data", "results"))
            open_rows = [r for r in flat if _status(r) == "open"]
            closed_rows = [r for r in flat if _status(r) != "open"]
        return {
            "open": [_norm_trade(r, "open") for r in open_rows],
            "closed": [_norm_trade(r, "closed") for r in closed_rows],
            "complete": False,
            "error": err,
        }

    def stats(self) -> dict:
        return {"calls": self.calls, "cache_hits": self.cache_hits,
                "credits_spent": self.spent, "credits_left": self.remaining()}


# -- normalisation ------------------------------------------------------
# Field names vary across this provider's endpoints (trade_return below is the
# same story). Every helper here tries the documented spelling first, falls back
# to the plausible ones, and returns None rather than raising on anything odd.

def _deep_surcharge(deep) -> int:
    """
    ?deep=N costs 1 credit per extra upstream call. Rounded UP on purpose:
    over-stating the local counter only stops us early, under-stating it is how
    you get a surprise 402 mid-run.
    """
    try:
        return max(0, math.ceil(float(deep)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _dig(d, *keys):
    """First present, non-None value among several possible spellings."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rows(d, *keys) -> list[dict]:
    """The list under whichever key the endpoint used; only dict rows survive."""
    v = _dig(d, *keys)
    return [r for r in v if isinstance(r, dict)] if isinstance(v, list) else []


def _error_of(d) -> str | None:
    """_get signals failure as {"_error": ..., "_body": ...}; flatten it."""
    if isinstance(d, list):
        return None
    if not isinstance(d, dict):
        return f"unexpected response type: {type(d).__name__}"
    e = d.get("_error")
    if e is None:
        return None
    body = d.get("_body")
    return f"{e}: {body}" if body else str(e)


def _status(t: dict) -> str:
    """No status field? A closedAt is the only other evidence the row is closed."""
    v = _dig(t, "status", "state")
    if v is None:
        return "closed" if _dig(t, "closedAt", "closed_at") else "open"
    return str(v).lower()


def _token(row: dict) -> dict:
    t = row.get("token")
    return t if isinstance(t, dict) else {}


def _norm_holding(h: dict) -> dict:
    tok = _token(h)
    return {
        "symbol": _dig(tok, "symbol", "ticker") or _dig(h, "symbol", "tokenSymbol"),
        "address": _dig(tok, "address", "tokenAddress") or _dig(h, "address", "tokenAddress"),
        "network_id": _dig(tok, "networkId", "network_id", "network") or _dig(h, "networkId", "network"),
        "chain": _dig(h, "chain", "chainId") or _dig(tok, "chain"),
        "amount": _num(_dig(h, "amount", "balance", "quantity")),
        "price_usd": _num(_dig(h, "priceUsd", "price_usd", "price")),
        "value_usd": _num(_dig(h, "valueUsd", "value_usd", "usdValue")),
        "change_24h": _num(_dig(h, "change24h", "change_24h", "priceChange24h")),
    }


def _norm_trade(t: dict, status_hint: str) -> dict:
    tok = _token(t)
    st = _dig(t, "status", "state")
    return {
        "trade_id": _dig(t, "tradeId", "trade_id", "id"),
        "symbol": _dig(tok, "symbol", "ticker") or _dig(t, "symbol", "tokenSymbol"),
        "chain": _dig(t, "chain", "chainId") or _dig(tok, "networkId", "chain"),
        # trust the row's own status; the hint is only which list it arrived in
        "status": str(st).lower() if st is not None else status_hint,
        "amount": _num(_dig(t, "amount", "quantity", "size")),
        "avg_entry_price": _num(_dig(t, "avgEntryPrice", "avg_entry_price", "entryPrice", "avgEntry")),
        "avg_exit_price": _num(_dig(t, "avgExitPrice", "avg_exit_price", "exitPrice", "avgExit")),
        "realized_pnl_usd": _num(_dig(t, "realizedPnlUsd", "realized_pnl_usd", "realizedPnl", "pnlUsd")),
        "unrealized_pnl_usd": _num(_dig(t, "unrealizedPnlUsd", "unrealized_pnl_usd", "unrealizedPnl")),
        "created_at": _dig(t, "createdAt", "created_at", "openedAt", "timestamp"),
        "closed_at": _dig(t, "closedAt", "closed_at", "exitAt"),
    }


# -- return extraction --------------------------------------------------
def trade_return(t: dict) -> float | None:
    """
    Convert one API trade row into a simple return (0.25 = +25%).
    Field names vary; try the plausible ones, in order of directness.
    """
    for k in ("roi", "returnPct", "pnlPct", "realizedPnlPct", "profitPct"):
        v = t.get(k)
        if isinstance(v, (int, float)):
            return v / 100.0 if abs(v) > 3 else float(v)
    # entry/exit prices
    e = t.get("entryPrice") or t.get("avgEntry") or t.get("avgEntryPrice")
    x = t.get("exitPrice") or t.get("avgExit") or t.get("avgExitPrice")
    try:
        if e and x and float(e) > 0:
            return float(x) / float(e) - 1.0
    except (TypeError, ValueError):
        pass
    # realized pnl over cost basis
    pnl = t.get("realizedPnlUsd") or t.get("pnlUsd") or t.get("realizedPnl")
    cost = (t.get("costUsd") or t.get("investedUsd") or t.get("costBasisUsd")
            or t.get("volumeUsd") or t.get("costBasis"))
    # Last resort: amount * avg entry. Only valid when both are present and the
    # row is a closed trade, otherwise it silently invents a cost basis.
    if not cost and e:
        try:
            amt = float(t.get("amount") or 0)
            cost = amt * float(e) if amt > 0 else None
        except (TypeError, ValueError):
            cost = None
    try:
        if pnl is not None and cost and float(cost) > 0:
            return float(pnl) / float(cost)
    except (TypeError, ValueError):
        pass
    return None


def trade_time(t: dict) -> float:
    for k in ("closedAt", "timestamp", "exitAt", "updatedAt", "createdAt", "openedAt"):
        v = t.get(k)
        if isinstance(v, (int, float)):
            return float(v if v < 1e12 else v / 1000.0)
        if isinstance(v, str):
            try:
                from datetime import datetime
                return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
    return 0.0
