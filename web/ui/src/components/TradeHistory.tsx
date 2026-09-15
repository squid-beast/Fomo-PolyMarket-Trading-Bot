import { Fragment, useEffect, useRef, useState } from "react";
import type { Trade, TradePage } from "../types";
import { money, pct, price, signClass, shortTime, fullTime, exitReason, solscanUrl } from "../format";
import { Icon } from "./Header";

type Filters = { q: string; mode: string; outcome: string; start: string; end: string; sort: string; page_size: string; page: number };
const defaults: Filters = { q: "", mode: "all", outcome: "all", start: "", end: "", sort: "newest", page_size: "20", page: 1 };
function csvCell(value: unknown): string {
  let text = value == null ? "" : String(value);
  // Spreadsheet applications may interpret leading whitespace before a formula.
  // Keep numeric P&L values numeric in the export; only text needs the guard.
  if (typeof value !== "number" && (/^[\s]*[=+\-@]/.test(text) || /^[\t\r\n]/.test(text))) text = "'" + text;
  return `"${text.replace(/"/g, '""')}"`;
}
function exportPage(rows: Trade[], page: number) {
  const fields: (keyof Trade)[] = ["id", "symbol", "token_address", "opened_at", "closed_at", "size_usd", "entry_quoted", "exit_quoted", "gross_pnl_usd", "total_costs_usd", "net_pnl_usd", "net_pnl_pct", "exit_reason", "hold_hours", "mode", "recorded_mode", "signature"];
  const csv = [fields.map(csvCell).join(","), ...rows.map(row => fields.map(field => csvCell(row[field])).join(","))].join("\r\n");
  const url = URL.createObjectURL(new Blob(["\uFEFF", csv], { type: "text/csv;charset=utf-8;" }));
  const a = document.createElement("a"); a.href = url; a.download = `copytrader-local-trades-page-${page}.csv`; a.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function TradeDetails({ trade: t }: { trade: Trade }) {
  const link = solscanUrl(t.signature);
  const fields = [
    ["Opened (UTC)", fullTime(t.opened_at)], ["Closed (UTC)", fullTime(t.closed_at)],
    ["Quoted entry price", price(t.entry_quoted)], ["Quoted exit price", price(t.exit_quoted)],
    ["Recorded size", money(t.size_usd)], ["Hold duration", t.hold_hours == null ? "Unknown" : `${t.hold_hours.toFixed(2)} hours`],
    ["Recorded gross P&L", money(t.gross_pnl_usd)], ["Recorded costs", money(t.total_costs_usd)],
    ["Recorded net P&L", `${money(t.net_pnl_usd)} (${pct(t.net_pnl_pct)})`], ["Recorded mode", t.recorded_mode || "Not recorded"],
  ];
  return <div className="trade-details"><div className="detail-heading"><strong>Trade #{t.id}</strong><span>Recorded model values · actual fills and settlement unverified</span></div><dl className="detail-grid">{fields.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl><div className="detail-address"><span>Token address</span><code>{t.token_address || "Not recorded"}</code></div><div className="detail-address"><span>Exit reason</span><span>{exitReason(t.exit_reason)}</span>{t.exit_reason && <code>{t.exit_reason}</code>}</div>{link && <a className="inline-link" href={link} target="_blank" rel="noreferrer">View recorded transaction on Solscan <Icon name="external" size={13}/></a>}</div>;
}
export function TradeHistory() {
  const [filters, setFilters] = useState<Filters>(defaults);
  const [result, setResult] = useState<TradePage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [expanded, setExpanded] = useState<number | null>(null);
  const serial = useRef(0);
  const update = (key: keyof Filters, value: string) => {
    setLoading(true); setResult(null); setExpanded(null);
    setFilters(f => ({ ...f, [key]: value, page: 1 }));
  };
  useEffect(() => {
    const controller = new AbortController();
    const current = ++serial.current;
    setLoading(true); setError(null); setResult(null); setExpanded(null);
    const timer = window.setTimeout(async () => {
      try {
        const params = new URLSearchParams();
        Object.entries(filters).forEach(([k, v]) => { if (v !== "") params.set(k, String(v)); });
        const response = await fetch(`/api/trades?${params}`, { signal: controller.signal });
        const body = await response.json();
        if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Some filters are invalid. Check the date range and try again.");
        if (current === serial.current && !controller.signal.aborted) setResult(body as TradePage);
      } catch (e) {
        if (!controller.signal.aborted && current === serial.current) setError(e instanceof Error ? e.message : "Unable to load trade history.");
      } finally { if (!controller.signal.aborted && current === serial.current) setLoading(false); }
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [filters, retry]);
  const changePage = (page: number) => { setLoading(true); setResult(null); setFilters(f => ({ ...f, page })); };
  return <>
    <div className="page-heading"><div><div className="eyebrow">Activity</div><h1>Trade history</h1><p>Search every locally recorded closed trade. FOMO account history is not imported.</p></div><button className="secondary" disabled={loading || !result?.items.length} onClick={() => result && exportPage(result.items, result.page)}>Export displayed page <Icon name="arrow" size={15}/></button></div>
    <div className="history-controls"><label className="search-field"><span>Search trades</span><div><Icon name="search"/><input type="search" placeholder="Token symbol or address" value={filters.q} maxLength={128} onChange={e => update("q", e.target.value)}/></div></label>
      <label><span>Recorded mode</span><select value={filters.mode} onChange={e => update("mode", e.target.value)}><option value="all">All modes</option><option value="live">Live</option><option value="paper">Paper</option><option value="unknown">Unknown</option></select></label>
      <label><span>Outcome</span><select value={filters.outcome} onChange={e => update("outcome", e.target.value)}><option value="all">All outcomes</option><option value="win">Win</option><option value="loss">Loss</option><option value="breakeven">Break-even</option></select></label>
      <label><span>From (UTC)</span><input type="date" value={filters.start} onChange={e => update("start", e.target.value)}/></label>
      <label><span>Through (UTC)</span><input type="date" value={filters.end} onChange={e => update("end", e.target.value)}/></label>
      <label><span>Order</span><select value={filters.sort} onChange={e => update("sort", e.target.value)}><option value="newest">Newest first</option><option value="oldest">Oldest first</option></select></label>
    </div>
    <div className="history-heading"><span aria-live="polite">{loading ? "Loading records…" : result ? `${result.total.toLocaleString()} matching trades` : "History unavailable"}</span><button className="text-button" onClick={() => { setFilters(defaults); setRetry(n => n + 1); }}>Reset filters</button></div>
    {error && <div className="empty error-state" role="alert"><strong>Trade history could not be loaded</strong><span>{error}</span><button onClick={() => setRetry(n => n + 1)}>Retry history</button></div>}
    {loading && <div className="history-loading" role="status"><span className="loading-ring"/> Loading local trade history…</div>}
    {!loading && result && <>
      {!!result.summaries.length && <div className="history-summary"><div className="summary-caption"><strong>All matching records</strong><span>Totals stay separate by recorded mode.</span></div>{result.summaries.map(s => <div className="mode-summary" key={s.mode}><span className="badge">{s.mode}</span><b className={signClass(s.net)}>{money(s.net)}</b><span>recorded net · {s.pnl_count} of {s.count} trades with P&L</span><small>{s.wins} wins · {s.losses} losses · {s.breakeven} break-even</small><small>Gross {money(s.gross)} ({s.gross_count}/{s.count}) · Costs {money(s.costs)} ({s.costs_count}/{s.count})</small></div>)}</div>}
      {result.items.length ? <div className="scroll history-table"><table><thead><tr><th>Token / details</th><th>Closed</th><th className="r">Recorded size</th><th className="r">Recorded net P&L</th><th>Exit reason</th><th>Mode</th></tr></thead><tbody>{result.items.map(t => <Fragment key={t.id}><tr className={expanded === t.id ? "expanded-row" : ""}><td><button className="trade-toggle" aria-expanded={expanded === t.id} aria-controls={`trade-${t.id}`} aria-label={`${expanded === t.id ? "Hide" : "Show"} ${t.symbol} trade ${t.id} details`} onClick={() => setExpanded(expanded === t.id ? null : t.id)}><span className="token-mark small">{t.symbol.slice(0, 1)}</span><strong>{t.symbol}</strong><span className={`chevron ${expanded === t.id ? "open" : ""}`}>›</span></button></td><td>{shortTime(t.closed_at)}</td><td className="r">{money(t.size_usd)}</td><td className={`r ${signClass(t.net_pnl_usd)}`}><b>{money(t.net_pnl_usd)}</b><small className="table-sub">{pct(t.net_pnl_pct)}</small></td><td>{exitReason(t.exit_reason)}</td><td><span className="badge">{t.mode}</span></td></tr>{expanded === t.id && <tr id={`trade-${t.id}`} className="detail-row"><td colSpan={6}><TradeDetails trade={t}/></td></tr>}</Fragment>)}</tbody></table></div> : <div className="empty"><Icon name="history" size={28}/><strong>{!result.storage_available ? "Local trade history is not available yet" : "No trades match these filters"}</strong><span>{!result.storage_available ? "Records will appear when a local trade store is available." : "Adjust the search, mode, outcome, or dates to see more records."}</span></div>}
      <div className="pagination"><label><span>Rows per page</span><select value={filters.page_size} onChange={e => update("page_size", e.target.value)}>{[10,20,50,100].map(n => <option key={n} value={n}>{n}</option>)}</select></label><span>{result.total ? `Page ${result.page} of ${result.pages}` : "0 pages"}</span><button disabled={result.page <= 1} onClick={() => changePage(result.page - 1)}>Previous</button><button disabled={result.page >= result.pages} onClick={() => changePage(result.page + 1)}>Next</button></div>
      <p className="footnote">{result.valuation} CSV exports include only the displayed page.</p>
    </>}
  </>;
}
