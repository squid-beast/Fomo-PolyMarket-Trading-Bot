export type Mode = "live" | "paper" | "unknown";
export type View = "overview" | "market" | "copy" | "history" | "rules";
export type ConnStatus = "connecting" | "live" | "retrying";
export interface Chain { ok: boolean | null; entries: number; reason?: string; broken_at?: number }
export interface Equity {
  equity: number | null; cash: number | null; starting: number; return_pct: number | null;
  open: number | null; trades: number | null; wins: number | null; win_rate: number | null;
  net: number | null; gross: number | null; costs: number | null; cost_drag: number | null;
}
export interface Position {
  symbol: string; size_usd: number | null; entry_price_quoted: number | null;
  opened_at: string; token_address?: string; qty?: number;
}
export interface Proposal {
  pid: string; symbol: string; token_address: string; size_usd: number;
  price: number; composite: number | null; scores: Record<string, number>;
  rationale: string[]; liquidity: number; round_trip_pct: number | null;
  age_sec: number | null; expires_in: number; status: string;
  source: string; provenance_verified: boolean; approval_available: boolean;
  approval_blocked_reason: string;
}
export interface Trade {
  id: number; symbol: string; token_address: string | null; size_usd: number | null;
  opened_at: string | null; closed_at: string | null; entry_quoted: number | null; exit_quoted: number | null;
  gross_pnl_usd: number | null; net_pnl_usd: number | null; net_pnl_pct: number | null;
  total_costs_usd: number | null; exit_reason: string | null; hold_hours: number | null;
  mode: Mode; recorded_mode: string | null; signature?: string | null;
}
export interface TradeSummary {
  mode: Mode; count: number; pnl_count: number; net: number | null; gross: number | null;
  costs: number | null; wins: number; losses: number; breakeven: number;
  gross_count: number; costs_count: number;
}
export interface TradePage {
  items: Trade[]; total: number; page: number; page_size: number; pages: number;
  summaries: TradeSummary[]; coverage: string; valuation: string; storage_available: boolean;
}
export interface Failure { ts: string; mode: string; kind: string; symbol: string | null; detail: string }
export interface Funnel { ts?: string; universe: number; passed_safety: number; scored: number; entries: number }
export interface Configuration {
  exits: { take_profit_pct: number | null; stop_loss_pct: number | null; trailing_stop_pct: number | null;
    trail_arm_pct: number | null; max_hold_hours: number | null; liquidity_collapse_pct: number | null;
    scale_out_ladder: [number, number][] | null };
  risk: { max_position_pct: number | null; min_position_usd: number | null; max_concurrent_positions: number | null;
    min_cash_reserve_pct: number | null; max_daily_loss_pct: number | null; max_total_exposure_pct: number | null;
    max_entries_per_day: number | null; min_expected_edge_pct: number | null; require_edge_gate: boolean | null };
  run: { scan_interval_sec: number | null; manage_interval_sec: number | null };
  configured_mode: "live" | "paper"; configured_mode_source: string; verified_execution_mode: null;
  chart_based_early_exit: boolean; scope: string; editable: boolean;
}
export interface State {
  generated: string; mode: Mode; heartbeat_sec: number | null; chain: Chain;
  costs_measured: boolean; edge_gate_on: boolean; equity: Equity; positions: Position[];
  proposals: Proposal[]; trades: Trade[]; equity_curve: [string, number][]; funnel: Funnel | null;
  failures: Failure[]; rejections: { reason: string; c: number }[]; friction_example: number;
  configuration: Configuration;
  integrations: { fomo_execution: boolean; fomo_market_feed: boolean; fomo_copy_trading: boolean;
    research_provider: string; research_provider_status: string; credential_status: string;
    scanner: string; executor: string; chain: string };
  account: { source: string; mode: Mode; mode_verified: boolean; equity_curve_scope: string;
    settlement_verified: boolean; history_coverage: string };
}
export interface FomoHolding {
  symbol: string | null; address: string | null; network_id: number | null; chain: string | null;
  amount: number | null; price_usd: number | null; value_usd: number | null; change_24h: number | null;
}
export interface FomoPosition {
  trade_id: string | null; symbol: string | null; chain: string | null; status: string | null;
  amount: number | null; avg_entry_price: number | null; avg_exit_price: number | null;
  realized_pnl_usd: number | null; unrealized_pnl_usd: number | null;
  created_at: string | null; closed_at: string | null;
}
export interface FomoCoverage {
  complete: boolean; closed_trade_cap: string; execution_available: boolean;
  execution_note: string; provider: string; provider_status: string;
}
export interface FomoCredits { spent: number; left: number; budget: number }
export interface FomoAccountPayload {
  handle: string; fetched_at: string | null; holdings: FomoHolding[]; total_value_usd: number | null;
  open_positions: FomoPosition[]; closed_trades: FomoPosition[]; coverage: FomoCoverage;
  credits: FomoCredits; error: string | null;
}
