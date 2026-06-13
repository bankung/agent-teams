// Shared agent badges — Kanban #1017. Reused by AgentCard (gallery) and
// AgentDetail (detail page) so the model-tier + domain chips read identically
// on both surfaces. Pure presentational; no client state.

import type { AgentModelTier, AgentDomain } from "@/lib/api";

// Model-tier chip. Color-coded per tier; `null` model → muted "default" chip
// (the agent has no `model:` key and inherits the session default). Colors
// follow the established zinc/accent palette (cf. RunModeBadge / laneColor):
//   opus   → violet (heaviest tier)
//   sonnet → blue   (mid tier)
//   haiku  → emerald (lightest tier)
//   null   → zinc    (muted "default")
const TIER_CLASS: Record<AgentModelTier, string> = {
  opus: "text-violet-700 bg-violet-50 dark:text-violet-300 dark:bg-violet-900/30",
  sonnet: "text-blue-700 bg-blue-50 dark:text-blue-300 dark:bg-blue-900/30",
  haiku:
    "text-emerald-700 bg-emerald-50 dark:text-emerald-300 dark:bg-emerald-900/30",
};

export function ModelTierBadge({ model }: { model: AgentModelTier | null }) {
  if (model === null) {
    return (
      <span
        data-agent-tier="default"
        title="no model key — inherits the session default tier"
        className="inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-500 bg-zinc-100 dark:text-zinc-400 dark:bg-zinc-800"
      >
        default
      </span>
    );
  }
  return (
    <span
      data-agent-tier={model}
      title={`model tier: ${model}`}
      className={`inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${TIER_CLASS[model]}`}
    >
      {model}
    </span>
  );
}

// Domain chip — muted, uppercase, mirrors the team chip used on the dashboard
// CompactProjectCard / inbox group header. Neutral zinc for every domain (the
// model-tier chip carries the color signal; the domain chip is a label).
export function DomainChip({ domain }: { domain: AgentDomain }) {
  return (
    <span
      data-agent-domain-chip={domain}
      title={`domain: ${domain}`}
      className="inline-flex shrink-0 items-center rounded bg-zinc-100 px-1 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400"
    >
      {domain}
    </span>
  );
}
