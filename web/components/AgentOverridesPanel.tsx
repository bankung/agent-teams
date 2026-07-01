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
// hot-reload/restart banner (#1019), cost preview (#1020), tool-scope viz
// (#1021).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getAgentOverrides,
  getAgents,
  patchAgentOverrides,
  type AgentModelTier,
  type AgentOverride,
  type AgentSummary,
} from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";
import { useAsyncData } from "@/lib/useAsyncData";
import { ModelTierBadge, DomainChip } from "./AgentBadges";

const NOTES_DEBOUNCE_MS = 400;

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

  const onToggleEnabled = useCallback(
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
                onToggleEnabled={() => onToggleEnabled(row.agent.name)}
                onChangeTier={(tier) => onChangeTier(row.agent.name, tier)}
                onChangeNotes={(value) => onChangeNotes(row.agent.name, value)}
              />
            ))}
        </ul>
      )}
    </section>
  );
}

function AgentOverrideRow({
  row,
  error,
  onToggleEnabled,
  onChangeTier,
  onChangeNotes,
}: {
  row: Row;
  error: string | null;
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
