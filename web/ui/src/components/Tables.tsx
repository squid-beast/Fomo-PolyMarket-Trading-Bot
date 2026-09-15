import type { Position, Trade, Failure, Funnel } from "../types";
import { money, signClass, shortTime, parseDetail, price, exitReason } from "../format";
export function Positions({ rows, available }: { rows: Position[]; available: boolean }) {
  if (!rows.length) return <div className="empty compact"><strong>{available ? "No open positions in the local snapshot" : "No position snapshot available"}</strong><span>FOMO account positions are not connected.</span></div>;
  return <div className="scroll"><table><thead><tr><th>Token</th><th className="r">Recorded size</th><th className="r">Quoted entry</th><th>Opened</th><th>Mode</th></tr></thead><tbody>{rows.map((p, i) => <tr key={p.token_address ?? i}><td><span className="token-name"><span className="token-mark small">{p.symbol.slice(0, 1)}</span>{p.symbol}</span></td><td className="r">{money(p.size_usd)}</td><td className="r">{price(p.entry_price_quoted)}</td><td>{shortTime(p.opened_at)}</td><td><span className="badge">Unknown</span></td></tr>)}</tbody></table></div>;
}
export function Failures({ rows }: { rows: Failure[] }) {
  if (!rows.length) return <div className="empty compact"><span>No recent failures in the available local records.</span></div>;
  return <div className="scroll"><table><thead><tr><th>When</th><th>Mode</th><th>Event</th><th>Token</th><th>Detail</th></tr></thead><tbody>{rows.map((f, i) => {
    const d = parseDetail(f.detail); const stillOpen = d.position_still_open === true;
    return <tr key={i}><td>{shortTime(f.ts)}</td><td>{f.mode || "Unknown"}</td><td className={stillOpen ? "neg" : ""}>{f.kind.replace(/_/g, " ")}{stillOpen && " · position still open"}</td><td>{f.symbol ?? "—"}</td><td>{String(d.error ?? d.reason ?? "No detail recorded")}</td></tr>;
  })}</tbody></table></div>;
}
export function Trades({ rows }: { rows: Trade[] }) {
  if (!rows.length) return <div className="empty compact"><strong>No closed trades in the local feed</strong><span>Use Trade history to search all recorded trades.</span></div>;
  return <div className="scroll"><table><thead><tr><th>Token</th><th>Closed</th><th className="r">Recorded net</th><th>Exit reason</th><th>Mode</th></tr></thead><tbody>{rows.slice(0, 5).map((t, i) => <tr key={t.id ?? i}><td><span className="token-name"><span className="token-mark small">{t.symbol.slice(0, 1)}</span>{t.symbol}</span></td><td>{shortTime(t.closed_at)}</td><td className={`r ${signClass(t.net_pnl_usd)}`}>{money(t.net_pnl_usd)}</td><td>{exitReason(t.exit_reason)}</td><td><span className="badge">{t.mode || "unknown"}</span></td></tr>)}</tbody></table></div>;
}
export function FunnelView({ f, rejections }: { f: Funnel | null; rejections: { reason: string; c: number }[] }) {
  if (!f) return <div className="empty"><strong>No completed local scan available</strong><span>The legacy scanner uses DexScreener data on Solana.</span></div>;
  const stages: [string, number][] = [["Discovered", f.universe], ["Passed safety", f.passed_safety], ["Scored", f.scored], ["Entries", f.entries]];
  const mx = Math.max(...stages.map(([, v]) => v), 1);
  return <><p className="meta">Last scan {shortTime(f.ts)}</p><div className="funnel">{stages.map(([label, v]) => <div className="funnel-row" key={label}><span>{label}</span><div className="funnel-track"><div style={{ width: `${v / mx * 100}%` }}/></div><b>{v ?? "—"}</b></div>)}</div>
    {!!rejections.length && <details className="rejections"><summary>Common safety rejections · all recorded scans</summary><div className="rejection-list">{rejections.map(r => <div key={r.reason}><span>{r.reason.split("(")[0]!.replace(/_/g, " ")}</span><b>{r.c.toLocaleString()}</b></div>)}</div></details>}</>;
}
