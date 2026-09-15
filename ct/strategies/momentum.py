"""
Momentum: is price trending up in a way that looks sustained rather than spiked?

Deliberately penalises vertical m5 moves. A token up 400% in five minutes is
not a momentum entry, it is someone else's exit.
"""
from __future__ import annotations
from .base import Strategy, Signal
from ..datasource import Pair


class Momentum(Strategy):
    name = "momentum"

    def evaluate(self, p: Pair) -> Signal:
        why: list[str] = []
        score = 0.0

        # sustained 6h trend is the backbone (want +5% to +60%)
        h6 = p.price_change_h6
        if h6 > 0:
            s = self._band(h6, 3.0, 60.0) * 35.0
            score += s
            why.append(f"6h trend +{h6:.1f}% (+{s:.0f})")
        else:
            why.append(f"6h trend {h6:.1f}% (+0)")

        # 1h should confirm, not dominate
        h1 = p.price_change_h1
        if 0.5 <= h1 <= 25.0:
            s = self._band(h1, 0.5, 15.0) * 25.0
            score += s
            why.append(f"1h confirming +{h1:.1f}% (+{s:.0f})")
        elif h1 > 25.0:
            why.append(f"1h overextended +{h1:.1f}% (+0)")
        else:
            why.append(f"1h not confirming {h1:.1f}% (+0)")

        # structure: 24h positive but 6h stronger => accelerating, not fading
        if p.price_change_h24 > 0 and h6 > p.price_change_h24 / 4:
            score += 20.0
            why.append("accelerating vs 24h (+20)")

        # PENALTY: vertical 5m spike = late
        m5 = p.price_change_m5
        if m5 > 12.0:
            pen = min(30.0, m5)
            score -= pen
            why.append(f"5m vertical spike +{m5:.1f}% (-{pen:.0f})")
        elif 0 < m5 <= 6.0:
            score += 20.0
            why.append(f"5m steady +{m5:.1f}% (+20)")

        return Signal(self.name, max(0.0, min(100.0, score)), why)
