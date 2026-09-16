"""Read-only, bounded queries over locally recorded trades.

The legacy trade store may have no mode column. Missing or unrecognised modes
remain visible as unknown; they must never be relabelled from web-process config.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta


def date_bounds(start: str | None, end: str | None) -> tuple[str | None, str | None]:
    def parse(value):
        if value is None:
            return None
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Dates must use YYYY-MM-DD (UTC).")
        return date.fromisoformat(value)

    first, last = parse(start), parse(end)
    if first and last and first > last:
        raise ValueError("Start date must be on or before end date.")
    return (first.isoformat() if first else None,
            (last + timedelta(days=1)).isoformat() if last else None)


def normalize_trade(row: dict) -> dict:
    recorded_mode = row.get("mode")
    return {**row, "recorded_mode": recorded_mode,
            "mode": recorded_mode if recorded_mode in ("live", "paper") else "unknown"}


def query_history(conn: sqlite3.Connection, *, page: int, page_size: int, q: str,
                  mode: str, outcome: str, start: str | None, end: str | None,
                  sort: str) -> dict:
    """Connection is supplied so tests can use a newly created in-memory store."""
    first, until = date_bounds(start, end)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
    mode_sql = ("CASE WHEN mode IN ('live','paper') THEN mode ELSE 'unknown' END"
                if "mode" in columns else "'unknown'")
    clauses, args = [], []
    if q:
        # Search terms are literal substrings: user '%' and '_' are not wildcards.
        term = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("(symbol LIKE ? ESCAPE '\\' OR token_address LIKE ? ESCAPE '\\')")
        args.extend([f"%{term}%"] * 2)
    if mode != "all":
        clauses.append(f"({mode_sql}) = ?")
        args.append(mode)
    if outcome != "all":
        clauses.append({"win": "net_pnl_usd > 0", "loss": "net_pnl_usd < 0",
                        "breakeven": "net_pnl_usd = 0"}[outcome])
    if first:
        clauses.append("julianday(closed_at) >= julianday(?)")
        args.append(first)
    if until:
        clauses.append("julianday(closed_at) < julianday(?)")
        args.append(until)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    direction = "DESC" if sort == "newest" else "ASC"
    conn.row_factory = sqlite3.Row
    # Keep page, count, and summaries on the same read snapshot during inserts.
    conn.execute("BEGIN")
    try:
        total = conn.execute("SELECT COUNT(*) FROM trades" + where, args).fetchone()[0]
        records = conn.execute(
            "SELECT * FROM trades" + where +
            f" ORDER BY julianday(closed_at) {direction}, id {direction} LIMIT ? OFFSET ?",
            [*args, page_size, (page - 1) * page_size]).fetchall()
        summaries = conn.execute(
            f"SELECT {mode_sql} AS mode, COUNT(*) AS count, "
            "COUNT(net_pnl_usd) AS pnl_count, SUM(net_pnl_usd) AS net, "
            "SUM(gross_pnl_usd) AS gross, SUM(total_costs_usd) AS costs, "
            "COUNT(gross_pnl_usd) AS gross_count, COUNT(total_costs_usd) AS costs_count, "
            "SUM(CASE WHEN net_pnl_usd > 0 THEN 1 ELSE 0 END) AS wins, "
            "SUM(CASE WHEN net_pnl_usd < 0 THEN 1 ELSE 0 END) AS losses, "
            "SUM(CASE WHEN net_pnl_usd = 0 THEN 1 ELSE 0 END) AS breakeven "
            "FROM trades" + where + f" GROUP BY {mode_sql}", args).fetchall()
    finally:
        conn.rollback()
    return {"items": [normalize_trade(dict(r)) for r in records],
            "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
            "summaries": [dict(r) for r in summaries], "storage_available": True,
            "coverage": "Local closed trade records; FOMO account history is not imported.",
            "valuation": "Recorded model values; settlement and actual fills are unverified."}
