"""
Safety filter — hard rejects that run BEFORE any scoring.

Design rule: a token must EARN its way past every check. Nothing here is a
score or a weight; these are binary gates. A strategy scoring 99/100 on a
token that fails one safety check is still rejected. Scoring never overrides
safety.

Every rejection is logged with its reason so the funnel is auditable.
"""
from __future__ import annotations
from dataclasses import dataclass
from .datasource import Pair


@dataclass
class SafetyResult:
    passed: bool
    reasons: list[str]

    @property
    def reason_str(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "ok"


class SafetyFilter:
    def __init__(self, cfg):
        s = cfg.get_path("safety", {})
        self.min_liq = float(s.get("min_liquidity_usd", 25000))
        self.max_liq = float(s.get("max_liquidity_usd", 5_000_000))
        self.min_age = float(s.get("min_pair_age_minutes", 45))
        self.max_age_d = float(s.get("max_pair_age_days", 30))
        self.min_vol = float(s.get("min_volume_h24_usd", 50000))
        self.max_vl = float(s.get("max_vol_to_liq_ratio", 60.0))
        self.min_vl = float(s.get("min_vol_to_liq_ratio", 0.35))
        self.min_tx = int(s.get("min_txns_h1", 25))
        self.max_fl = float(s.get("max_fdv_to_liq_ratio", 250.0))
        self.min_br = float(s.get("min_buy_ratio_h1", 0.35))
        self.max_br = float(s.get("max_buy_ratio_h1", 0.88))
        self.blocked = {b.upper() for b in (s.get("blocked_symbols") or [])}
        # --- Tier 2 gates ---
        self.max_off_high = float(s.get("max_pct_off_1h_high", 25.0))
        self.max_top10 = float(s.get("max_top10_holder_pct", 25.0))
        self.require_verified = bool(s.get("require_verified", False))
        self.block_collisions = bool(s.get("block_ticker_collisions", True))
        self.min_holders = int(s.get("min_holders", 0))
        self.max_sell_buy = float(s.get("max_sell_buy_ratio_h1", 1.35))
        # Initialised here so check() is safe standalone. A filter that
        # raises AttributeError when prescan() was skipped would take the
        # whole scan down; without prescan it simply finds no collisions.
        self._symbol_counts: dict[str, int] = {}
        self._active_prefixes: dict[str, set] = {}

    def prescan(self, pairs) -> None:
        """
        Count symbols so ticker collisions can be detected — the
        JACKET / Jacket / JACKCAT problem, where lookalike tokens trend at the
        same moment and you buy the wrong one.

        Only ACTIVE tokens count. Nineteen dead clones of "STAR" doing $46/day
        create no real confusion risk for the one legitimate STAR; flagging the
        good token because impostors exist would reject most of the market.
        A collision needs at least two tokens a person could plausibly mix up,
        which means at least two that are actually trading.
        """
        self._symbol_counts = {}
        self._active_prefixes = {}
        for p in pairs:
            if p.liquidity_usd < self.min_liq or p.volume_h24 < self.min_vol:
                continue                       # not confusable with anything
            k = p.symbol.strip().upper()
            self._symbol_counts[k] = self._symbol_counts.get(k, 0) + 1
            if len(k) >= 4:                    # near-miss: shared 4-char stem
                self._active_prefixes.setdefault(k[:4], set()).add(k)

    def check(self, p: Pair) -> SafetyResult:
        r: list[str] = []

        if p.price_usd <= 0:
            r.append("no_price")
        if p.symbol.upper() in self.blocked:
            r.append("blocked_symbol")

        # -- liquidity band ------------------------------------------------
        if p.liquidity_usd < self.min_liq:
            r.append(f"liquidity_too_low({p.liquidity_usd:,.0f}<{self.min_liq:,.0f})")
        elif p.liquidity_usd > self.max_liq:
            r.append(f"liquidity_too_high({p.liquidity_usd:,.0f})")

        # -- age band: skip the launch-snipe window entirely ----------------
        age_m = p.age_minutes
        if age_m < self.min_age:
            r.append(f"too_new({age_m:.0f}m<{self.min_age:.0f}m)")
        elif age_m > self.max_age_d * 1440:
            r.append(f"too_old({age_m/1440:.1f}d)")

        # -- activity ------------------------------------------------------
        if p.volume_h24 < self.min_vol:
            r.append(f"volume_too_low({p.volume_h24:,.0f})")
        if p.txns_h1 < self.min_tx:
            r.append(f"too_few_txns_h1({p.txns_h1}<{self.min_tx})")

        # -- wash-trading / dead-pool smell --------------------------------
        vl = p.vol_to_liq
        if vl > self.max_vl:
            r.append(f"wash_trading_smell(vol/liq={vl:.0f}x)")
        elif vl < self.min_vl:
            r.append(f"stagnant(vol/liq={vl:.2f}x)")

        # -- thin float propped at a huge valuation ------------------------
        if p.fdv_to_liq > self.max_fl:
            r.append(f"fdv_liq_ratio({p.fdv_to_liq:.0f}x>{self.max_fl:.0f}x)")

        # -- one-sided flow ------------------------------------------------
        if p.txns_h1 >= self.min_tx:
            br = p.buy_ratio_h1
            if br < self.min_br:
                r.append(f"sell_pressure(buy_ratio={br:.2f})")
            elif br > self.max_br:
                r.append(f"manufactured_buying(buy_ratio={br:.2f})")

        # -- Tier 2: fading off a spike ------------------------------------
        # We lack a true 1h high from this feed, so use the shape that matters:
        # a strong 1h run that is now rolling over on the 5m.
        if p.price_change_h1 > 20.0 and p.price_change_m5 < -3.0:
            r.append(f"rolling_over(h1+{p.price_change_h1:.0f}%,m5{p.price_change_m5:.1f}%)")
        if p.price_change_h6 > 0 and p.price_change_h1 < -self.max_off_high:
            r.append(f"faded_from_high(h1 {p.price_change_h1:.0f}%)")

        # -- Tier 2: sellers decisively winning ---------------------------
        if p.txns_h1_buys > 0:
            sb = p.txns_h1_sells / p.txns_h1_buys
            if sb > self.max_sell_buy:
                r.append(f"distribution(sell/buy={sb:.2f}>{self.max_sell_buy})")

        # -- Tier 2: ticker collision --------------------------------------
        if self.block_collisions:
            k = p.symbol.strip().upper()
            n = self._symbol_counts.get(k, 0)
            if n > 1:
                r.append(f"ticker_collision({n}_ACTIVE_tokens_share_'{k}')")
            elif len(k) >= 4:
                # near-miss lookalikes, e.g. JACKET beside JACKCAT
                sibs = self._active_prefixes.get(k[:4], set()) - {k}
                if sibs:
                    r.append(f"lookalike_ticker('{k}' vs {sorted(sibs)[:3]})")

        # -- Tier 2: holder data, when the feed provides it ----------------
        # DexScreener does not expose holders or concentration. These fire only
        # when a richer source (e.g. the fomo API) has populated them, and are
        # silently skipped otherwise rather than giving false assurance.
        top10 = getattr(p, "top10_holder_pct", None)
        if top10 is not None and top10 > self.max_top10:
            r.append(f"holder_concentration({top10:.1f}%>{self.max_top10:.0f}%)")
        holders = getattr(p, "holders", None)
        if holders is not None and self.min_holders and holders < self.min_holders:
            r.append(f"too_few_holders({holders}<{self.min_holders})")
        verified = getattr(p, "verified", None)
        if self.require_verified and verified is False:
            r.append("unverified_token")

        return SafetyResult(passed=not r, reasons=r)
