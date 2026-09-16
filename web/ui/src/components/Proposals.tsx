import { useEffect, useState } from "react";
import type { Proposal, ConnStatus } from "../types";
import { money, percent } from "../format";
export function Proposals({ items, onDecide, status, generated }: {
  items: Proposal[]; onDecide: (pid: string, a: "approve" | "reject") => Promise<void>;
  status: ConnStatus; generated: string;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const reject = async (p: Proposal) => {
    setBusy(p.pid); setError(null);
    try { await onDecide(p.pid, "reject"); }
    catch (e) { setError(`${p.symbol}: ${e instanceof Error ? e.message : "Unable to record decision. Try again."}`); }
    finally { setBusy(null); }
  };
  return <>{error && <div className="notice warning" role="alert">{error}</div>}
    {!items.length && <div className="empty"><strong>No proposals to review</strong><span>Eligible candidates from the local scanner appear here.</span></div>}
    {items.map(p => {
      const elapsed = Math.max(0, (now - new Date(generated).getTime()) / 1000);
      const expires = Math.max(0, Math.floor(p.expires_in - elapsed));
      const stale = status !== "live" || !Number.isFinite(elapsed) || elapsed > 45;
      return <article className="proposal" key={p.pid}>
        <div className="proposal-heading"><div className="token-mark">{p.symbol.slice(0, 1)}</div><div><h3>{p.symbol}</h3><span className="meta">{p.source || "Unknown source"}</span></div><span className="spacer"/><span className="badge">{stale ? "Feed stale" : expires ? `${expires}s remaining` : "Expired"}</span></div>
        <div className="proposal-metrics"><div><span>Proposed size</span><b>{money(p.size_usd)}</b></div><div><span>Signal score</span><b>{p.composite?.toFixed(1) ?? "—"}<small> / 100</small></b></div><div><span>Liquidity</span><b>{money(p.liquidity)}</b></div><div><span>Est. round-trip cost</span><b>{percent(p.round_trip_pct)}</b></div></div>
        <details open><summary>Why this candidate</summary><ul className="why">{p.rationale.length ? p.rationale.map((r, i) => <li key={i}>{r}</li>) : <li>No rationale recorded.</li>}</ul></details>
        <p className="meta break">Token: {p.token_address}</p><div className="proposal-actions"><button disabled title={p.approval_blocked_reason}>Approve unavailable</button><button disabled={busy !== null} onClick={() => reject(p)}>{busy === p.pid ? "Recording…" : "Reject proposal"}</button><span className="meta">FOMO execution not connected</span></div>
      </article>;
    })}</>;
}
