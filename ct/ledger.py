"""
AUDIT LEDGER — append-only, hash-chained.

The analytics tables answer "how is the strategy doing". This answers a
different and harder question: "what exactly happened, and can I prove it".

Three properties the analytics tables do not have:

  1. EVERY TRANSITION IS RECORDED, failures included. A swap that failed is a
     more important record than one that succeeded, because it is the case
     where your belief about your position and reality have diverged.

  2. LIVE AND PAPER ARE NEVER CONFUSED. Every row carries its mode. Analysing
     paper fills believing they were real money is the worst silent error
     this system could make.

  3. IT IS TAMPER-EVIDENT. Each entry hashes the previous entry's hash, so any
     later edit or deletion breaks the chain at that exact point and every
     record after it. You cannot quietly rewrite a bad day.

Nothing here is ever UPDATEd or DELETEd. Corrections are new entries.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT NOT NULL,
  mode       TEXT NOT NULL,              -- live | paper
  kind       TEXT NOT NULL,
  symbol     TEXT,
  token      TEXT,
  size_usd   REAL,
  price      REAL,
  qty        REAL,
  net_usd    REAL,
  signature  TEXT,                       -- on-chain tx id, when there is one
  ok         INTEGER,                    -- 1 success, 0 failure, NULL n/a
  detail     TEXT,                       -- canonical JSON
  prev_hash  TEXT NOT NULL,
  hash       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_kind  ON ledger(kind);
CREATE INDEX IF NOT EXISTS idx_ledger_token ON ledger(token);
CREATE INDEX IF NOT EXISTS idx_ledger_mode  ON ledger(mode);
"""

# Every event kind the system can emit. Anything not here is a bug.
KINDS = {
    "service_start", "service_stop", "mode_change", "preflight_block",
    "proposed", "approved", "rejected", "expired",
    "entry_filled", "entry_failed", "entry_aborted", "risk_block",
    "exit_filled", "exit_failed",
    "wallet_drift", "low_gas", "loop_error",
}


def _canon(obj) -> str:
    """Stable JSON so the same content always hashes identically."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


class Ledger:
    def __init__(self, path: str = "ledger.db", mode: str = "paper"):
        self.path = Path(path)
        self.mode = mode
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # -- writing ---------------------------------------------------------
    def _tip(self) -> str:
        r = self.db.execute("SELECT hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
        return r["hash"] if r else GENESIS

    @staticmethod
    def _num(v):
        """
        Coerce numerics to exactly what SQLite will hand back from a REAL column.

        Without this, recording qty=100 (int) into a REAL column returns 100.0
        on read, the recomputed hash differs, and verify() reports tampering on
        a perfectly clean chain. A tamper detector that cries wolf is worse than
        none, because you learn to ignore it.
        """
        return None if v is None else float(v)

    def record(self, kind: str, *, symbol=None, token=None, size_usd=None,
               price=None, qty=None, net_usd=None, signature=None, ok=None,
               mode: str | None = None, **detail) -> str:
        if kind not in KINDS:
            detail = {**detail, "_unregistered_kind": kind}
        size_usd, price = self._num(size_usd), self._num(price)
        qty, net_usd = self._num(qty), self._num(net_usd)
        ok = None if ok is None else int(ok)
        ts = datetime.now(timezone.utc).isoformat()
        m = mode or self.mode
        prev = self._tip()
        body = _canon({"ts": ts, "mode": m, "kind": kind, "symbol": symbol,
                       "token": token, "size_usd": size_usd, "price": price,
                       "qty": qty, "net_usd": net_usd, "signature": signature,
                       "ok": ok, "detail": detail})
        h = hashlib.sha256((prev + body).encode()).hexdigest()
        self.db.execute(
            "INSERT INTO ledger(ts,mode,kind,symbol,token,size_usd,price,qty,net_usd,"
            "signature,ok,detail,prev_hash,hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, m, kind, symbol, token, size_usd, price, qty, net_usd, signature,
             ok, _canon(detail), prev, h))
        self.db.commit()
        return h

    # -- verification -----------------------------------------------------
    def _row_hash(self, r, prev: str) -> str:
        body = _canon({"ts": r["ts"], "mode": r["mode"], "kind": r["kind"],
                       "symbol": r["symbol"], "token": r["token"],
                       "size_usd": r["size_usd"], "price": r["price"],
                       "qty": r["qty"], "net_usd": r["net_usd"],
                       "signature": r["signature"], "ok": r["ok"],
                       "detail": json.loads(r["detail"] or "{}")})
        return hashlib.sha256((prev + body).encode()).hexdigest()

    def verify_from(self, seq: int, prev_hash: str) -> dict:
        """
        Verify only entries after `seq`, continuing from `prev_hash`.

        The chain is append-only, so once a prefix is verified it stays
        verified — re-walking it on every call is wasted work. A full verify()
        is O(n) with a SHA256 per row: ~110ms at 20k rows and climbing, which
        would saturate anything calling it on a short interval. This lets a
        long-running reader keep a checkpoint and pay only for new rows.

        A full verify() is still the right call for an audit; this is for
        live monitoring.
        """
        rows = self.db.execute("SELECT * FROM ledger WHERE seq > ? ORDER BY seq",
                               (seq,)).fetchall()
        prev = prev_hash
        for r in rows:
            if r["prev_hash"] != prev:
                return {"ok": False, "broken_at": r["seq"], "checked": len(rows),
                        "reason": "prev_hash does not match the preceding entry "
                                  "— a record was deleted or reordered"}
            if r["hash"] != self._row_hash(r, prev):
                return {"ok": False, "broken_at": r["seq"], "checked": len(rows),
                        "reason": "content hash mismatch — this entry was edited "
                                  "after it was written"}
            prev = r["hash"]
        last = self.db.execute("SELECT seq FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
        return {"ok": True, "checked": len(rows), "tip": prev,
                "seq": last["seq"] if last else seq,
                "entries": self.db.execute("SELECT COUNT(*) c FROM ledger").fetchone()["c"]}

    def verify(self) -> dict:
        """Recompute the whole chain. Reports the first break, if any."""
        rows = self.db.execute("SELECT * FROM ledger ORDER BY seq").fetchall()
        prev = GENESIS
        for r in rows:
            body = _canon({"ts": r["ts"], "mode": r["mode"], "kind": r["kind"],
                           "symbol": r["symbol"], "token": r["token"],
                           "size_usd": r["size_usd"], "price": r["price"],
                           "qty": r["qty"], "net_usd": r["net_usd"],
                           "signature": r["signature"], "ok": r["ok"],
                           "detail": json.loads(r["detail"] or "{}")})
            expect = hashlib.sha256((prev + body).encode()).hexdigest()
            if r["prev_hash"] != prev:
                return {"ok": False, "entries": len(rows), "broken_at": r["seq"],
                        "reason": "prev_hash does not match the preceding entry "
                                  "— a record was deleted or reordered"}
            if r["hash"] != expect:
                return {"ok": False, "entries": len(rows), "broken_at": r["seq"],
                        "reason": "content hash mismatch — this entry was edited after it was written"}
            prev = r["hash"]
        return {"ok": True, "entries": len(rows), "tip": prev}

    # -- reading ----------------------------------------------------------
    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self.db.execute(sql, args).fetchall()

    def failures(self) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM ledger WHERE ok = 0 ORDER BY seq DESC")

    def live_signatures(self) -> list[str]:
        return [r["signature"] for r in self.q(
            "SELECT DISTINCT signature FROM ledger WHERE mode='live' "
            "AND signature IS NOT NULL AND length(signature) > 20")]

    def summary(self) -> dict:
        out = {}
        for r in self.q("SELECT mode, kind, COUNT(*) c FROM ledger GROUP BY mode, kind"):
            out.setdefault(r["mode"], {})[r["kind"]] = r["c"]
        return out
