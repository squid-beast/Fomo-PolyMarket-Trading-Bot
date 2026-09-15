import type { Position, Trade, Failure, Funnel } from "../types";
import { money, pct, signClass, shortTime, parseDetail } from "../format";

const Empty = ({ children }: { children: React.ReactNode }) =>
  <div className="empty">{children}</div>;

export function Positions({ rows }: { rows: Position[] }) {
  if (!rows.length) return <Empty>No open positions.</Empty>;
  return (
    <div className="scroll">
      <table>
        <thead><tr><th>Token</th><th className="r">Size</th>
          <th className="r">Entry</th><th className="r">Opened</th></tr></thead>
        <tbody>
          {rows.map((p, i) => (
            <tr key={p.token_address ?? i}>
              <td>{p.symbol}</td>
              <td className="r">{money(p.size_usd)}</td>
              <td className="r">{Number(p.entry_price_quoted).toPrecision(4)}</td>
              <td className="r">{shortTime(p.opened_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Failures({ rows }: { rows: Failure[] }) {
  if (!rows.length) return <Empty>No failures recorded.</Empty>;
  return (
    <div className="scroll">
      <table>
        <thead><tr><th>When</th><th>Mode</th><th>Event</th>
          <th>Token</th><th>Detail</th></tr></thead>
        <tbody>
          {rows.map((f, i) => {
            const d = parseDetail(f.detail);
            const stillOpen = d.position_still_open === true;
            return (
              <tr key={i}>
                <td>{shortTime(f.ts)}</td>
                <td>{f.mode}</td>
                <td className={stillOpen ? "neg" : ""}>
                  {f.kind}{stillOpen && " — STILL HOLDING"}
                </td>
                <td>{f.symbol ?? "—"}</td>
                <td>{String(d.error ?? d.reason ?? "").slice(0, 70)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function Trades({ rows }: { rows: Trade[] }) {
  if (!rows.length) return <Empty>No closed trades yet.</Empty>;
  return (
    <div className="scroll">
      <table>
        <thead><tr><th>Token</th><th className="r">Size</th><th className="r">Quoted</th>
          <th className="r">Net</th><th className="r">Costs</th><th>Exit</th>
          <th className="r">Held</th></tr></thead>
        <tbody>
          {rows.slice(0, 40).map((t, i) => {
            const qm = t.entry_quoted ? (t.exit_quoted / t.entry_quoted - 1) * 100 : 0;
            return (
              <tr key={i}>
                <td>{t.symbol}</td>
                <td className="r">{money(t.size_usd)}</td>
                <td className={`r ${signClass(qm)}`}>{pct(qm, 1)}</td>
                <td className={`r ${signClass(t.net_pnl_usd)}`}>{pct(t.net_pnl_pct)}</td>
                <td className="r">{money(t.total_costs_usd)}</td>
                <td>{String(t.exit_reason ?? "").split("(")[0]}</td>
                <td className="r">{t.hold_hours.toFixed(1)}h</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const STAGES = ["#9ec5f4", "#6da7ec", "#3987e5", "#256abf"];

export function FunnelView({ f, rejections }:
  { f: Funnel | null; rejections: { reason: string; c: number }[] }) {
  if (!f) return <Empty>No completed scans yet.</Empty>;
  const stages: [string, number][] = [
    ["Universe", f.universe], ["Passed safety", f.passed_safety],
    ["Scored", f.scored], ["Entered", f.entries],
  ];
  const mx = Math.max(...stages.map(([, v]) => v), 1);
  return (
    <>
      {stages.map(([label, v], i) => (
        <div className="fr" key={label}>
          <div className="fl">{label}</div>
          <div className="ft" style={{
            width: `${Math.max((v / mx) * 100, 0.4)}%`, background: STAGES[i],
          }} />
          <div className="fv">{v}</div>
        </div>
      ))}
      {rejections.length > 0 && (
        <div className="scroll narrow" style={{ marginTop: 16 }}>
          <table>
            <thead><tr><th>Rejected for</th><th className="r">Count</th></tr></thead>
            <tbody>
              {rejections.map((r) => (
                <tr key={r.reason}>
                  <td>{r.reason.split("(")[0]!.replace(/_/g, " ")}</td>
                  <td className="r">{r.c.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
