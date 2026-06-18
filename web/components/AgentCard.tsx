// AgentCard — Kanban #1017. One card in the agent gallery grid. Presentational
// (no state); the whole card is a Link into /agents/{name}. Invalid agents
// (validation_errors carrying severity='error') get a red accent + the first
// error surfaced inline + via title tooltip.

import Link from "next/link";

import type { AgentSummary } from "@/lib/api";
import { ModelTierBadge, DomainChip } from "./AgentBadges";

function firstError(agent: AgentSummary): string | null {
  const err = agent.validation_errors.find((e) => e.severity === "error");
  if (!err) return null;
  // file:line — message, so the operator can jump straight to the offending
  // frontmatter line in the source file.
  return `${err.file}:${err.line} — ${err.message}`;
}

export function AgentCard({ agent }: { agent: AgentSummary }) {
  const invalid = !agent.valid;
  const errText = invalid ? firstError(agent) : null;

  return (
    <Link
      href={`/agents/${encodeURIComponent(agent.name)}`}
      data-agent-card
      data-agent-name={agent.name}
      data-agent-domain={agent.domain}
      data-agent-valid={agent.valid ? "true" : "false"}
      title={errText ?? agent.name}
      className={`glass-card flex flex-col gap-2 rounded-md border bg-white p-3 transition-colors dark:bg-zinc-900 ${
        invalid
          ? "border-red-300 hover:border-red-400 dark:border-red-800/70 dark:hover:border-red-700"
          : "border-zinc-200 hover:border-zinc-300 dark:border-zinc-800 dark:hover:border-zinc-700"
      }`}
    >
      <header className="flex items-start justify-between gap-2">
        <span className="min-w-0 truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          {agent.name}
        </span>
        <ModelTierBadge model={agent.model} />
      </header>

      <p className="line-clamp-1 text-xs text-zinc-600 dark:text-zinc-400">
        {agent.description}
      </p>

      <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-zinc-500 dark:text-zinc-400">
        <DomainChip domain={agent.domain} />
        <span
          className="tabular-nums"
          title={
            agent.tool_count === null
              ? "grants all tools"
              : `${agent.tool_count} tool${agent.tool_count === 1 ? "" : "s"}`
          }
        >
          {agent.tools_summary}
        </span>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-700">
          ·
        </span>
        <span
          className="tabular-nums"
          title={`${agent.hook_count} hook${agent.hook_count === 1 ? "" : "s"}`}
        >
          {agent.hook_count} hook{agent.hook_count === 1 ? "" : "s"}
        </span>
      </div>

      <footer className="flex items-center gap-1.5 text-[10px] text-zinc-400 dark:text-zinc-500">
        <span className="truncate font-mono" title={agent.source_file}>
          {agent.source_file}
        </span>
        {invalid ? (
          <span
            data-agent-invalid
            className="ml-auto inline-flex shrink-0 items-center rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-red-700 dark:bg-red-900/40 dark:text-red-200"
          >
            invalid
          </span>
        ) : null}
      </footer>

      {/* First blocking error surfaced inline (in addition to the title
          tooltip) so the problem is visible without hovering. */}
      {errText ? (
        <p className="text-[11px] leading-snug text-red-600 dark:text-red-400">
          {errText}
        </p>
      ) : null}
    </Link>
  );
}
