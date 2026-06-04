"""Per-agent-name tool authorization gate for Mode-A HTTP tools (Kanban #1799 P0).

A pure decision function + its own audit writer. Wired into the
`/api/tools/email/{gmail,outlook}/trash` handlers (`routers/tools_email.py`).

RELATIONSHIP TO THE OTHER GATES (do not conflate — design doc):
  - This is a DIFFERENT, COMPLEMENTARY layer to
    `langgraph/tools/permission_gate.check_permission()` (Mode B, tier-based,
    gates LangGraph specialist tools). Both are LEFT UNTOUCHED by #1799. P0 is
    per-AGENT-NAME membership for the Mode-A HTTP tool world.
  - It is ORTHOGONAL to `tools/email/gate.py`'s daily-units cap
    (`check_and_increment`), which stays the single combined-units gate. P0
    adds NO per-role units.

Styled after `permission_gate.check_permission` (pure, no DB, trivially
testable) and follows the grant+refusal-audit precedent of
`routers/credentials.py::_policy_grants_use` (the audit trail covers BOTH
allow AND deny).

GRANT STORE — `config.tool_grants` (the free-form `projects.config` JSONB where
`enabled_roles` already lives; NOT `tools_config`, whose `ToolsConfig` model is
`extra="forbid"` and would 422 on an extra subkey). Shape:

    { "<agent-type-name>": ["<tool_name>", ...] }

`role` is an AGENT-TYPE-NAME STRING (`secretary`, `dev-backend`, ...),
cross-team — NOT the int role codes `enabled_roles` uses. Membership-only:
presence of a tool in a role's list = allowed.

ENFORCEMENT SEMANTICS (opt-in restriction — design doc "RESOLVED"):
  - `tool_grants` key ABSENT          -> ALLOW  (unrestricted — default)
  - role NOT a key in `tool_grants`   -> ALLOW  (opt-in: only listed roles are
                                                 restricted)
  - role IS a key, tool IN its list   -> ALLOW
  - role IS a key, tool NOT in list   -> DENY   (caller raises HTTP 403)
  - role IS a key, list is EMPTY      -> DENY for every tool (explicit lockout)

  Absent role signal (`role is None`) is treated as "no role to restrict" ->
  ALLOW. The trust boundary below explains why this is acceptable in Mode A.

🔒 TRUST BOUNDARY: the role arrives via the OPTIONAL, SPOOFABLE `X-Agent-Role`
header (see `session_project.optional_agent_role_header`). A hard 403 here
therefore stops AGENT DRIFT/CONFUSION (the real Mode-A threat in a
single-operator system) — it does NOT stop a malicious agent, which is OUT of
the Mode-A threat model. The fully-enforced wall in Mode A remains the Claude
Code layer (per-agent `tools:` list + hooks + settings.json allow-list).
`config.tool_grants` becomes a hard wall against malice only once unspoofable
identity exists (Mode B). Do not oversell it.

The gate writes its OWN JSONL audit row (role + tool + decision) — it does NOT
touch `gate.py`'s FROZEN `log_audit` (interface frozen by #1604/#1608).
"""

from __future__ import annotations

import datetime
import json
import os
from enum import Enum
from pathlib import Path
from typing import Any


class GrantDecision(str, Enum):
    """Two-way verdict returned by `check_grant`.

    `str, Enum` so the value serializes straight into the audit row without
    explicit `.value` access (mirrors `permission_gate.PermissionDecision`).
    """

    ALLOW = "allow"
    DENY = "deny"


# Audit log path. Configurable via TOOL_GRANTS_AUDIT_PATH; defaults to
# /repo/logs/ (durable, outside _scratch which is gitignored and excluded from
# the nightly backup tarball). Override with TOOL_GRANTS_AUDIT_PATH env var.
# (#1848 NIT-2: moved from _scratch/ to a durable sink).
_AUDIT_PATH = Path(
    os.environ.get(
        "TOOL_GRANTS_AUDIT_PATH", "/repo/logs/tool-grants-audit.jsonl"
    )
)


def _evaluate(
    config: dict[str, Any] | None, role: str | None, tool_name: str
) -> GrantDecision:
    """Pure membership evaluation — the enforcement table in the module docstring.

    Intentionally permissive about config SHAPE (a hand-edited row whose
    `tool_grants` is not a dict, or whose role value is not a list, must NOT
    500 the request) but applies "absent/unknown -> allow" because P0
    enforcement is OPT-IN: a corrupted or unexpected shape should not silently
    LOCK OUT a role that the operator never meant to restrict.
    """
    if not config or not isinstance(config, dict):
        return GrantDecision.ALLOW

    grants = config.get("tool_grants")
    if not isinstance(grants, dict):
        # Key absent, or present-but-malformed -> unrestricted.
        return GrantDecision.ALLOW

    if role is None or role not in grants:
        # No role signal, or this role is not listed -> opt-in: unrestricted.
        return GrantDecision.ALLOW

    allowed = grants.get(role)
    if not isinstance(allowed, list):
        # Role key present but its value is malformed (not a list). The
        # boundary validator rejects this on write; a hand-edited row that
        # slips through is treated as "restricted role, nothing allowed" —
        # over-block beats under-block once a role IS explicitly listed.
        return GrantDecision.DENY

    return GrantDecision.ALLOW if tool_name in allowed else GrantDecision.DENY


def _write_audit(
    project_id: int | None,
    role: str | None,
    tool_name: str,
    decision: GrantDecision,
) -> None:
    """Append one JSONL audit row (role + tool + decision).

    Best-effort, mirrors `tools/email/gate.py::log_audit`: a write failure must
    NOT break the request, so the file write is guarded. Schema:
      {ts, project_id, role, tool, decision}
    """
    row = {
        "ts": datetime.datetime.now(datetime.UTC)
        .replace(tzinfo=None)
        .isoformat()
        + "Z",
        "project_id": project_id,
        "role": role,
        "tool": tool_name,
        "decision": decision.value,
    }
    try:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError:
        # Audit is observability, not correctness — never let a disk hiccup
        # turn an allowed call into a 500.
        pass


def check_grant(
    config: dict[str, Any] | None,
    role: str | None,
    tool_name: str,
    *,
    project_id: int | None = None,
) -> GrantDecision:
    """Decide whether `role` may call `tool_name` under `config.tool_grants`.

    Returns `GrantDecision.ALLOW` or `GrantDecision.DENY` per the enforcement
    table in the module docstring. ALWAYS writes an audit row (for BOTH allow
    and deny) before returning — the caller raises HTTP 403 on `DENY`.

    `project_id` is audit-only (it scopes the JSONL row); it does NOT affect
    the decision.
    """
    decision = _evaluate(config, role, tool_name)
    _write_audit(project_id, role, tool_name, decision)
    return decision
