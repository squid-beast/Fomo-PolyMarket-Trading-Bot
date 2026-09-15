import type { State } from "../types";

export function Banners({ state, status }: { state: State; status: string }) {
  const b: React.ReactNode[] = [];

  if (state.mode === "live")
    b.push(<div key="live" className="banner live">
      <b>LIVE MODE — real money.</b> Every approval below signs a real transaction.
    </div>);

  if (status === "retrying")
    b.push(<div key="conn" className="banner bad">
      <b>Stream disconnected.</b> These numbers are frozen at the last update and
      may no longer be true. Reconnecting automatically.
    </div>);

  if (state.chain.ok === false)
    b.push(<div key="chain" className="banner bad">
      <b>Audit chain failed verification.</b> {state.chain.reason}
      {state.chain.broken_at != null && ` (entry ${state.chain.broken_at})`} Stop
      trading and investigate before trusting any number here.
    </div>);

  if (!state.costs_measured)
    b.push(<div key="costs" className="banner info">
      Cost parameters are <b>assumptions, not measurements</b>. Friction on a $20
      trade in a $300K pool is modelled at <b>{state.friction_example}%</b> round trip.
    </div>);

  if (state.edge_gate_on)
    b.push(<div key="edge" className="banner info">
      <b>The edge gate is on, so entries are being refused.</b> That is correct —
      no signal has a measured edge yet. It is not a malfunction.
    </div>);

  return <>{b}</>;
}
