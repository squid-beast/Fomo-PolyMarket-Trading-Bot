export const money = (v: number | null | undefined): string => {
  const n = Number(v ?? 0);
  return (n < 0 ? "-" : "") + "$" +
    Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

export const pct = (v: number | null | undefined, digits = 2): string =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;

/** Sign class for colouring. Returns "" for zero so nothing is falsely green. */
export const signClass = (v: number | null | undefined): string =>
  v == null || v === 0 ? "" : v > 0 ? "pos" : "neg";

export const shortTime = (iso: string): string =>
  String(iso ?? "").slice(0, 16).replace("T", " ");

export const parseDetail = (raw: string): Record<string, unknown> => {
  try { return JSON.parse(raw || "{}"); } catch { return {}; }
};
