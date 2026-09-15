"""Paper portfolio. Every fill priced through the CostModel — no exceptions."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from .costs import CostModel, Fill
from .datasource import Pair


@dataclass
class Position:
    token_address: str
    symbol: str
    pair_address: str
    qty: float
    entry_price_eff: float      # what we ACTUALLY paid per token, after costs
    entry_price_quoted: float   # what the screen said
    size_usd: float
    entry_liquidity: float
    opened_at: datetime
    high_water_quoted: float
    entry_cost_usd: float
    strategy_scores: dict = field(default_factory=dict)
    composite: float = 0.0

    def unrealised(self, price_quoted: float) -> float:
        return self.qty * price_quoted - self.size_usd

    def pnl_pct_quoted(self, price_quoted: float) -> float:
        if self.entry_price_quoted <= 0:
            return 0.0
        return (price_quoted / self.entry_price_quoted - 1.0) * 100.0


@dataclass
class Trade:
    symbol: str
    token_address: str
    opened_at: datetime
    closed_at: datetime
    size_usd: float
    entry_quoted: float
    exit_quoted: float
    gross_pnl_usd: float
    total_costs_usd: float
    net_pnl_usd: float
    net_pnl_pct: float
    exit_reason: str
    hold_hours: float
    composite: float


class Portfolio:
    def __init__(self, cfg, cost_model: CostModel):
        self.cfg = cfg
        self.cm = cost_model
        self.starting = float(cfg.get_path("portfolio.starting_equity_usd", 1000.0))
        self.cash = self.starting
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self._exits: dict[str, datetime] = {}
        self._marks: dict[str, float] = {}

    # -- valuation ----------------------------------------------------
    def total_exposure(self) -> float:
        return sum(p.size_usd for p in self.positions.values())

    def equity(self) -> float:
        held = 0.0
        for p in self.positions.values():
            mark = self._marks.get(p.token_address, p.entry_price_quoted)
            held += p.qty * mark
        return self.cash + held

    def mark(self, token: str, price: float) -> None:
        self._marks[token] = price

    # -- history helpers ----------------------------------------------
    def last_exit_at(self, token: str):
        return self._exits.get(token)

    def entries_today(self, now: datetime) -> int:
        d = now.date()
        return sum(1 for p in self.positions.values() if p.opened_at.date() == d) + \
               sum(1 for t in self.trades if t.opened_at.date() == d)

    def daily_loss_pct(self, now: datetime) -> float:
        d = now.date()
        realised = sum(t.net_pnl_usd for t in self.trades if t.closed_at.date() == d)
        return max(0.0, -realised) / self.starting * 100.0 if self.starting else 0.0

    # -- execution ----------------------------------------------------
    def open(self, pair: Pair, size_usd: float, scores: dict, composite: float) -> tuple[Position, Fill]:
        fill = self.cm.fill("entry", pair.price_usd, size_usd, pair.liquidity_usd)
        qty = size_usd / fill.effective_price
        now = datetime.now(timezone.utc)
        pos = Position(
            token_address=pair.token_address, symbol=pair.symbol, pair_address=pair.pair_address,
            qty=qty, entry_price_eff=fill.effective_price, entry_price_quoted=pair.price_usd,
            size_usd=size_usd, entry_liquidity=pair.liquidity_usd, opened_at=now,
            high_water_quoted=pair.price_usd, entry_cost_usd=fill.total_cost_usd,
            strategy_scores=scores, composite=composite,
        )
        self.cash -= size_usd
        self.positions[pair.token_address] = pos
        self.mark(pair.token_address, pair.price_usd)
        return pos, fill

    def close(self, token: str, price_quoted: float, liquidity: float,
              reason: str, is_stop: bool = False) -> tuple[Trade, Fill]:
        pos = self.positions.pop(token)
        notional = pos.qty * price_quoted
        fill = self.cm.fill("exit", price_quoted, notional, liquidity, is_stop=is_stop)
        proceeds = pos.qty * fill.effective_price
        now = datetime.now(timezone.utc)

        gross = notional - pos.size_usd
        costs = pos.entry_cost_usd + fill.total_cost_usd
        net = proceeds - pos.size_usd

        self.cash += proceeds
        self._exits[token] = now
        self._marks.pop(token, None)

        t = Trade(
            symbol=pos.symbol, token_address=token, opened_at=pos.opened_at, closed_at=now,
            size_usd=pos.size_usd, entry_quoted=pos.entry_price_quoted, exit_quoted=price_quoted,
            gross_pnl_usd=gross, total_costs_usd=costs, net_pnl_usd=net,
            net_pnl_pct=(net / pos.size_usd * 100.0) if pos.size_usd else 0.0,
            exit_reason=reason, hold_hours=(now - pos.opened_at).total_seconds() / 3600.0,
            composite=pos.composite,
        )
        self.trades.append(t)
        return t, fill

    # -- stats --------------------------------------------------------
    def stats(self) -> dict:
        n = len(self.trades)
        wins = [t for t in self.trades if t.net_pnl_usd > 0]
        losses = [t for t in self.trades if t.net_pnl_usd <= 0]
        net = sum(t.net_pnl_usd for t in self.trades)
        gross = sum(t.gross_pnl_usd for t in self.trades)
        costs = sum(t.total_costs_usd for t in self.trades)
        gw = sum(t.net_pnl_usd for t in wins)
        gl = abs(sum(t.net_pnl_usd for t in losses))
        return {
            "equity": round(self.equity(), 2), "cash": round(self.cash, 2),
            "starting": self.starting, "open_positions": len(self.positions),
            "return_pct": round((self.equity() / self.starting - 1) * 100, 2) if self.starting else 0,
            "trades": n, "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
            "gross_pnl": round(gross, 2), "total_costs": round(costs, 2), "net_pnl": round(net, 2),
            "profit_factor": round(gw / gl, 2) if gl else None,
            "avg_win": round(gw / len(wins), 2) if wins else 0.0,
            "avg_loss": round(-gl / len(losses), 2) if losses else 0.0,
            "cost_drag_pct_of_gross": round(costs / abs(gross) * 100, 1) if gross else None,
        }
