"use client";

// Kanban #1212 AA4 — compact card per AA3 audit flag, rendered inside the
// /review page's per-project section. Component uses a neutral functional
// name per the project's code-keyword discipline (see operator memory);
// mirrors the project-auditor naming precedent (#1210/#1211).
//
// REFACTOR OBLIGATION (Kanban #1183): if the HITL drawer option-card
// redesign ships later, extract a shared `<QuestionOptionCard>` component
// from this card's 4-button action row + the HITL drawer's equivalent.
// Both surfaces use the same Continue/Adjust/KeepPaused/Terminate vocabulary
// + the same yellow/orange/red severity gradient. Avoid duplicating the
// per-button color/copy decisions twice — pick the abstraction layer at
// shared-component extraction time.

import Link from "next/link";
import { useState } from "react";

import {
  resolveFlag,
  type AuditFlagAction,
  type ProjectRead,
  type ResolveFlagAdjustments,
  type ResolveFlagResponse,
  type TaskRead,
} from "@/lib/api";
import { AdjustFlagForm } from "./AdjustFlagForm";

type Props = {
  flag: TaskRead;
  project: ProjectRead;
  selected: boolean;
  onSelectChange: (next: boolean) => void;
  // Caller (the page's ReviewClient) owns the post-resolve refresh +
  // optimistic removal of the flag from the list. We notify after a
  // successful resolveFlag so the page can re-render without the flag.
  onResolved: (response: ResolveFlagResponse) => void;
  // Terminate goes through a shared extra-friction modal owned by the
  // page (so single + mass modes use the same component). The card just
  // requests the page to open the modal targeting this flag.
  onTerminateRequest: () => void;
};

// Reasons fall back gracefully when the auditor hasn't populated them.
function pickReasons(flag: TaskRead): string[] {
  const fromPayload = flag.question_payload?.reasons;
  if (Array.isArray(fromPayload)) {
    return fromPayload.filter((r): r is string => typeof r === "string");
  }
  // The auditor's evidence is mirrored into question_payload.reasons by the
  // flag pipeline (audit_flag.py:_format_flag_question) when available.
  // Defensive: when missing, render the flag's question text as a single
  // pseudo-reason so the operator sees SOMETHING.
  const q = flag.question_payload?.question;
  return q ? [q] : ["(no reasons captured)"];
}

function pickBurnRate(flag: TaskRead): {
  spend?: number;
  cap?: number;
  pct?: number;
} {
  const metrics = flag.question_payload?.metrics;
  if (!metrics || typeof metrics !== "object") return {};
  const m = metrics as Record<string, unknown>;
  const burn = m.budget_burn_rate ?? m.budget;
  if (burn && typeof burn === "object") {
    const b = burn as Record<string, unknown>;
    const spend = typeof b.spend === "number" ? b.spend : undefined;
    const cap = typeof b.cap === "number" ? b.cap : undefined;
    const pct = typeof b.vs_cap === "number" ? b.vs_cap * 100 : undefined;
    return { spend, cap, pct };
  }
  return {};
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

// Status badge — color reflects the auditor's recommendation captured in
// latest_audit_summary, falling back to "review" amber when unknown.
function StatusBadge({ flag, project }: { flag: TaskRead; project: ProjectRead }) {
  const rec = flag.question_payload?.latest_audit_summary?.recommendation;
  const isPaused = project.is_paused === true || rec === "pause";
  const label = isPaused ? "pause" : "review";
  const cls = isPaused
    ? "text-yellow-800 bg-yellow-100 dark:text-yellow-200 dark:bg-yellow-900/40"
    : "text-amber-800 bg-amber-100 dark:text-amber-200 dark:bg-amber-900/40";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide ${cls}`}
      title={
        isPaused ? "Project is paused pending operator decision" : "Review"
      }
      data-flag-status={label}
    >
      ⚠ {label}
    </span>
  );
}

export function ProjectFlagCard({
  flag,
  project,
  selected,
  onSelectChange,
  onResolved,
  onTerminateRequest,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [adjustOpen, setAdjustOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const payload = flag.question_payload;
  const streak = payload?.breach_streak_days ?? 1;
  const reasons = pickReasons(flag).slice(0, 2);
  const allReasons = pickReasons(flag);
  const burn = pickBurnRate(flag);
  const auditHistory = payload?.audit_history ?? [];

  async function fireAction(
    action: AuditFlagAction,
    adjustments?: ResolveFlagAdjustments,
  ) {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const body =
        action === "adjust_continue" && adjustments
          ? { action, adjustments }
          : { action };
      const result = await resolveFlag(flag.id, project.id, body);
      onResolved(result);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : `${action} failed`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <article
      className="flex flex-col gap-2 rounded-md border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900"
      data-flag-card
      data-flag-id={flag.id}
      data-project-id={project.id}
      data-project-name={project.name}
    >
      {/* Row 1 — selection + project link + status badge + streak */}
      <header className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={selected}
          onChange={(e) => onSelectChange(e.target.checked)}
          disabled={submitting}
          className="h-4 w-4 shrink-0 rounded border-zinc-300 text-zinc-700 focus:ring-zinc-500 dark:border-zinc-600 dark:bg-zinc-950"
          aria-label={`Select flag for ${project.name}`}
          data-flag-select-checkbox
        />
        <Link
          href={`/p/${project.name}`}
          className="truncate text-sm font-semibold text-zinc-900 hover:underline dark:text-zinc-100"
          data-flag-project-link
        >
          {project.name}
        </Link>
        <StatusBadge flag={flag} project={project} />
        <span
          className="ml-auto inline-flex items-center rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
          title={`Breach streak: ${streak} day(s)`}
          data-flag-streak
        >
          Day {streak} of breach
        </span>
      </header>

      {/* Row 2 — burn rate summary. Static N% number when sparkline data
          isn't available client-side (sparkline rendering deferred). */}
      <div
        className="flex items-baseline gap-2 text-[12px] tabular-nums text-zinc-600 dark:text-zinc-400"
        data-flag-burn-rate
      >
        {burn.spend !== undefined && burn.cap !== undefined ? (
          <>
            <span className="font-medium text-zinc-800 dark:text-zinc-200">
              ${burn.spend.toFixed(2)}
            </span>
            <span className="text-zinc-400">/</span>
            <span>${burn.cap.toFixed(2)} cap</span>
            {burn.pct !== undefined && (
              <span
                className={
                  burn.pct >= 100
                    ? "ml-1 inline-flex items-center rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-semibold text-red-700 dark:bg-red-900/30 dark:text-red-300"
                    : "ml-1 inline-flex items-center rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 dark:bg-amber-900/30 dark:text-amber-300"
                }
              >
                {burn.pct.toFixed(0)}%
              </span>
            )}
          </>
        ) : (
          <span className="text-zinc-400 dark:text-zinc-600">
            burn rate: not reported by auditor
          </span>
        )}
      </div>

      {/* Row 3 — top 2 reasons (bullet list, truncated to 80 chars) */}
      <ul
        className="list-disc space-y-0.5 pl-5 text-[12px] text-zinc-700 dark:text-zinc-300"
        data-flag-reasons
      >
        {reasons.map((r, i) => (
          <li key={i} className="break-words">
            {truncate(r, 80)}
          </li>
        ))}
      </ul>

      {error !== null && (
        <p
          role="alert"
          className="text-xs text-red-700 dark:text-red-300"
          data-flag-error
        >
          {error}
        </p>
      )}

      {/* Row 4 — 4 action buttons inline. Yellow/orange/red gradient maps
          to the action's reversibility (gray = noop, yellow = adjust-then-
          continue, orange = keep-paused, red = terminate). */}
      <div className="flex flex-wrap items-center gap-1.5" data-flag-actions>
        <button
          type="button"
          onClick={() => fireAction("continue")}
          disabled={submitting || adjustOpen}
          className="rounded border border-zinc-300 bg-zinc-100 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide text-zinc-700 hover:bg-zinc-200 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
          data-flag-action="continue"
        >
          Continue
        </button>
        <button
          type="button"
          onClick={() => setAdjustOpen((v) => !v)}
          disabled={submitting}
          className="rounded border border-yellow-500 bg-yellow-100 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide text-yellow-800 hover:bg-yellow-200 disabled:opacity-50 dark:border-yellow-600 dark:bg-yellow-900/30 dark:text-yellow-300 dark:hover:bg-yellow-900/50"
          data-flag-action="adjust_continue"
          aria-expanded={adjustOpen}
        >
          {adjustOpen ? "Close adjust" : "Adjust + Continue"}
        </button>
        <button
          type="button"
          onClick={() => fireAction("keep_paused")}
          disabled={submitting || adjustOpen}
          className="rounded border border-orange-500 bg-orange-100 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide text-orange-800 hover:bg-orange-200 disabled:opacity-50 dark:border-orange-600 dark:bg-orange-900/30 dark:text-orange-300 dark:hover:bg-orange-900/50"
          data-flag-action="keep_paused"
        >
          Keep Paused
        </button>
        <button
          type="button"
          onClick={onTerminateRequest}
          disabled={submitting || adjustOpen}
          className="rounded border border-red-600 bg-red-100 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide text-red-700 hover:bg-red-200 disabled:opacity-50 dark:border-red-500 dark:bg-red-900/30 dark:text-red-300 dark:hover:bg-red-900/50"
          data-flag-action="terminate"
        >
          Terminate
        </button>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="ml-auto rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] text-zinc-600 hover:border-zinc-300 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400"
          aria-expanded={expanded}
          data-flag-expand-toggle
        >
          {expanded ? "▾ Collapse" : "▸ Expand"}
        </button>
      </div>

      {/* Inline adjust form — renders directly below the action row when
          the operator clicks "Adjust + Continue". Submit calls resolveFlag
          via fireAction('adjust_continue', payload). */}
      {adjustOpen && (
        <AdjustFlagForm
          project={project}
          onCancel={() => setAdjustOpen(false)}
          onSubmit={async (adjustments) => {
            await fireAction("adjust_continue", adjustments);
            setAdjustOpen(false);
          }}
        />
      )}

      {/* Expand panel — full reasons list + raw_evidence + audit_history */}
      {expanded && (
        <div
          className="flex flex-col gap-3 border-t border-zinc-200 pt-2 text-[12px] dark:border-zinc-800"
          data-flag-expand-panel
        >
          {allReasons.length > 2 && (
            <div>
              <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                All reasons
              </h4>
              <ul className="list-disc space-y-0.5 pl-5 text-zinc-700 dark:text-zinc-300">
                {allReasons.map((r, i) => (
                  <li key={i} className="break-words">
                    {r}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {payload?.raw_evidence !== undefined && (
            <div>
              <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Raw evidence
              </h4>
              <pre className="max-h-40 overflow-auto rounded border border-zinc-200 bg-zinc-50 p-2 font-mono text-[10px] text-zinc-700 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-300">
                {JSON.stringify(payload.raw_evidence, null, 2)}
              </pre>
            </div>
          )}
          {auditHistory.length > 0 && (
            <div>
              <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Audit history
              </h4>
              <ul className="space-y-0.5 font-mono text-[11px] text-zinc-600 dark:text-zinc-400">
                {auditHistory.map((auditId) => (
                  <li key={auditId} data-flag-audit-history-entry>
                    audit task #{auditId}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </article>
  );
}
