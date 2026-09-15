import type { State, ConnStatus } from "../types";

export function Header({ state, status }: { state: State | null; status: ConnStatus }) {
  const live = state?.mode === "live";
  const hb = state?.heartbeat_sec ?? null;
  const daemonStale = hb === null || hb > 900;
  const chain = state?.chain;

  return (
    <header>
      <div className="hrow">
        <h1>copytrader</h1>
        {state && <span className={`badge ${live ? "live" : "paper"}`}>{state.mode}</span>}
        <span className="spacer" />
        <span className="meta" title="stream connection">
          <span className={`dot ${status === "live" ? "ok" : "bad"}`} />
          {status === "live" ? "streaming" : status === "connecting" ? "connecting…" : "reconnecting…"}
        </span>
        <span className="meta" title="daemon heartbeat">
          <span className={`dot ${daemonStale ? "bad" : "ok"}`} />
          {hb === null ? "daemon down" : daemonStale ? `stale ${hb}s` : `daemon ${hb}s`}
        </span>
        {chain && chain.ok !== null && (
          <span className="meta" style={{ color: chain.ok ? undefined : "var(--crit)" }}>
            {chain.ok ? `chain ok · ${chain.entries.toLocaleString()}` : "CHAIN BROKEN"}
          </span>
        )}
      </div>
    </header>
  );
}
