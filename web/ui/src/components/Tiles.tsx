import type { Equity } from "../types";
import { money, pct, signClass } from "../format";

export function Tiles({ e }: { e: Equity }) {
  const tiles: [string, string, string, string][] = [
    ["Equity", money(e.equity), `from ${money(e.starting)}`, signClass(e.equity - e.starting)],
    ["Return", pct(e.return_pct), "net of all costs", signClass(e.return_pct)],
    ["Cash", money(e.cash), e.cash <= 0 ? "no room to act" : "available", e.cash <= 0 ? "neg" : ""],
    ["Open", String(e.open), "positions", ""],
    ["Closed", String(e.trades), e.trades ? `${e.wins}W / ${e.trades - e.wins}L` : "none yet", ""],
    ["Costs paid", e.trades ? money(e.costs) : "—",
      e.cost_drag != null ? `${e.cost_drag}% of gross` : "no trades", e.costs ? "neg" : ""],
  ];
  return (
    <div className="tiles">
      {tiles.map(([k, v, n, c]) => (
        <div className="tile" key={k}>
          <div className="k">{k}</div>
          <div className={`v ${c}`}>{v}</div>
          <div className="n">{n}</div>
        </div>
      ))}
    </div>
  );
}
