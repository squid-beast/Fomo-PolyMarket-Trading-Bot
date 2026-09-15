"""
Orchestrator. Wires: discover -> safety -> score -> risk -> paper fill,
plus position management on its own cadence.

Sequencing note: EXITS ARE PROCESSED BEFORE ENTRIES. Capital and risk slots
must be freed before new entries compete for them, otherwise the engine
under-trades for reasons that have nothing to do with the strategy.
"""
from __future__ import annotations
from datetime import datetime, timezone
from .config import load as load_cfg
from .costs import CostModel
from .datasource import DexScreener, SEEDS
from .safety import SafetyFilter
from .strategies import load_strategies
from .risk import RiskEngine
from .portfolio import Portfolio
from .store import Store


class Engine:
    def __init__(self, cfg=None, db_path="paper.db", verbose=True):
        self.cfg = cfg or load_cfg()
        self.verbose = verbose
        self.cm = CostModel(self.cfg)
        self.ds = DexScreener(self.cfg.get_path("run.chain", "solana"))
        self.safety = SafetyFilter(self.cfg)
        self.strategies = load_strategies(self.cfg)
        self.risk = RiskEngine(self.cfg)
        self.pf = Portfolio(self.cfg, self.cm)
        self.store = Store(db_path)
        s = self.cfg.get_path("strategies", {})
        self.min_score = float(s.get("min_composite_score", 62))
        self.min_agree = int(s.get("min_strategies_agreeing", 2))
        self.weights = s.get("weights", {}) or {}
        e = self.cfg.get_path("exits", {})
        self.stop = float(e.get("stop_loss_pct", 12))
        self.tp = float(e.get("take_profit_pct", 25))
        self.trail = float(e.get("trailing_stop_pct", 10))
        self.trail_arm = float(e.get("trail_arm_pct", 15))
        self.max_hold = float(e.get("max_hold_hours", 24))
        self.liq_collapse = float(e.get("liquidity_collapse_pct", 40))

    def _log(self, *a):
        if self.verbose:
            print(*a, flush=True)

    # -- scoring --------------------------------------------------------
    def score(self, pair):
        sigs = [s.evaluate(pair) for s in self.strategies]
        tw = sum(float(self.weights.get(s.name, 1.0)) for s in sigs)
        comp = sum(s.score * float(self.weights.get(s.name, 1.0)) for s in sigs) / tw if tw else 0.0
        return comp, sigs

    # -- exits ----------------------------------------------------------
    def manage(self, scan_id=None) -> int:
        exits = 0
        for token in list(self.pf.positions.keys()):
            pos = self.pf.positions[token]
            fresh = self.ds.refresh(pos.pair_address)
            if not fresh or fresh.price_usd <= 0:
                continue
            price, liq = fresh.price_usd, fresh.liquidity_usd
            self.pf.mark(token, price)
            if price > pos.high_water_quoted:
                pos.high_water_quoted = price

            pnl = pos.pnl_pct_quoted(price)
            peak = (pos.high_water_quoted / pos.entry_price_quoted - 1) * 100
            drop_from_peak = (pos.high_water_quoted - price) / pos.high_water_quoted * 100 \
                if pos.high_water_quoted else 0
            held = (datetime.now(timezone.utc) - pos.opened_at).total_seconds() / 3600
            liq_drop = (pos.entry_liquidity - liq) / pos.entry_liquidity * 100 \
                if pos.entry_liquidity else 0

            reason, is_stop = None, False
            if pnl <= -self.stop:
                reason, is_stop = f"stop_loss({pnl:.1f}%)", True
            elif liq_drop >= self.liq_collapse:
                reason, is_stop = f"liquidity_collapse(-{liq_drop:.0f}%)", True
            elif peak >= self.trail_arm and drop_from_peak >= self.trail:
                reason = f"trailing_stop(peak+{peak:.1f}%,-{drop_from_peak:.1f}%)"
            elif pnl >= self.tp:
                reason = f"take_profit(+{pnl:.1f}%)"
            elif held >= self.max_hold:
                reason = f"max_hold({held:.1f}h)"

            if reason:
                t, fill = self.pf.close(token, price, liq, reason, is_stop=is_stop)
                self.store.log_fill(t.symbol, token, fill)
                self.store.log_trade(t)
                exits += 1
                self._log(f"  EXIT  {t.symbol:<12} {reason:<34} net ${t.net_pnl_usd:+.2f} ({t.net_pnl_pct:+.2f}%)")
        return exits

    # -- full cycle -----------------------------------------------------
    def scan(self) -> dict:
        scan_id = self.store.start_scan()
        self._log(f"\n=== scan #{scan_id}  {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC ===")

        exits = self.manage(scan_id)   # exits FIRST — free capital and slots

        limit = int(self.cfg.get_path("run.max_candidates_per_scan", 400))
        universe = self.ds.discover(SEEDS, limit=limit)
        self._log(f"  universe: {len(universe)}")

        self.safety.prescan(universe)   # count symbols -> detect ticker collisions
        survivors = []
        for p in universe:
            r = self.safety.check(p)
            if r.passed:
                survivors.append(p)
            else:
                self.store.log_rejection(scan_id, p, "safety", r.reason_str)
        self._log(f"  passed safety: {len(survivors)}")

        scored = []
        for p in survivors:
            comp, sigs = self.score(p)
            agree = sum(1 for s in sigs if s.fires)
            scores = {s.name: round(s.score, 1) for s in sigs}
            if comp < self.min_score:
                self.store.log_rejection(scan_id, p, "score", f"composite {comp:.1f}<{self.min_score}")
                continue
            if agree < self.min_agree:
                self.store.log_rejection(scan_id, p, "score", f"only {agree} strategies agree")
                continue
            scored.append((comp, p, sigs, scores))
        scored.sort(key=lambda x: -x[0])
        self._log(f"  scored above threshold: {len(scored)}")

        entries = 0
        for comp, p, sigs, scores in scored:
            v = self.risk.evaluate(self.pf, p, self.cm)
            if not v.approved:
                self.store.log_decision(scan_id, p, "REJECTED_BY_RISK", comp, scores, v.size_usd, v.reason_str)
                self._log(f"  RISK-BLOCK {p.symbol:<12} {v.reason_str}")
                continue
            pos, fill = self.pf.open(p, v.size_usd, scores, comp)
            self.store.log_decision(scan_id, p, "ENTER", comp, scores, v.size_usd, "approved")
            self.store.log_fill(p.symbol, p.token_address, fill)
            entries += 1
            self._log(f"  ENTER {p.symbol:<12} score={comp:.1f} ${v.size_usd:.2f} "
                      f"@ {fill.effective_price:.8f} (cost {fill.total_cost_pct:.2f}%)")

        self.store.log_equity(self.pf)
        self.store.finish_scan(scan_id, universe=len(universe), passed_safety=len(survivors),
                               scored=len(scored), entries=entries, exits=exits,
                               equity=self.pf.equity())
        self.store.commit()
        st = self.pf.stats()
        self._log(f"  equity ${st['equity']:.2f} ({st['return_pct']:+.2f}%)  "
                  f"open={st['open_positions']} trades={st['trades']}")
        return st
