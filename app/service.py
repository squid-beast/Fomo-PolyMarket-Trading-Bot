#!/usr/bin/env python3
"""
The 24/7 service. This is the thing that manages your money — not a chat session.

  ENTRIES  -> proposed to you, executed only on approval, expire if ignored
  EXITS    -> automatic, never wait for a human
  RISK     -> enforced in code; nothing upstream can override it
  STATE    -> persisted, so a restart does not lose track of open positions

It refuses to start in live mode if the account is too small to be traded
inside its own risk limits. That refusal is a feature.
"""
from __future__ import annotations
import json, os, signal, sys, time, uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ct import config
from ct.costs import CostModel
from ct.datasource import DexScreener, SEEDS
from ct.safety import SafetyFilter
from ct.strategies import load_strategies
from ct.risk import RiskEngine
from ct.portfolio import Portfolio
from ct.store import Store
from ct.ledger import Ledger
from app.notifier import Notifier, Proposal
from app.executor import JupiterExecutor, ExecutionError, SOL_MINT, LAMPORTS
from app.wallet import Wallet

STATE = Path(os.environ.get("STATE_FILE", "state.json"))


def env(k, d=None):
    v = os.environ.get(k)
    return v if v not in (None, "") else d


def envf(k, d):
    try:
        return float(env(k, d))
    except (TypeError, ValueError):
        return float(d)


class Service:
    def __init__(self):
        self.cfg = config.load(env("CONFIG_FILE", "config.yaml"))
        self.live = env("LIVE_TRADING", "false").lower() == "true"
        self.cm = CostModel(self.cfg)
        self.ds = DexScreener(self.cfg.get_path("run.chain", "solana"))
        self.safety = SafetyFilter(self.cfg)
        self.strategies = load_strategies(self.cfg)
        self.risk = RiskEngine(self.cfg)
        self.pf = Portfolio(self.cfg, self.cm)
        self.store = Store(env("DB_FILE", "paper.db"))
        self.ledger = Ledger(env("LEDGER_FILE", "ledger.db"),
                             mode="live" if self.live else "paper")
        self.notif = Notifier(env("TELEGRAM_BOT_TOKEN", ""), env("TELEGRAM_CHAT_ID", ""))
        self.rpc = env("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
        self.wallet = Wallet(self.rpc)
        self.ex = JupiterExecutor(self.rpc, env("JUPITER_API_KEY"), dry_run=not self.live)

        self.proposals: dict[str, Proposal] = {}
        self.ttl = envf("PROPOSAL_TTL_MIN", 10) * 60
        self.scan_every = int(envf("SCAN_INTERVAL_SEC", 300))
        self.manage_every = int(envf("MANAGE_INTERVAL_SEC", 60))
        self.max_impact = envf("MAX_PRICE_IMPACT_PCT", 3.0)
        self.slippage_bps = int(envf("SLIPPAGE_BPS", 150))
        self.running = True
        self._last_scan = 0.0
        self._last_recon = 0.0
        self._last_summary = None
        self._load()

    # -- persistence ----------------------------------------------------
    def _load(self):
        if not STATE.exists():
            return
        try:
            d = json.loads(STATE.read_text())
            self.pf.cash = d.get("cash", self.pf.cash)
            for p in d.get("positions", []):
                from ct.portfolio import Position
                p["opened_at"] = datetime.fromisoformat(p["opened_at"])
                self.pf.positions[p["token_address"]] = Position(**p)
            self.log(f"restored {len(self.pf.positions)} open positions from state")
        except Exception as e:
            self.log(f"state load failed ({e}); starting clean")

    def _save(self):
        try:
            pos = []
            for p in self.pf.positions.values():
                d = dict(p.__dict__)
                d["opened_at"] = p.opened_at.isoformat()
                pos.append(d)
            STATE.write_text(json.dumps({"cash": self.pf.cash, "positions": pos,
                                         "saved": datetime.now(timezone.utc).isoformat()}, default=str))
        except Exception as e:
            self.log(f"state save failed: {e}")

    @property
    def mode(self) -> str:
        return "live" if self.live else "paper"

    def _set_paper(self, why: str):
        """A live->paper fallback is a material event. It gets recorded."""
        self.ledger.record("mode_change", ok=0, was="live", now="paper", reason=why)
        self.live = False
        self.ex.dry_run = True
        self.ledger.mode = "paper"

    def log(self, *a):
        print(f"[{datetime.now(timezone.utc):%H:%M:%S}]", *a, flush=True)

    # -- preflight ------------------------------------------------------
    def preflight(self) -> bool:
        eq = self.pf.equity()
        floor = float(self.cfg.get_path("risk.min_position_usd", 15))
        pct = float(self.cfg.get_path("risk.max_position_pct", 2))
        needed = floor / (pct / 100.0)
        self.log(f"mode: {'LIVE' if self.live else 'PAPER'}   equity ${eq:,.2f}")
        if eq * pct / 100.0 < floor:
            msg = (f"Account ${eq:,.2f} cannot be traded inside its own risk limits.\n"
                   f"At {pct}% max position that is ${eq*pct/100:.2f}/trade, "
                   f"below the ${floor:.2f} floor where fixed costs dominate.\n"
                   f"Needs about ${needed:,.0f} to operate. Running PAPER only.")
            self.log("PREFLIGHT: " + msg.replace("\n", " "))
            self.notif.alert("⚠️ Account below operating floor", msg)
            self.ledger.record("preflight_block", size_usd=eq, ok=0,
                               reason="account below operating floor",
                               max_position_usd=round(eq * pct / 100, 2),
                               floor_usd=floor, needed_usd=round(needed))
            if self.live:
                self._set_paper("account below operating floor")
            return True
        if self.live:
            try:
                self.log(f"signing wallet: {self.ex.pubkey}")
                sol = self.wallet.sol_balance(self.ex.pubkey)
                self.log(f"wallet SOL balance: {sol:.4f}")
                if sol < 0.02:
                    self.notif.alert("⚠️ Low SOL", f"Only {sol:.4f} SOL for gas. "
                                     "Swaps will start failing below ~0.02.")
                    self.ledger.record("low_gas", qty=sol, ok=0,
                                       wallet=self.ex.pubkey)
            except ExecutionError as e:
                self.log(f"LIVE requested but key unusable: {e}")
                self.notif.alert("⚠️ Falling back to paper", str(e)[:300])
                self._set_paper(f"signing key unusable: {e}")
        return True

    # -- scoring --------------------------------------------------------
    def score(self, pair):
        sigs = [s.evaluate(pair) for s in self.strategies]
        w = self.cfg.get_path("strategies.weights", {}) or {}
        tw = sum(float(w.get(s.name, 1.0)) for s in sigs) or 1.0
        comp = sum(s.score * float(w.get(s.name, 1.0)) for s in sigs) / tw
        return comp, sigs

    # -- exits: AUTOMATIC ------------------------------------------------
    def manage(self):
        e = self.cfg.get_path("exits", {})
        for token in list(self.pf.positions.keys()):
            pos = self.pf.positions[token]
            fresh = self.ds.refresh(pos.pair_address)
            if not fresh or fresh.price_usd <= 0:
                continue
            price, liq = fresh.price_usd, fresh.liquidity_usd
            self.pf.mark(token, price)
            pos.high_water_quoted = max(pos.high_water_quoted, price)

            pnl = pos.pnl_pct_quoted(price)
            peak = (pos.high_water_quoted / pos.entry_price_quoted - 1) * 100
            dfp = (pos.high_water_quoted - price) / pos.high_water_quoted * 100 if pos.high_water_quoted else 0
            held = (datetime.now(timezone.utc) - pos.opened_at).total_seconds() / 3600
            liq_drop = (pos.entry_liquidity - liq) / pos.entry_liquidity * 100 if pos.entry_liquidity else 0

            reason, is_stop = None, False
            if pnl <= -float(e.get("stop_loss_pct", 12)):
                reason, is_stop = f"stop loss ({pnl:.1f}%)", True
            elif liq_drop >= float(e.get("liquidity_collapse_pct", 40)):
                reason, is_stop = f"liquidity collapse (-{liq_drop:.0f}%)", True
            elif peak >= float(e.get("trail_arm_pct", 15)) and dfp >= float(e.get("trailing_stop_pct", 10)):
                reason = f"trailing stop (peak +{peak:.0f}%, gave back {dfp:.0f}%)"
            elif pnl >= float(e.get("take_profit_pct", 25)):
                reason = f"take profit (+{pnl:.1f}%)"
            elif held >= float(e.get("max_hold_hours", 24)):
                reason = f"max hold ({held:.1f}h)"
            if not reason:
                continue

            sig = ""
            if self.live:
                try:
                    res = self.ex.swap(token, SOL_MINT, int(pos.qty), self.slippage_bps,
                                       max(self.max_impact, 10.0))  # exits get a wider cap: getting out matters more
                    sig = res.get("signature", "")
                    if not res.get("ok"):
                        self.notif.alert("⚠️ EXIT FAILED", f"{pos.symbol}: {res}")
                        self.log(f"EXIT FAILED {pos.symbol}: {res}")
                        # STILL HOLDING. The most important row in the database.
                        self.ledger.record("exit_failed", symbol=pos.symbol, token=token,
                                           qty=pos.qty, price=price, signature=sig, ok=0,
                                           attempted_reason=reason, result=res,
                                           position_still_open=True)
                        continue
                except ExecutionError as ex_:
                    self.notif.alert("⚠️ EXIT ERROR", f"{pos.symbol}: {ex_}")
                    self.ledger.record("exit_failed", symbol=pos.symbol, token=token,
                                       qty=pos.qty, price=price, ok=0,
                                       attempted_reason=reason, error=str(ex_),
                                       position_still_open=True)
                    continue
            t, fill = self.pf.close(token, price, liq, reason, is_stop=is_stop)
            self.store.log_fill(t.symbol, token, fill, sig, self.mode)
            self.store.log_trade(t, sig, self.mode)
            self.ledger.record("exit_filled", symbol=t.symbol, token=token,
                               size_usd=t.size_usd, price=price, qty=pos.qty,
                               net_usd=t.net_pnl_usd, signature=sig, ok=1,
                               reason=reason, net_pct=round(t.net_pnl_pct, 3),
                               gross_usd=round(t.gross_pnl_usd, 4),
                               costs_usd=round(t.total_costs_usd, 4),
                               hold_hours=round(t.hold_hours, 3),
                               entry_quoted=t.entry_quoted, exit_quoted=t.exit_quoted)
            self.log(f"EXIT {t.symbol} {reason} net ${t.net_pnl_usd:+.2f}")
            self.notif.exit_notice(t.symbol, reason, t.net_pnl_usd, t.net_pnl_pct, sig)
        self._save()

    # -- entries: PROPOSED --------------------------------------------
    def scan(self):
        sid = self.store.start_scan()
        universe = self.ds.discover(SEEDS, limit=int(self.cfg.get_path("run.max_candidates_per_scan", 400)))
        self.safety.prescan(universe)   # count symbols -> detect ticker collisions
        survivors = []
        for p in universe:
            r = self.safety.check(p)
            if r.passed:
                survivors.append(p)
            else:
                self.store.log_rejection(sid, p, "safety", r.reason_str)

        min_score = float(self.cfg.get_path("strategies.min_composite_score", 62))
        min_agree = int(self.cfg.get_path("strategies.min_strategies_agreeing", 2))
        ranked = []
        for p in survivors:
            comp, sigs = self.score(p)
            if comp < min_score or sum(1 for s in sigs if s.fires) < min_agree:
                self.store.log_rejection(sid, p, "score", f"composite {comp:.1f}")
                continue
            ranked.append((comp, p, sigs))
        ranked.sort(key=lambda x: -x[0])

        proposed = 0
        for comp, p, sigs in ranked:
            if any(pr.token_address == p.token_address and pr.status == "pending"
                   for pr in self.proposals.values()):
                continue
            v = self.risk.evaluate(self.pf, p, self.cm)
            if not v.approved:
                self.store.log_decision(sid, p, "REJECTED_BY_RISK", comp,
                                        {s.name: s.score for s in sigs}, v.size_usd, v.reason_str)
                self.ledger.record("risk_block", symbol=p.symbol, token=p.token_address,
                                   size_usd=v.size_usd, stage="scan", reason=v.reason_str)
                continue
            pid = uuid.uuid4().hex[:10]
            why = [f"{s.name}: {s.score:.0f}/100" for s in sigs]
            why += [r for s in sigs for r in s.rationale[:2]]
            pr = Proposal(pid, p.symbol, p.token_address, v.size_usd, p.price_usd, comp,
                          {s.name: s.score for s in sigs}, why, p.liquidity_usd,
                          self.cm.round_trip_pct(v.size_usd, p.liquidity_usd))
            self.proposals[pid] = pr
            self.store.save_proposal(pr, self.mode, self.ttl)
            self.notif.propose(pr, self.ttl / 60)
            self.store.log_decision(sid, p, "PROPOSED", comp,
                                    {s.name: s.score for s in sigs}, v.size_usd, "awaiting approval")
            self.ledger.record("proposed", symbol=p.symbol, token=p.token_address,
                               size_usd=v.size_usd, price=p.price_usd,
                               pid=pid, composite=round(comp, 2),
                               scores={s.name: round(s.score, 1) for s in sigs},
                               liquidity=p.liquidity_usd,
                               round_trip_pct=round(pr.round_trip_pct, 3))
            self.log(f"PROPOSED {p.symbol} ${v.size_usd:.2f} score={comp:.1f}")
            proposed += 1

        self.store.log_equity(self.pf)
        self.store.finish_scan(sid, universe=len(universe), passed_safety=len(survivors),
                               scored=len(ranked), entries=proposed, exits=0,
                               equity=self.pf.equity())
        self.store.commit()
        self.log(f"scan: {len(universe)} -> safety {len(survivors)} -> scored {len(ranked)} "
                 f"-> proposed {proposed}")

    # -- approvals ------------------------------------------------------
    def handle_callbacks(self):
        for action, pid, cb in self.notif.poll():
            pr = self.proposals.get(pid)
            if not pr:
                self.notif.ack(cb, "Unknown or already cleared")
                continue
            if pr.status != "pending":
                self.notif.ack(cb, f"Already {pr.status}")
                continue
            if pr.expired(self.ttl):
                pr.status = "expired"
                self.store.decide_proposal(pid, "expired", "timeout")
                self.notif.ack(cb, "Expired — signal is stale")
                self.notif.resolve(pr, "EXPIRED", "Signal was too old to act on.")
                self.ledger.record("expired", symbol=pr.symbol, token=pr.token_address,
                                   pid=pid, age_s=round(pr.age_s(), 1), tapped_late=True)
                continue
            if action == "reject":
                pr.status = "rejected"
                self.store.decide_proposal(pid, "rejected", "telegram")
                self.notif.ack(cb, "Rejected")
                self.notif.resolve(pr, "REJECTED")
                self.ledger.record("rejected", symbol=pr.symbol, token=pr.token_address,
                                   size_usd=pr.size_usd, pid=pid, by="user",
                                   age_s=round(pr.age_s(), 1))
                continue
            self.store.decide_proposal(pid, "approved", "telegram")
            self.notif.ack(cb, "Approved — executing")
            self.ledger.record("approved", symbol=pr.symbol, token=pr.token_address,
                               size_usd=pr.size_usd, pid=pid, by="user",
                               age_s=round(pr.age_s(), 1))
            self.execute(pr)

    def handle_ui_decisions(self):
        """
        The web UI records a decision; it never executes. We act on it here so
        the approval still passes through risk.evaluate() before any swap.
        """
        for row in self.store.pending_decisions():
            pid = row["pid"]
            pr = self.proposals.get(pid)
            if not pr:
                self.store.decide_proposal(pid, row["status"], "ui")
                self.db_mark(pid, "proposal no longer in memory (service restarted)")
                continue
            if pr.status != "pending":
                self.db_mark(pid, f"already {pr.status}")
                continue
            if row["status"] == "rejected":
                pr.status = "rejected"
                self.ledger.record("rejected", symbol=pr.symbol, token=pr.token_address,
                                   size_usd=pr.size_usd, pid=pid, by="ui")
                self.notif.resolve(pr, "REJECTED (from dashboard)")
                self.db_mark(pid, "handled")
                continue
            if pr.expired(self.ttl):
                pr.status = "expired"
                self.ledger.record("expired", symbol=pr.symbol, token=pr.token_address,
                                   pid=pid, age_s=round(pr.age_s(), 1), tapped_late=True)
                self.notif.resolve(pr, "EXPIRED", "Approved too late — signal was stale.")
                self.db_mark(pid, "expired before it could run")
                continue
            self.ledger.record("approved", symbol=pr.symbol, token=pr.token_address,
                               size_usd=pr.size_usd, pid=pid, by="ui",
                               age_s=round(pr.age_s(), 1))
            self.log(f"UI approved {pr.symbol} — executing")
            self.execute(pr)
            self.db_mark(pid, "handled")

    def db_mark(self, pid: str, detail: str) -> None:
        """Stamp detail so pending_decisions() stops returning this row."""
        self.store.db.execute("UPDATE proposals SET detail=? WHERE pid=?", (detail, pid))
        self.store.commit()

    def execute(self, pr: Proposal):
        fresh = None
        for p in self.ds.search(pr.symbol):
            if p.token_address == pr.token_address:
                fresh = p
                break
        if not fresh or fresh.price_usd <= 0:
            pr.status = "rejected"
            self.notif.resolve(pr, "ABORTED", "Could not re-price the token at execution time.")
            self.ledger.record("entry_aborted", symbol=pr.symbol, token=pr.token_address,
                               size_usd=pr.size_usd, ok=0, pid=pr.pid,
                               reason="could not re-price at execution time")
            return
        # re-check risk against CURRENT state, not the state when proposed
        v = self.risk.evaluate(self.pf, fresh, self.cm)
        if not v.approved:
            pr.status = "rejected"
            self.notif.resolve(pr, "BLOCKED BY RISK", v.reason_str)
            self.ledger.record("risk_block", symbol=pr.symbol, token=pr.token_address,
                               size_usd=v.size_usd, ok=0, pid=pr.pid,
                               stage="execution", reason=v.reason_str)
            self.log(f"risk blocked {pr.symbol} at execution: {v.reason_str}")
            return
        sig = ""
        if self.live:
            try:
                lam = int(v.size_usd / self._sol_price() * LAMPORTS)
                res = self.ex.swap(SOL_MINT, pr.token_address, lam,
                                   self.slippage_bps, self.max_impact)
                if not res.get("ok"):
                    pr.status = "rejected"
                    self.notif.resolve(pr, "NOT EXECUTED", str(res)[:300])
                    self.ledger.record("entry_failed", symbol=pr.symbol,
                                       token=pr.token_address, size_usd=v.size_usd,
                                       signature=res.get("signature", ""), ok=0,
                                       pid=pr.pid, result=res)
                    return
                sig = res.get("signature", "")
            except ExecutionError as e:
                pr.status = "rejected"
                self.notif.resolve(pr, "EXECUTION ERROR", str(e)[:300])
                self.ledger.record("entry_failed", symbol=pr.symbol,
                                   token=pr.token_address, size_usd=v.size_usd,
                                   ok=0, pid=pr.pid, error=str(e))
                return
        pos, fill = self.pf.open(fresh, v.size_usd, pr.scores, pr.composite)
        self.store.log_fill(fresh.symbol, fresh.token_address, fill, sig, self.mode)
        self.ledger.record("entry_filled", symbol=fresh.symbol, token=fresh.token_address,
                           size_usd=v.size_usd, price=fill.effective_price, qty=pos.qty,
                           signature=sig, ok=1, pid=pr.pid,
                           quoted_price=fill.quoted_price,
                           entry_cost_usd=round(fill.total_cost_usd, 4),
                           entry_cost_pct=round(fill.total_cost_pct, 3),
                           composite=round(pr.composite, 2), liquidity=fresh.liquidity_usd)
        pr.status = "approved"
        self.store.decide_proposal(pr.pid, "filled", "daemon")
        self.store.db.execute("UPDATE proposals SET status='filled' WHERE pid=?", (pr.pid,))
        self.store.commit()
        self._save()
        link = f"\nhttps://solscan.io/tx/{sig}" if sig and len(sig) > 20 else ""
        self.notif.resolve(pr, "ENTERED",
                           f"${v.size_usd:.2f} @ ${fill.effective_price:.8f}\n"
                           f"cost {fill.total_cost_pct:.2f}% • "
                           f"stop {self.cfg.get_path('exits.stop_loss_pct')}% • "
                           f"target +{self.cfg.get_path('exits.take_profit_pct')}%{link}")
        self.log(f"ENTERED {fresh.symbol} ${v.size_usd:.2f}")

    def _sol_price(self) -> float:
        try:
            q = self.ex.quote(SOL_MINT, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                              LAMPORTS, 100)
            return q.out_amount / 1e6
        except Exception:
            return 100.0

    def expire_stale(self):
        for pr in self.proposals.values():
            if pr.expired(self.ttl):
                pr.status = "expired"
                self.store.decide_proposal(pr.pid, "expired", "timeout")
                self.notif.resolve(pr, "EXPIRED", "No response — signal went stale, no trade taken.")
                self.ledger.record("expired", symbol=pr.symbol, token=pr.token_address,
                                   size_usd=pr.size_usd, pid=pr.pid,
                                   age_s=round(pr.age_s(), 1), tapped_late=False)
                self.log(f"expired {pr.symbol}")

    def reconcile(self):
        if not self.live:
            return
        try:
            expected = {t: p.qty for t, p in self.pf.positions.items()}
            issues = self.wallet.reconcile(expected, self.ex.pubkey)
            if issues:
                self.notif.alert("⚠️ Wallet drift", "\n".join(issues[:6]))
                self.ledger.record("wallet_drift", ok=0, issues=issues,
                                   wallet=self.ex.pubkey)
                self.log("DRIFT: " + "; ".join(issues[:4]))
        except Exception as e:
            self.log(f"reconcile failed: {e}")

    def daily_summary(self):
        today = datetime.now(timezone.utc).date()
        if self._last_summary == today:
            return
        self._last_summary = today
        s = self.pf.stats()
        self.notif.alert("Daily summary", (
            f"Equity ${s['equity']:,.2f} ({s['return_pct']:+.2f}%)\n"
            f"Open {s['open_positions']} • Closed {s['trades']} • Win {s['win_rate']:.0f}%\n"
            f"Costs paid ${s['total_costs']:,.2f}"))

    # -- main loop --------------------------------------------------------
    def run(self):
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "running", False))
        self.preflight()
        self.ledger.record("service_start", size_usd=self.pf.equity(),
                           open_positions=len(self.pf.positions),
                           scan_interval=self.scan_every, ttl_min=self.ttl / 60)
        self.notif.alert("Service started",
                         f"Mode: <b>{'LIVE' if self.live else 'PAPER'}</b>\n"
                         f"Equity ${self.pf.equity():,.2f}\n"
                         f"Entries need your approval. Exits are automatic.")
        while self.running:
            try:
                self.handle_callbacks()
                self.handle_ui_decisions()
                self.expire_stale()
                now = time.time()
                self.manage()
                if now - self._last_scan >= self.scan_every:
                    self.scan()
                    self._last_scan = now
                if now - self._last_recon >= 900:
                    self.reconcile()
                    self._last_recon = now
                if datetime.now(timezone.utc).hour == 0:
                    self.daily_summary()
            except Exception as e:
                self.log(f"loop error: {type(e).__name__}: {e}")
                self.notif.alert("⚠️ Loop error", f"{type(e).__name__}: {str(e)[:250]}")
                self.ledger.record("loop_error", ok=0, error=f"{type(e).__name__}: {e}")
            for _ in range(self.manage_every):
                if not self.running:
                    break
                time.sleep(1)
        self._save()
        self.ledger.record("service_stop", size_usd=self.pf.equity(),
                           open_positions=len(self.pf.positions),
                           unmanaged=[p.symbol for p in self.pf.positions.values()])
        self.notif.alert("Service stopped", f"Equity ${self.pf.equity():,.2f}. "
                         f"{len(self.pf.positions)} positions still open — these are NOT being managed now.")
        self.log("stopped")


if __name__ == "__main__":
    Service().run()
