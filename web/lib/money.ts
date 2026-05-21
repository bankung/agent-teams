// Money formatting — Kanban #1329 (M6 FE).
//
// All P&L amounts come off the BE as Decimal-as-strings (e.g. "500.0000")
// to avoid float-rounding. Render-time conversion uses `Intl.NumberFormat`
// with the row's currency code (uppercase ISO 4217). Unknown / non-standard
// codes fall back to a plain "<amount> <CODE>" form so the UI never throws.
//
// This helper is intentionally tiny — only formatting + parsing. Bucketing /
// rollup / first-currency-observed logic lives on the BE.

export function parseMoney(amount: string | number): number {
  if (typeof amount === "number") return Number.isFinite(amount) ? amount : 0;
  const n = Number.parseFloat(amount);
  return Number.isFinite(n) ? n : 0;
}

export function formatMoney(amount: string | number, currency: string): string {
  const n = parseMoney(amount);
  if (!Number.isFinite(n)) return "—";
  const code = (currency ?? "").trim().toUpperCase() || "USD";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: code,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(n);
  } catch {
    // Unknown currency code → plain decimal + uppercase code.
    return `${n.toFixed(2)} ${code}`;
  }
}

// formatPercent — 1 decimal, with explicit sign for positive values. Used on
// the P&L card to show margin % (net / revenue). NaN / non-finite → em-dash.
export function formatSignedPercent(n: number): string {
  if (!Number.isFinite(n)) return "—";
  const rounded = n.toFixed(1);
  if (n > 0) return `+${rounded}%`;
  return `${rounded}%`;
}
