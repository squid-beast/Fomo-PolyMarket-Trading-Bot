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

    def token_balances(self, pubkey: str) -> dict[str, float]:
        """{mint: ui_amount} for every non-zero SPL balance."""
        r = self._rpc("getTokenAccountsByOwner",
                      [pubkey, {"programId": TOKEN_PROGRAM}, {"encoding": "jsonParsed"}])
        out: dict[str, float] = {}
        for acc in (r or {}).get("value", []) or []:
            try:
                info = acc["account"]["data"]["parsed"]["info"]
                amt = float(info["tokenAmount"]["uiAmount"] or 0)
                if amt > 0:
                    out[info["mint"]] = out.get(info["mint"], 0.0) + amt
            except (KeyError, TypeError, ValueError):
                continue
        return out

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
