// Shared agent badges — Kanban #1017. Reused by AgentCard (gallery) and
// AgentDetail (detail page) so the model-tier + domain chips read identically
// on both surfaces. Pure presentational; no client state.
//
// Kanban #1021 adds the tool-scope risk chips (ToolChipsRow) + the card-level
// risk indicator (RiskBadge). RISK_ORDER below is the SINGLE source of truth
// for severity ranking on the FE — the backend independently ranks risk_class
// when picking which class wins ties server-side (e.g. sorting groups on the
// detail page), so if the ranking ever changes it must be updated in BOTH
// places: here and the backend's equivalent constant (see the backend's
// tool_scope classifier module for its mirror of this order).

import type { AgentModelTier, AgentDomain, ToolChip, ToolChipRiskClass } from "@/lib/api";

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

// ============================================================================
// Kanban #1021 — tool-scope risk chips.
// ============================================================================

// RISK_ORDER — severity low→high. Index = rank; higher index = higher risk.
// This is the ONE place the FE decides "which risk_class wins" (used by
// highestRisk() below for the card badge, and by the detail page's group
// ordering). See the file-header note re: keeping this in sync with the BE.
export const RISK_ORDER: ToolChipRiskClass[] = [
  "always-safe",
  "read-only",
  "external",
  "write-edit",
  "shell-or-destructive",
];

// RISK_META — one entry per risk class: chip color (house tokens, matches
// TaskToolCalls' TIER_CLASS zinc=neutral/amber=warn/blue=info/red=danger;
// read-only + always-safe share neutral zinc), human label (detail-page group
// headers), and the 1-line "why" copy reused by both the per-chip tooltip
// (AC5) and the detail page's per-group explanation (AC6).
export const RISK_META: Record<
  ToolChipRiskClass,
  { cls: string; label: string; why: string }
> = {
  "always-safe": {
    cls: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
    label: "Always safe",
    why: "no side effects — safe to run unattended.",
  },
  "read-only": {
    cls: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
    label: "Read-only",
    why: "reads state only — cannot change files, data, or systems.",
  },
  external: {
    cls: "bg-blue-50 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
    label: "External",
    why: "reaches outside the sandbox (network/web) — exposure risk without mutation.",
  },
  "write-edit": {
    cls: "bg-amber-50 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300",
    label: "Write / edit",
    why: "can create or modify files/data — reviewable but mutating.",
  },
  "shell-or-destructive": {
    cls: "bg-red-50 text-red-800 dark:bg-red-900/30 dark:text-red-300",
    label: "Shell or destructive",
    why: "can run arbitrary commands or destructive ops — highest blast radius.",
  },
};

// highestRisk — the highest-ranked risk_class among a chip list, or null for
// an empty list (AgentCard hides the badge in that case).
export function highestRisk(chips: ToolChip[]): ToolChipRiskClass | null {
  let best: ToolChipRiskClass | null = null;
  let bestRank = -1;
  for (const c of chips) {
    const rank = RISK_ORDER.indexOf(c.risk_class);
    if (rank > bestRank) {
      bestRank = rank;
      best = c.risk_class;
    }
  }
  return best;
}

// TOOL_DESCRIPTIONS — 1-line "what it is" for tool names actually seen across
// the live .claude/agents/*.md roster's explicit `tools:` lists today (grep-
// verified: Read/Grep/Glob/Bash/Write/Edit/WebFetch/WebSearch + the "All
// tools" sentinel). Unknown names (custom/future tools) fall back to a
// generic line built from the risk class alone (see toolTooltip()).
const TOOL_DESCRIPTIONS: Record<string, string> = {
  "All tools": "grants every tool available to the session, unrestricted.",
  Read: "reads a file's contents.",
  Grep: "searches file contents by pattern.",
  Glob: "finds files by name pattern.",
  Edit: "makes a targeted text replacement in an existing file.",
  Write: "creates or overwrites a file.",
  Bash: "runs an arbitrary shell command.",
  WebFetch: "fetches and reads the contents of a URL.",
  WebSearch: "runs a web search query.",
};

// toolTooltip — full tooltip text for one chip: name + what-it-is + why the
// risk class. Falls back to a generic "what-it-is" line for names outside
// TOOL_DESCRIPTIONS (custom MCP tools, future additions).
export function toolTooltip(chip: ToolChip): string {
  const what = TOOL_DESCRIPTIONS[chip.name] ?? "a tool granted to this agent.";
  const meta = RISK_META[chip.risk_class];
  return `${chip.name} — ${what} Risk: ${meta.label} — ${meta.why}`;
}

// RiskBadge — card-level overall risk indicator, driven by the HIGHEST-risk
// chip present. Renders nothing when the chip list is empty (defensive — the
// BE always sends tool_chips, but a legacy/degraded payload could omit it).
export function RiskBadge({ chips }: { chips: ToolChip[] }) {
  const risk = highestRisk(chips);
  if (risk === null) return null;
  const meta = RISK_META[risk];
  return (
    <span
      data-agent-risk-badge={risk}
      title={`overall risk: ${meta.label} — ${meta.why}`}
      className={`inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${meta.cls}`}
    >
      {meta.label}
    </span>
  );
}

// ToolChipsRow — one row of tool-scope chips, capped at `cap` (default 4)
// with a "+N more" affordance carrying the overflow tool names in its title
// tooltip (no popover per the brief — a title attr is enough).
export function ToolChipsRow({
  chips,
  cap = 4,
}: {
  chips: ToolChip[];
  cap?: number;
}) {
  if (chips.length === 0) return null;
  const shown = chips.slice(0, cap);
  const overflow = chips.slice(cap);

  return (
    <div data-agent-tool-chips className="flex flex-wrap items-center gap-1">
      {shown.map((c) => (
        <span
          key={c.name}
          data-tool-chip
          data-tool-chip-risk={c.risk_class}
          title={toolTooltip(c)}
          className={`inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${RISK_META[c.risk_class].cls}`}
        >
          {c.name}
        </span>
      ))}
      {overflow.length > 0 ? (
        <span
          data-tool-chip-overflow
          title={overflow.map((c) => c.name).join(", ")}
          className="inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[10px] font-medium text-zinc-500 dark:text-zinc-400"
        >
          +{overflow.length} more
        </span>
      ) : null}
    </div>
  );
}
