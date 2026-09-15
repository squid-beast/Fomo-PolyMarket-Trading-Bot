import type { ConnStatus, State, View } from "../types";
export const views: { id: View; label: string; icon: string }[] = [
  { id: "overview", label: "Overview", icon: "overview" },
  { id: "market", label: "Market analysis", icon: "chart" },
  { id: "copy", label: "Copy trading", icon: "copy" },
  { id: "history", label: "Trade history", icon: "history" },
  { id: "rules", label: "Rules & setup", icon: "sliders" },
];
export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const paths: Record<string, React.ReactNode> = {
    overview: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
    chart: <><path d="M3 3v18h18M6 15l4-5 4 3 6-9"/></>,
    copy: <><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M15 8V3H3v13h5"/></>,
    history: <><path d="M3 10a9 9 0 1 1 2 8M3 4v6h6M12 7v5l3 2"/></>,
    sliders: <><path d="M4 7h6m4 0h6M4 17h10m4 0h2"/><circle cx="12" cy="7" r="2"/><circle cx="16" cy="17" r="2"/></>,
    arrow: <path d="M5 12h14m-5-5 5 5-5 5"/>,
    external: <path d="M14 3h7v7m0-7L10 14M10 3H3v18h18v-7"/>,
    lock: <><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4M12 14v3"/></>,
    search: <><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] ?? paths.overview}</svg>;
}
export function Sidebar({ view, navigate }: { view: View; navigate: (v: View) => void }) {
  return <aside className="sidebar"><a className="brand" href="#overview" onClick={() => navigate("overview")}>
    <span className="brand-mark" aria-hidden="true">c<span>t</span></span><span>copytrader<small>FOMO workspace</small></span></a>
    <div className="nav-label">Workspace</div><nav aria-label="Workspace navigation">{views.map(v =>
      <button key={v.id} className={`nav-item ${view === v.id ? "active" : ""}`} aria-current={view === v.id ? "page" : undefined} onClick={() => navigate(v.id)}><Icon name={v.icon}/><span>{v.label}</span></button>)}</nav>
    <div className="sidebar-bottom"><div className="local-label"><span className="dot muted-dot"/> Local workspace</div>
      <p>FOMO account trading<br/>requires an integration.</p><a className="sidebar-link" href="https://fomo.family" target="_blank" rel="noreferrer">Open FOMO <Icon name="external" size={13}/></a>
      <span className="version">Independent companion</span></div></aside>;
}
export function Header({ view, status, lastEventAt, state }: { view: View; status: ConnStatus; lastEventAt: number; state: State | null }) {
  // The SSE dot is the WEB process. /api/stream re-pushes every 20s whether or
  // not anything changed, so it stays green with the trader daemon dead. The
  // daemon's own liveness is heartbeat_sec, and it needs its own indicator.
  const hb = state?.heartbeat_sec ?? null;
  const daemon = state == null ? null : hb == null ? "down" : hb > 900 ? "stale" : "ok";
  const mode = state?.configuration.configured_mode;
  return <header className="topbar"><div className="breadcrumb">Workspace <span>/</span> <strong>{views.find(v => v.id === view)?.label}</strong></div>
    {mode && <span className={`badge ${mode === "live" ? "live" : ""}`} title={state?.configuration.configured_mode_source}>{mode === "live" ? "LIVE — real money" : "Paper"}</span>}
    {daemon && <div className={`stream-status ${daemon === "ok" ? "" : "neg"}`} title="The trading daemon, not the dashboard. Exits are managed here."><span className={`dot ${daemon === "ok" ? "ok-dot" : "bad-dot"}`}/>{daemon === "ok" ? `Daemon ${hb}s ago` : daemon === "stale" ? `Daemon stale ${hb}s` : "Daemon not reporting"}</div>}
    <div className="stream-status" title="Connection to the local dashboard only. This does not enable trading."><span className={`dot ${status === "live" ? "ok-dot" : "muted-dot"}`}/>{status === "live" ? "Local feed connected" : status === "retrying" ? "Feed reconnecting" : "Connecting to local feed"}</div>
    <time className="last-update">{lastEventAt ? new Date(lastEventAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—"}</time></header>;
}
