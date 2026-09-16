import type { Equity } from "../types";
import { money } from "../format";
export function Tiles({ e }: { e: Equity | undefined }) {
  return <div className="account-stats">
    <div className="main-stat"><span>Recorded equity</span><strong>{money(e?.equity)}</strong><small>Local snapshot · mode unknown</small></div>
    <div><span>Recorded cash</span><strong>{money(e?.cash)}</strong><small>Balance not reconciled to FOMO</small></div>
    <div><span>Open positions</span><strong>{e?.open ?? "—"}</strong><small>From the local snapshot</small></div>
    <div><span>Account net P&L</span><strong>—</strong><small>Requires verified account mode</small></div>
  </div>;
}
