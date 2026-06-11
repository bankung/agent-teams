"use client";

import { useState } from "react";

import type {
  ProjectRead,
  ResolveFlagAdjustments,
  ResolveFlagResponse,
} from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";

// Kanban #1212 GOV4 — inline form for the "Adjust + Continue" action on an
// GOV3 audit flag. Opens directly below the ProjectFlagCard's action row
// (not a modal — keeps the operator in the flag's context).
//
// 3 sections (locked spec brief):
//   1. Budget bump: 3 numeric inputs (daily / monthly / total). Validates
//      hierarchy daily <= monthly <= total when any pair is set. Empty
//      string = "leave unchanged" (only fields the operator edits are
//      added to the adjustments payload).
//   2. Threshold relax: schema-aware form for `health_thresholds` JSONB.
//      Known keys mirror project-auditor.md defaults. Each numeric input
//      pre-fills from project.health_thresholds (or auditor default when
//      missing). Empty = "leave unchanged".
//   3. Free-text annotation: optional textarea. Sent as
//      adjustments.description_annotation (max 1000 chars). Empty = omitted
//      from the payload (BE no-op).
//
// Submit: assembles a ResolveFlagAdjustments object with only the keys the
// operator touched + calls onSubmit (caller owns the resolveFlag call +
// refresh).

// Auditor defaults — keep in lockstep with .claude/agents/project-auditor.md
// (the FE only renders these as placeholders; the BE auditor reads the same
// constants from its own copy).
const HEALTH_THRESHOLD_DEFAULTS = {
  budget_burn_threshold_pct: 100,
  budget_window_hours: 24,
  failure_rate_threshold_pct: 20,
  failure_rate_window_days: 7,
  drift_threshold: 0.5,
  min_sample_size: 10,
} as const;

type ThresholdKey = keyof typeof HEALTH_THRESHOLD_DEFAULTS;

const THRESHOLD_HINTS: Record<ThresholdKey, string> = {
  budget_burn_threshold_pct:
    "vs_cap >= this % flips a budget breach (default 100)",
  budget_window_hours: "rolling window for daily burn metric (default 24h)",
  failure_rate_threshold_pct:
    "task failure rate >= this % flips a breach (default 20)",
  failure_rate_window_days: "window for failure-rate metric (default 7d)",
  drift_threshold:
    "drift score >= this flips a breach (default 0.5, range 0-1)",
  min_sample_size:
    "skip failure-rate breach when sample is below this (default 10)",
};

type Props = {
  project: ProjectRead;
  // Caller owns the actual `resolveFlag` call so we can centralize
  // error handling + the post-resolve refresh in the page. We only
  // assemble the adjustments payload here + delegate.
  onSubmit: (
    adjustments: ResolveFlagAdjustments,
  ) => Promise<ResolveFlagResponse | void>;
  onCancel: () => void;
};

function parseOptionalNumber(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const n = Number.parseFloat(trimmed);
  if (!Number.isFinite(n) || n < 0) return Number.NaN; // sentinel for invalid
  return n;
}

export function AdjustFlagForm({ project, onSubmit, onCancel }: Props) {
  // Budget inputs start EMPTY — "empty = leave unchanged" contract. Existing
  // values are shown as placeholder text only so the operator can see the
  // current value without pre-filling a submission.
  const [daily, setDaily] = useState("");
  const [monthly, setMonthly] = useState("");
  const [total, setTotal] = useState("");

  // Threshold inputs — pre-fill from project.health_thresholds when present;
  // empty placeholder shows the auditor default.
  const existing = (project.health_thresholds ?? {}) as Record<
    string,
    unknown
  >;
  const initialThresholds: Record<ThresholdKey, string> = {
    budget_burn_threshold_pct:
      existing.budget_burn_threshold_pct != null
        ? String(existing.budget_burn_threshold_pct)
        : "",
    budget_window_hours:
      existing.budget_window_hours != null
        ? String(existing.budget_window_hours)
        : "",
    failure_rate_threshold_pct:
      existing.failure_rate_threshold_pct != null
        ? String(existing.failure_rate_threshold_pct)
        : "",
    failure_rate_window_days:
      existing.failure_rate_window_days != null
        ? String(existing.failure_rate_window_days)
        : "",
    drift_threshold:
      existing.drift_threshold != null
        ? String(existing.drift_threshold)
        : "",
    min_sample_size:
      existing.min_sample_size != null
        ? String(existing.min_sample_size)
        : "",
  };
  const [thresholds, setThresholds] = useState(initialThresholds);
  const [annotation, setAnnotation] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const setThreshold = (key: ThresholdKey, value: string) => {
    setThresholds((prev) => ({ ...prev, [key]: value }));
    if (error !== null) setError(null);
  };

  function buildAdjustments(): ResolveFlagAdjustments | string {
    // Returns the payload OR an error string. The form layer wraps this so
    // a hierarchy violation surfaces inline before any network call.
    const adj: ResolveFlagAdjustments = {};

    // Budget triad. Empty = leave unchanged; '' is the sentinel for
    // "user didn't edit this field". Send the raw string (BE accepts the
    // numeric string form for Numeric(10,4) columns).
    const dailyN = parseOptionalNumber(daily);
    const monthlyN = parseOptionalNumber(monthly);
    const totalN = parseOptionalNumber(total);
    if (Number.isNaN(dailyN) || Number.isNaN(monthlyN) || Number.isNaN(totalN)) {
      return "Budget values must be non-negative numbers";
    }
    // Hierarchy: daily <= monthly <= total, when both sides of a comparison
    // are set. Use the project's existing value as the fallback for the
    // "unset" side so the operator can't accidentally invert by leaving
    // monthly blank while bumping daily.
    const effDaily = dailyN ?? Number.parseFloat(project.budget_daily_usd ?? "NaN");
    const effMonthly =
      monthlyN ?? Number.parseFloat(project.budget_monthly_usd ?? "NaN");
    const effTotal =
      totalN ?? Number.parseFloat(project.budget_total_usd ?? "NaN");
    if (Number.isFinite(effDaily) && Number.isFinite(effMonthly) && effDaily > effMonthly) {
      return `Hierarchy violation: daily ($${effDaily}) > monthly ($${effMonthly})`;
    }
    if (Number.isFinite(effMonthly) && Number.isFinite(effTotal) && effMonthly > effTotal) {
      return `Hierarchy violation: monthly ($${effMonthly}) > total ($${effTotal})`;
    }
    if (dailyN !== null) adj.budget_daily_usd = String(dailyN);
    if (monthlyN !== null) adj.budget_monthly_usd = String(monthlyN);
    if (totalN !== null) adj.budget_total_usd = String(totalN);

    // Health thresholds — only include keys the operator edited (i.e.
    // string value is non-empty). Merge with existing on the BE? No —
    // ADJUST_CONTINUE_ALLOWED_KEYS replaces the whole `health_thresholds`
    // JSONB. Defensive: build a full object that preserves existing keys
    // not touched by this form, so the operator's edit doesn't accidentally
    // drop an unrelated key the BE adds in the future.
    const editedThresholds: Record<string, number | null> = {};
    let touchedAnyThreshold = false;
    for (const key of Object.keys(thresholds) as ThresholdKey[]) {
      const raw = thresholds[key].trim();
      if (raw === "") continue;
      const n = Number.parseFloat(raw);
      if (!Number.isFinite(n) || n < 0) {
        return `Invalid value for ${key}: must be a non-negative number`;
      }
      editedThresholds[key] = n;
      touchedAnyThreshold = true;
    }
    if (touchedAnyThreshold) {
      // Replace-semantics on the BE — merge ALL existing keys we didn't edit
      // so the operator's adjust doesn't accidentally clear unrelated keys.
      // Let the BE validate value types; we must not silently drop non-numeric
      // values (string / bool) that future BE code might store here.
      const merged: Record<string, unknown> = { ...existing };
      for (const [k, v] of Object.entries(editedThresholds)) merged[k] = v;
      // Cast: merged may contain non-numeric values from existing keys that
      // the BE added. We preserve them as-is; the BE validates on write.
      adj.health_thresholds = merged as Record<string, number | null>;
    }

    // Annotation — sent as description_annotation in the adjustments dict.
    // Max 1000 chars enforced client-side (textarea maxLength) and server-side.
    const trimmedAnnotation = annotation.trim();
    if (trimmedAnnotation.length > 0) {
      adj.description_annotation = trimmedAnnotation;
    }

    // Must touch at least ONE allowlisted key — BE 422s on empty filtered
    // adjustments. Same gate here so the operator sees the error before
    // the round-trip.
    if (Object.keys(adj).length === 0) {
      return "No adjustments to submit. Edit at least one field.";
    }
    return adj;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    const built = buildAdjustments();
    if (typeof built === "string") {
      setError(built);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit(built);
    } catch (err: unknown) {
      setError(extractErrorMessage(err, "adjust+continue failed"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="mt-2 flex flex-col gap-3 rounded border border-yellow-300 bg-yellow-50/50 p-3 dark:border-yellow-700 dark:bg-yellow-900/10"
      data-adjust-flag-form
    >
      {/* Budget bump section */}
      <fieldset className="flex flex-col gap-2">
        <legend className="text-xs font-semibold uppercase tracking-wide text-zinc-700 dark:text-zinc-300">
          Budget bump (USD)
        </legend>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <label className="text-[11px] text-zinc-600 dark:text-zinc-400">
            Daily
            <input
              type="number"
              min="0"
              step="0.01"
              inputMode="decimal"
              value={daily}
              onChange={(e) => {
                setDaily(e.target.value);
                if (error !== null) setError(null);
              }}
              placeholder={
                project.budget_daily_usd != null
                  ? `current: $${project.budget_daily_usd}`
                  : "no limit set"
              }
              disabled={submitting}
              className="mt-0.5 block w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
              data-adjust-budget-daily
            />
          </label>
          <label className="text-[11px] text-zinc-600 dark:text-zinc-400">
            Monthly
            <input
              type="number"
              min="0"
              step="0.01"
              inputMode="decimal"
              value={monthly}
              onChange={(e) => {
                setMonthly(e.target.value);
                if (error !== null) setError(null);
              }}
              placeholder={
                project.budget_monthly_usd != null
                  ? `current: $${project.budget_monthly_usd}`
                  : "no limit set"
              }
              disabled={submitting}
              className="mt-0.5 block w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
              data-adjust-budget-monthly
            />
          </label>
          <label className="text-[11px] text-zinc-600 dark:text-zinc-400">
            Total
            <input
              type="number"
              min="0"
              step="0.01"
              inputMode="decimal"
              value={total}
              onChange={(e) => {
                setTotal(e.target.value);
                if (error !== null) setError(null);
              }}
              placeholder={
                project.budget_total_usd != null
                  ? `current: $${project.budget_total_usd}`
                  : "no limit set"
              }
              disabled={submitting}
              className="mt-0.5 block w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
              data-adjust-budget-total
            />
          </label>
        </div>
        <p className="text-[10px] text-zinc-500 dark:text-zinc-500">
          Empty = leave unchanged. Hierarchy: daily ≤ monthly ≤ total enforced.
        </p>
      </fieldset>

      {/* Threshold relax section */}
      <fieldset className="flex flex-col gap-2 border-t border-yellow-300/60 pt-2 dark:border-yellow-700/60">
        <legend className="text-xs font-semibold uppercase tracking-wide text-zinc-700 dark:text-zinc-300">
          Threshold relax
        </legend>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {(Object.keys(HEALTH_THRESHOLD_DEFAULTS) as ThresholdKey[]).map(
            (key) => (
              <label
                key={key}
                className="text-[11px] text-zinc-600 dark:text-zinc-400"
                title={THRESHOLD_HINTS[key]}
              >
                <span className="font-mono">{key}</span>
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  inputMode="decimal"
                  value={thresholds[key]}
                  onChange={(e) => setThreshold(key, e.target.value)}
                  placeholder={String(HEALTH_THRESHOLD_DEFAULTS[key])}
                  disabled={submitting}
                  className="mt-0.5 block w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                  data-adjust-threshold={key}
                />
                <span className="block text-[10px] text-zinc-500 dark:text-zinc-500">
                  {THRESHOLD_HINTS[key]}
                </span>
              </label>
            ),
          )}
        </div>
      </fieldset>

      {/* Free-text annotation section */}
      <fieldset className="flex flex-col gap-2 border-t border-yellow-300/60 pt-2 dark:border-yellow-700/60">
        <legend className="text-xs font-semibold uppercase tracking-wide text-zinc-700 dark:text-zinc-300">
          Free-text annotation (optional)
        </legend>
        <textarea
          value={annotation}
          onChange={(e) => {
            setAnnotation(e.target.value);
            if (error !== null) setError(null);
          }}
          rows={2}
          maxLength={1000}
          placeholder="Optional note appended to the flag resolution record"
          disabled={submitting}
          className="block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
          data-adjust-annotation
        />
        <p className="text-[10px] text-zinc-500 dark:text-zinc-500">
          {annotation.length}/1000 chars. Sent as{" "}
          <span className="font-mono">description_annotation</span> in the
          adjustments payload. Leave blank to omit.
        </p>
      </fieldset>

      {error !== null && (
        <p
          role="alert"
          className="text-xs text-red-700 dark:text-red-300"
          data-adjust-flag-error
        >
          {error}
        </p>
      )}

      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={submitting}
          className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
          data-adjust-flag-cancel
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={submitting}
          className="rounded border border-yellow-600 bg-yellow-500 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-yellow-600 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-yellow-500 dark:bg-yellow-600 dark:hover:bg-yellow-700"
          data-adjust-flag-submit
        >
          {submitting ? "Applying…" : "Apply + Continue"}
        </button>
      </div>
    </form>
  );
}
