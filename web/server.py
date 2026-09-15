#!/usr/bin/env python3
"""
Read-mostly control panel.

THE ONE RULE HERE: this server never executes a trade. Approving in the UI
writes a DECISION row; the daemon picks it up and runs it back through
risk.evaluate() before anything is signed. The hard gate always sits between
a human clicking a button and the keys.

Bind to localhost only. There is no auth, because there is no reason to expose
this. Reach it from elsewhere over an SSH tunnel:
    ssh -N -L 8787:127.0.0.1:8787 you@your-vps
"""
from __future__ import annotations
import asyncio, json, os, sqlite3, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ct import config
from ct.costs import CostModel
from ct.ledger import Ledger

ROOT = Path(__file__).resolve().parents[1]
DB = os.environ.get("DB_FILE", str(ROOT / "paper.db"))
LEDGER = os.environ.get("LEDGER_FILE", str(ROOT / "ledger.db"))
STATE = os.environ.get("STATE_FILE", str(ROOT / "state.json"))
CFG = config.load(os.environ.get("CONFIG_FILE", str(ROOT / "config.yaml")))
LIVE = os.environ.get("LIVE_TRADING", "false").lower() == "true"

app = FastAPI(title="copytrader", docs_url=None, redoc_url=None)


class ChangeWatcher:
    """
    Detects that another process wrote, without querying any table.

    SQLite bumps PRAGMA data_version on a read connection whenever a DIFFERENT
    connection commits. Checking it costs ~3 microseconds, so we can look 4x a
    second and still be free, then build the (expensive) payload only when
    something actually changed. That is the whole trick behind pushing with no
    delay and no polling cost.

    state.json is watched by mtime because the daemon touches it every cycle
    without necessarily writing to a database.
    """

    def __init__(self, paths: list[str], mtime_paths: list[str]):
        self._conns = {}
        for p in paths:
            try:
                if Path(p).exists():
                    self._conns[p] = sqlite3.connect(f"file:{p}?mode=ro", uri=True,
                                                     check_same_thread=False)
            except sqlite3.Error:
                pass
        self._mtime_paths = mtime_paths

    def token(self) -> tuple:
        vs = []
        for p, c in list(self._conns.items()):
            try:
                vs.append(c.execute("PRAGMA data_version").fetchone()[0])
            except sqlite3.Error:
                vs.append(-1)
        # a database that did not exist at startup must still be picked up
        for p in (DB, LEDGER):
            if p not in self._conns and Path(p).exists():
                try:
                    self._conns[p] = sqlite3.connect(f"file:{p}?mode=ro", uri=True,
                                                     check_same_thread=False)
                    vs.append(0)
                except sqlite3.Error:
                    pass
        for p in self._mtime_paths:
            try:
                vs.append(int(Path(p).stat().st_mtime_ns))
            except OSError:
                vs.append(0)
        return tuple(vs)


WATCHER = ChangeWatcher([DB, LEDGER], [STATE])


class ChainMonitor:
    """
    Keeps a verified checkpoint so live monitoring costs nothing.

    A full verify() is O(n) with a SHA256 per row — 116ms at 20k rows and
    still climbing. Calling that on every SSE push would saturate the loop
    within a week of normal logging. The chain is append-only, so a verified
    prefix stays verified: we keep the last good (seq, hash) and only check
    rows added since.
    """

    def __init__(self):
        self.seq = 0
        self.tip = "0" * 64
        self.result = {"ok": None, "entries": 0, "reason": "no ledger yet"}

    def check(self) -> dict:
        if not Path(LEDGER).exists():
            return self.result
        try:
            L = Ledger(LEDGER)
            r = L.verify_from(self.seq, self.tip)
            if r["ok"]:
                self.seq, self.tip = r.get("seq", self.seq), r.get("tip", self.tip)
                self.result = {"ok": True, "entries": r.get("entries", 0)}
            else:
                # never advance the checkpoint past a break
                self.result = {"ok": False, "entries": r.get("checked", 0),
                               "reason": r.get("reason", ""),
                               "broken_at": r.get("broken_at")}
        except Exception as e:
            self.result = {"ok": False, "entries": 0, "reason": f"could not verify: {e}"}
        return self.result


CHAIN = ChainMonitor()
_CACHE: dict = {"rejections": ([], 0.0)}
REJECT_TTL = 20.0          # aggregate over a growing table; refresh slowly


def rows(db_path: str, sql: str, args: tuple = ()) -> list[dict]:
    if not Path(db_path).exists():
        return []
    try:
        c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    except sqlite3.Error:
        return []
    finally:
        try: c.close()
        except Exception: pass


def jloads(v, default):
    try: return json.loads(v) if v else default
    except (TypeError, ValueError): return default


def build_state() -> dict:
    cm = CostModel(CFG)
    now = datetime.now(timezone.utc)

    # --- chain integrity (incremental — see ChainMonitor) -----------------
    chain = CHAIN.check()

    # --- is the daemon alive? state.json is touched every cycle ----------
    heartbeat = None
    if Path(STATE).exists():
        heartbeat = round(now.timestamp() - Path(STATE).stat().st_mtime)
    positions = jloads(Path(STATE).read_text() if Path(STATE).exists() else "", {}) or {}
    cash = positions.get("cash", 0.0)
    open_pos = positions.get("positions", []) or []

    trades = rows(DB, "SELECT * FROM trades ORDER BY closed_at DESC LIMIT 60")
    curve = rows(DB, "SELECT ts, equity FROM equity_curve ORDER BY id DESC LIMIT 400")[::-1]
    scans = rows(DB, "SELECT * FROM scans WHERE universe IS NOT NULL ORDER BY id DESC LIMIT 1")
    props = rows(DB, "SELECT * FROM proposals WHERE status='pending' ORDER BY ts DESC LIMIT 20")
    recent_props = rows(DB, "SELECT * FROM proposals ORDER BY ts DESC LIMIT 40")
    fails = rows(LEDGER, "SELECT * FROM ledger WHERE ok=0 ORDER BY seq DESC LIMIT 25")
    # This aggregate scans a table that grows by ~280 rows per scan, so it is
    # cached. It is context, not something anyone watches change in real time.
    cached, at = _CACHE["rejections"]
    if time.monotonic() - at > REJECT_TTL:
        cached = rows(DB, "SELECT reason, COUNT(*) c FROM rejections WHERE stage='safety' "
                          "GROUP BY substr(reason,1,instr(reason||'(','(')-1) "
                          "ORDER BY c DESC LIMIT 10")
        _CACHE["rejections"] = (cached, time.monotonic())
    rejects = cached

    net = sum(t["net_pnl_usd"] or 0 for t in trades)
    gross = sum(t["gross_pnl_usd"] or 0 for t in trades)
    costs = sum(t["total_costs_usd"] or 0 for t in trades)
    wins = [t for t in trades if (t["net_pnl_usd"] or 0) > 0]
    start = float(CFG.get_path("portfolio.starting_equity_usd", 1000))
    equity = curve[-1]["equity"] if curve else (cash or start)

    for p in props:
        p["scores"] = jloads(p["scores"], {})
        p["rationale"] = jloads(p["rationale"], [])
        try:
            age = (now - datetime.fromisoformat(p["ts"])).total_seconds()
        except Exception:
            age = 0
        p["age_sec"] = round(age)
        p["expires_in"] = round(max(0, (p["ttl_sec"] or 600) - age))

    return {
        "generated": now.isoformat(),
        "mode": "live" if LIVE else "paper",
        "heartbeat_sec": heartbeat,
        "chain": chain,
        "costs_measured": bool(CFG.get_path("costs.measured", False)),
        "edge_gate_on": bool(CFG.get_path("risk.require_edge_gate", True)),
        "equity": {
            "equity": round(equity, 2), "cash": round(cash, 2), "starting": start,
            "return_pct": round((equity / start - 1) * 100, 2) if start else 0,
            "open": len(open_pos), "trades": len(trades), "wins": len(wins),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else None,
            "net": round(net, 2), "gross": round(gross, 2), "costs": round(costs, 2),
            "cost_drag": round(costs / abs(gross) * 100, 1) if gross else None,
        },
        "positions": open_pos,
        "proposals": props,
        "proposal_history": recent_props,
        "trades": trades,
        "equity_curve": [[r["ts"], r["equity"]] for r in curve],
        "funnel": scans[0] if scans else None,
        "failures": fails,
        "rejections": rejects,
        "friction_example": round(cm.round_trip_pct(20, 300_000), 2),
    }


@app.get("/api/state")
def state():
    return JSONResponse(build_state())


@app.get("/api/stream")
async def stream():
    """
    Server-Sent Events. Chosen over WebSocket deliberately: this is a one-way
    feed, EventSource reconnects on its own with backoff, and it survives
    proxies that mangle upgrades. Approvals are ordinary POSTs, so nothing
    needs a client-to-server channel.
    """
    async def gen():
        last_token = None
        last_push = 0.0
        # tell the browser to retry in 2s if the connection drops
        yield "retry: 2000\n\n"
        while True:
            token = WATCHER.token()
            now = time.monotonic()
            changed = token != last_token
            # heartbeat every 20s keeps intermediaries from closing an idle
            # connection, and lets the UI notice a dead server quickly
            if changed or now - last_push > 20:
                try:
                    payload = build_state()
                except Exception as e:
                    yield f"event: error\ndata: {json.dumps({'error': str(e)[:200]})}\n\n"
                    await asyncio.sleep(2)
                    continue
                yield f"event: state\ndata: {json.dumps(payload)}\n\n"
                last_token, last_push = token, now
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",   # nginx must not buffer an event stream
    })


@app.post("/api/proposals/{pid}/{action}")
def decide(pid: str, action: str):
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve or reject")
    if not Path(DB).exists():
        raise HTTPException(503, "no database yet — is the service running?")
    from ct.store import Store
    st = Store(DB)
    status = "approved" if action == "approve" else "rejected"
    # Atomic: only moves a row out of 'pending', so a double-click cannot
    # approve twice even if two tabs are open.
    ok = st.decide_proposal(pid, status, "ui")
    if not ok:
        cur = rows(DB, "SELECT status FROM proposals WHERE pid=?", (pid,))
        raise HTTPException(409, f"already {cur[0]['status'] if cur else 'gone'}")
    return {"ok": True, "pid": pid, "status": status,
            "note": "recorded — the daemon re-checks risk before executing"}


@app.get("/")
def index():
    idx = Path(__file__).parent / "static" / "index.html"
    if not idx.exists():
        return JSONResponse(
            {"error": "UI not built",
             "fix": "cd web/ui && npm install && npm run build"}, status_code=503)
    return FileResponse(str(idx))


# Vite emits hashed files under /assets, so that path must be mounted too.
# /static is kept for anything referenced the old way.
_STATIC = Path(__file__).parent / "static"
_ASSETS = _STATIC / "assets"
if _ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS)), name="assets")
if _STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("UI_HOST", "127.0.0.1"),
                port=int(os.environ.get("UI_PORT", "8787")), log_level="warning")
