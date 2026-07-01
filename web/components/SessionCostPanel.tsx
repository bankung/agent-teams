"use client";

// SessionCostPanel — per-session cost roll-up over the usage_events ledger.
// Kanban #2728 (endpoint) / #2735 (this panel).
//
// Prop-driven for the INITIAL render (RTL determinism parity with
// MonthlySpendSection / CostSummary): the Board fetches page 1 server-side and
// passes it down as `data`. The default-state test therefore needs no async.
// "Load more" pages are fetched client-side (injected `fetcher`, default = the
// real listUsageSessions client) and APPENDED to local state.
//
// Collapse behaviour mirrors CostSummary / MonthlySpendSection exactly:
// persisted via usePersistentState (localStorage + same-tab StorageEvent).

import { useState } from "react";

import {
  listUsageSessions,
  type UsageSessionRow,
  type UsageSessionsResponse,
} from "@/lib/api";
import { usePersistentState } from "@/lib/usePersistentState";

// ── helpers ──────────────────────────────────────────────────────────────────

function parseUsd(raw: string): number {
  const n = Number.parseFloat(raw);
  return Number.isFinite(n) ? n : 0;
}

function formatUsd(n: number): string {
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatUsd4(n: number): string {
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 4 })}`;
}

function formatInt(n: number): string {
  return n.toLocaleString("en-US");
}

// cache_hit_ratio is a float in [0,1] → one-dp percentage (0.8123 → "81.2%").
function formatPct(ratio: number): string {
  const safe = Number.isFinite(ratio) ? ratio : 0;
  return `${(safe * 100).toFixed(1)}%`;
}

// first 8 chars of the session id — enough to disambiguate at a glance; the
// full id lives in the row's title attribute.
function shortId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

// Full ISO timestamp → "Jun 25, 2026, 14:03" (local tz). These are real
// instants (not date-only billing boundaries), so local-tz rendering is right.
function fmtTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ── icons ─────────────────────────────────────────────────────────────────────

function ChevronDownIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="4 6 8 10 12 6" />
    </svg>
  );
}

function ChevronRightIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="6 4 10 8 6 12" />
    </svg>
  );
}

// ── per-session row + agent drilldown ────────────────────────────────────────

function SessionRow({ session }: { session: UsageSessionRow }) {
  const [open, setOpen] = useState(false);

  const total = parseUsd(session.total_cost_usd);
  const full = session.session_ext_id;

  return (
    <div className="rounded-md border border-zinc-200/70 bg-white/60 dark:border-zinc-700/50 dark:bg-zinc-900/30">
      {/* session header row */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
        {/* short session id (full id in title) */}
        <span
          className="font-mono text-xs text-zinc-600 dark:text-zinc-300"
          title={full}
        >
          {shortId(full)}
        </span>

        {/* total cost */}
        <span
          className="text-xs font-semibold text-zinc-800 dark:text-zinc-100 tabular-nums"
          title={`${formatUsd4(total)} USD total for this session`}
        >
          {formatUsd(total)}
        </span>

        {/* cache hit ratio as a percentage */}
        <span
          className="text-xs text-emerald-700 dark:text-emerald-300 tabular-nums"
          title={`Cache hit ratio ${formatPct(session.cache_hit_ratio)} (cache_read / (input + cache_creation + cache_read))`}
        >
          {formatPct(session.cache_hit_ratio)} cache
        </span>

        {/* ledger event count — NOT transcript turns (#2728) */}
        <span
          className="text-xs text-zinc-500 dark:text-zinc-400 tabular-nums"
          title={`${session.event_count} ledger events (usage_events rows, not transcript turns)`}
        >
          {session.event_count} events
        </span>

        {/* last activity timestamp */}
        <span
          className="text-xs text-zinc-500 dark:text-zinc-400 tabular-nums"
          title={`Last activity ${session.last_occurred_at}`}
        >
          {fmtTimestamp(session.last_occurred_at)}
        </span>

        {/* drilldown toggle — per-agent breakdown */}
        {session.agents.length > 0 && (
          <button
            type="button"
            aria-expanded={open}
            aria-label={`${open ? "Collapse" : "Expand"} agent breakdown for session ${shortId(full)}`}
            onClick={() => setOpen((v) => !v)}
            className="ml-auto flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-700 dark:text-zinc-500 dark:hover:text-zinc-200"
          >
            {open ? <ChevronDownIcon /> : <ChevronRightIcon />}
            <span>{open ? "Hide" : "Agents"}</span>
          </button>
        )}
      </div>

      {/* per-agent drilldown — Lead first (BE pre-sorts Lead-first, cost desc) */}
      {open && (
        <ul className="border-t border-zinc-100 dark:border-zinc-800 divide-y divide-zinc-100 dark:divide-zinc-800">
          {session.agents.map((a, i) => {
            const aCost = parseUsd(a.cost_usd);
            const isLead = a.agent_name === null;
            const label = isLead ? "Lead" : a.agent_name;
            return (
              <li
                key={`${a.agent_name ?? "__lead"}__${a.model}__${i}`}
                className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-1.5"
                data-agent-row
              >
                <span className="flex-1 min-w-0 truncate text-xs text-zinc-600 dark:text-zinc-300">
                  {isLead ? (
                    <span className="font-medium text-blue-700 dark:text-blue-300">
                      {label}
                    </span>
                  ) : (
                    label
                  )}
                </span>
                <span className="text-xs text-zinc-400 dark:text-zinc-500 tabular-nums">
                  {a.model}
                </span>
                <span
                  className="text-xs font-medium text-zinc-700 dark:text-zinc-200 tabular-nums"
                  title={`${formatUsd4(aCost)} USD · ${formatInt(a.input_tokens)} in / ${formatInt(a.output_tokens)} out`}
                >
                  {formatUsd(aCost)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ── main component ────────────────────────────────────────────────────────────

type Props = {
  // Initial page (page 1) fetched by the Board and passed down — prop-driven so
  // the default render is synchronous (RTL determinism).
  data: UsageSessionsResponse;
  // Project to scope subsequent "Load more" fetches. Omit for portfolio-wide.
  projectId?: number;
  // Injectable fetcher (defaults to the real client) so tests can mock the
  // pagination append without module-mocking.
  fetcher?: typeof listUsageSessions;
  defaultCollapsed?: boolean;
  storageKey?: string;
  className?: string;
};

export function SessionCostPanel({
  data,
  projectId,
  fetcher = listUsageSessions,
  defaultCollapsed = false,
  storageKey,
  className = "mb-5",
}: Props) {
  // Accumulated sessions + the offset/limit that produced the current page set.
  // Seeded from the prop; "Load more" appends to this.
  const [sessions, setSessions] = useState<UsageSessionRow[]>(data.sessions);
  const [offset, setOffset] = useState<number>(data.offset);
  const limit = data.limit;
  // hasMore: the last page returned exactly `limit` rows → more may exist.
  const [hasMore, setHasMore] = useState<boolean>(data.returned === data.limit);
  const [loading, setLoading] = useState(false);

  const collapsible = defaultCollapsed && storageKey != null;

  // Mirror CostSummary: persisted collapse via usePersistentState. SSR snapshot
  // = expanded default (no hydration mismatch); client reads localStorage.
  const [storedExpanded, setStoredExpanded] = usePersistentState<boolean>(
    storageKey ?? "session-cost:__noop",
    !defaultCollapsed,
    { deserialize: (raw) => JSON.parse(raw) !== false },
  );
  const expanded = collapsible ? storedExpanded : !defaultCollapsed;

  function toggle() {
    if (!collapsible) return;
    setStoredExpanded(!expanded);
  }

  async function loadMore() {
    if (loading || !hasMore) return; // guard against double-fire
    setLoading(true);
    const nextOffset = offset + limit;
    try {
      const page = await fetcher({ projectId, limit, offset: nextOffset });
      setSessions((prev) => [...prev, ...page.sessions]);
      setOffset(nextOffset);
      setHasMore(page.returned === page.limit);
    } catch (_) {
      // Supplementary pagination — a failed page must not blank the panel.
      // Stop offering "Load more" on error (operator can re-expand / refresh).
      setHasMore(false);
    } finally {
      setLoading(false);
    }
  }

  const isEmpty = sessions.length === 0;

  return (
    <section
      data-session-cost-panel
      aria-label="Per-session cost"
      className={`${className} rounded-lg border border-zinc-200/60 bg-zinc-50/40 p-3 dark:border-zinc-800/60 dark:bg-zinc-900/20`}
    >
      <div
        className="flex items-center gap-2 flex-wrap"
        style={{ marginBottom: expanded ? "0.75rem" : 0 }}
      >
        {collapsible ? (
          <button
            type="button"
            onClick={toggle}
            aria-expanded={expanded}
            className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
          >
            {expanded ? <ChevronDownIcon /> : <ChevronRightIcon />}
            Session Cost
          </button>
        ) : (
          <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Session Cost
          </h2>
        )}

        {/* Compact inline summary when collapsible + collapsed */}
        {collapsible && !expanded && (
          <span className="text-xs text-zinc-600 dark:text-zinc-400 tabular-nums">
            {sessions.length} session{sessions.length === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {expanded && (
        <>
          {isEmpty ? (
            <p className="text-sm text-zinc-400 dark:text-zinc-600">
              No session cost recorded yet.
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {sessions.map((session) => (
                <SessionRow
                  key={session.session_ext_id}
                  session={session}
                />
              ))}

              {hasMore && (
                <button
                  type="button"
                  onClick={loadMore}
                  disabled={loading}
                  data-session-load-more
                  className="mt-1 self-start rounded-md border border-zinc-200 px-3 py-1.5 text-xs text-zinc-600 hover:border-zinc-300 hover:text-zinc-900 disabled:cursor-not-allowed disabled:opacity-60 dark:border-zinc-700 dark:text-zinc-300 dark:hover:text-zinc-100"
                >
                  {loading ? "Loading…" : "Load more"}
                </button>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
