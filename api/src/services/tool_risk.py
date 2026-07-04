"""Tool-scope risk classification (Kanban #1021).

Single-source table mapping a frontmatter ``tools:`` entry name to a risk
class, so the gallery API (``AgentSummary.tool_chips`` / ``AgentDetail.tool_chips``)
can expose "how dangerous is this agent's tool grant" without every consumer
re-deriving the taxonomy.

Classes (exactly these five strings; contract-locked):
  * ``read-only``            — Read, Grep, Glob, WebFetch, WebSearch.
  * ``write-edit``           — Write, Edit, NotebookEdit.
  * ``shell-or-destructive`` — Bash, PowerShell; ALSO the fail-closed default
    for any tool name not explicitly listed below (see ``classify_tool``).
  * ``external``             — any tool whose name starts with ``mcp__`` or
    ``MCP_`` (third-party/external-service surface).
  * ``always-safe``          — TodoWrite only.

Fail-closed by design: an unrecognized tool name is classified
``shell-or-destructive`` (the highest-risk bucket), never silently treated as
safe. A risk indicator that under-reports is worse than one that over-reports
— a false "looks safe" chip on a tool nobody has audited yet would defeat the
whole point of this surface.
"""

from __future__ import annotations

from src.schemas.agent_metadata import ALL_TOOLS_LITERAL

RiskClass = str  # one of the five literals documented above

# Explicit per-tool classification. Covers every literal tool name actually
# found in `.claude/agents/*.md` `tools:` frontmatter as of #1021 (Read, Grep,
# Glob, Bash, Write, Edit, WebFetch, WebSearch), PLUS the wider tool families
# named in the #1021 brief that no file lists explicitly today but that a
# future frontmatter could — classifying them now means a new file doesn't
# silently fall through to a judgment call later.
#
# NOT the same set as `agent_metadata.KNOWN_TOOLS` (the validator's "is this
# name familiar, warn if not" list) — that list tracks WHICH names are
# recognized; this one tracks HOW RISKY a name is, and the two purposes are
# not required to produce identical sets. The one invariant that DOES hold
# (Kanban #1021, dev-reviewer lockstep fix): every key here MUST already be
# in KNOWN_TOOLS — a name the risk classifier confidently classifies should
# never also trip the validator's "unrecognized tool" WARNING. That subset
# relationship is a ONE-WAY containment (TOOL_RISK_TABLE keys ⊆ KNOWN_TOOLS,
# not equality — KNOWN_TOOLS may carry names this table has no opinion on
# yet), enforced by
# `test_agent_tool_chips.test_risk_table_names_are_known_tools`, not a prod
# import between the two modules (keeps the schema/service layering clean).
TOOL_RISK_TABLE: dict[str, RiskClass] = {
    # --- read-only: inspection, no mutation, no external side effect -------
    "Read": "read-only",
    "Grep": "read-only",
    "Glob": "read-only",
    "WebFetch": "read-only",
    "WebSearch": "read-only",
    # --- write-edit: mutates local files, no shell/network reach -----------
    "Write": "write-edit",
    "Edit": "write-edit",
    "NotebookEdit": "write-edit",
    # --- shell-or-destructive: arbitrary command execution ------------------
    "Bash": "shell-or-destructive",
    "PowerShell": "shell-or-destructive",
    # --- always-safe: state bookkeeping, no filesystem/shell/network reach --
    "TodoWrite": "always-safe",
    # --- judgment calls (not present in any current tools: list; see below) -
    # Agent/Task spawn a subagent — that subagent may itself carry Bash/Write,
    # so the SPAWNING capability is treated as shell-or-destructive rather
    # than assuming the child is scoped down.
    "Agent": "shell-or-destructive",
    "Task": "shell-or-destructive",
    # Skill executes a packaged skill's own tool calls (same transitive-risk
    # reasoning as Agent/Task above) — shell-or-destructive.
    "Skill": "shell-or-destructive",
    # SendMessage resumes another agent session and can cause it to take
    # further action — same transitive-risk reasoning as Agent/Task/Skill
    # above (dev-reviewer catch, #1021: grouping it with the two genuinely
    # inert primitives below under-reported its risk).
    "SendMessage": "shell-or-destructive",
    # AskUserQuestion / TaskStop are interaction/control-flow primitives with
    # no filesystem/shell/network/inter-agent reach of their own — classified
    # read-only (they only ever surface a prompt to the user or halt the
    # CURRENT agent; they cannot mutate a file, run a shell command, reach an
    # external service, or cause another agent to act).
    "AskUserQuestion": "read-only",
    "TaskStop": "read-only",
}

# Pseudo-entry for the frontmatter shorthand meaning "every tool available":
# the `"All tools"` literal, or the `tools:` key omitted entirely. Also what
# `build_tool_chips` falls back to for any OTHER off-spec value (e.g. a stray
# `"*"`, or a non-list/non-string the YAML parser produced) — there is no
# name-matched special case for those; they share this same generic
# non-list branch (mirrors `_summarize_tools`'s existing "malformed → treated
# as all tools" convention, see agent_validation.py). Highest risk by
# construction — it is a superset of everything else in this table,
# including shell-or-destructive. Name matches ALL_TOOLS_LITERAL exactly so
# the chip's displayed text is the same string the rest of the gallery API
# already uses for "all tools" (tools_summary).
ALL_TOOLS_CHIP: dict[str, str] = {
    "name": ALL_TOOLS_LITERAL,
    "risk_class": "shell-or-destructive",
}


def classify_tool(tool_name: str) -> RiskClass:
    """Classify one tool name. Fail-closed: unknown → ``shell-or-destructive``.

    ``mcp__`` / ``MCP_`` prefixed names (any external/third-party MCP server
    tool) are ``external`` regardless of whether the exact name is in the
    table — the MCP tool universe is open-ended and cannot be enumerated
    here.
    """
    if tool_name.startswith("mcp__") or tool_name.startswith("MCP_"):
        return "external"
    return TOOL_RISK_TABLE.get(tool_name, "shell-or-destructive")


def build_tool_chips(
    tools: list[str] | str | None,
) -> list[dict[str, str]]:
    """Derive ``tool_chips`` from an already-parsed frontmatter ``tools`` value.

    Mirrors the three shapes ``_summarize_one_file`` / ``AgentDetail.tools``
    already carry:
      * ``list[str]``            — one chip per entry, FRONTMATTER ORDER
        preserved (no sorting).
      * the ``"All tools"`` literal, or ``None`` (key absent = inherit all) —
        the single ``ALL_TOOLS_CHIP`` pseudo-entry.
      * anything else (a malformed value the validator already flagged as an
        ERROR) — treated the same as ``None`` so this function never raises;
        the gallery's existing "invalid file still renders a row" posture
        extends to chips too.
    """
    if isinstance(tools, list):
        return [{"name": t, "risk_class": classify_tool(t)} for t in tools]
    # ALL_TOOLS_CHIP is returned directly (no defensive copy): it is a
    # module-level constant of two strings and nothing downstream (Pydantic
    # serialization) mutates the dict it is handed.
    return [ALL_TOOLS_CHIP]
