// AgentDetail — Kanban #1017 AC[3]. Detail body for /agents/[name]:
//   - metadata row (same badges as the gallery card)
//   - full description
//   - validation diagnostics (when any)
//   - raw frontmatter in a monospace, scrollable <pre>
//   - "Recent spawns" list — task_id deep-links to /p/{project_name}?task={id}
//     (the board card highlight/scroll handler — cf. Board.tsx #1001 follow-up).
//
// Presentational; no client state. Relative spawn times via formatRelative.

import Link from "next/link";

import type { AgentDetail as AgentDetailType } from "@/lib/api";
import { formatRelative } from "@/lib/time";
import { ModelTierBadge, DomainChip } from "./AgentBadges";

function SeverityChip({ severity }: { severity: "error" | "warning" }) {
  const cls =
    severity === "error"
      ? "text-red-700 bg-red-100 dark:text-red-200 dark:bg-red-900/40"
      : "text-amber-700 bg-amber-100 dark:text-amber-200 dark:bg-amber-900/40";
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${cls}`}
    >
      {severity}
    </span>
  );
}

export function AgentDetail({ agent }: { agent: AgentDetailType }) {
  const invalid = !agent.valid;

  return (
    <article
      data-agent-detail
      data-agent-name={agent.name}
      data-agent-domain={agent.domain}
      data-agent-valid={agent.valid ? "true" : "false"}
      className="flex flex-col gap-5"
    >
      {/* Metadata row — same badges as the gallery card. */}
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">
            {agent.name}
          </h1>
          <ModelTierBadge model={agent.model} />
          <DomainChip domain={agent.domain} />
          {invalid ? (
            <span
              data-agent-invalid
              className="inline-flex items-center rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-red-700 dark:bg-red-900/40 dark:text-red-200"
            >
              invalid
            </span>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500 dark:text-zinc-400">
          <span
            className="tabular-nums"
            title={
              agent.tool_count === null
                ? "grants all tools"
                : `${agent.tool_count} tools`
            }
          >
            {agent.tools_summary}
          </span>
          <span aria-hidden className="text-zinc-300 dark:text-zinc-700">
            ·
          </span>
          <span className="tabular-nums">
            {agent.hook_count} hook{agent.hook_count === 1 ? "" : "s"}
          </span>
          <span aria-hidden className="text-zinc-300 dark:text-zinc-700">
            ·
          </span>
          <span className="font-mono" title={agent.source_file}>
            {agent.source_file}
          </span>
        </div>
      </div>

      {/* Full description. */}
      <section className="flex flex-col gap-1.5">
        <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Description
        </h2>
        <p
          data-agent-description
          className="whitespace-pre-wrap text-sm leading-relaxed text-zinc-700 dark:text-zinc-300"
        >
          {agent.full_description}
        </p>
      </section>

      {/* Validation diagnostics — rendered only when present. */}
      {agent.validation_errors.length > 0 ? (
        <section data-agent-diagnostics className="flex flex-col gap-1.5">
          <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Validation diagnostics
          </h2>
          <ul className="flex flex-col gap-1">
            {agent.validation_errors.map((d, i) => (
              <li
                key={`${d.file}:${d.line}:${d.field}:${i}`}
                data-agent-diagnostic
                data-severity={d.severity}
                className="flex flex-wrap items-center gap-2 rounded border border-zinc-200 bg-zinc-50/60 px-2 py-1.5 text-xs dark:border-zinc-800 dark:bg-zinc-950/40"
              >
                <SeverityChip severity={d.severity} />
                <span className="font-mono text-zinc-500 dark:text-zinc-400">
                  {d.file}:{d.line}
                </span>
                <span className="font-mono text-zinc-400 dark:text-zinc-500">
                  {d.field}
                </span>
                <span className="text-zinc-700 dark:text-zinc-300">
                  {d.message}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* Raw frontmatter — monospace, scrollable. */}
      <section className="flex flex-col gap-1.5">
        <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Frontmatter
        </h2>
        <pre
          data-agent-frontmatter
          className="glass-surface max-h-80 overflow-auto rounded-md border border-zinc-200 bg-zinc-50 p-3 font-mono text-xs leading-relaxed text-zinc-800 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-200"
        >
          {agent.raw_frontmatter}
        </pre>
      </section>

      {/* Recent spawns — task_id links to the board deep-link. */}
      <section className="flex flex-col gap-1.5">
        <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Recent spawns
          <span className="ml-1.5 font-normal normal-case text-zinc-400 dark:text-zinc-500 tabular-nums">
            ({agent.spawns.length})
          </span>
        </h2>
        {agent.spawns.length === 0 ? (
          <p
            data-agent-spawns-empty
            className="rounded border border-zinc-200 bg-zinc-50/60 p-3 text-xs text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-400"
          >
            This agent hasn’t been spawned for any tasks yet.
          </p>
        ) : (
          <ul data-agent-spawns className="flex flex-col">
            {agent.spawns.map((s) => (
              <li
                key={`${s.project_id}:${s.task_id}`}
                data-spawn-row
                data-task-id={s.task_id}
                className="border-t border-zinc-100 first:border-t-0 dark:border-zinc-800"
              >
                {/* task_id is always numeric (DB bigint) - no encoding needed; project_name IS encoded. */}
                <Link
                  href={`/p/${encodeURIComponent(s.project_name)}?task=${s.task_id}`}
                  className="flex flex-wrap items-center gap-2 px-1 py-2 text-xs hover:bg-zinc-50 dark:hover:bg-zinc-900/60"
                >
                  <span className="font-mono text-zinc-500 dark:text-zinc-400">
                    #{s.task_id}
                  </span>
                  <span className="truncate text-zinc-700 dark:text-zinc-300">
                    {s.project_name}
                  </span>
                  {s.model ? (
                    <span className="inline-flex shrink-0 items-center rounded bg-zinc-100 px-1 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
                      {s.model}
                    </span>
                  ) : null}
                  <span
                    className="ml-auto shrink-0 text-[11px] text-zinc-500 dark:text-zinc-400 tabular-nums"
                    title={s.at ?? undefined}
                  >
                    {s.at ? formatRelative(s.at) : "—"}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </article>
  );
}
