"""
Read-only wallet sync.

The service's internal state can drift from reality — you might trade manually
in the fomo app, or a swap might land after the service gave up on it. So the
daemon reconciles against actual on-chain holdings rather than trusting itself.
"""
from __future__ import annotations
import requests

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


class Wallet:
    def __init__(self, rpc_url: str, timeout: int = 25):
        self.rpc, self.timeout = rpc_url, timeout
        self.s = requests.Session()

    def _rpc(self, method: str, params: list):
        try:
            r = self.s.post(self.rpc, timeout=self.timeout, json={
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            return r.json().get("result")
        except Exception:
            return None

    def sol_balance(self, pubkey: str) -> float:
        r = self._rpc("getBalance", [pubkey])
        try:
            return r["value"] / 1e9
        except (TypeError, KeyError):
            return 0.0

    def token_amounts(self, pubkey: str) -> dict[str, dict] | None:
        """
        {mint: {"raw": int, "decimals": int, "ui": float}} per mint held.

        `raw` is the ATOMIC on-chain amount — the unit Jupiter's `amount` takes.
        The RPC sends it as a string and it stays an int all the way through:
        putting it through a float is the precision bug this exists to avoid.

        None means the RPC did not answer — UNKNOWN, which is NOT "holds
        nothing". Anything about to spend money must fail closed on None.
        """
        r = self._rpc("getTokenAccountsByOwner",
                      [pubkey, {"programId": TOKEN_PROGRAM}, {"encoding": "jsonParsed"}])
        if not isinstance(r, dict):
            return None
        out: dict[str, dict] = {}
        for acc in r.get("value", []) or []:
            try:
                info = acc["account"]["data"]["parsed"]["info"]
                ta = info["tokenAmount"]
                raw, dec = int(ta["amount"]), int(ta["decimals"])
                ui = float(ta["uiAmount"] or 0)
                mint = info["mint"]
            except (KeyError, TypeError, ValueError):
                continue
            e = out.setdefault(mint, {"raw": 0, "decimals": dec, "ui": 0.0})
            e["raw"] += raw          # one owner can hold several accounts per mint
            e["ui"] += ui
        return out

    def token_balances(self, pubkey: str) -> dict[str, float]:
        """{mint: ui_amount} for every non-zero SPL balance."""
        return {m: a["ui"] for m, a in (self.token_amounts(pubkey) or {}).items()
                if a["ui"] > 0}

    def reconcile(self, expected: dict[str, float], pubkey: str,
                  tolerance: float = 0.02) -> list[str]:
        """Flag any position the chain disagrees with. Drift is a bug, not noise."""
        actual = self.token_balances(pubkey)
        issues = []
        for mint, qty in expected.items():
            have = actual.get(mint, 0.0)
            if qty > 0 and have <= 0:
                issues.append(f"{mint[:8]}… expected {qty:.4f}, wallet holds NONE")
            elif qty > 0 and abs(have - qty) / qty > tolerance:
                issues.append(f"{mint[:8]}… expected {qty:.4f}, wallet holds {have:.4f}")
        for mint, have in actual.items():
            if mint not in expected and have > 0:
                issues.append(f"{mint[:8]}… held on-chain but UNTRACKED ({have:.4f})")
        return issues
