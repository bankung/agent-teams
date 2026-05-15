"""Specialist-tool permission gate (Kanban #979).

Pure decision function consumed by the specialist node loop (wired by #981):
given a project's `tools_config` (already fetched from the DB) + a `Tool`
object (typically `GLOBAL_REGISTRY.get(name)`), return one of three
verdicts that the loop acts on:

  - `auto_allow` → invoke the tool with no human gate
  - `halt`       → stop the agent, surface the proposed tool call to the
                   user via halt_reason, resume on approval
  - `reject`     → refuse to invoke (master kill switch on, or tier in
                   neither allow nor halt list — the defensive default)

Design notes:

- Pure function, no DB access. The caller (specialist node) is responsible
  for fetching `projects.tools_config` once per task / session and passing
  it in. This keeps the gate trivially testable (12+ cases below) and
  decouples it from the SQLAlchemy / FastAPI machinery.
- `tools_enabled=False` is the master kill switch. Even tiers explicitly
  listed in `auto_allow_tiers` reject when the switch is off — the test
  matrix below pins this behavior.
- `None` tools_config (legacy / hand-edited row that pre-dates migration
  `0027_projects_tools_config`) is treated identically to
  `tools_enabled=False` — over-block over under-block on misconfiguration.
- Tiers in neither `auto_allow_tiers` nor `halt_tiers` fall through to
  `reject` (defensive default). The Pydantic `ToolsConfig` validator at
  the API boundary enforces the two lists are disjoint, but the gate
  itself does NOT assume the config has been Pydantic-validated — a
  hand-edited DB row with malformed lists must NOT 500 the agent loop.

See proposed standard `_scratch/standards-proposal-permission-tiers.md`
for the human-MA-bound rationale on the 4-tier taxonomy.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from .base import Tool


class PermissionDecision(str, Enum):
    """Three-way verdict returned by `check_permission`.

    `str, Enum` so the value can be serialized into the audit log (#980)
    and tool-call halt_reason metadata without explicit `.value` access.
    """

    AUTO_ALLOW = "auto_allow"
    HALT = "halt"
    REJECT = "reject"


def check_permission(
    tools_config: dict[str, Any] | None,
    tool: Tool,
) -> PermissionDecision:
    """Decide whether `tool` is allowed to invoke under `tools_config`.

    Lookup order:

    1. If `tools_config` is None or `tools_enabled` is not truthy → REJECT.
       Master kill switch. The agent gets a "tools disabled for this
       project" error back; the user enables tools via the FE config UI
       (gated by #943) by PATCHing `tools_enabled=true`.
    2. If `tool.tier.value` is in `auto_allow_tiers` → AUTO_ALLOW.
    3. Else if `tool.tier.value` is in `halt_tiers` → HALT.
    4. Else → REJECT (defensive default — covers (a) tiers in neither
       list, (b) malformed lists from a hand-edited row, (c) any future
       Tier enum additions that haven't been added to either list yet).

    The gate is intentionally permissive about config SHAPE (any
    dict-like is accepted) but strict about ABSENCE: missing or non-list
    `auto_allow_tiers` / `halt_tiers` keys are treated as empty lists,
    which after step 2/3 cascade to step 4 (REJECT). This mirrors the
    "over-block over under-block" policy: a corrupted config should NOT
    silently auto-allow.
    """
    if not tools_config:
        return PermissionDecision.REJECT
    if not tools_config.get("tools_enabled"):
        return PermissionDecision.REJECT

    tier_value = tool.tier.value

    auto_allow = tools_config.get("auto_allow_tiers") or []
    if isinstance(auto_allow, list) and tier_value in auto_allow:
        return PermissionDecision.AUTO_ALLOW

    halt = tools_config.get("halt_tiers") or []
    if isinstance(halt, list) and tier_value in halt:
        return PermissionDecision.HALT

    return PermissionDecision.REJECT
