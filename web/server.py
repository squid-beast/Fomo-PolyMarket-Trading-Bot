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
import asyncio, json, os, re, sqlite3, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ct import config
from ct.costs import CostModel
from ct.ledger import Ledger
from web.history import date_bounds, normalize_trade, query_history

ROOT = Path(__file__).resolve().parents[1]
DB = os.environ.get("DB_FILE", str(ROOT / "paper.db"))
LEDGER = os.environ.get("LEDGER_FILE", str(ROOT / "ledger.db"))
STATE = os.environ.get("STATE_FILE", str(ROOT / "state.json"))
CFG = config.load(os.environ.get("CONFIG_FILE", str(ROOT / "config.yaml")))
LIVE = os.environ.get("LIVE_TRADING", "false").lower() == "true"
# FOMO research access is not an order executor. Keep the decision boundary
# closed until an independently verified FOMO execution integration exists.
FOMO_EXECUTION_AVAILABLE = False

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


def strict_rows(db_path: str, sql: str, args: tuple = ()) -> list[dict]:
    """Read rows while preserving storage errors for mutation endpoints.

    The dashboard's optional panels may treat a missing or malformed local
    store as empty. A decision endpoint must distinguish that from a real
    missing proposal, otherwise an unreadable database is reported as a 404.
    """
    if not Path(db_path).exists():
        return []
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def jloads(v, default):
    try: return json.loads(v) if v else default
    except (TypeError, ValueError): return default


def workspace_metadata() -> dict:
    # An explicit allowlist avoids accidentally publishing future secret config.
    fields = {
        "exits": ("take_profit_pct", "stop_loss_pct", "trailing_stop_pct", "trail_arm_pct",
                  "max_hold_hours", "liquidity_collapse_pct", "scale_out_ladder"),
        "risk": ("max_position_pct", "min_position_usd", "max_concurrent_positions",
                 "min_cash_reserve_pct", "max_daily_loss_pct", "max_total_exposure_pct",
                 "max_entries_per_day", "min_expected_edge_pct", "require_edge_gate"),
        "run": ("scan_interval_sec", "manage_interval_sec"),
    }
    return {
        "integrations": {
            "fomo_execution": FOMO_EXECUTION_AVAILABLE,
            "fomo_market_feed": False, "fomo_copy_trading": False,
            "research_provider": "https://fomoapi.io",
            "research_provider_status": "Independent, unofficial analytics provider; research only",
            "credential_status": "not inspected",
            "scanner": "DexScreener", "executor": "Jupiter", "chain": "Solana",
        },
        "configuration": {
            **{section: {key: CFG.get_path(f"{section}.{key}") for key in keys}
               for section, keys in fields.items()},
            "scope": "Web process configuration snapshot; daemon overrides and reload state unverified",
            "editable": False,
            "configured_mode": "live" if LIVE else "paper",
            "configured_mode_source": "Web process LIVE_TRADING setting only; not daemon verification",
            "verified_execution_mode": None,
            "chart_based_early_exit": False,
        },
        "account": {
            "source": "Legacy local snapshot", "mode": "unknown", "mode_verified": False,
            "equity_curve_scope": "Latest 400 local snapshots; modes were not recorded",
            "settlement_verified": False,
            "history_coverage": "Local closed trade records; FOMO account history is not imported",
        },
    }


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
    cash = positions.get("cash")
    open_pos = positions.get("positions", []) or []

    trades = [normalize_trade(t) for t in rows(
        DB, "SELECT * FROM trades ORDER BY julianday(closed_at) DESC, id DESC LIMIT 60")]
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

    start = float(CFG.get_path("portfolio.starting_equity_usd", 1000))
    equity = curve[-1]["equity"] if curve else None

    for p in props:
        p["scores"] = jloads(p["scores"], {})
        p["rationale"] = jloads(p["rationale"], [])
        try:
            stamp = datetime.fromisoformat(p["ts"])
            if stamp.tzinfo is None:
                raise ValueError("proposal timestamp has no timezone")
            age = max(0, (now - stamp).total_seconds())
        except Exception:
            age = None
        p["age_sec"] = round(age) if age is not None else None
        p["expires_in"] = (round(max(0, p["ttl_sec"] - age))
                           if age is not None and p.get("ttl_sec") is not None else 0)
        p["source"] = "Legacy DexScreener scan · Solana"
        p["provenance_verified"] = False
        p["approval_available"] = False
        p["approval_blocked_reason"] = ("Edge gate: no measured expected_edge_pct has been "
                                        "supplied, so risk.evaluate() rejects this entry.")

    return {
        "generated": now.isoformat(),
        # Configured mode, from this process's LIVE_TRADING. Not daemon-verified —
        # that distinction lives in configuration.verified_execution_mode, which
        # stays None. Withholding it entirely hid LIVE from the dashboard.
        "mode": "live" if LIVE else "paper",
        **workspace_metadata(),
        "heartbeat_sec": heartbeat,
        "chain": chain,
        "costs_measured": bool(CFG.get_path("costs.measured", False)),
        "edge_gate_on": bool(CFG.get_path("risk.require_edge_gate", True)),
        "equity": {
            "equity": round(equity, 2) if equity is not None else None,
            "cash": round(cash, 2) if cash is not None else None, "starting": start,
            # Neither the legacy snapshot nor curve has a verified account mode.
            # Recent mixed-mode trades cannot establish current-account returns.
            "return_pct": None, "open": len(open_pos) if positions else None,
            "trades": None, "wins": None, "win_rate": None,
            "net": None, "gross": None, "costs": None, "cost_drag": None,
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


@app.get("/api/trades")
def trade_history(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str, Query(max_length=128)] = "",
    mode: Literal["all", "live", "paper", "unknown"] = "all",
    outcome: Literal["all", "win", "loss", "breakeven"] = "all",
    start: str | None = None, end: str | None = None,
    sort: Literal["newest", "oldest"] = "newest",
):
    try:
        date_bounds(start, end)
        if any(ord(char) < 32 for char in q):
            raise ValueError("Search must not contain control characters.")
        # SQLite binds LIMIT/OFFSET as signed 64-bit integers.
        if (page - 1) * page_size > 9_223_372_036_854_775_807:
            raise ValueError("Page is too large for the local history store.")
    except (ValueError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if not Path(DB).exists():
        return {"items": [], "total": 0, "page": page, "page_size": page_size,
                "pages": 0, "summaries": [], "storage_available": False,
                "coverage": "No local trade store is available. FOMO history is not imported.",
                "valuation": "Recorded model values; settlement and actual fills are unverified."}
    conn = None
    try:
        conn = sqlite3.connect(Path(DB).resolve().as_uri() + "?mode=ro", uri=True)
        return query_history(conn, page=page, page_size=page_size, q=q.strip(), mode=mode,
                             outcome=outcome, start=start, end=end, sort=sort)
    except sqlite3.Error as exc:
        raise HTTPException(503, "Local trade history could not be read. Retry after checking the store.") from exc
    finally:
        if conn is not None:
            conn.close()


# --- FOMO research account -------------------------------------------------
# Deliberately NOT part of build_state() or the SSE stream. That stream rebuilds
# on every database write — several times a minute while the daemon scans — and
# fomoapi.io is metered. Wiring a paid API into a push feed would spend a month
# of credits in a day. This endpoint is pulled by one panel, on demand, only.
#
# The arithmetic behind the policy below: the free tier is 250,000 credits per
# month, and one live refresh is balances (250) + trades (250 + 1 per deep call,
# deep=5) ≈ 505 credits. That is ~495 refreshes a month, ~16 a day. So the
# default path (refresh=false) never touches the network — it reads the same
# disk cache the research scripts write, which costs nothing — and an explicit
# refresh is additionally rate limited per process, so a stuck UI or a held-down
# button cannot burn the month in an afternoon.
FOMO_BUDGET_CREDITS = 250_000
FOMO_MIN_REFRESH_SEC = 60.0
FOMO_REFRESH_MAX_AGE = 60.0     # a live refresh still reuses anything this fresh
# spent: what THIS web process has spent since it started. Research runs draw on
# the same monthly pool and are not counted here, so treat it as a floor, not a
# balance — the same reason nothing else in this file claims to be verified.
_FOMO: dict = {"last_refresh": 0.0, "spent": 0, "fetched_at": {}}
# Handles are interpolated into the provider's URL path, so the charset is an
# allowlist that cannot start a '../' segment.
FOMO_HANDLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


@app.get("/api/fomo/account")
def fomo_account(handle: Annotated[str, Query(max_length=64)] = "",
                 refresh: bool = False):
    name = handle.strip()
    if not FOMO_HANDLE.fullmatch(name):
        raise HTTPException(422, "Handle must be 1–64 characters: a letter or digit "
                                 "first, then letters, digits, '_', '-' or '.'")

    notes: list[str] = []
    max_age = None                  # None = serve whatever is on disk, zero credits
    if refresh:
        waited = time.monotonic() - _FOMO["last_refresh"]
        if waited < FOMO_MIN_REFRESH_SEC:
            notes.append(f"Live refresh skipped: the minimum interval is "
                         f"{int(FOMO_MIN_REFRESH_SEC)}s and the last one was "
                         f"{int(waited)}s ago. Showing cached data.")
        else:
            max_age = FOMO_REFRESH_MAX_AGE
            # Claim the slot before fetching, not after: a slow or failing fetch
            # must not leave the door open for a retry storm.
            _FOMO["last_refresh"] = time.monotonic()

    payload = {
        # A cache file written by an earlier process carries no recorded fetch
        # time, so this is null rather than a guess.
        "handle": name, "fetched_at": _FOMO["fetched_at"].get(name),
        "holdings": [], "total_value_usd": None,
        "open_positions": [], "closed_trades": [],
        "coverage": {
            "complete": False,
            "closed_trade_cap": "25 most recent per chain; the provider sends no cursor",
            # Not a feature flag: the provider documents no order-placement
            # endpoint at all, so this can only ever be False.
            "execution_available": False,
            "execution_note": "fomoapi.io is a read-only analytics provider; "
                              "it documents no order-placement endpoint",
            "provider": "https://fomoapi.io",
            "provider_status": "Independent, unofficial FOMO analytics; research only",
        },
        "credits": {"spent": _FOMO["spent"], "left": 0, "budget": FOMO_BUDGET_CREDITS},
        "error": None,
    }

    from ct.research.fomo_client import BudgetExceeded, FomoClient
    try:
        # Per request, and handed only the credits this process has left, so the
        # client's own budget guard still means something. The cache lives at the
        # repo root, shared with the research scripts, which is what makes the
        # default path free.
        # max_age is set ONLY on a refresh that cleared the interval gate, so it
        # doubles as "this request is allowed to spend". Everything else gets a
        # zero budget: a cache hit still returns, a cache MISS raises
        # BudgetExceeded instead of quietly buying 505 credits of data.
        spend_ok = max_age is not None
        client = FomoClient(
            cache_dir=str(ROOT / ".fomo_cache"),
            budget_credits=(max(0, FOMO_BUDGET_CREDITS - _FOMO["spent"])
                            if spend_ok else 0))
    except RuntimeError as exc:     # no FOMO_API_KEY here — an honest empty panel,
        client = None               # not a broken page
        notes.append(f"FOMO research access is not configured: {exc}")

    if client is not None:
        try:
            bal = client.balances(name, max_age=max_age)
            trades = client.account_trades(name, deep=5, max_age=max_age)
        except BudgetExceeded as exc:
            notes.append(
                "No cached snapshot for this handle yet. Press Refresh to spend "
                "about 505 credits fetching one." if not spend_ok
                else f"Credit budget guard tripped: {exc}")
        except Exception as exc:
            notes.append(f"FOMO request failed: {exc}")
        else:
            payload["holdings"] = bal.get("holdings") or []
            payload["total_value_usd"] = bal.get("total_value_usd")
            payload["open_positions"] = trades.get("open") or []
            payload["closed_trades"] = trades.get("closed") or []
            failed = [m for m in (bal.get("error"), trades.get("error")) if m]
            notes.extend(failed)
            if max_age is not None and not failed:
                stamp = datetime.now(timezone.utc).isoformat()
                if len(_FOMO["fetched_at"]) > 32:    # a panel, not a directory
                    _FOMO["fetched_at"].clear()
                _FOMO["fetched_at"][name] = stamp
                payload["fetched_at"] = stamp
        _FOMO["spent"] += client.spent

    payload["credits"] = {"spent": _FOMO["spent"],
                          "left": max(0, FOMO_BUDGET_CREDITS - _FOMO["spent"]),
                          "budget": FOMO_BUDGET_CREDITS}
    payload["error"] = "; ".join(notes) or None
    return payload


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
    if action == "approve":
        raise HTTPException(409, "Entries are blocked: no strategy has supplied a measured "
                            "expected_edge_pct, so the edge gate rejects every entry. "
                            "See docs/FINDINGS.md and .claude/rules/non-negotiables.md §2.")
    if not Path(DB).exists():
        raise HTTPException(503, "no database yet — is the service running?")
    try:
        cur = strict_rows(DB, "SELECT status FROM proposals WHERE pid=?", (pid,))
    except sqlite3.Error as exc:
        raise HTTPException(503, "Local proposal store could not be read. Retry after checking the store.") from exc
    if not cur:
        raise HTTPException(404, "Proposal no longer exists. Refresh the local feed.")
    from ct.store import Store
    st = Store(DB)
    status = "approved" if action == "approve" else "rejected"
    # Atomic: only moves a row out of 'pending', so a double-click cannot
    # approve twice even if two tabs are open.
    try:
        ok = st.decide_proposal(pid, status, "ui")
    finally:
        st.db.close()
    if not ok:
        try:
            cur = strict_rows(DB, "SELECT status FROM proposals WHERE pid=?", (pid,))
        except sqlite3.Error as exc:
            raise HTTPException(503, "Local proposal store could not be read. Retry after checking the store.") from exc
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
