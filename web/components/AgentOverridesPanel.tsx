"use client";

// AgentOverridesPanel — Kanban #1018. Per-project agent enable/disable +
// model-tier override + notes, mounted under the "This project" settings
// category.
//
// Data flow: fetch BOTH the full agent roster (GET /api/agents, platform-
// level) and this project's overrides (GET /api/projects/{id}/agent-
// overrides) via useAsyncData, then merge for display — a roster agent with
// no override row is implicitly enabled with no tier ("Default") and empty
// notes. Every control PATCHes only its own changed field (partial upsert);
// the toggle + tier select are optimistic with revert-on-error, the notes
// input is debounced (400ms, matches the InboxBadge SSE debounce) before it
// PATCHes.
//
// Explicitly OUT of scope (see #1018 brief): lead_overrides UI (#1024),
// hot-reload/restart banner (#1019), tool-scope viz (#1021).
//
// Kanban #1020 (frontend half) adds the cost-preview badge + budget-impact
// confirm on top of #1018's toggle/tier/notes surface:
//   - after roster+overrides land, lazy-fetch GET .../cost-estimate for every
//     listed agent concurrently (Promise.all, per-row failure tolerated —
//     that row just renders without a badge; no retry, no per-row spinner).
//   - AgentCostBadge renders a traffic-light dot + compact projected-cost
//     text next to the toggle.
//   - toggling ON an agent whose cached estimate has traffic_light="red"
//     intercepts the normal optimistic-toggle path and opens
//     AgentCostConfirmModal first; Confirm re-invokes the original toggle,
//     Cancel is a no-op (toggle never flips). OFF and non-red toggles are
//     unchanged from #1018.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getAgentCostEstimate,
  getAgentOverrides,
  getAgents,
  patchAgentOverrides,
  type AgentCostEstimate,
  type AgentModelTier,
  type AgentOverride,
  type AgentSummary,
} from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";
import { useAsyncData } from "@/lib/useAsyncData";
import { ModelTierBadge, DomainChip } from "./AgentBadges";
import { ModalShell } from "./ModalShell";

const NOTES_DEBOUNCE_MS = 400;

// #1020 — traffic-light dot + text color, mirrors the palette convention in
// AgentBadges.TIER_CLASS (violet/blue/emerald) but keyed to red/yellow/green
// severity instead of model tier. Split into dot/text maps (rather than one
// combined class string) so AgentCostBadge can apply each to its own span
// without parsing a class string apart.
const TRAFFIC_LIGHT_DOT_CLASS: Record<AgentCostEstimate["traffic_light"], string> = {
  green: "bg-emerald-500 dark:bg-emerald-400",
  yellow: "bg-amber-500 dark:bg-amber-400",
  red: "bg-red-600 dark:bg-red-400",
};
const TRAFFIC_LIGHT_TEXT_CLASS: Record<AgentCostEstimate["traffic_light"], string> = {
  green: "text-emerald-700 dark:text-emerald-300",
  yellow: "text-amber-700 dark:text-amber-300",
  red: "text-red-700 dark:text-red-300",
};

function formatUsdPerMonth(n: number): string {
  return `$${n.toFixed(2)}/mo`;
}

// Merged row — roster fields + the resolved (override ?? default) controls.
type Row = {
  agent: AgentSummary;
  enabled: boolean;
  modelOverride: AgentModelTier | null;
  notes: string;
};

function mergeRows(
  roster: AgentSummary[],
  overrides: AgentOverride[],
): Row[] {
  const byName = new Map(overrides.map((o) => [o.name, o]));
  return roster.map((agent) => {
    const o = byName.get(agent.name);
    return {
      agent,
      enabled: o?.enabled ?? true,
      modelOverride: o?.model_override ?? null,
      notes: o?.notes ?? "",
    };
  });
}

// Tier select round-trips through a string; "" encodes null (Default).
function encodeTier(v: AgentModelTier | null): string {
  return v ?? "";
}
function decodeTier(s: string): AgentModelTier | null {
  return s === "" ? null : (s as AgentModelTier);
}

type Props = {
  projectId: number;
};

export function AgentOverridesPanel({ projectId }: Props) {
  const {
    data: roster,
    loading: rosterLoading,
    error: rosterError,
  } = useAsyncData(getAgents, [], { errorFallback: "Could not load agents" });
  const {
    data: overridesResp,
    loading: overridesLoading,
    error: overridesError,
  } = useAsyncData(() => getAgentOverrides(projectId), [projectId], {
    errorFallback: "Could not load agent overrides",
  });

  // Local resolved state, seeded from the merge once both fetches land.
  // Per-row optimistic edits mutate this map directly; a failed PATCH reverts
  // the single field that failed rather than refetching everything.
  const [rowState, setRowState] = useState<Map<string, Row> | null>(null);
  const [rowError, setRowError] = useState<Map<string, string>>(new Map());
  const notesTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(
    new Map(),
  );

  // #1020 — per-agent cost estimate cache, keyed by agent name. Populated by
  // a single Promise.all fan-out once the roster is known (see the effect
  // below); a row absent from this map (fetch still in flight, or failed)
  // simply renders without a badge — no loading spinner, no retry.
  const [costEstimates, setCostEstimates] = useState<
    Map<string, AgentCostEstimate>
  >(new Map());

  // #1020 AC5 — red-toggle-ON confirm gate. Non-null while the modal is open;
  // holds the agent name so onConfirmRedToggle can re-invoke the underlying
  // toggle for exactly that row.
  const [pendingRedToggle, setPendingRedToggle] = useState<string | null>(
    null,
  );

  // #1018 N1 — clear any pending debounced notes writes on unmount (a project
  // switch now remounts this component per M1's key={project.id}, so a
  // mid-debounce unmount is a real path, not just a fast-navigate edge case).
  // Without this, a timer firing after unmount would call setRowState /
  // setRowError on a detached instance.
  useEffect(() => {
    const timers = notesTimers.current;
    return () => {
      for (const t of timers.values()) clearTimeout(t);
    };
  }, []);

  // #1020 — lazy concurrent estimate fetch, once the roster is known. Fires
  // once per (roster identity, projectId) — roster only changes identity on
  // the initial load (useAsyncData never replaces array identity on its own
  // after that), so this does not re-fire on every optimistic row edit.
  // Promise.allSettled tolerates individual 404/500s: a failed row is simply
  // never added to the map, so AgentCostBadge renders nothing for it.
  useEffect(() => {
    if (!roster || roster.length === 0) return;
    let cancelled = false;
    void Promise.allSettled(
      roster.map((agent) =>
        getAgentCostEstimate(agent.name, projectId).then((estimate) => [
          agent.name,
          estimate,
        ] as const),
      ),
    ).then((results) => {
      if (cancelled) return;
      const next = new Map<string, AgentCostEstimate>();
      for (const r of results) {
        if (r.status === "fulfilled") next.set(r.value[0], r.value[1]);
      }
      setCostEstimates(next);
    });
    return () => {
      cancelled = true;
    };
  }, [roster, projectId]);

  const merged = useMemo(() => {
    if (!roster || !overridesResp) return null;
    return mergeRows(roster, overridesResp.agents);
  }, [roster, overridesResp]);

  // Seed rowState the first time the merge resolves (or if it changes
  // identity, e.g. projectId switch) — subsequent renders read from rowState
  // so local optimistic edits aren't clobbered by re-renders.
  const rows: Row[] | null = useMemo(() => {
    if (rowState) return Array.from(rowState.values());
    return merged;
  }, [rowState, merged]);

  // Lazy seed: rowState starts null and is only materialized inside a mutator
  // (onToggleEnabled/onChangeTier/onChangeNotes) the first time the operator
  // touches a control — a plain assignment during render would violate React
  // purity, so ensureSeeded() is the single seeding path, called from event
  // handlers only.
  const ensureSeeded = useCallback((): Map<string, Row> => {
    if (rowState) return rowState;
    const seeded = new Map((merged ?? []).map((r) => [r.agent.name, r]));
    return seeded;
  }, [rowState, merged]);

  const patchOne = useCallback(
    async (
      name: string,
      patch: { enabled?: boolean; model_override?: AgentModelTier | null; notes?: string | null },
      revert: (m: Map<string, Row>) => Map<string, Row>,
    ) => {
      // shortcut: revert() closes over the pre-edit field value captured at
      // call time, not the latest rowState — under concurrent edits to the
      // SAME row (e.g. toggle then tier-change before the toggle's PATCH
      // settles), a later revert can clobber an unrelated field's optimistic
      // update. Fine for a single-operator settings UI; upgrade: a per-row
      // request-sequence counter (only apply a revert if no newer patch is
      // in flight) if this ever becomes multi-operator.
      try {
        await patchAgentOverrides(projectId, [{ name, ...patch }]);
        setRowError((prev) => {
          if (!prev.has(name)) return prev;
          const next = new Map(prev);
          next.delete(name);
          return next;
        });
      } catch (err: unknown) {
        setRowState((prev) => (prev ? revert(prev) : prev));
        setRowError((prev) => {
          const next = new Map(prev);
          next.set(name, extractErrorMessage(err, "Save failed"));
          return next;
        });
      }
    },
    [projectId],
  );

  // #1020 — the original #1018 optimistic-flip logic, unconditional. Renamed
  // to applyToggle; onToggleEnabled below wraps it with the red-confirm gate.
  const applyToggle = useCallback(
    (name: string) => {
      const base = ensureSeeded();
      const current = base.get(name);
      if (!current) return;
      const nextEnabled = !current.enabled;
      const optimistic = new Map(base);
      optimistic.set(name, { ...current, enabled: nextEnabled });
      setRowState(optimistic);
      void patchOne(name, { enabled: nextEnabled }, (m) => {
        const reverted = new Map(m);
        const row = reverted.get(name);
        if (row) reverted.set(name, { ...row, enabled: current.enabled });
        return reverted;
      });
    },
    [ensureSeeded, patchOne],
  );

  // #1020 AC5 — intercept ONLY the OFF→ON edge on a red-traffic-light agent.
  // Every other case (turning OFF, or a non-red/no-estimate agent) is
  // unchanged #1018 behavior — apply immediately.
  const onToggleEnabled = useCallback(
    (name: string) => {
      const base = ensureSeeded();
      const current = base.get(name);
      if (!current) return;
      const turningOn = !current.enabled;
      const estimate = costEstimates.get(name);
      if (turningOn && estimate?.traffic_light === "red") {
        setPendingRedToggle(name);
        return;
      }
      applyToggle(name);
    },
    [ensureSeeded, costEstimates, applyToggle],
  );

  const onConfirmRedToggle = useCallback(() => {
    if (pendingRedToggle) applyToggle(pendingRedToggle);
    setPendingRedToggle(null);
  }, [pendingRedToggle, applyToggle]);

  const onCancelRedToggle = useCallback(() => {
    setPendingRedToggle(null);
  }, []);

  const onChangeTier = useCallback(
    (name: string, tier: AgentModelTier | null) => {
      const base = ensureSeeded();
      const current = base.get(name);
      if (!current) return;
      const prevTier = current.modelOverride;
      const optimistic = new Map(base);
      optimistic.set(name, { ...current, modelOverride: tier });
      setRowState(optimistic);
      void patchOne(name, { model_override: tier }, (m) => {
        const reverted = new Map(m);
        const row = reverted.get(name);
        if (row) reverted.set(name, { ...row, modelOverride: prevTier });
        return reverted;
      });
    },
    [ensureSeeded, patchOne],
  );

  const onChangeNotes = useCallback(
    (name: string, value: string) => {
      const base = ensureSeeded();
      const current = base.get(name);
      if (!current) return;
      const optimistic = new Map(base);
      optimistic.set(name, { ...current, notes: value });
      setRowState(optimistic);

      const timers = notesTimers.current;
      const existing = timers.get(name);
      if (existing) clearTimeout(existing);
      timers.set(
        name,
        setTimeout(() => {
          timers.delete(name);
          void patchOne(name, { notes: value.trim() === "" ? null : value }, (m) => m);
        }, NOTES_DEBOUNCE_MS),
      );
    },
    [ensureSeeded, patchOne],
  );

  const loading = rosterLoading || overridesLoading;
  const error = rosterError ?? overridesError;

  return (
    <section
      data-agent-overrides-panel
      aria-labelledby="project-agent-overrides-heading"
      className="flex flex-col gap-4"
    >
      <header className="flex flex-col gap-1">
        <h2
          id="project-agent-overrides-heading"
          className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
        >
          Agents
        </h2>
        <p className="text-[12px] text-zinc-500 dark:text-zinc-400 leading-5">
          Enable or disable each installed agent for this project, and
          optionally override its model tier. Agents with no override here
          are enabled with the tier set on the agent file.
        </p>
      </header>

      {error !== null && (
        <p role="alert" className="text-[12px] text-red-700 dark:text-red-300">
          {error}
        </p>
      )}

      {loading && rows === null ? (
        <p className="text-[12px] text-zinc-400 dark:text-zinc-500">
          Loading agents…
        </p>
      ) : rows === null ? null : (
        <ul
          data-agent-overrides-list
          className="flex flex-col divide-y divide-zinc-100 rounded-md border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-900"
        >
          {rows
            .slice()
            .sort((a, b) => a.agent.name.localeCompare(b.agent.name))
            .map((row) => (
              <AgentOverrideRow
                key={row.agent.name}
                row={row}
                error={rowError.get(row.agent.name) ?? null}
                costEstimate={costEstimates.get(row.agent.name) ?? null}
                onToggleEnabled={() => onToggleEnabled(row.agent.name)}
                onChangeTier={(tier) => onChangeTier(row.agent.name, tier)}
                onChangeNotes={(value) => onChangeNotes(row.agent.name, value)}
              />
            ))}
        </ul>
      )}

      {/* #1020 AC5 — budget-impact confirm, shown only when turning ON a
          red-traffic-light agent. */}
      <AgentCostConfirmModal
        agentName={pendingRedToggle}
        estimate={pendingRedToggle ? (costEstimates.get(pendingRedToggle) ?? null) : null}
        onConfirm={onConfirmRedToggle}
        onCancel={onCancelRedToggle}
      />
    </section>
  );
}

// #1020 — traffic-light dot + compact projected-cost text, rendered next to
// the toggle. `estimate === null` (fetch still in flight, or failed) renders
// nothing — no spinner, no error state, per the brief.
//
// The dash state has two distinct causes, both worth a different tooltip
// (dev-reviewer #1020 fix pass): spawn_count_last_30d === 0 means the agent
// genuinely has no spawn history in this project ("no history"); count > 0
// with projected_monthly_usd === null means spawns exist but none carry cost
// data yet ("N spawns, no cost data") — a different, more actionable state
// for the operator. Both render the same neutral dash text/style; only the
// title differs.
function AgentCostBadge({ estimate }: { estimate: AgentCostEstimate | null }) {
  if (estimate === null) return null;
  const { avg_cost_per_spawn, spawn_count_last_30d, projected_monthly_usd, traffic_light } =
    estimate;
  const noHistory = spawn_count_last_30d === 0;
  const noCostData = !noHistory && projected_monthly_usd === null;
  const avgLabel = avg_cost_per_spawn !== null ? `$${avg_cost_per_spawn.toFixed(4)}` : "n/a";
  const spawnLabel = `${spawn_count_last_30d} spawn${spawn_count_last_30d === 1 ? "" : "s"} last 30d`;
  const title = noCostData
    ? `${spawnLabel}, no cost data`
    : `avg ${avgLabel}/spawn · ${spawnLabel}`;
  return (
    <span
      data-agent-cost-badge={traffic_light}
      title={title}
      className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium tabular-nums"
    >
      <span
        aria-hidden="true"
        className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${TRAFFIC_LIGHT_DOT_CLASS[traffic_light]}`}
      />
      <span className={TRAFFIC_LIGHT_TEXT_CLASS[traffic_light]}>
        {projected_monthly_usd !== null
          ? formatUsdPerMonth(projected_monthly_usd)
          : "no history"}
      </span>
    </span>
  );
}

// #1020 AC5 — budget-impact confirm modal. Mirrors PauseProjectModal's
// externally-controlled-open pattern (no internal trigger button — the
// parent panel owns open/close via pendingRedToggle).
function AgentCostConfirmModal({
  agentName,
  estimate,
  onConfirm,
  onCancel,
}: {
  agentName: string | null;
  estimate: AgentCostEstimate | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const open = agentName !== null;
  return (
    <ModalShell
      open={open}
      onClose={onCancel}
      labelledBy="agent-cost-confirm-title"
      maxWidth="sm"
      backdropProps={{ "data-agent-cost-confirm-modal": agentName ?? undefined }}
    >
      <h2
        id="agent-cost-confirm-title"
        className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
      >
        Enable <span className="font-mono normal-case">{agentName}</span>?
      </h2>

      <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
        This agent&rsquo;s projected spend is flagged red against this
        project&rsquo;s budget. Review before enabling.
      </p>

      {estimate !== null && (
        <dl className="mt-3 flex flex-col gap-1.5 rounded border border-zinc-200 bg-zinc-50 p-2.5 text-xs dark:border-zinc-800 dark:bg-zinc-950">
          <div className="flex items-center justify-between">
            <dt className="text-zinc-500 dark:text-zinc-400">Projected monthly cost</dt>
            <dd className="font-mono tabular-nums text-zinc-900 dark:text-zinc-100" data-agent-cost-confirm-projected>
              {estimate.projected_monthly_usd !== null
                ? formatUsdPerMonth(estimate.projected_monthly_usd)
                : "no history"}
            </dd>
          </div>
          <div className="flex items-center justify-between">
            <dt className="text-zinc-500 dark:text-zinc-400">% of project budget</dt>
            <dd className="font-mono tabular-nums text-zinc-900 dark:text-zinc-100">
              {estimate.vs_project_budget_pct !== null
                ? `${estimate.vs_project_budget_pct.toFixed(1)}%`
                : "n/a"}
            </dd>
          </div>
          <div className="flex items-center justify-between">
            <dt className="text-zinc-500 dark:text-zinc-400">Avg cost / spawn</dt>
            <dd className="font-mono tabular-nums text-zinc-900 dark:text-zinc-100">
              {estimate.avg_cost_per_spawn !== null
                ? `$${estimate.avg_cost_per_spawn.toFixed(4)}`
                : "n/a"}
            </dd>
          </div>
        </dl>
      )}

      <div className="mt-4 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
          data-agent-cost-confirm-cancel
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onConfirm}
          className="rounded border border-red-600 bg-red-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-red-700 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-red-500 dark:bg-red-500 dark:hover:bg-red-600"
          data-agent-cost-confirm-confirm
        >
          Enable anyway
        </button>
      </div>
    </ModalShell>
  );
}

function AgentOverrideRow({
  row,
  error,
  costEstimate,
  onToggleEnabled,
  onChangeTier,
  onChangeNotes,
}: {
  row: Row;
  error: string | null;
  costEstimate: AgentCostEstimate | null;
  onToggleEnabled: () => void;
  onChangeTier: (tier: AgentModelTier | null) => void;
  onChangeNotes: (value: string) => void;
}) {
  const { agent, enabled, modelOverride, notes } = row;
  return (
    <li
      data-agent-override-row={agent.name}
      data-agent-override-enabled={enabled ? "true" : "false"}
      className="flex flex-col gap-2 px-3 py-2.5"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 truncate text-[13px] font-medium text-zinc-800 dark:text-zinc-200">
          {agent.name}
        </span>
        <DomainChip domain={agent.domain} />
        <ModelTierBadge model={agent.model} />
        <AgentCostBadge estimate={costEstimate} />

        <span className="ml-auto flex items-center gap-3">
          <select
            value={encodeTier(modelOverride)}
            onChange={(e) => onChangeTier(decodeTier(e.target.value))}
            aria-label={`Model tier override for ${agent.name}`}
            data-agent-override-tier
            className="rounded border border-zinc-200 bg-transparent px-1.5 py-1 text-[11px] text-zinc-600 focus:border-zinc-400 focus:outline-none min-h-[36px] sm:min-h-0 dark:border-zinc-700 dark:text-zinc-300"
          >
            <option value="">Default</option>
            <option value="haiku">Haiku</option>
            <option value="sonnet">Sonnet</option>
            <option value="opus">Opus</option>
          </select>

          <label className="inline-flex cursor-pointer items-center gap-1.5">
            <span className="sr-only">
              {enabled ? "Enabled" : "Disabled"} — toggle {agent.name}
            </span>
            <input
              type="checkbox"
              checked={enabled}
              onChange={onToggleEnabled}
              data-agent-override-toggle
              className="h-4 w-4 rounded border-zinc-300 text-emerald-600 focus:ring-emerald-500 dark:border-zinc-600"
            />
          </label>
        </span>
      </div>

      <input
        type="text"
        value={notes}
        onChange={(e) => onChangeNotes(e.target.value)}
        placeholder="Notes (optional)"
        aria-label={`Notes for ${agent.name}`}
        data-agent-override-notes
        className="w-full rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] text-zinc-700 placeholder:text-zinc-400 focus:border-zinc-400 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-300 dark:placeholder:text-zinc-500"
      />

      {error !== null && (
        <p role="alert" className="text-[11px] text-red-700 dark:text-red-300">
          {error}
        </p>
      )}
    </li>
  );
}
