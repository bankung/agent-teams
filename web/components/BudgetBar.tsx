// BudgetBar — Kanban #951 AC #5. Compact horizontal spend-vs-cap bar shown
// on each project card on the dashboard.
//
// Rendering rules (per spawn brief):
//   - HIDDEN when all 3 cap fields (daily/monthly/total) are null. The caller
//     is responsible for short-circuiting render in that case; the component
//     itself just returns null if `cap <= 0` to be defensive.
//   - For V1, spend = `cost_usage.total_cost_usd` (PROJECT LIFETIME from the
//     stats endpoint — per-period spend is not yet returned by BE). Cap is
//     picked by caller using the precedence: total > monthly > daily (display
//     the most-constraining cap that's set; lifetime spend vs daily/monthly
//     cap is V1's best approximation, flagged in tooltip).
//   - Color: green <60%, amber 60–100%, red >100%.
//   - Layout: bar on the left flex-grows; "$X.XX / $Y.YY (Z%)" label on the
//     right, tabular-nums for jitter-free reflow.
//
// All consumers (dashboard cards + a potential project-detail surface) get
// the same look so the design intent stays consistent.

type Period = "daily" | "monthly" | "total";

export function BudgetBar({
  spendUsd,
  capUsd,
  period,
}: {
  spendUsd: number;
  capUsd: number;
  period: Period;
}) {
  // Defensive: a non-positive cap means "no usable cap" — render nothing so a
  // bad inbound value (e.g. backend mis-serializes "0") doesn't divide-by-zero
  // or render a meaningless 100% bar.
  if (!Number.isFinite(capUsd) || capUsd <= 0) return null;
  const safeSpend = Number.isFinite(spendUsd) && spendUsd > 0 ? spendUsd : 0;
  const rawPct = (safeSpend / capUsd) * 100;
  // Clamp the fill width to 100% so over-budget bars don't overflow visually;
  // the numeric label still shows the true percent (e.g. "143%").
  const fillPct = Math.min(100, Math.max(0, rawPct));
  const displayPct = Math.round(rawPct);

  // Color buckets per brief: green <60%, amber 60–100%, red >100%. The TRUE
  // pct (rawPct) drives the bucket, not the clamped fillPct.
  const overBudget = rawPct > 100;
  const warning = !overBudget && rawPct >= 60;
  const fillClass = overBudget
    ? "bg-red-500 dark:bg-red-500"
    : warning
      ? "bg-amber-500 dark:bg-amber-400"
      : "bg-emerald-500 dark:bg-emerald-400";
  const textClass = overBudget
    ? "text-red-700 dark:text-red-300"
    : warning
      ? "text-amber-700 dark:text-amber-300"
      : "text-zinc-600 dark:text-zinc-400";

  // V1 limitation: when displaying a daily/monthly cap, the spend is still
  // project-lifetime. Surface that caveat in the tooltip so users don't read
  // the bar as "you've used 80% of today's budget".
  const tooltip =
    period === "total"
      ? `$${safeSpend.toFixed(4)} spent of $${capUsd.toFixed(4)} lifetime cap (${displayPct}%)`
      : `$${safeSpend.toFixed(4)} lifetime spend vs $${capUsd.toFixed(4)} ${period} cap (${displayPct}%) — per-period spend not yet tracked`;

  return (
    <div
      data-budget-bar
      data-budget-period={period}
      data-budget-over={overBudget ? "true" : "false"}
      data-budget-warning={warning ? "true" : "false"}
      className="flex items-center gap-1.5 text-[11px] tabular-nums"
      title={tooltip}
      role="img"
      aria-label={`Budget: $${safeSpend.toFixed(2)} of $${capUsd.toFixed(2)} ${period} (${displayPct}%)`}
    >
      <div
        className="h-1.5 flex-1 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800"
        aria-hidden
      >
        <div
          className={`h-full ${fillClass} transition-[width]`}
          style={{ width: `${fillPct}%` }}
        />
      </div>
      <span className={`shrink-0 ${textClass}`}>
        ${safeSpend.toFixed(2)} / ${capUsd.toFixed(2)}{" "}
        <span className="text-zinc-400 dark:text-zinc-500">
          ({displayPct}% {period})
        </span>
      </span>
    </div>
  );
}

// pickBudgetDisplay — Helper used by callers to select which cap to show
// given the 3 nullable cap strings off ProjectRead. Returns null when all
// three are null/blank/invalid → caller renders nothing.
//
// Precedence (per spawn brief V1 spec): total > monthly > daily. The rationale
// is that the only available spend signal in V1 is lifetime; comparing it to
// a lifetime cap is the only fully-coherent comparison, with monthly/daily as
// best-effort fallbacks until the BE adds per-period spend.
export function pickBudgetDisplay(p: {
  budget_total_usd: string | null;
  budget_monthly_usd: string | null;
  budget_daily_usd: string | null;
}): { period: Period; capUsd: number } | null {
  const tryParse = (s: string | null): number | null => {
    if (s === null) return null;
    const n = Number.parseFloat(s);
    return Number.isFinite(n) && n > 0 ? n : null;
  };
  const total = tryParse(p.budget_total_usd);
  if (total !== null) return { period: "total", capUsd: total };
  const monthly = tryParse(p.budget_monthly_usd);
  if (monthly !== null) return { period: "monthly", capUsd: monthly };
  const daily = tryParse(p.budget_daily_usd);
  if (daily !== null) return { period: "daily", capUsd: daily };
  return null;
}
