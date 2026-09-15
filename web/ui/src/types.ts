export type Mode = "live" | "paper";

export interface Chain { ok: boolean | null; entries: number; reason?: string; broken_at?: number }

export interface Equity {
  equity: number; cash: number; starting: number; return_pct: number;
  open: number; trades: number; wins: number; win_rate: number | null;
  net: number; gross: number; costs: number; cost_drag: number | null;
}

export interface Position {
  symbol: string; size_usd: number; entry_price_quoted: number;
  opened_at: string; token_address?: string; qty?: number;
}

export interface Proposal {
  pid: string; symbol: string; token_address: string; size_usd: number;
  price: number; composite: number; scores: Record<string, number>;
  rationale: string[]; liquidity: number; round_trip_pct: number;
  age_sec: number; expires_in: number; status: string;
}

export interface Trade {
  symbol: string; size_usd: number; entry_quoted: number; exit_quoted: number;
  net_pnl_usd: number; net_pnl_pct: number; total_costs_usd: number;
  exit_reason: string; hold_hours: number; mode?: string;
}

export interface Failure {
  ts: string; mode: string; kind: string; symbol: string | null; detail: string;
}

export interface Funnel {
  universe: number; passed_safety: number; scored: number; entries: number;
}

export interface State {
  generated: string;
  mode: Mode;
  heartbeat_sec: number | null;
  chain: Chain;
  costs_measured: boolean;
  edge_gate_on: boolean;
  equity: Equity;
  positions: Position[];
  proposals: Proposal[];
  trades: Trade[];
  equity_curve: [string, number][];
  funnel: Funnel | null;
  failures: Failure[];
  rejections: { reason: string; c: number }[];
  friction_example: number;
}

export type ConnStatus = "connecting" | "live" | "retrying";
