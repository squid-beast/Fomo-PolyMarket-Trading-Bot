import { useLiveState } from "./useLiveState";
import { Header } from "./components/Header";
import { Banners } from "./components/Banners";
import { Tiles } from "./components/Tiles";
import { Proposals } from "./components/Proposals";
import { EquityChart } from "./components/EquityChart";
import { Positions, Failures, Trades, FunnelView } from "./components/Tables";

function Section({ title, cap, children }:
  { title: string; cap: string; children: React.ReactNode }) {
  return (
    <section>
      <h2>{title}</h2>
      <p className="cap">{cap}</p>
      {children}
    </section>
  );
}

export default function App() {
  const { state, status, lastEventAt, decide } = useLiveState();

  if (!state) {
    return (
      <>
        <Header state={null} status={status} />
        <div className="wrap">
          <div className="empty" style={{ marginTop: 40 }}>
            {status === "retrying"
              ? "Cannot reach the server. Is the daemon running?"
              : "Connecting to the live stream…"}
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <Header state={state} status={status} />
      <div className="wrap">
        <Banners state={state} status={status} />
        <Tiles e={state.equity} />

        <Section title="Pending approvals"
          cap="Approving records a decision. The daemon re-checks risk before anything is signed.">
          <Proposals items={state.proposals} onDecide={decide} />
        </Section>

        <Section title="Equity" cap="Account value after every cost. Hover for detail.">
          <EquityChart points={state.equity_curve} />
        </Section>

        <Section title="Open positions" cap="Exits are automatic — these are managed without you.">
          <Positions rows={state.positions} />
        </Section>

        <Section title="Failures"
          cap="A failed exit means the position is still open. These matter most.">
          <Failures rows={state.failures} />
        </Section>

        <Section title="Closed trades"
          cap="Quoted move vs what was actually realised after friction.">
          <Trades rows={state.trades} />
        </Section>

        <Section title="Latest scan"
          cap="How many candidates survive each stage. Most scans should end in zero.">
          <FunnelView f={state.funnel} rejections={state.rejections} />
        </Section>

        <p className="foot">
          pushed {lastEventAt ? new Date(lastEventAt).toLocaleTimeString() : "—"} · live stream, not polling
        </p>
      </div>
    </>
  );
}
