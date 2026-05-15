// Relative-time formatter — shared between dashboard cards and TaskToolCalls.
// Returns "Xs ago" / "Xm ago" / "Xh ago" / "Xd ago" with an absolute YYYY-MM-DD
// fallback for anything older than 14 days. `null` → "no activity yet" so the
// caller doesn't have to branch.
//
// Clock-skew clamp: server timestamps may be slightly in the future relative
// to the browser clock (NTP drift). Clamping `diffMs` to >= 0 turns those into
// "just now" rather than a confusing "-Nm ago". (Kanban #873.)
export function formatRelative(iso: string | null): string {
  if (iso === null) return "no activity yet";
  const then = Date.parse(iso);
  // Malformed ISO → fall back to canonical em-dash rather than echoing the
  // bad string to the UI. (no-value marker, same as the null branch above.)
  if (!Number.isFinite(then)) return "—";
  const diffMs = Math.max(0, Date.now() - then);
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 5) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 14) return `${diffDay}d ago`;
  // Older than two weeks — just show the date part.
  return iso.slice(0, 10);
}
