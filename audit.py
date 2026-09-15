#!/usr/bin/env python3
"""
Audit tool for the trade ledger.

  python3 audit.py verify              chain integrity — was anything edited?
  python3 audit.py onchain             do the live signatures actually exist on Solana?
  python3 audit.py failures            every failure, newest first
  python3 audit.py summary             event counts by mode
  python3 audit.py export trades.csv   accounting/tax export (FIFO cost basis)
  python3 audit.py open                positions the ledger says are still open

LIVE and PAPER are never mixed. Every export is tagged and filtered by mode.
"""
from __future__ import annotations
import csv, json, sys, os
from collections import defaultdict, deque
from ct.ledger import Ledger

LEDGER = os.environ.get("LEDGER_FILE", "ledger.db")
RPC = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")


def _L():
    return Ledger(LEDGER)


def cmd_verify():
    r = _L().verify()
    print()
    if r["ok"]:
        print(f"  CHAIN INTACT — {r['entries']:,} entries")
        print(f"  tip {r['tip'][:32]}…")
        print("\n  Every record hashes to the one before it. Nothing was edited,")
        print("  deleted or reordered since it was written.")
    else:
        print(f"  *** CHAIN BROKEN AT ENTRY {r['broken_at']} ***")
        print(f"  {r['reason']}")
        print(f"\n  Entries up to {r['broken_at']-1} are still trustworthy.")
        print("  Everything from that point on must be treated as unverified.")
    print()
    return 0 if r["ok"] else 1


def cmd_onchain():
    import requests
    L = _L()
    sigs = L.live_signatures()
    print(f"\n  {len(sigs)} live transaction signatures in the ledger")
    if not sigs:
        print("  (nothing to check — no live trades recorded yet)\n")
        return 0
    bad = []
    for i in range(0, len(sigs), 100):
        chunk = sigs[i:i + 100]
        try:
            r = requests.post(RPC, timeout=30, json={
                "jsonrpc": "2.0", "id": 1, "method": "getSignatureStatuses",
                "params": [chunk, {"searchTransactionHistory": True}]})
            vals = r.json().get("result", {}).get("value", [])
        except Exception as e:
            print(f"  RPC error: {e}")
            return 1
        for s, v in zip(chunk, vals):
            if v is None:
                bad.append((s, "NOT FOUND on chain"))
            elif v.get("err"):
                bad.append((s, f"on chain but FAILED: {v['err']}"))
    if bad:
        print(f"  *** {len(bad)} PROBLEM SIGNATURES ***")
        for s, why in bad[:20]:
            print(f"    {s[:44]}…  {why}")
        print("\n  A logged trade with no matching on-chain transaction means the")
        print("  ledger and reality disagree. Investigate before trusting P&L.\n")
        return 1
    print(f"  all {len(sigs)} confirmed on chain\n")
    return 0


def cmd_failures():
    rows = _L().failures()
    print(f"\n  {len(rows)} failure events\n")
    if not rows:
        print("  none recorded\n")
        return 0
    for r in rows[:60]:
        d = json.loads(r["detail"] or "{}")
        note = d.get("error") or d.get("reason") or d.get("result") or ""
        flag = "  <-- POSITION STILL OPEN" if d.get("position_still_open") else ""
        print(f"  [{r['seq']:>5}] {r['ts'][:19]}  {r['mode']:<5} {r['kind']:<16} "
              f"{(r['symbol'] or '-'):<12} {str(note)[:60]}{flag}")
    print()
    return 0


def cmd_summary():
    s = _L().summary()
    print()
    for mode in sorted(s):
        total = sum(s[mode].values())
        print(f"  {mode.upper()}  ({total:,} events)")
        for k, c in sorted(s[mode].items(), key=lambda x: -x[1]):
            print(f"      {c:>7,}  {k}")
        print()
    if not s:
        print("  ledger is empty\n")
    return 0


def cmd_open():
    """Reconstruct open positions from the ledger alone — independent of state.json."""
    L = _L()
    held = defaultdict(float)
    meta = {}
    for r in L.q("SELECT * FROM ledger WHERE kind IN ('entry_filled','exit_filled') "
                 "AND ok=1 ORDER BY seq"):
        if r["kind"] == "entry_filled":
            held[r["token"]] += r["qty"] or 0
            meta[r["token"]] = (r["symbol"], r["mode"])
        else:
            held[r["token"]] -= r["qty"] or 0
    live = {t: q for t, q in held.items() if q > 1e-9}
    print(f"\n  {len(live)} open position(s) per the ledger\n")
    for t, q in live.items():
        sym, mode = meta.get(t, ("?", "?"))
        print(f"    {sym:<14} {q:>18,.4f} units   [{mode}]   {t[:16]}…")
    print("\n  Compare against your wallet. A mismatch is a bug, not noise.\n")
    return 0


def cmd_export(path="trades.csv", mode=None):
    """
    FIFO cost-basis export. One row per CLOSED lot — the taxable events.
    Open positions are not included; they are unrealised.
    """
    L = _L()
    q = "SELECT * FROM ledger WHERE kind IN ('entry_filled','exit_filled') AND ok=1"
    args = ()
    if mode:
        q += " AND mode=?"
        args = (mode,)
    rows = L.q(q + " ORDER BY seq", args)

    lots: dict[str, deque] = defaultdict(deque)
    out = []
    for r in rows:
        tok, qty = r["token"], (r["qty"] or 0)
        if r["kind"] == "entry_filled":
            d = json.loads(r["detail"] or "{}")
            lots[tok].append({"ts": r["ts"], "qty": qty,
                              "cost_usd": r["size_usd"] or 0,
                              "price": r["price"] or 0,
                              "fee": d.get("entry_cost_usd", 0),
                              "sig": r["signature"] or ""})
            continue
        d = json.loads(r["detail"] or "{}")
        remaining = qty
        proceeds_total = (r["size_usd"] or 0) + (r["net_usd"] or 0)
        px = r["price"] or 0
        while remaining > 1e-12 and lots[tok]:
            lot = lots[tok][0]
            take = min(remaining, lot["qty"])
            frac = take / lot["qty"] if lot["qty"] else 0
            basis = lot["cost_usd"] * frac
            proceeds = px * take
            out.append({
                "mode": r["mode"],
                "acquired_utc": lot["ts"][:19],
                "disposed_utc": r["ts"][:19],
                "symbol": r["symbol"],
                "token_address": tok,
                "quantity": f"{take:.9f}",
                "cost_basis_usd": f"{basis:.6f}",
                "proceeds_usd": f"{proceeds:.6f}",
                "fees_usd": f"{(lot['fee'] * frac) + d.get('costs_usd', 0) * (take/qty if qty else 0):.6f}",
                "realised_pnl_usd": f"{proceeds - basis:.6f}",
                "exit_reason": d.get("reason", ""),
                "hold_hours": d.get("hold_hours", ""),
                "buy_signature": lot["sig"],
                "sell_signature": r["signature"] or "",
            })
            lot["qty"] -= take
            lot["cost_usd"] -= basis
            remaining -= take
            if lot["qty"] <= 1e-12:
                lots[tok].popleft()

    if not out:
        print("\n  No closed lots to export yet.\n")
        return 0
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    realised = sum(float(r["realised_pnl_usd"]) for r in out)
    by_mode = defaultdict(float)
    for r in out:
        by_mode[r["mode"]] += float(r["realised_pnl_usd"])
    print(f"\n  wrote {len(out)} closed lots -> {path}  (FIFO cost basis)")
    for m, v in by_mode.items():
        print(f"    {m:<6} realised P&L  ${v:+,.2f}")
    print(f"    {'TOTAL':<6} realised P&L  ${realised:+,.2f}")
    print("\n  Open positions are excluded — they are unrealised.")
    print("  This is a record of what happened, not tax advice.\n")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    fns = {"verify": cmd_verify, "onchain": cmd_onchain, "failures": cmd_failures,
           "summary": cmd_summary, "open": cmd_open}
    if cmd == "export":
        sys.exit(cmd_export(*(sys.argv[2:4] or ["trades.csv"])))
    if cmd not in fns:
        print(__doc__)
        sys.exit(2)
    sys.exit(fns[cmd]())
