"""Static in-code registry of governable Mode-A HTTP tools (Kanban #1799 P0).

Mirrors the STATIC-data pattern of `services/integrations_registry.py`: this
module is data only (tool name -> metadata), no secrets, no DB. It is the
allow-list vocabulary the `config.tool_grants` validator checks against — a
grant naming a tool absent from this registry is a typo / stale entry and is
rejected at the API boundary (422).

What an entry is:
  - `tier`    — the EXISTING `ToolTier` vocabulary (`read|write|network|
                destructive`) reused from `schemas/project.py` so a future
                convergence with the LangGraph `permission_gate` (Mode B,
                tier-based) is cheap. NOT a new enum.
  - `version` — a free-form forward-compat string ("v1"). Lets a future tool
                rev be distinguished without a schema change.

Both `tier` and `version` are FORWARD-COMPAT METADATA — the P0 `check_grant`
membership test does NOT consume them. There is DELIBERATELY no `cost_units`
key here: per-message unit costs already live as router constants
(`_TRASH_UNITS_PER_MESSAGE` etc. in `routers/tools_email.py`) and the single
combined-units cap stays the `tools/email/gate.py` daily gate. Duplicating
costs into the registry would create a second source of truth — out of P0
scope (see design doc "Registry as cost source of truth" deferral).

Tool naming convention: `<provider>.<action>` (e.g. `gmail.trash`). One entry
per governable action; add an action by adding one entry here + wiring the
gate at the handler (design doc "Add a tool in the future" checklist).

DISCOVERY is Lead-mediated (design doc): there is no agent-facing manifest
endpoint in Mode A. The Lead reads `config.tool_grants[role]` + this registry
and injects the allowed-tool spec into each spawn brief. Nothing to build for
discovery in P0 — this docstring is the discovery contract reference.
"""

from __future__ import annotations

from typing import Final, TypedDict

from src.schemas.project import ToolTier


class ToolEntry(TypedDict):
    tier: ToolTier
    version: str


# ---------------------------------------------------------------------------
# The registry. `<provider>.<action>` -> {tier, version}. Seeded with the two
# email-trash actions the P0 gate wires into (`routers/tools_email.py`).
# Both are tier `destructive` — moving mail to trash/Deleted-Items is the
# Mode-A action with the highest blast radius in today's catalog.
# ---------------------------------------------------------------------------

TOOL_REGISTRY: Final[dict[str, ToolEntry]] = {
    "gmail.trash": {"tier": "destructive", "version": "v1"},
    "outlook.trash": {"tier": "destructive", "version": "v1"},
}


def is_known_tool(tool_name: str) -> bool:
    """True iff `tool_name` is a registered governable tool."""
    return tool_name in TOOL_REGISTRY


def get_tool(tool_name: str) -> ToolEntry | None:
    """Return the registry entry for `tool_name`, or None if unknown."""
    return TOOL_REGISTRY.get(tool_name)
