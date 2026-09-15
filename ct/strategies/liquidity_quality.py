"""
Liquidity quality: can we actually GET OUT?

This strategy exists because the others measure opportunity and this one
measures escapability. In thin pools the exit is the whole trade.
"""
from __future__ import annotations
from .base import Strategy, Signal
from ..datasource import Pair


class LiquidityQuality(Strategy):
    name = "liquidity_quality"

    def evaluate(self, p: Pair) -> Signal:
        why: list[str] = []
        score = 0.0

        # deeper pool = cheaper exit. $50k..$1.5M is the sweet band.
        s = self._band(p.liquidity_usd, 50_000, 1_500_000) * 35.0
        score += s
        why.append(f"liquidity ${p.liquidity_usd:,.0f} (+{s:.0f})")

        # healthy vol/liq turnover: active but not churning
        vl = p.vol_to_liq
        if 1.0 <= vl <= 15.0:
            score += 25.0
            why.append(f"healthy turnover {vl:.1f}x (+25)")
        elif vl > 30.0:
            score -= 15.0
            why.append(f"excessive turnover {vl:.1f}x (-15)")
        else:
            why.append(f"turnover {vl:.1f}x (+0)")

        # FDV/liquidity: how propped-up is the valuation
        fl = p.fdv_to_liq
        if fl <= 40.0:
            s2 = (1.0 - self._band(fl, 5.0, 40.0)) * 25.0
            score += s2
            why.append(f"FDV/liq {fl:.0f}x (+{s2:.0f})")
        else:
            why.append(f"FDV/liq {fl:.0f}x — thin float (+0)")

        # two-sided flow = a real market with real sellers to buy from
        br = p.buy_ratio_h1
        if 0.45 <= br <= 0.65:
            score += 15.0
            why.append(f"balanced flow (buy ratio {br:.2f}) (+15)")

        return Signal(self.name, max(0.0, min(100.0, score)), why)
