"""Operator-vs-AI write-authorization primitive (Kanban #1857, Phase 1 of #1852).

A pure decision function + its own JSONL audit writer + a FastAPI header
dependency. This is the platform's FIRST signal the API can use to tell
"the OPERATOR did this" apart from "an AI agent did this" — the prerequisite
#1205 §10 Q2 named and #1652 §B.3 #5 flagged as blocking.

DESIGN — see `context/projects/agent-teams/shared/design/operator-vs-ai-auth-1852.md`
(Option B: env-injected operator token; Option D framing). Phase 1 applies the
check to the #1275 `verified_by='user'` AC path only (`routers/tasks.py` PATCH).

MECHANISM
  - `OPERATOR_ACTION_KEY` lives in the **api container's env ONLY** (root `.env`
    → the `api` service `environment:` block in docker-compose.yml). It MUST NOT
    appear in the langgraph worker env nor any agent-readable env. An agent that
    wants to forge `X-Operator-Token` has nothing to forge it WITH. This is the
    one load-bearing discipline (design §7) — identical in kind to the existing
    `CREDENTIALS_MASTER_KEY` discipline the platform already lives with.
  - The operator presents the secret out-of-band via the `X-Operator-Token`
    request header. `check_operator_proof` constant-time-compares it against
    `os.environ["OPERATOR_ACTION_KEY"]`.

ACTIVATION SEMANTICS (fail-OPEN when unset — opt-in, mirrors #1799/#1474)
  - `OPERATOR_ACTION_KEY` UNSET/empty  -> gate INACTIVE. `check_operator_proof`
    returns OPERATOR for ANY token (incl. None) and a one-time WARN is logged.
    This lets the code LAND without breaking the running app: the live `.env`
    has no key yet, so existing PATCH flows are unaffected. The operator
    ACTIVATES the gate by setting the key (see the activation checklist in the
    design doc / `.env.example`).
  - `OPERATOR_ACTION_KEY` SET          -> gate ACTIVE (fail-CLOSED). A request
    is OPERATOR only when it carries a token that constant-time-matches the key;
    otherwise NOT_OPERATOR. The caller (the gated route) raises HTTP 403.

🔒 TRUST BOUNDARY (do NOT oversell — design §1.2 / §7): the threat in scope is
AGENT DRIFT / context-confusion in a SINGLE-OPERATOR system, NOT a host-level
adversary. A static shared token is the minimum-viable unspoofable identity for
that threat (no PKI, no per-payload signing — both explicitly deferred per
#1799). A drifting agent has no token at all; it is not trying to crack one. If
the token ever leaks into agent-readable scope the whole distinction collapses,
so keeping it out of the agent env is THE discipline that matters.

The module writes its OWN JSONL audit row (allow AND deny), mirroring
`services/tool_grants.py` — it does NOT touch any other audit trail.
"""

from __future__ import annotations

import datetime
import hmac
import json
import logging
import os
from enum import Enum
from pathlib import Path
from typing import Annotated

from fastapi import Header

logger = logging.getLogger(__name__)

# Env var holding the operator secret. Read at request time (not import time) so
# pytest monkeypatch.setenv toggles the gate per-test.
_OPERATOR_KEY_ENV = "OPERATOR_ACTION_KEY"

# Audit log path. Configurable via OPERATOR_AUTH_AUDIT_PATH; defaults to the same
# _scratch bind-mount the other gates write to, in a sibling file so the trails
# stay separable. Mirrors `tool_grants.py::_AUDIT_PATH`.
_AUDIT_PATH = Path(
    os.environ.get(
        "OPERATOR_AUTH_AUDIT_PATH", "/repo/_scratch/operator-auth-audit.jsonl"
    )
)

# One-time INACTIVE warning guard — module-level so the WARN is emitted once per
# process, not on every gated request (mirrors a typical inactive-gate log).
_inactive_warned = False


class OperatorDecision(str, Enum):
    """Two-way verdict returned by `check_operator_proof`.

    `str, Enum` so the value serializes straight into the audit row without
    explicit `.value` access (mirrors `tool_grants.GrantDecision`).
    """

    OPERATOR = "operator"
    NOT_OPERATOR = "not_operator"


def _gate_active() -> bool:
    """True when `OPERATOR_ACTION_KEY` is set to a non-empty value.

    Empty / unset -> gate INACTIVE (fail-open). Read live from os.environ so a
    test can toggle it via monkeypatch and the operator can activate it by
    editing `.env` + recreating the api container.
    """
    return bool(os.environ.get(_OPERATOR_KEY_ENV, "").strip())


def _evaluate(token_header: str | None) -> OperatorDecision:
    """Pure verdict: is this request backed by a valid operator proof?

    INACTIVE (key unset/empty) -> OPERATOR for any token (fail-open), with a
    one-time WARN. ACTIVE -> constant-time compare; OPERATOR iff the token
    matches the configured key, else NOT_OPERATOR.
    """
    global _inactive_warned

    key = os.environ.get(_OPERATOR_KEY_ENV, "").strip()
    if not key:
        if not _inactive_warned:
            logger.warning(
                "operator-proof gate inactive: %s unset", _OPERATOR_KEY_ENV
            )
            _inactive_warned = True
        return OperatorDecision.OPERATOR

    if not token_header:
        return OperatorDecision.NOT_OPERATOR

    # Constant-time compare — never a plain `==` on a secret (timing oracle).
    if hmac.compare_digest(token_header, key):
        return OperatorDecision.OPERATOR
    return OperatorDecision.NOT_OPERATOR


def _write_audit(decision: OperatorDecision, *, active: bool) -> None:
    """Append one JSONL audit row (decision + whether the gate was active).

    Best-effort, mirrors `tool_grants.py::_write_audit`: a write failure must
    NOT break the request, so the file write is guarded. The token itself is
    NEVER logged (it is a secret). Schema:
      {ts, decision, gate_active}
    """
    row = {
        "ts": datetime.datetime.now(datetime.UTC)
        .replace(tzinfo=None)
        .isoformat()
        + "Z",
        "decision": decision.value,
        "gate_active": active,
    }
    try:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError:
        # Audit is observability, not correctness — never let a disk hiccup
        # turn an allowed call into a 500.
        pass


def check_operator_proof(token_header: str | None) -> OperatorDecision:
    """Decide whether `token_header` proves operator presence for THIS request.

    Returns `OperatorDecision.OPERATOR` or `NOT_OPERATOR` per the activation
    semantics in the module docstring. Writes an audit row only when the gate is
    ACTIVE — inactive (fail-open) passes carry no signal worth persisting, and
    every task PATCH would append a noise row while the gate is dormant.
    The caller raises HTTP 403 on `NOT_OPERATOR`.
    """
    active = _gate_active()
    decision = _evaluate(token_header)
    if active:
        _write_audit(decision, active=active)
    return decision


async def require_operator_proof(
    x_operator_token: Annotated[
        str | None, Header(alias="X-Operator-Token")
    ] = None,
) -> OperatorDecision:
    """FastAPI dependency: extract the OPTIONAL `X-Operator-Token` header.

    Mirrors `session_project.optional_agent_role_header`'s header-extraction
    shape. Returns the `OperatorDecision` from `check_operator_proof` (which
    also writes the audit row). The header is OPTIONAL at the dependency layer —
    the gated ROUTE decides whether the absence of a valid proof is fatal (it is
    only fatal when the PATCH actually attempts a gated `verified_by` value).

    This separation matters: a PATCH that touches NO gated field must succeed
    with no token at all, so the 403 lives in the route's per-field check, not
    in this dependency.
    """
    return check_operator_proof(x_operator_token)
