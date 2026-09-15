import { useState } from "react";
import type { Proposal } from "../types";
import { money } from "../format";

export function Proposals({
  items, onDecide,
}: { items: Proposal[]; onDecide: (pid: string, a: "approve" | "reject") => Promise<void> }) {
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const act = async (p: Proposal, action: "approve" | "reject") => {
    if (action === "approve" &&
        !confirm(`Approve ${p.symbol} for ${money(p.size_usd)}?\n\n` +
                 `The daemon re-checks risk before signing.`)) return;
    setBusy((s) => new Set(s).add(p.pid));
    setError(null);
    try {
      await onDecide(p.pid, action);
    } catch (e) {
      setError(`${p.symbol}: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy((s) => { const n = new Set(s); n.delete(p.pid); return n; });
    }
  };

  if (!items.length)
    return <div className="empty">Nothing awaiting approval.</div>;

  return (
    <>
      {error && <div className="banner bad" role="alert">{error}</div>}
      {items.map((p) => {
        const expired = p.expires_in <= 0;
        const working = busy.has(p.pid);
        return (
          <div className="prop" key={p.pid}>
            <div className="prop-top">
              <span className="prop-sym">{p.symbol}</span>
              <span className="meta">{money(p.size_usd)} · score {p.composite.toFixed(1)}/100</span>
              <span className="spacer" />
              <span className={`countdown ${expired ? "neg" : ""}`}>
                {expired ? "expired" : `expires in ${p.expires_in}s`}
              </span>
            </div>
            <div className="meta">
              liquidity {money(p.liquidity)} · round trip {p.round_trip_pct.toFixed(2)}% ·
              {" "}needs +{p.round_trip_pct.toFixed(2)}% just to break even
            </div>
            <ul className="why">
              {p.rationale.slice(0, 5).map((r, i) => <li key={i}>{r}</li>)}
            </ul>
            <div className="acts">
              <button className="ok" disabled={expired || working} onClick={() => act(p, "approve")}>
                {working ? "…" : "Approve"}
              </button>
              <button className="no" disabled={working} onClick={() => act(p, "reject")}>
                Reject
              </button>
            </div>
          </div>
        );
      })}
    </>
  );
}
