export const money = (v: number | null | undefined): string =>
  v == null || !Number.isFinite(v) ? "—" : (v < 0 ? "−" : "") + "$" +
    Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const pct = (v: number | null | undefined, digits = 1): string =>
  v == null || !Number.isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
export const percent = (v: number | null | undefined): string => v == null ? "—" : `${v}%`;
export const price = (v: number | null | undefined): string =>
  v == null || !Number.isFinite(v) ? "—" : `$${v.toLocaleString(undefined, { maximumSignificantDigits: 6 })}`;
export const signClass = (v: number | null | undefined): string =>
  v == null || v === 0 ? "" : v > 0 ? "pos" : "neg";
export const shortTime = (iso: string | null | undefined): string => {
  if (!iso) return "Unknown";
  const date = new Date(iso);
  return Number.isNaN(date.valueOf()) ? "Unknown" : date.toLocaleString(undefined,
    { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};
export const fullTime = (iso: string | null | undefined): string =>
  iso && !Number.isNaN(new Date(iso).valueOf()) ? new Date(iso).toISOString().replace("T", " ") : "Unknown";
export const parseDetail = (raw: string): Record<string, unknown> => {
  try { return JSON.parse(raw || "{}"); } catch { return {}; }
};
const reasons: Record<string, string> = {
  take_profit: "Profit target reached", stop_loss: "Loss limit reached", trailing_stop: "Trailing stop reached",
  max_hold: "Time limit reached", max_hold_hours: "Time limit reached", liquidity_collapse: "Liquidity dropped",
  scale_out: "Partial profit taken", manual: "Manual close", daily_loss: "Daily loss limit reached",
};
export const exitReason = (value: string | null): string => {
  if (!value) return "Reason not recorded";
  const name = value.split("(")[0]!.trim();
  return reasons[name] ?? name.replace(/_/g, " ").replace(/^\w/, c => c.toUpperCase());
};
export function solscanUrl(signature: string | null | undefined): string | null {
  if (!signature || !/^[1-9A-HJ-NP-Za-km-z]{64,88}$/.test(signature)) return null;
  const alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
  let value = 0n;
  for (const char of signature) value = value * 58n + BigInt(alphabet.indexOf(char));
  let bytes = 0;
  while (value > 0n) { bytes++; value >>= 8n; }
  const leading = signature.match(/^1*/)?.[0].length ?? 0;
  return bytes + leading === 64 ? `https://solscan.io/tx/${signature}` : null;
}
