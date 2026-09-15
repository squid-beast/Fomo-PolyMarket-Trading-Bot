"""
Volume surge: is CURRENT activity genuinely above this token's own baseline?

Compares the 1h rate against the 24h average rate for the same token, so it
is self-normalising — no cross-token volume comparison, which would just
rank by market cap.
"""
from __future__ import annotations
from .base import Strategy, Signal
from ..datasource import Pair


class VolumeSurge(Strategy):
    name = "volume_surge"

    def evaluate(self, p: Pair) -> Signal:
        why: list[str] = []
        score = 0.0

        baseline_h = p.volume_h24 / 24.0
        if baseline_h <= 0:
            return Signal(self.name, 0.0, ["no 24h volume baseline"])

        surge = p.volume_h1 / baseline_h
        if surge >= 1.2:
            s = self._band(surge, 1.2, 6.0) * 40.0
            score += s
            why.append(f"1h volume {surge:.1f}x its own baseline (+{s:.0f})")
        else:
            why.append(f"1h volume {surge:.1f}x baseline — no surge (+0)")

        # 5m rate should corroborate the 1h surge
        if p.volume_h1 > 0:
            m5_rate = (p.volume_m5 * 12.0) / p.volume_h1
            if m5_rate >= 1.0:
                s = self._band(m5_rate, 1.0, 3.0) * 20.0
                score += s
                why.append(f"5m rate {m5_rate:.1f}x 1h rate (+{s:.0f})")

        # transaction count must back the dollar volume (else: few huge wash trades)
        if p.txns_h1 >= 40:
            s = self._band(p.txns_h1, 40, 400) * 25.0
            score += s
            why.append(f"{p.txns_h1} txns/1h (+{s:.0f})")
        else:
            why.append(f"only {p.txns_h1} txns/1h (+0)")

        # average trade size sanity: huge $/tx on few txns = manufactured
        avg = p.volume_h1 / p.txns_h1 if p.txns_h1 else 0
        if 0 < avg <= 2000:
            score += 15.0
            why.append(f"retail-sized avg trade ${avg:,.0f} (+15)")
        elif avg > 10000:
            score -= 20.0
            why.append(f"whale/wash avg trade ${avg:,.0f} (-20)")

        return Signal(self.name, max(0.0, min(100.0, score)), why)
