"""
Decision log. SQLite.

Records EVERY scan, every rejection with its reason, every decision and every
fill together with the market snapshot that produced it. The point is that
months from now you can answer "what did the system see, and why did it act"
without guessing. This log is the actual deliverable of paper trading.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
  universe INTEGER, passed_safety INTEGER, scored INTEGER,
  entries INTEGER, exits INTEGER, equity REAL, notes TEXT);
CREATE TABLE IF NOT EXISTS rejections (
  id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, ts TEXT,
  symbol TEXT, token_address TEXT, stage TEXT, reason TEXT, snapshot TEXT);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, ts TEXT,
  symbol TEXT, token_address TEXT, action TEXT, composite REAL,
  scores TEXT, size_usd REAL, risk_reason TEXT, snapshot TEXT);
CREATE TABLE IF NOT EXISTS fills (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, token_address TEXT,
  side TEXT, quoted_price REAL, effective_price REAL, size_usd REAL,
  slippage_pct REAL, total_cost_usd REAL, total_cost_pct REAL, breakdown TEXT,
  signature TEXT, mode TEXT);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, token_address TEXT,
  opened_at TEXT, closed_at TEXT, size_usd REAL, entry_quoted REAL, exit_quoted REAL,
  gross_pnl_usd REAL, total_costs_usd REAL, net_pnl_usd REAL, net_pnl_pct REAL,
  exit_reason TEXT, hold_hours REAL, composite REAL, signature TEXT, mode TEXT);
CREATE TABLE IF NOT EXISTS proposals (
  pid TEXT PRIMARY KEY, ts TEXT, symbol TEXT, token_address TEXT,
  size_usd REAL, price REAL, composite REAL, scores TEXT, rationale TEXT,
  liquidity REAL, round_trip_pct REAL, ttl_sec REAL, mode TEXT,
  status TEXT,            -- pending | approved | rejected | expired | filled | aborted
  decided_by TEXT,        -- ui | telegram | timeout
  decided_at TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS equity_curve (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, equity REAL, cash REAL,
  open_positions INTEGER, exposure REAL);
CREATE INDEX IF NOT EXISTS idx_rej_scan ON rejections(scan_id);
CREATE INDEX IF NOT EXISTS idx_dec_scan ON decisions(scan_id);
CREATE INDEX IF NOT EXISTS idx_rej_stage ON rejections(stage);
CREATE INDEX IF NOT EXISTS idx_prop_status ON proposals(status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str = "paper.db"):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        """Add columns to pre-existing databases. Never drops anything."""
        for table, col, typ in (("fills", "signature", "TEXT"), ("fills", "mode", "TEXT"),
                                ("trades", "signature", "TEXT"), ("trades", "mode", "TEXT")):
            have = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if col not in have:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")

    def start_scan(self) -> int:
        cur = self.db.execute("INSERT INTO scans(ts) VALUES(?)", (_now(),))
        self.db.commit()
        return cur.lastrowid

    def finish_scan(self, scan_id: int, **kw) -> None:
        cols = ", ".join(f"{k}=?" for k in kw)
        self.db.execute(f"UPDATE scans SET {cols} WHERE id=?", (*kw.values(), scan_id))
        self.db.commit()

    def log_rejection(self, scan_id, pair, stage, reason) -> None:
        self.db.execute(
            "INSERT INTO rejections(scan_id,ts,symbol,token_address,stage,reason,snapshot)"
            " VALUES(?,?,?,?,?,?,?)",
            (scan_id, _now(), pair.symbol, pair.token_address, stage, reason,
             json.dumps(pair.snapshot())))

    def log_decision(self, scan_id, pair, action, composite, scores, size_usd, risk_reason) -> None:
        self.db.execute(
            "INSERT INTO decisions(scan_id,ts,symbol,token_address,action,composite,scores,"
            "size_usd,risk_reason,snapshot) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (scan_id, _now(), pair.symbol, pair.token_address, action, composite,
             json.dumps(scores), size_usd, risk_reason, json.dumps(pair.snapshot())))

    def log_fill(self, symbol, token, fill, signature: str = "", mode: str = "paper") -> None:
        d = fill.as_dict()
        self.db.execute(
            "INSERT INTO fills(ts,symbol,token_address,side,quoted_price,effective_price,"
            "size_usd,slippage_pct,total_cost_usd,total_cost_pct,breakdown,signature,mode)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), symbol, token, d["side"], d["quoted_price"], d["effective_price"],
             d["size_usd"], d["slippage_pct"], d["total_cost_usd"], d["total_cost_pct"],
             json.dumps(d), signature, mode))

    def log_trade(self, t, signature: str = "", mode: str = "paper") -> None:
        self.db.execute(
            "INSERT INTO trades(symbol,token_address,opened_at,closed_at,size_usd,entry_quoted,"
            "exit_quoted,gross_pnl_usd,total_costs_usd,net_pnl_usd,net_pnl_pct,exit_reason,"
            "hold_hours,composite,signature,mode) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (t.symbol, t.token_address, t.opened_at.isoformat(), t.closed_at.isoformat(),
             t.size_usd, t.entry_quoted, t.exit_quoted, t.gross_pnl_usd, t.total_costs_usd,
             t.net_pnl_usd, t.net_pnl_pct, t.exit_reason, t.hold_hours, t.composite,
             signature, mode))

    # -- proposals: shared between the daemon, Telegram and the web UI ----
    # The UI only ever records a DECISION here. It never executes. The daemon
    # picks the decision up and runs it through risk.evaluate() again, so the
    # hard gate still sits between any approval and the keys.
    def save_proposal(self, p, mode: str, ttl_sec: float) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO proposals(pid,ts,symbol,token_address,size_usd,price,"
            "composite,scores,rationale,liquidity,round_trip_pct,ttl_sec,mode,status,"
            "decided_by,decided_at,detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (p.pid, _now(), p.symbol, p.token_address, p.size_usd, p.price, p.composite,
             json.dumps(p.scores), json.dumps(p.rationale), p.liquidity,
             p.round_trip_pct, ttl_sec, mode, p.status, None, None, None))
        self.db.commit()

    def decide_proposal(self, pid: str, status: str, by: str, detail: str = "") -> bool:
        """Returns True only if this call is what moved it out of 'pending'."""
        cur = self.db.execute(
            "UPDATE proposals SET status=?, decided_by=?, decided_at=?, detail=? "
            "WHERE pid=? AND status='pending'", (status, by, _now(), detail, pid))
        self.db.commit()
        return cur.rowcount > 0

    def pending_decisions(self) -> list:
        """Proposals a human decided elsewhere (the UI) that the daemon must act on."""
        return self.q("SELECT * FROM proposals WHERE status IN ('approved','rejected') "
                      "AND decided_by='ui' AND (detail IS NULL OR detail='')")

    def open_proposals(self) -> list:
        return self.q("SELECT * FROM proposals WHERE status='pending' ORDER BY ts DESC")

    def log_equity(self, pf) -> None:
        self.db.execute(
            "INSERT INTO equity_curve(ts,equity,cash,open_positions,exposure) VALUES(?,?,?,?,?)",
            (_now(), pf.equity(), pf.cash, len(pf.positions), pf.total_exposure()))

    def commit(self) -> None:
        self.db.commit()

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self.db.execute(sql, args).fetchall()
