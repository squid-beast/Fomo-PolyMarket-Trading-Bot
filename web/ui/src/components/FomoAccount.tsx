import { useCallback, useEffect, useState } from "react";
import type { FomoAccountPayload, FomoCoverage, FomoHolding, FomoPosition } from "../types";
import { money, pct, price, signClass, shortTime } from "../format";
import { Icon } from "./Header";

const STORE_KEY = "copytrader.fomo-handle";
// Storage can be unavailable (private windows, blocked site data); the panel must still work.
const readHandle = (): string => { try { return localStorage.getItem(STORE_KEY) ?? ""; } catch { return ""; } };
const rememberHandle = (handle: string): void => { try { localStorage.setItem(STORE_KEY, handle); } catch { /* not required */ } };
// No shared helper formats a bare token quantity or a credit count; money()/price() both add a currency mark.
const num = (v: number | null | undefined): string =>
  v == null || !Number.isFinite(v) ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 4 });

function Token({ symbol }: { symbol: string | null }) {
  const name = symbol || "Unknown";
  return <span className="token-name"><span className="token-mark small">{name.slice(0, 1)}</span>{name}</span>;
}

// The provider reports several distinct blocking conditions through one error string.
function errorNote(error: string): [string, string, "warning" | "info"] {
  const text = error.toLowerCase();
  if (text.includes("no cached snapshot") || text.includes("skipped") || text.includes("minimum interval"))
    return ["Showing cached data", "No metered read was made. Press Refresh live to spend credits on a fresh snapshot.", "info"];
  if (text.includes("key") || text.includes("credential") || text.includes("401") || text.includes("unauthorized"))
    return ["Analytics key not configured", "The fomoapi.io key is configured outside this UI. No account data can be read until the service has it.", "warning"];
  if (text.includes("rate") || text.includes("429") || text.includes("budget") || text.includes("credit"))
    return ["Provider rate limit or credit budget reached", "Any rows shown below are from an earlier cached read. Wait before requesting another live refresh.", "warning"];
  return ["FOMO account lookup failed", "Rows below, if any, are from an earlier cached read. Nothing was executed; this provider is read-only.", "warning"];
}

function Coverage({ c }: { c: FomoCoverage }) {
  return <>
    <div className="detail-heading"><strong>Coverage and limits</strong><span>Stated by the provider · not verified here</span></div>
    <div className="facts-list">
      <div><span>Closed trade coverage</span><strong>{c.closed_trade_cap}</strong></div>
      <div><span>Complete history</span><strong className="neg">{c.complete ? "Reported complete" : "Never — the provider always reports complete: false"}</strong></div>
      <div><span>Order placement</span><strong className="neg">{c.execution_available ? "Reported available" : "Unavailable"}</strong></div>
      <div><span>Execution note</span><strong>{c.execution_note}</strong></div>
      <div><span>Provider</span><strong>{c.provider}</strong></div>
      <div><span>Provider status</span><strong>{c.provider_status}</strong></div>
    </div>
  </>;
}

function Holdings({ rows }: { rows: FomoHolding[] }) {
  if (!rows.length) return <div className="empty compact"><strong>No holdings in this snapshot</strong><span>The provider returned an empty balance list for this handle.</span></div>;
  return <div className="scroll"><table><thead><tr><th>Token</th><th>Chain</th><th className="r">Amount</th><th className="r">Price</th><th className="r">Value</th><th className="r">24h change</th></tr></thead><tbody>
    {rows.map((h, i) => <tr key={h.address ?? i}>
      <td><Token symbol={h.symbol}/></td>
      <td><span className="badge">{h.chain || "unknown"}</span></td>
      <td className="r">{num(h.amount)}</td>
      <td className="r">{price(h.price_usd)}</td>
      <td className="r">{money(h.value_usd)}</td>
      <td className={`r ${signClass(h.change_24h)}`}>{pct(h.change_24h)}</td>
    </tr>)}
  </tbody></table></div>;
}

function OpenPositions({ rows }: { rows: FomoPosition[] }) {
  if (!rows.length) return <div className="empty compact"><strong>No open positions reported</strong><span>Open positions come from the same read-only feed as the holdings above.</span></div>;
  return <div className="scroll"><table><thead><tr><th>Token</th><th>Chain</th><th className="r">Amount</th><th className="r">Avg entry</th><th className="r">Unrealised P&L</th><th>Opened</th></tr></thead><tbody>
    {rows.map((p, i) => <tr key={p.trade_id ?? i}>
      <td><Token symbol={p.symbol}/></td>
      <td><span className="badge">{p.chain || "unknown"}</span></td>
      <td className="r">{num(p.amount)}</td>
      <td className="r">{price(p.avg_entry_price)}</td>
      <td className={`r ${signClass(p.unrealized_pnl_usd)}`}>{money(p.unrealized_pnl_usd)}</td>
      <td>{shortTime(p.created_at)}</td>
    </tr>)}
  </tbody></table></div>;
}

function ClosedTrades({ rows }: { rows: FomoPosition[] }) {
  if (!rows.length) return <div className="empty compact"><strong>No closed trades reported</strong><span>The provider returns at most the 25 most recent closed trades per chain.</span></div>;
  return <div className="scroll"><table><thead><tr><th>Token</th><th>Chain</th><th className="r">Avg entry</th><th className="r">Avg exit</th><th className="r">Realised P&L</th><th>Closed</th></tr></thead><tbody>
    {rows.map((t, i) => <tr key={t.trade_id ?? i}>
      <td><Token symbol={t.symbol}/></td>
      <td><span className="badge">{t.chain || "unknown"}</span></td>
      <td className="r">{price(t.avg_entry_price)}</td>
      <td className="r">{price(t.avg_exit_price)}</td>
      <td className={`r ${signClass(t.realized_pnl_usd)}`}>{money(t.realized_pnl_usd)}</td>
      <td>{shortTime(t.closed_at)}</td>
    </tr>)}
  </tbody></table></div>;
}

export function FomoAccount() {
  const [handle, setHandle] = useState(readHandle);
  const [data, setData] = useState<FomoAccountPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Never polls and never rides the SSE stream: every live read spends metered provider credits.
  const load = useCallback(async (query: string, refresh: boolean) => {
    const wanted = query.trim();
    if (!wanted) return;
    setLoading(true); setError(null);
    try {
      const response = await fetch(`/api/fomo/account?handle=${encodeURIComponent(wanted)}&refresh=${refresh}`);
      const body: unknown = await response.json();
      if (!response.ok) {
        const detail = (body as { detail?: unknown }).detail;
        throw new Error(typeof detail === "string" ? detail : `Lookup failed (HTTP ${response.status}).`);
      }
      setData(body as FomoAccountPayload);
      rememberHandle(wanted);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to reach the local FOMO endpoint.");
    } finally { setLoading(false); }
  }, []);

  // Mount reads the cached copy only. Live refresh stays behind the button.
  useEffect(() => { const saved = readHandle(); if (saved) void load(saved, false); }, [load]);

  const busy = loading || !handle.trim();
  const note = data?.error ? errorNote(data.error) : error ? errorNote(error) : null;
  return <>
    <div className="notice info">Read-only provider. fomoapi.io documents no order-placement endpoint, so nothing in this panel can open, close or size a position.</div>
    <form className="history-controls" onSubmit={e => { e.preventDefault(); void load(handle, false); }}>
      <label className="search-field"><span>FOMO handle</span><div><Icon name="search"/><input type="search" placeholder="Trader handle" value={handle} maxLength={64} onChange={e => setHandle(e.target.value)}/></div></label>
      <label><span>Cached read</span><button type="submit" disabled={busy}>Load cached</button></label>
      <label><span>Metered read</span><button type="button" disabled={busy} onClick={() => void load(handle, true)}>Refresh live <Icon name="arrow" size={15}/></button></label>
    </form>

    {note && <div className={`notice ${note[2]}`} role={note[2] === "info" ? "status" : "alert"}><strong>{note[0]}</strong> {note[1]}</div>}
    {loading && <div className="history-loading" role="status"><span className="loading-ring"/> Reading the FOMO account…</div>}
    {!loading && !data && !error && <div className="empty"><Icon name="search" size={28}/><strong>No FOMO account loaded</strong><span>Enter a handle to read the cached snapshot. Live refreshes stay manual because each one spends provider credits.</span></div>}

    {!loading && data && <>
      <div className="history-summary">
        <div className="summary-caption"><strong>{data.handle}</strong><span>Provider snapshot · balances and P&L are reported by fomoapi.io and are not reconciled locally.</span></div>
        <div className="mode-summary"><span className="badge">Reported value</span><b>{money(data.total_value_usd)}</b><span>total across chains</span><small>Unverified · not this workspace's equity</small></div>
        <div className="mode-summary"><span className="badge">Credits</span><b className={data.credits.left <= 0 ? "neg" : ""}>{num(data.credits.left)}</b><span>at most {num(data.credits.budget)} left of the budget</span><small>{num(data.credits.spent)} spent by this web process · research runs share the same monthly pool and are not counted here</small></div>
        <div className="mode-summary"><span className="badge">Fetched</span><b>{shortTime(data.fetched_at)}</b><span>cached until you refresh</span><small>A live refresh spends credits</small></div>
      </div>

      <Coverage c={data.coverage}/>

      <div className="detail-heading"><strong>Holdings</strong><span>{data.holdings.length} reported · prices from the provider</span></div>
      <Holdings rows={data.holdings}/>

      <div className="detail-heading"><strong>Open positions</strong><span>{data.open_positions.length} reported · unrealised P&L is the provider's own mark</span></div>
      <OpenPositions rows={data.open_positions}/>

      <div className="detail-heading"><strong>Recent closed trades</strong><span>{data.closed_trades.length} shown · {data.coverage.closed_trade_cap}</span></div>
      <ClosedTrades rows={data.closed_trades}/>

      <p className="footnote">Partial history: the provider reports complete: {String(data.coverage.complete)} and offers no cursor, so win rates computed from these rows are not a full record. This feed is research input only — it cannot execute.</p>
    </>}
  </>;
}
