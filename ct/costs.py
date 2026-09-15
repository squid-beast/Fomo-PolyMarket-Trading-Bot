"""
THE COST MODEL.

This is the most important file in the project. A paper-trading engine that
does not model friction honestly will report profits that do not exist.

Every simulated fill goes through here. Nothing bypasses it.

Each parameter is tagged measured/assumed in config.yaml. Today they are ALL
assumed. Replacing them with numbers measured from real micro-trades is what
turns this from a plausible simulation into a calibrated one.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Literal

Side = Literal["entry", "exit"]


@dataclass
class Fill:
    """A simulated fill with full cost attribution."""
    side: Side
    quoted_price: float
    effective_price: float
    size_usd: float
    slippage_pct: float
    platform_fee_usd: float
    pool_fee_usd: float
    fixed_cost_usd: float
    slippage_usd: float
    adverse_usd: float
    total_cost_usd: float
    total_cost_pct: float

    def as_dict(self) -> dict:
        return asdict(self)


class CostModel:
    def __init__(self, cfg):
        c = cfg.get_path("costs", {})
        self.platform_fee = float(c.get("platform_fee_pct", 0.5)) / 100.0
        self.pool_fee = float(c.get("pool_fee_pct", 1.0)) / 100.0
        self.fixed_tx = float(c.get("fixed_tx_cost_usd", 0.04))
        self.failed_rate = float(c.get("failed_tx_rate", 0.08))
        self.adverse = float(c.get("adverse_move_bps", 30)) / 10000.0
        self.stop_extra = float(c.get("stop_loss_extra_slip_bps", 150)) / 10000.0

    # -- price impact -------------------------------------------------------
    @staticmethod
    def slippage_frac(size_usd: float, liquidity_usd: float) -> float:
        """
        Constant-product price impact.

        For x*y=k, spending dx against quote reserve X, the average execution
        price is worse than spot by exactly dx/(X+dx).

        DexScreener's liquidity.usd is total pool TVL (both sides), so the
        quote-side reserve is ~liquidity/2.

        Note what this says: for SMALL orders slippage is nearly zero. That is
        correct and it matters — it means at small size the killer is fees and
        fixed costs, not price impact. The model should not flatter itself by
        pretending otherwise.
        """
        if liquidity_usd <= 0:
            return 1.0
        reserve = liquidity_usd / 2.0
        return size_usd / (reserve + size_usd)

    # -- fills --------------------------------------------------------------
    def fill(self, side: Side, quoted_price: float, size_usd: float,
             liquidity_usd: float, is_stop: bool = False) -> Fill:
        slip = self.slippage_frac(size_usd, liquidity_usd)
        adverse = self.adverse + (self.stop_extra if (is_stop and side == "exit") else 0.0)

        platform_usd = size_usd * self.platform_fee
        pool_usd = size_usd * self.pool_fee
        slip_usd = size_usd * slip
        adverse_usd = size_usd * adverse
        # failed txs cost gas but move nothing; amortise into expected fixed cost
        fixed_usd = self.fixed_tx * (1.0 + self.failed_rate)

        total = platform_usd + pool_usd + slip_usd + adverse_usd + fixed_usd
        drag = total / size_usd if size_usd > 0 else 1.0

        # Entry: you effectively pay MORE per token. Exit: you receive LESS.
        eff = quoted_price * (1.0 + drag) if side == "entry" else quoted_price * (1.0 - drag)

        return Fill(
            side=side, quoted_price=quoted_price, effective_price=max(eff, 1e-18),
            size_usd=size_usd, slippage_pct=slip * 100.0,
            platform_fee_usd=platform_usd, pool_fee_usd=pool_usd,
            fixed_cost_usd=fixed_usd, slippage_usd=slip_usd, adverse_usd=adverse_usd,
            total_cost_usd=total, total_cost_pct=drag * 100.0,
        )

    def round_trip_pct(self, size_usd: float, liquidity_usd: float) -> float:
        """Total friction to open AND close a position, as % of size."""
        a = self.fill("entry", 1.0, size_usd, liquidity_usd)
        b = self.fill("exit", 1.0, size_usd, liquidity_usd)
        return a.total_cost_pct + b.total_cost_pct

    def breakeven_move_pct(self, size_usd: float, liquidity_usd: float) -> float:
        """How far price must rise before the trade is worth anything."""
        return self.round_trip_pct(size_usd, liquidity_usd)
