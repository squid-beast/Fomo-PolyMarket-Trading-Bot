"""
DexScreener client.

HONEST LIMITATION, stated up front: DexScreener exposes no public
"all trending pairs" endpoint. Discovery here seeds from boosted tokens
(which are PAID promotions, therefore a biased universe) plus keyword
search. That bias is real and is recorded in the decision log so it can
be corrected for later. This module is deliberately swappable.
"""
from __future__ import annotations
import time
import threading
from dataclasses import dataclass
from typing import Any, Iterable
import requests

BASE = "https://api.dexscreener.com"
UA = {"User-Agent": "copytrader-paper/0.1 (research)"}


class _RateLimiter:
    def __init__(self, per_min: int):
        self.interval = 60.0 / max(per_min, 1)
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delta = time.time() - self._last
            if delta < self.interval:
                time.sleep(self.interval - delta)
            self._last = time.time()


@dataclass
class Pair:
    """Normalised view of a DexScreener pair. All the engine ever sees."""
    chain: str
    dex: str
    pair_address: str
    token_address: str
    symbol: str
    name: str
    price_usd: float
    liquidity_usd: float
    fdv: float
    market_cap: float
    volume_h24: float
    volume_h6: float
    volume_h1: float
    volume_m5: float
    txns_h1_buys: int
    txns_h1_sells: int
    txns_h24_buys: int
    txns_h24_sells: int
    price_change_m5: float
    price_change_h1: float
    price_change_h6: float
    price_change_h24: float
    pair_created_at_ms: int
    url: str

    # -- derived -----------------------------------------------------------
    @property
    def age_minutes(self) -> float:
        if not self.pair_created_at_ms:
            return 0.0
        return (time.time() * 1000 - self.pair_created_at_ms) / 60000.0

    @property
    def txns_h1(self) -> int:
        return self.txns_h1_buys + self.txns_h1_sells

    @property
    def buy_ratio_h1(self) -> float:
        t = self.txns_h1
        return self.txns_h1_buys / t if t else 0.0

    @property
    def vol_to_liq(self) -> float:
        return self.volume_h24 / self.liquidity_usd if self.liquidity_usd else 0.0

    @property
    def fdv_to_liq(self) -> float:
        return self.fdv / self.liquidity_usd if self.liquidity_usd else 0.0

    def snapshot(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d.update(age_minutes=round(self.age_minutes, 1), buy_ratio_h1=round(self.buy_ratio_h1, 3),
                 vol_to_liq=round(self.vol_to_liq, 2), fdv_to_liq=round(self.fdv_to_liq, 2))
        return d


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def parse_pair(raw: dict) -> Pair | None:
    try:
        base = raw.get("baseToken") or {}
        liq = raw.get("liquidity") or {}
        vol = raw.get("volume") or {}
        txn = raw.get("txns") or {}
        chg = raw.get("priceChange") or {}
        h1, h24 = txn.get("h1") or {}, txn.get("h24") or {}
        return Pair(
            chain=raw.get("chainId", ""), dex=raw.get("dexId", ""),
            pair_address=raw.get("pairAddress", ""), token_address=base.get("address", ""),
            symbol=(base.get("symbol") or "?")[:24], name=(base.get("name") or "?")[:64],
            price_usd=_f(raw.get("priceUsd")), liquidity_usd=_f(liq.get("usd")),
            fdv=_f(raw.get("fdv")), market_cap=_f(raw.get("marketCap")),
            volume_h24=_f(vol.get("h24")), volume_h6=_f(vol.get("h6")),
            volume_h1=_f(vol.get("h1")), volume_m5=_f(vol.get("m5")),
            txns_h1_buys=_i(h1.get("buys")), txns_h1_sells=_i(h1.get("sells")),
            txns_h24_buys=_i(h24.get("buys")), txns_h24_sells=_i(h24.get("sells")),
            price_change_m5=_f(chg.get("m5")), price_change_h1=_f(chg.get("h1")),
            price_change_h6=_f(chg.get("h6")), price_change_h24=_f(chg.get("h24")),
            pair_created_at_ms=_i(raw.get("pairCreatedAt")), url=raw.get("url", ""),
        )
    except Exception:
        return None


class DexScreener:
    def __init__(self, chain: str = "solana", timeout: int = 20):
        self.chain = chain
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update(UA)
        self._slow = _RateLimiter(55)    # boosts/token-pairs endpoints
        self._fast = _RateLimiter(280)   # search/tokens endpoints

    def _get(self, path: str, limiter: _RateLimiter, **params) -> Any:
        limiter.wait()
        try:
            r = self.s.get(f"{BASE}{path}", params=params or None, timeout=self.timeout)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            return None

    # -- discovery ---------------------------------------------------------
    def boosted_tokens(self) -> list[str]:
        out: list[str] = []
        for ep in ("/token-boosts/latest/v1", "/token-boosts/top/v1"):
            data = self._get(ep, self._slow)
            if isinstance(data, list):
                out += [d.get("tokenAddress") for d in data
                        if d.get("chainId") == self.chain and d.get("tokenAddress")]
        seen, uniq = set(), []
        for a in out:
            if a not in seen:
                seen.add(a); uniq.append(a)
        return uniq

    def pairs_for_tokens(self, addresses: Iterable[str]) -> list[Pair]:
        """Batch lookup, 30 addresses max per call."""
        addrs = list(addresses)
        pairs: list[Pair] = []
        for i in range(0, len(addrs), 30):
            chunk = ",".join(addrs[i:i + 30])
            data = self._get(f"/tokens/v1/{self.chain}/{chunk}", self._fast)
            if isinstance(data, list):
                pairs += [p for p in (parse_pair(r) for r in data) if p]
        return pairs

    def search(self, query: str) -> list[Pair]:
        data = self._get("/latest/dex/search", self._fast, q=query)
        if not isinstance(data, dict):
            return []
        return [p for p in (parse_pair(r) for r in data.get("pairs") or [])
                if p and p.chain == self.chain]

    def refresh(self, pair_address: str) -> Pair | None:
        """Re-fetch a single pair by address, for position management."""
        data = self._get(f"/latest/dex/pairs/{self.chain}/{pair_address}", self._fast)
        raw = None
        if isinstance(data, dict):
            ps = data.get("pairs") or ([data.get("pair")] if data.get("pair") else [])
            raw = ps[0] if ps else None
        return parse_pair(raw) if raw else None

    # -- universe ----------------------------------------------------------
    def discover(self, seeds: list[str], limit: int = 120) -> list[Pair]:
        pairs: list[Pair] = []
        boosted = self.boosted_tokens()
        if boosted:
            pairs += self.pairs_for_tokens(boosted)
        for s in seeds:
            pairs += self.search(s)
        # dedupe on pair address, keep deepest liquidity per token
        best: dict[str, Pair] = {}
        for p in pairs:
            if not p.pair_address or p.price_usd <= 0:
                continue
            cur = best.get(p.token_address)
            if cur is None or p.liquidity_usd > cur.liquidity_usd:
                best[p.token_address] = p
        ranked = sorted(best.values(), key=lambda x: x.volume_h24, reverse=True)
        return ranked[:limit]


# Wide seed set. Search returns up to 30 pairs per query, so the universe
# size is roughly len(SEEDS)*30 before dedupe. These are deliberately generic
# so the universe is not curated toward any thesis.
SEEDS = [
    "SOL", "USDC", "USDT", "bonk", "wif", "pump", "ai", "cat", "dog", "pepe",
    "moon", "meme", "inu", "baby", "trump", "elon", "gpt", "agent", "coin",
    "sol", "jup", "ray", "grok", "doge", "shib", "wojak", "chad", "frog",
    "bull", "bear", "gold", "fire", "king", "boss", "rich", "rock", "star",
]
