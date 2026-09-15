import sqlite3

import pytest

from web.history import date_bounds, query_history


def history_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE trades (
          id INTEGER PRIMARY KEY, symbol TEXT, token_address TEXT,
          opened_at TEXT, closed_at TEXT, size_usd REAL,
          entry_quoted REAL, exit_quoted REAL, gross_pnl_usd REAL,
          total_costs_usd REAL, net_pnl_usd REAL, net_pnl_pct REAL,
          exit_reason TEXT, hold_hours REAL, mode TEXT
        )
    """)
    for i in range(1, 76):
        mode = ("paper", "live", "legacy")[i % 3]
        net = 2.0 if i % 2 else -1.0
        conn.execute(
            "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (i, f"T{i:03}", f"ADDR{i:03}", "2026-08-01T00:00:00Z",
             f"2026-09-{(i % 28) + 1:02}T00:00:00Z", 20.0, .001,
             .0011, net + .5, .5, net, net / 20 * 100, "take_profit",
             1.0, mode),
        )
    conn.commit()
    return conn


def test_history_totals_cover_all_rows_and_keep_modes_separate():
    result = query_history(history_db(), page=2, page_size=50, q="", mode="all",
                           outcome="all", start=None, end=None, sort="newest")
    assert result["total"] == 75
    assert len(result["items"]) == 25
    assert {row["mode"] for row in result["items"]} == {"live", "paper", "unknown"}
    assert {summary["mode"] for summary in result["summaries"]} == {"live", "paper", "unknown"}
    assert sum(summary["count"] for summary in result["summaries"]) == 75


def test_history_filters_literal_search_date_and_outcome():
    result = query_history(history_db(), page=1, page_size=20, q="T%",
                           mode="unknown", outcome="win", start="2026-09-01",
                           end="2026-09-30", sort="oldest")
    assert result["total"] == 0
    result = query_history(history_db(), page=1, page_size=20, q="T075",
                           mode="paper", outcome="win", start=None, end=None,
                           sort="newest")
    assert result["total"] == 1
    result = query_history(history_db(), page=1, page_size=20, q="T074",
                           mode="unknown", outcome="loss", start=None, end=None,
                           sort="newest")
    assert result["total"] == 1  # Unrecognised modes stay unknown.


def test_history_rejects_reversed_or_malformed_dates():
    with pytest.raises(ValueError):
        date_bounds("2026-09-30", "2026-09-01")
    with pytest.raises(ValueError):
        date_bounds("09/01/2026", None)
