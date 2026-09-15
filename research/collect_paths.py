#!/usr/bin/env python3
"""
Collect REAL minute-level price paths for Solana memecoin pools.

Source: GeckoTerminal (free, no key, 1000 minute candles ≈ 16h per pool).

This is the data the exit rules get fitted on. Arbitrary stop/target numbers
picked by hand are worth nothing; these paths are what actually happened.
"""
from __future__ import annotations
import json, time, sys
from pathlib import Path
import requests

GT = "https://api.geckoterminal.com/api/v2"
OUT = Path("paths")
S = requests.Session()
S.headers.update({"Accept": "application/json", "User-Agent": "copytrader-research/0.1"})
LAST = [0.0]


def get(path, **params):
    # free tier ~30 calls/min
    dt = time.time() - LAST[0]
    if dt < 2.1:
        time.sleep(2.1 - dt)
    LAST[0] = time.time()
    try:
        r = S.get(f"{GT}{path}", params=params or None, timeout=30)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def pools(kind="trending_pools", pages=4):
    out = []
    for p in range(1, pages + 1):
        d = get(f"/networks/solana/{kind}", page=p)
        for row in (d or {}).get("data", []):
            a = row.get("attributes", {})
            try:
                liq = float(a.get("reserve_in_usd") or 0)
                mc = float(a.get("market_cap_usd") or a.get("fdv_usd") or 0)
                vol = float((a.get("volume_usd") or {}).get("h24") or 0)
            except (TypeError, ValueError):
                continue
            out.append({"id": row.get("id", ""),
                        "address": row.get("id", "").split("_")[-1],
                        "name": a.get("name", ""), "liquidity": liq,
                        "market_cap": mc, "volume_h24": vol,
                        "created": a.get("pool_created_at", ""),
                        "source": kind})
    return out


def ohlcv(addr, tf="minute", agg=1, limit=1000):
    d = get(f"/networks/solana/pools/{addr}/ohlcv/{tf}", aggregate=agg, limit=limit)
    try:
        rows = d["data"]["attributes"]["ohlcv_list"]
    except (TypeError, KeyError):
        return []
    # API returns newest-first; we want chronological
    return sorted(rows, key=lambda r: r[0])


def main():
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    OUT.mkdir(exist_ok=True)
    cand = []
    for kind, pg in (("trending_pools", 10), ("pools", 6), ("new_pools", 4)):
        got = pools(kind, pg)
        print(f"  {kind}: {len(got)}")
        cand += got

    seen, uniq = set(), []
    for c in cand:
        if c["address"] and c["address"] not in seen:
            seen.add(c["address"]); uniq.append(c)

    # only pools our safety filter would plausibly consider
    ok = [c for c in uniq if 15_000 <= c["liquidity"] <= 80_000_000 and c["volume_h24"] >= 25_000]
    print(f"  unique {len(uniq)} -> plausible for our filter: {len(ok)}")
    ok.sort(key=lambda c: -c["volume_h24"])

    saved = 0
    for c in ok[:target]:
        f = OUT / f"{c['address']}.json"
        if f.exists():
            saved += 1; continue
        rows = ohlcv(c["address"])
        if len(rows) < 150:
            continue
        f.write_text(json.dumps({"meta": c, "ohlcv": rows}))
        saved += 1
        if saved % 10 == 0:
            print(f"    saved {saved}…", flush=True)
    print(f"  saved {saved} price paths to {OUT}/")


if __name__ == "__main__":
    main()
