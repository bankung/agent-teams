"use client";

// AgentGallery — Kanban #1017. Interactive shell around the agent card grid.
// Owns the sort + filter state; operates purely client-side over the SSR'd
// `agents` prop (the page Server-Component-fetches the listing, mirroring the
// dashboard / inbox convention — no client fetch, so no async-fetch RTL race).
//
// Sort:   name | model | domain.
// Filters (AND-compose): by domain, by model tier, by has-hooks (≥1).
//   - domain / model are single-select-per-dimension toggle chips.
//   - has-hooks is a boolean toggle.
// Chip labels carry live counts of the agents that match that chip alone
// (computed against the OTHER active filters so counts narrow as you filter).

import { useMemo, useState } from "react";

import type { AgentSummary, AgentModelTier, AgentDomain } from "@/lib/api";
import { AgentCard } from "./AgentCard";

type SortKey = "name" | "model" | "domain";

// Model tiers in heaviest→lightest order for the sort + the filter-chip order.
const MODEL_ORDER: AgentModelTier[] = ["opus", "sonnet", "haiku"];

// Rank for the "model" sort: known tiers heaviest-first, null ("default") last.
function modelRank(model: AgentModelTier | null): number {
  if (model === null) return MODEL_ORDER.length;
  return MODEL_ORDER.indexOf(model);
}

// Model filter value: a concrete tier, the "default" sentinel (= agents with
// no `model:` key / null model), or null (= no model filter active).
type ModelFilter = AgentModelTier | "default" | null;

type Filters = {
  domain: AgentDomain | null;
  model: ModelFilter;
  hasHooks: boolean;
};

const EMPTY_FILTERS: Filters = { domain: null, model: null, hasHooks: false };

// Does an agent's model match the model filter? "default" matches null-model
// agents; a concrete tier matches that tier; null filter matches everything.
function modelMatches(agentModel: AgentModelTier | null, f: ModelFilter): boolean {
  if (f === null) return true;
  if (f === "default") return agentModel === null;
  return agentModel === f;
}

// Apply a (possibly partial) filter set to the agent list. Each predicate is
// skipped when its filter is inactive, so the three filters AND-compose.
function matches(agent: AgentSummary, f: Filters): boolean {
  if (f.domain !== null && agent.domain !== f.domain) return false;
  if (!modelMatches(agent.model, f.model)) return false;
  if (f.hasHooks && agent.hook_count < 1) return false;
  return true;
}

export function AgentGallery({ agents }: { agents: AgentSummary[] }) {
  const [sort, setSort] = useState<SortKey>("name");
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);

  // Distinct domains present in the data, in first-seen-sorted order. The BE
  // pre-sorts by name; we derive the domain chip set from the actual agents so
  // an empty domain never gets a (0) chip.
  const domains = useMemo(() => {
    const set = new Set<AgentDomain>();
    for (const a of agents) set.add(a.domain);
    return Array.from(set).sort();
  }, [agents]);

  // Model tiers present in the data (in MODEL_ORDER), plus whether any agent
  // has a null model (→ the "default" chip).
  const modelChips = useMemo(() => {
    const present = new Set(agents.map((a) => a.model));
    const tiers = MODEL_ORDER.filter((t) => present.has(t));
    return { tiers, hasDefault: present.has(null) };
  }, [agents]);

  // Filtered + sorted view.
  const visible = useMemo(() => {
    const filtered = agents.filter((a) => matches(a, filters));
    const sorted = [...filtered].sort((a, b) => {
      if (sort === "model") {
        const r = modelRank(a.model) - modelRank(b.model);
        if (r !== 0) return r;
        return a.name.localeCompare(b.name);
      }
      if (sort === "domain") {
        const d = a.domain.localeCompare(b.domain);
        if (d !== 0) return d;
        return a.name.localeCompare(b.name);
      }
      return a.name.localeCompare(b.name);
    });
    return sorted;
  }, [agents, filters, sort]);

  // Count of agents matching a candidate chip combined with the OTHER active
  // filters (so the count reflects what clicking the chip would yield). For the
  // chip's OWN dimension we override that dimension with the candidate value.
  function countFor(partial: Partial<Filters>): number {
    const probe: Filters = { ...filters, ...partial };
    return agents.filter((a) => matches(a, probe)).length;
  }

  const anyFilter =
    filters.domain !== null || filters.model !== null || filters.hasHooks;

  return (
    <div className="flex flex-col gap-3">
      {/* Controls: sort select + filter chips. */}
      <div
        data-agent-controls
        className="flex flex-col gap-2 rounded-md border border-zinc-200 bg-zinc-50/60 p-3 dark:border-zinc-800 dark:bg-zinc-950/40"
      >
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-zinc-600 dark:text-zinc-400">
            <span className="font-medium uppercase tracking-wide">Sort</span>
            <select
              data-agent-sort
              value={sort}
              onChange={(e) => setSort(e.target.value as SortKey)}
              className="rounded border border-zinc-300 bg-white px-1.5 py-0.5 text-xs text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
            >
              <option value="name">name</option>
              <option value="model">model</option>
              <option value="domain">domain</option>
            </select>
          </label>

          <span
            className="ml-auto text-[11px] text-zinc-500 dark:text-zinc-400 tabular-nums"
            data-agent-count
          >
            {visible.length} of {agents.length}
          </span>
          {anyFilter ? (
            <button
              type="button"
              data-agent-clear-filters
              onClick={() => setFilters(EMPTY_FILTERS)}
              className="rounded border border-zinc-300 px-1.5 py-0.5 text-[11px] text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-800"
            >
              Clear
            </button>
          ) : null}
        </div>

        {/* Domain filter chips. */}
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] font-medium uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
            Domain
          </span>
          {domains.map((d) => {
            const active = filters.domain === d;
            return (
              <FilterChip
                key={d}
                data-filter-chip
                data-filter-kind="domain"
                data-filter-value={d}
                active={active}
                count={countFor({ domain: active ? null : d })}
                onClick={() =>
                  setFilters((f) => ({ ...f, domain: active ? null : d }))
                }
              >
                {d}
              </FilterChip>
            );
          })}
        </div>

        {/* Model-tier + has-hooks filter chips. */}
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] font-medium uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
            Model
          </span>
          {modelChips.tiers.map((t) => {
            const active = filters.model === t;
            return (
              <FilterChip
                key={t}
                data-filter-chip
                data-filter-kind="model"
                data-filter-value={t}
                active={active}
                count={countFor({ model: active ? null : t })}
                onClick={() =>
                  setFilters((f) => ({ ...f, model: active ? null : t }))
                }
              >
                {t}
              </FilterChip>
            );
          })}
          {modelChips.hasDefault ? (
            <FilterChip
              data-filter-chip
              data-filter-kind="model"
              data-filter-value="default"
              active={filters.model === "default"}
              count={countFor({
                model: filters.model === "default" ? null : "default",
              })}
              onClick={() =>
                setFilters((f) => ({
                  ...f,
                  model: f.model === "default" ? null : "default",
                }))
              }
            >
              default
            </FilterChip>
          ) : null}

          <span aria-hidden className="mx-1 text-zinc-300 dark:text-zinc-700">
            |
          </span>
          <FilterChip
            data-filter-chip
            data-filter-kind="has-hooks"
            data-filter-value="true"
            active={filters.hasHooks}
            count={countFor({ hasHooks: !filters.hasHooks })}
            onClick={() => setFilters((f) => ({ ...f, hasHooks: !f.hasHooks }))}
          >
            has hooks
          </FilterChip>
        </div>
      </div>

      {/* Grid. */}
      {visible.length === 0 ? (
        <p
          data-agent-grid-empty
          className="rounded border border-zinc-200 bg-zinc-50 p-4 text-sm text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900/40 dark:text-zinc-400"
        >
          No agents match the active filters.
        </p>
      ) : (
        <div
          data-agent-grid
          className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
        >
          {visible.map((agent) => (
            <AgentCard key={agent.name} agent={agent} />
          ))}
        </div>
      )}
    </div>
  );
}

// FilterChip — a toggleable pill with a trailing count. Spreads `data-*` props
// through to the button so tests can target by kind/value.
function FilterChip({
  active,
  count,
  onClick,
  children,
  ...rest
}: {
  active: boolean;
  count: number;
  onClick: () => void;
  children: React.ReactNode;
} & Record<`data-${string}`, string>) {
  return (
    <button
      type="button"
      {...rest}
      data-active={active ? "true" : "false"}
      aria-pressed={active}
      onClick={onClick}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors ${
        active
          ? "border-violet-300 bg-violet-100 text-violet-800 dark:border-violet-700 dark:bg-violet-900/40 dark:text-violet-200"
          : "border-zinc-300 bg-white text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800"
      }`}
    >
      <span>{children}</span>
      <span className="tabular-nums opacity-60">{count}</span>
    </button>
  );
}
