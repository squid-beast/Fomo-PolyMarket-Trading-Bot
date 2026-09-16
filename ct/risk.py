"""
RISK ENGINE — the hard gate.

Sits AFTER scoring and AFTER any LLM opinion. Nothing upstream can override
it. If Claude says 99% confidence and the risk engine says no, the answer is
no. This is the single most important architectural property of the system.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta


@dataclass
class RiskVerdict:
    approved: bool
    size_usd: float
    reasons: list[str]

    @property
    def reason_str(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "approved"


class RiskEngine:
    def __init__(self, cfg):
        r = cfg.get_path("risk", {})
        self.max_pos_pct = float(r.get("max_position_pct", 2.0))
        self.max_token_pct = float(r.get("max_token_exposure_pct", 2.0))
        self.max_total_pct = float(r.get("max_total_exposure_pct", 20.0))
        self.max_concurrent = int(r.get("max_concurrent_positions", 6))
        self.max_daily_loss_pct = float(r.get("max_daily_loss_pct", 3.0))
        self.min_position_usd = float(r.get("min_position_usd", 15.0))
        self.cooldown_min = int(r.get("cooldown_minutes_after_exit", 240))
        self.max_entries_day = int(r.get("max_entries_per_day", 12))
        self.min_cash_pct = float(r.get("min_cash_reserve_pct", 25.0))
        self.min_edge_pct = float(r.get("min_expected_edge_pct", 1.5))
        self.require_edge = bool(r.get("require_edge_gate", True))

    def evaluate(self, pf, pair, cost_model, expected_edge_pct: float | None = None) -> RiskVerdict:
        reasons: list[str] = []
        equity = pf.equity()
        now = datetime.now(timezone.utc)

        # --- circuit breakers ------------------------------------------
        if pf.daily_loss_pct(now) >= self.max_daily_loss_pct:
            reasons.append(f"DAILY_LOSS_LIMIT({pf.daily_loss_pct(now):.1f}%>={self.max_daily_loss_pct}%)")
        if pf.entries_today(now) >= self.max_entries_day:
            reasons.append(f"max_entries_today({pf.entries_today(now)})")
        if len(pf.positions) >= self.max_concurrent:
            reasons.append(f"max_concurrent({len(pf.positions)})")

        # --- no doubling up / cooldown ---------------------------------
        if pair.token_address in pf.positions:
            reasons.append("already_holding")
        last = pf.last_exit_at(pair.token_address)
        if last and now - last < timedelta(minutes=self.cooldown_min):
            mins = (now - last).total_seconds() / 60
            reasons.append(f"cooldown({mins:.0f}m<{self.cooldown_min}m)")

        # --- CASH FLOOR: never deploy the last of the account ----------
        # Being at $0 cash means no ability to act on anything, including
        # taking profit on the position that is working.
        floor_cash = equity * (self.min_cash_pct / 100.0)
        spendable = max(0.0, pf.cash - floor_cash)
        if spendable <= 0:
            reasons.append(f"cash_floor(${pf.cash:.2f} at/below {self.min_cash_pct:.0f}% reserve)")

        # --- sizing ----------------------------------------------------
        size = equity * (self.max_pos_pct / 100.0)
        exposure = pf.total_exposure()
        headroom = equity * (self.max_total_pct / 100.0) - exposure
        if headroom < size:
            # Trimming size to fit the exposure cap is an ADJUSTMENT, not a
            # rejection. Only the floor check below can reject on size.
            size = max(0.0, headroom)
        if size > spendable:
            size = max(0.0, spendable)

        # --- the floor that $25 accounts fail --------------------------
        if size < self.min_position_usd:
            reasons.append(f"position_below_floor(${size:.2f}<${self.min_position_usd:.2f})")

        # --- liquidity must be KNOWN before anything downstream ---------
        # DexScreener returns 0.0 when it has no liquidity.usd at all (see
        # ct/datasource._f), and a malformed value can arrive as NaN. Both the
        # friction gate and the edge gate below need a real pool size, so
        # without one they used to be SKIPPED — which let the pairs we know
        # least about return approved=True. ct/safety.py rejects thin pools in
        # the scan path, but the risk engine is a hard gate on its own
        # (non-negotiables #1). Missing data is a reason to refuse, never a
        # reason to skip a gate. `not liq > 0` (rather than `liq <= 0`) is
        # deliberate: it also catches NaN.
        liq = pair.liquidity_usd
        if not isinstance(liq, (int, float)) or not liq > 0:
            reasons.append(f"no_liquidity_data(liquidity_usd={liq!r})")

        # --- friction sanity: never enter a trade the costs eat --------
        elif size > 0:
            rt = cost_model.round_trip_pct(size, pair.liquidity_usd)
            if rt > 8.0:
                reasons.append(f"friction_too_high({rt:.1f}%_round_trip)")

            # --- EDGE GATE -------------------------------------------------
            # The expected move must clear round-trip friction by a margin.
            # Research on real price paths (research/FINDINGS.md) measured the
            # current entry signal at roughly +0.65% over 15 minutes, decaying
            # to -4.69% over 2 hours, against ~4% friction. With no calibrated
            # edge supplied, this gate refuses to trade — which is correct.
            if self.require_edge:
                if expected_edge_pct is None:
                    reasons.append(
                        f"no_measured_edge(signal unvalidated; friction {rt:.1f}%)")
                elif expected_edge_pct < rt + self.min_edge_pct:
                    reasons.append(
                        f"edge_below_friction(edge {expected_edge_pct:.2f}% < "
                        f"friction {rt:.2f}% + margin {self.min_edge_pct:.2f}%)")

        return RiskVerdict(approved=not reasons, size_usd=round(size, 2), reasons=reasons)
