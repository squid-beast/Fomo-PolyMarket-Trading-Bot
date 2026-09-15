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
"""
from __future__ import annotations
import json, os, time, hashlib
from pathlib import Path
import requests

BASE = "https://api.fomoapi.io"
COST = {"trades": 250, "balances": 250, "search": 250, "leaderboard": 250,
        "user": 2500, "following": 250, "default": 250}


class BudgetExceeded(RuntimeError):
    pass


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

    def _get(self, path: str, kind: str = "default", **params):
        ck = self._ck(path, params)
        if ck.exists():
            self.cache_hits += 1
            return json.loads(ck.read_text())
        cost = COST.get(kind, 250)
        if self.spent + cost > self.budget:
            raise BudgetExceeded(
                f"would spend {cost}, only {self.remaining()} credits left "
                f"(spent {self.spent:,}/{self.budget:,})")
        try:
            r = self.s.get(f"{BASE}{path}", params=params or None, timeout=self.timeout)
        except Exception as e:
            return {"_error": str(e)}
        self.calls += 1
        self.spent += cost
        if r.status_code == 429:
            time.sleep(3)
            self.spent -= cost
            return self._get(path, kind, **params)
        if r.status_code != 200:
            body = r.text[:200]
            if r.status_code == 402:
                raise BudgetExceeded(f"API reports credits exhausted: {body}")
            return {"_error": f"http {r.status_code}", "_body": body}
        data = r.json()
        ck.write_text(json.dumps(data))
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

    def stats(self) -> dict:
        return {"calls": self.calls, "cache_hits": self.cache_hits,
                "credits_spent": self.spent, "credits_left": self.remaining()}


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
    e, x = t.get("entryPrice") or t.get("avgEntry"), t.get("exitPrice") or t.get("avgExit")
    try:
        if e and x and float(e) > 0:
            return float(x) / float(e) - 1.0
    except (TypeError, ValueError):
        pass
    # realized pnl over cost basis
    pnl = t.get("realizedPnlUsd") or t.get("pnlUsd") or t.get("realizedPnl")
    cost = t.get("costUsd") or t.get("investedUsd") or t.get("costBasisUsd") or t.get("volumeUsd")
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
