import type { State, ConnStatus } from "../types";
import { Icon } from "./Header";
export function Banners({ state, status, openSetup }: { state: State | null; status: ConnStatus; openSetup: () => void }) {
  const live = state?.configuration.configured_mode === "live";
  const stillOpen = (state?.failures ?? []).some(f => {
    try { return JSON.parse(f.detail || "{}").position_still_open === true; } catch { return false; }
  });
  const hb = state?.heartbeat_sec ?? null;
  return <>{live
    // Never claim the system cannot trade while LIVE_TRADING is on: the daemon
    // signs real Jupiter swaps and manages exits regardless of this dashboard.
    ? <div className="availability critical"><Icon name="lock"/><div><strong>LIVE — real money</strong><span>The daemon can open and close real positions. Only this dashboard's approve button is disabled; Telegram approval and all automatic exits are live.</span></div><button className="text-button" onClick={openSetup}>View setup <Icon name="arrow" size={15}/></button></div>
    : <div className="availability"><Icon name="lock"/><div><strong>Paper mode</strong><span>Entries are blocked by the edge gate: no strategy has supplied a measured expected_edge_pct. This provider cannot execute orders either.</span></div><button className="text-button" onClick={openSetup}>View setup <Icon name="arrow" size={15}/></button></div>}
    {state && hb == null && <div className="notice bad" role="alert"><strong>The trading daemon is not reporting.</strong> Automatic exits are not being managed. Open positions are unprotected until it is back.</div>}
    {stillOpen && <div className="notice bad" role="alert"><strong>A recorded exit failed and the position is still open.</strong> The system believes it closed a position it still holds. Check Failures below and reconcile before acting on any number here.</div>}
    {status === "retrying" && <div className="notice warning" role="status">The local feed is disconnected. Displayed data may be stale; reconnecting automatically.</div>}
    {state?.chain.ok === false && <div className="notice bad" role="alert"><strong>Audit chain failed verification.</strong> {state.chain.reason}{state.chain.broken_at != null && ` (entry ${state.chain.broken_at})`} — stop trading and investigate before trusting any number here.</div>}
    {state && !state.costs_measured && <div className="notice info">Cost inputs are assumptions, not measurements. A $20 trade in a $300K pool is modelled at about {state.friction_example}% round-trip friction.</div>}
    {state?.edge_gate_on && <div className="notice info">The measured-edge gate is on. Future entries need validated edge above estimated friction; a score alone cannot bypass that check.</div>}
  </>;
}
