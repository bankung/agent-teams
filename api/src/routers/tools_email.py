"""Email tools router (Kanban #1604 Gmail; #1608 appends Outlook below marker).

Mounts under `/api/tools/email/*`. All endpoints require the `X-Project-Id`
header (via `require_project_id_header`) so the daily-units cap, token store,
and audit log are scoped per project.

Endpoint surface (Gmail):
  POST /auth/gmail/start    → returns {auth_url}
  GET  /auth/gmail/callback → exchanges code, stores creds, returns JSON
  GET  /auth/gmail/status   → {authenticated, email, expires_at}
  POST /gmail/trash         → trash by query OR explicit message_ids
  GET  /gmail/usage         → daily-units counter snapshot

Karpathy cuts:
  - Router calls `gmail_client` directly — no service layer.
  - In-memory token store; lost on container restart.
  - Callback returns JSON, not a redirect.

Coordination with #1608 (Outlook): #1608 appends Outlook routes BELOW the
`# >>> #1608 OUTLOOK ROUTES BELOW` marker at the bottom of this file.
DO NOT touch their section from this router; the two namespaces share only
the `router` object + helpers above the marker.
"""

from __future__ import annotations

import datetime
import functools
import json
import logging
import os
import re
from enum import Enum
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from src.constants import RecordStatus, TaskStatus, TaskType
from src.db import get_session
from src.models.project import Project
from src.models.task import Task
from src.schemas.tools_email import (
    AuthStatusResponse,
    GmailArchiveRequest,
    GmailAttachmentRequest,
    GmailAttachmentResponse,
    GmailAuthStartResponse,
    GmailCallbackResponse,
    GmailDraftRequest,
    GmailDraftResponse,
    GmailForwardRequest,
    GmailReplyRequest,
    GmailSendRequest,
    GmailSendResponse,
    GmailGetRequest,
    GmailGetResponse,
    GmailLabel,
    GmailLabelsRequest,
    GmailLabelsResponse,
    GmailMarkRequest,
    GmailModifyResponse,
    GmailSearchRequest,
    GmailSearchResponse,
    GmailSearchItem,
    GmailThreadMessage,
    GmailThreadRequest,
    GmailThreadResponse,
    GmailTrashRequest,
    GmailTrashResponse,
    OutlookArchiveRequest,
    OutlookAuthStartResponse,
    OutlookCallbackResponse,
    OutlookDraftRequest,
    OutlookDraftResponse,
    OutlookForwardRequest,
    OutlookReplyRequest,
    OutlookSendRequest,
    OutlookSendResponse,
    OutlookGetRequest,
    OutlookGetResponse,
    OutlookMarkRequest,
    OutlookModifyResponse,
    OutlookSearchRequest,
    OutlookSearchResponse,
    OutlookSearchItem,
    OutlookTrashRequest,
    OutlookTrashResponse,
    UsageResponse,
)
from src.services.session_project import (
    optional_agent_role_header,
    require_project_id_header,
)
from src.services.operator_auth import OperatorDecision, _gate_active, require_operator_proof
from src.services.tool_grants import GrantDecision, check_grant
from src.tools.email import (
    gate,
    gmail_client,
    outlook_client,
    token_store,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools/email", tags=["tools-email"])

# Cost constants — sourced from Gmail API quota reference (research note
# 2026-05-27 in `_scratch/research-email-tools-2026-05-27.md`).
_TRASH_UNITS_PER_MESSAGE = 20
_LIST_UNITS_PER_CALL = 5  # rough; we charge once per list invocation.

# Hard cap on list-page expansion when resolving a `query`. The bulk-threshold
# gate then applies on the resolved count — this is a separate ceiling so a
# user with `?force=true` still can't accidentally enumerate a 50k-id inbox.
_MAX_LIST_RESULTS = 1000


# ---------------------------------------------------------------------------
# Tool-governance gate (Kanban #1799 P0)
# ---------------------------------------------------------------------------

# 403 detail for a tool-grant denial. Stable string (a future source-text-lock
# test can scan for it). The role + tool are interpolated so the agent's log
# shows exactly which (role, tool) pair was refused.
_DETAIL_TOOL_GRANT_DENIED_TEMPLATE = (
    "tool_grant_denied: role {role!r} is not granted tool {tool!r} for this "
    "project (config.tool_grants). Ask the Lead to add it to the role's "
    "allow-list, or call without an X-Agent-Role header if this role should "
    "be unrestricted."
)


async def _enforce_tool_grant_or_403(
    session: AsyncSession,
    session_project_id: int,
    role: str | None,
    tool_name: str,
) -> None:
    """Run the #1799 per-agent-name tool-governance gate; raise 403 on deny.

    Reads the project's `config` (the JSONB holding `tool_grants`) and calls
    the pure `services.tool_grants.check_grant`. Enforcement is OPT-IN and
    defaults UNRESTRICTED: `tool_grants` absent, or `role` unlisted, or no
    role header -> ALLOW. Only an explicitly-listed role missing the tool ->
    403. The gate writes its own audit row for BOTH allow and deny.

    Wired at the START of the trash handlers, AFTER role resolution and BEFORE
    the daily-cap / bulk / auth gates — a forbidden role should be turned away
    before any OAuth/quota work. A missing project row is treated as
    unrestricted (None config -> ALLOW) rather than 404: the existing handler
    flow never 404s on the project, and the cross-project guard already lives
    in `require_project_id_header`'s session binding.
    """
    config = (
        await session.execute(
            select(Project.config)
            .where(Project.id == session_project_id)
            .where(Project.status == RecordStatus.ACTIVE)
        )
    ).scalar_one_or_none()

    decision = check_grant(
        config, role, tool_name, project_id=session_project_id
    )
    if decision is GrantDecision.DENY:
        # Cap the reflected role at 64 chars so the spoofable X-Agent-Role
        # header is never echoed verbatim into a 403 body (#1848 NIT-1).
        safe_role = (role or "")[:64]
        raise HTTPException(
            status_code=403,
            detail=_DETAIL_TOOL_GRANT_DENIED_TEMPLATE.format(
                role=safe_role, tool=tool_name
            ),
        )


# ---------------------------------------------------------------------------
# Operator-proof tier gate (Kanban #1859 — Phase 3 of #1852)
# ---------------------------------------------------------------------------
#
# COMPOSES ON TOP OF the #1799 Layer-0 grant gate above — it does NOT replace
# it. Layer-0 answers "WHICH role may call this tool"; this gate answers
# "is the OPERATOR present for THIS write". The two are orthogonal: a call must
# pass Layer-0 first, then (for tiers above `read`) carry an operator-proof.
#
# Tier model (per #1852 design §5 / #1859 AC):
#   read           OPEN  — Layer-0 grant only, no operator-proof.
#   reply          PROOF — operator-proof required; 403 if absent.
#   send_internal  PROOF — operator-proof required; 403 if absent.
#   delete         PROOF — operator-proof required; 403 if absent. (trash = delete-class)
#   external_send  ESCALATE — operator-proof + 202 HALT pending out-of-band confirm.
#
# FAIL-OPEN when unset: `require_operator_proof` returns OPERATOR for any request
# when OPERATOR_ACTION_KEY is unset (gate INACTIVE), so this 403 is DORMANT on the
# live deployment (no key in .env yet) and existing trash flows are unaffected.
# The operator ACTIVATES by setting the key + presenting X-Operator-Token. This is
# the SAME activation discipline as the #1857 Phase-1 verified_by gate.


class EmailTier(str, Enum):
    """Operator-proof requirement tier for an email action.

    `str, Enum` so the value serializes straight into the audit/detail strings
    (mirrors `operator_auth.OperatorDecision` / `tool_grants.GrantDecision`).
    """

    READ = "read"  # OPEN — no operator-proof.
    # Tier-1 mutations (mark-read/unread/archive/draft) — recoverable, so OPEN:
    # Layer-0 role-gated + audited, but NO operator-proof (NOT in _PROOF_REQUIRED_TIERS).
    MODIFY = "modify"
    REPLY = "reply"  # PROOF.
    SEND_INTERNAL = "send_internal"  # PROOF.
    DELETE = "delete"  # PROOF (trash maps here).
    EXTERNAL_SEND = "external_send"  # PROOF + out-of-band confirm.


# Tiers that require an operator-proof (everything above `read`). `read` and
# `modify` are OPEN. Frozenset for O(1) membership + immutability.
_PROOF_REQUIRED_TIERS = frozenset(
    {
        EmailTier.REPLY,
        EmailTier.SEND_INTERNAL,
        EmailTier.DELETE,
        EmailTier.EXTERNAL_SEND,
    }
)


# ---------------------------------------------------------------------------
# Secretary-action audit sink (Kanban #1585 AC5/AC8)
# ---------------------------------------------------------------------------
#
# A SECOND, action-level JSONL trail, distinct from `gate.log_audit` (which
# records the daily-units accounting per provider call). This one captures the
# OPERATOR-FACING shape of every Tier-1/2 email action: who (agent_role), what
# (action), at which tier, on which messages, under which approval mode, and the
# result. One line per action. Best-effort guarded write — mirrors
# `services/tool_grants.py::_write_audit`: a disk hiccup must NEVER turn a
# successful action into a 500. Rotation/gzip is OUT OF SCOPE (follow-up).

# Configurable via EMAIL_ACTIONS_AUDIT_PATH; defaults to the _runtime bind-mount
# (durable across container restarts; same mount the lead_project_id file uses).
_EMAIL_ACTIONS_PATH = Path(
    os.environ.get("EMAIL_ACTIONS_AUDIT_PATH", "/repo/_runtime/email-actions.jsonl")
)


def _write_action_audit(
    *,
    agent_role: str | None,
    action: str,
    tier: EmailTier,
    message_ids: list[str],
    approval_mode: str,
    result: str,
) -> None:
    """Append one JSONL secretary-action audit row (AC5/AC8).

    Best-effort, guarded (mirrors tool_grants._write_audit): never breaks the
    request. Schema:
      {ts, agent_role, action, tier, message_ids, approval_mode, result}
    """
    row = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "agent_role": agent_role,
        "action": action,
        "tier": tier.value,
        "message_ids": list(message_ids),
        "approval_mode": approval_mode,
        "result": result,
    }
    try:
        _EMAIL_ACTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _EMAIL_ACTIONS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError as exc:
        # Audit is observability, not correctness — never let a disk hiccup
        # turn a successful action into a 500.
        logger.warning("_write_action_audit: write failed (best-effort): %s", exc)


def _resolve_approval_mode() -> str:
    """Return the actual approval_mode string for a proof-gated action audit row.

    Three outcomes for proof-required tiers (DELETE, REPLY, SEND_INTERNAL):
      - Gate ACTIVE + OPERATOR proof presented -> "operator_proof" (verified).
      - Gate ACTIVE + NOT_OPERATOR           -> action is rejected (403) before
        this helper is called; never reaches _write_action_audit. The
        operator_proof parameter is therefore structurally unreachable here —
        the 403 fires before the audit path, so the parameter carried no
        information and has been removed.
      - Gate INACTIVE (OPERATOR_ACTION_KEY unset, fail-open) -> "dormant".

    The MODIFY tier ("auto") and the EXTERNAL_SEND tier ("operator_confirm") are
    handled at the call sites and do NOT go through this helper.
    """
    if _gate_active():
        return "operator_proof"
    return "dormant"


# ---------------------------------------------------------------------------
# D5 Tier-3 audit — Kanban action-log task per EXECUTED send (Kanban #2100 AC[2])
# ---------------------------------------------------------------------------
#
# Every EXECUTED Tier-3 send (reply/forward/send-internal/external-send that
# actually fires) creates a Kanban action-log task IN-PROCESS via the ORM (NOT
# an HTTP curl) — mirrors the in-process child INSERT in services/handoff_spawn.py
# (Task(...) + session.add). project = the request's session-bound project. This
# is ADDITIVE to the _write_action_audit JSONL row (D5 leaves both trails).

# Cap how much of the body is embedded in the audit task description — the full
# body may be large + private-ish; an excerpt is enough for an operator trail.
_AUDIT_BODY_EXCERPT_CHARS = 500

# #2100 WARN-3 — non-printable strip for the D5 audit excerpt. The body is
# operator/agent-supplied and lands in a Kanban task description (an LLM-facing
# surface). Mirrors the #2136 convention (nodes.py:1517 / sandbox.py:90): keep
# ASCII-printable (\x20-\x7E) + the Thai block (U+0E00-U+0E7F = ฀-๿), replace
# everything else (control chars, injected ANSI/CR/LF) with '?'. Applied BEFORE
# the 500-char cap so the cap counts post-sanitize chars.
_NON_PRINTABLE_RE = re.compile(r"[^\x20-\x7E฀-๿]")


async def _write_send_audit_task(
    session: AsyncSession,
    *,
    session_project_id: int,
    provider: str,
    action: str,
    recipient: str,
    subject: str,
    body: str,
) -> None:
    """Create a Kanban [email-audit] chore task for one EXECUTED Tier-3 send (D5).

    The Task is added to the request session and COMMITTED — the send already
    fired (mail is out), so the audit row must be durable, not roll back when the
    request-scoped session closes (get_session does NOT auto-commit). A commit
    failure is caught + logged: an audit-row failure must NEVER turn a successful
    send into a 500 (a missing audit is strictly better than a 500 after the mail
    already went out). We roll back on failure so the session is left clean.

    Description carries recipient + subject + body-excerpt + timestamp so the
    operator can review what was sent without leaving Kanban.
    """
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # #2100 WARN-3 — strip non-printable/control chars (keep ASCII-printable +
    # Thai, per #2136) BEFORE the cap so an injected control sequence can't
    # pollute the Kanban task description (an LLM-facing surface).
    safe_body = _NON_PRINTABLE_RE.sub("?", body)
    excerpt = safe_body[:_AUDIT_BODY_EXCERPT_CHARS]
    if len(safe_body) > _AUDIT_BODY_EXCERPT_CHARS:
        excerpt += "…"
    description = (
        f"Tier-3 {provider} {action} executed at {ts}.\n\n"
        f"Recipient: {recipient}\n"
        f"Subject: {subject}\n"
        f"Body excerpt:\n{excerpt}\n"
    )
    audit_task = Task(
        project_id=session_project_id,
        title=f"[email-audit] {provider} {action} → {recipient[:120]}",
        description=description,
        process_status=TaskStatus.DONE,  # an action-LOG row, already-happened.
        task_type=TaskType.CHORE,
        task_kind="ai",
    )
    session.add(audit_task)
    try:
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — audit is observability, not correctness.
        logger.warning(
            "_write_send_audit_task: commit failed (best-effort) for project=%d %s.%s: %s",
            session_project_id, provider, action, type(exc).__name__,
        )
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Internal-recipient enforcement for send-internal (Kanban #2100 WARN-1)
# ---------------------------------------------------------------------------
#
# send-internal is the LOWER-blast send tier: it requires an operator-proof but
# does NOT escalate via the out-of-band 202 HALT that external-send does. But
# the route enforced NOTHING about the recipient being internal — an agent with a
# SEND_INTERNAL proof could mail ANY external address through it, bypassing the
# external-send confirm. This gate closes that downgrade.
#
# ACTIVATION (mirrors the OPERATOR_ACTION_KEY dormant-warning posture, #2045):
#   INTERNAL_EMAIL_DOMAIN SET   -> ACTIVE. Every address in to/cc/bcc on a
#     send-internal call MUST end with '@<domain>' (case-insensitive). Any
#     mismatch -> 403 {"error":"recipient_not_internal", ...} BEFORE any provider
#     call or audit write. (Route an external recipient through /external-send.)
#   INTERNAL_EMAIL_DOMAIN UNSET -> DORMANT. One-time startup-style WARNING is
#     logged (module-level guard, like operator_auth._inactive_warned) and the
#     call falls through (single-operator back-compat — the live .env has no
#     domain yet, so existing send-internal flows are unaffected).
#
# Read at call time (not import time) so pytest monkeypatch.setenv toggles it.
_INTERNAL_DOMAIN_ENV = "INTERNAL_EMAIL_DOMAIN"

# One-time DORMANT warning guard — emit the WARN once per process, not per call
# (mirrors operator_auth._inactive_warned).
_internal_domain_dormant_warned = False

# Source-text-locked: scannable stable error key for the recipient downgrade 403.
_DETAIL_RECIPIENT_NOT_INTERNAL = (
    "recipient_not_internal: send-internal requires every recipient (to/cc/bcc) "
    "to be under INTERNAL_EMAIL_DOMAIN; one or more addresses are external. Route "
    "external recipients through /external-send (which adds the out-of-band confirm)."
)


def _extract_addresses(line: str | None) -> list[str]:
    """Parse a recipient line into bare email addresses.

    Uses stdlib `email.utils.getaddresses` so comma-separated and display-name
    forms (`Name <a@b.com>, c@d.com`) both resolve to their addr-specs. Empty /
    None -> []. The returned addresses are lowercased for case-insensitive
    domain comparison.
    """
    from email.utils import getaddresses

    if not line or not line.strip():
        return []
    return [addr.lower() for _name, addr in getaddresses([line]) if addr]


def _enforce_internal_recipients_or_403(
    *, to: str, cc: str | None, bcc: str | None
) -> None:
    """#2100 WARN-1 — refuse a send-internal whose recipients aren't all internal.

    When INTERNAL_EMAIL_DOMAIN is SET, every address in to/cc/bcc must end with
    '@<domain>' (case-insensitive). Any mismatch (or any unparseable address that
    lacks the suffix) raises HTTP 403 with the stable `recipient_not_internal`
    detail. When UNSET, logs a one-time dormant WARNING and returns (fall-through).

    Provider-agnostic (both Gmail + Outlook send-internal call this). MUST be
    invoked BEFORE any provider call or audit write so a downgrade attempt leaves
    no mail sent and no audit trail (the 403 is the only side effect besides the
    one-time dormant warning).
    """
    global _internal_domain_dormant_warned

    domain = os.environ.get(_INTERNAL_DOMAIN_ENV, "").strip().lower()
    if not domain:
        if not _internal_domain_dormant_warned:
            logger.warning(
                "internal-recipient gate dormant: %s unset — send-internal does "
                "NOT enforce recipient domain (single-operator back-compat)",
                _INTERNAL_DOMAIN_ENV,
            )
            _internal_domain_dormant_warned = True
        return

    suffix = f"@{domain}"
    addresses = (
        _extract_addresses(to)
        + _extract_addresses(cc)
        + _extract_addresses(bcc)
    )
    # An empty parse (no resolvable address) is itself a mismatch — fail closed
    # rather than letting an unparseable line slip through the internal gate.
    if not addresses or any(not addr.endswith(suffix) for addr in addresses):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "recipient_not_internal",
                "message": _DETAIL_RECIPIENT_NOT_INTERNAL,
            },
        )


# Source-text-locked: pinned by the #1859 smoke tests (verbatim detail assert).
# The `tier` is interpolated so the agent's log shows exactly which tier was
# refused. Mirrors the stable-string convention of the Layer-0 denial above.
_DETAIL_OPERATOR_PROOF_REQUIRED_TEMPLATE = (
    "operator_proof_required: email tier {tier!r} is above 'read' and requires "
    "an operator-proof (X-Operator-Token). The #1799 role grant answers WHICH "
    "role; this gate answers OPERATOR-PRESENT. Present the operator token "
    "out-of-band, or call a read-tier endpoint if no mutation is intended."
)

# Source-text-locked: pinned by the #1859 external-send escalation test.
_DETAIL_EXTERNAL_SEND_CONFIRM_PENDING = (
    "operator_confirm_pending: external-send is the highest-blast tier and "
    "requires out-of-band operator confirmation. Re-issue carrying "
    "X-Operator-Token after approving to resume. "
    "HALT semantics mirror the HITL interrupt/resume loop."
)


def _enforce_operator_tier_or_403(
    tier: EmailTier, operator_proof: OperatorDecision
) -> None:
    """Run the #1859 tiered operator-proof gate; raise 403 on a missing proof.

    Wired AFTER `_enforce_tool_grant_or_403` (Layer-0) in the gated handlers.
    `read`-tier actions are OPEN (no-op here). Any tier in
    `_PROOF_REQUIRED_TIERS` requires `operator_proof is OperatorDecision.OPERATOR`
    — otherwise 403 with a stable detail.

    The proof is resolved by the `require_operator_proof` FastAPI dependency,
    which fail-OPENS (returns OPERATOR for any request) when OPERATOR_ACTION_KEY
    is unset, so this gate is dormant on the live deployment until the operator
    activates it. The `external_send` tier is handled by
    `_escalate_external_send_or_202` (push confirm) rather than a bare 403 — call
    that helper instead of this one for external sends.
    """
    if tier not in _PROOF_REQUIRED_TIERS:
        return
    if operator_proof is not OperatorDecision.OPERATOR:
        raise HTTPException(
            status_code=403,
            detail=_DETAIL_OPERATOR_PROOF_REQUIRED_TEMPLATE.format(tier=tier.value),
        )


def _escalate_external_send_or_202(
    operator_proof: OperatorDecision,
    *,
    project_id: int,
    summary: str,
) -> None:
    """Highest-blast (`external_send`) escalation: operator out-of-band confirm gate.

    Reuses the HITL HALT/resume SEMANTICS (the request HALTS with a 202 +
    `halt_reason`, mirroring the interrupt/resume loop) WITHOUT a new DB row:
    the operator resumes by re-issuing the call carrying a valid
    `X-Operator-Token`, which makes `operator_proof is OPERATOR` true on the
    retry and lets this helper pass through. No pending-confirm table is needed
    for the single-operator MVP (see report — migration explicitly NOT taken).

    Flow:
      - operator_proof IS OPERATOR  -> the operator already approved out-of-band
        (presented the token); pass through (the caller proceeds with the send).
      - operator_proof NOT OPERATOR -> raise HTTP 202 with
        `halt_reason=operator_confirm_required`. The caller does NOT proceed;
        the external send is HALTED pending the out-of-band confirmation.
    """
    if operator_proof is OperatorDecision.OPERATOR:
        return

    raise HTTPException(
        status_code=202,
        detail={
            "error": "operator_confirm_required",
            "halt_reason": "operator_confirm_required",
            "message": _DETAIL_EXTERNAL_SEND_CONFIRM_PENDING,
        },
    )


# ---------------------------------------------------------------------------
# Gmail — OAuth dance
# ---------------------------------------------------------------------------


@router.post("/auth/gmail/start", response_model=GmailAuthStartResponse)
async def gmail_auth_start(
    session_project_id: int = Depends(require_project_id_header),
) -> GmailAuthStartResponse:
    """Begin the Gmail OAuth flow. Returns the URL to open in a browser."""
    try:
        auth_url = gmail_client.auth_start(session_project_id)
    except RuntimeError as exc:
        # Missing env vars — log internally, do not leak path/env-var hints to caller.
        logger.warning("gmail oauth start: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="oauth_not_configured; check server logs",
        ) from exc
    return GmailAuthStartResponse(auth_url=auth_url)


@router.get("/auth/gmail/callback", response_model=GmailCallbackResponse)
async def gmail_auth_callback(
    code: str = Query(..., min_length=1, max_length=2048),
    state: str = Query(..., min_length=1, max_length=512),
    session: AsyncSession = Depends(get_session),
) -> GmailCallbackResponse:
    """Exchange the OAuth code for credentials and store them.

    Note: the callback endpoint does NOT require X-Project-Id — Google's
    redirect won't carry custom headers. The project binding is recovered
    from the `state` value (set by `auth_start` and validated server-side).
    """
    try:
        project_id, creds = await run_in_threadpool(gmail_client.auth_callback, code, state)
    except Exception as exc:
        # Don't leak upstream error strings (may contain client_secret or env hints).
        logger.warning("gmail oauth callback: %s", exc)
        raise HTTPException(
            status_code=400,
            detail="oauth_callback_failed: unknown or expired state; restart flow",
        ) from exc

    await token_store.put("gmail", project_id, creds, session)
    summary = gmail_client.creds_summary(creds)
    return GmailCallbackResponse(
        project_id=project_id,
        authenticated=True,
        email=summary.get("email"),
    )


@router.get("/auth/gmail/status", response_model=AuthStatusResponse)
async def gmail_auth_status(
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> AuthStatusResponse:
    """Return current auth status for Gmail on this project."""
    st = await token_store.status("gmail", session_project_id, session)
    return AuthStatusResponse(**st)


# ---------------------------------------------------------------------------
# Gmail — trash
# ---------------------------------------------------------------------------


async def _require_creds(provider: str, session_project_id: int, session: AsyncSession):
    """Fetch <provider> creds or raise 401. Local helper — keeps the routes lean."""
    creds = await token_store.get(provider, session_project_id, session)
    if creds is None:
        raise HTTPException(
            status_code=401,
            detail=(
                f"{provider} not authenticated; start the OAuth flow at "
                f"POST /api/tools/email/auth/{provider}/start"
            ),
        )
    return creds


def _cap_check_or_429(provider: str, session_project_id: int, units: int, action: str) -> None:
    """Run gate.check_and_increment; raise 429 with info on refusal.

    Also writes an audit row for the refusal so the JSONL trail captures
    every blocked attempt (not just upstream calls).
    """
    ok, info = gate.check_and_increment(session_project_id, units)
    if not ok:
        gate.log_audit(
            provider, session_project_id, action, units, success=False,
            error_code="daily_cap_reached",
        )
        raise HTTPException(
            status_code=429,
            detail={"error": "daily_cap_reached", **info},
        )


def _bulk_check_or_400(count: int, force: bool) -> None:
    """Run gate.check_bulk_threshold; raise 400 on refusal."""
    ok, info = gate.check_bulk_threshold(count, force=force)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "bulk_threshold",
                "count": info["count"],
                "threshold": info["threshold"],
                "hint": "add ?force=true if intentional",
            },
        )


@router.post("/gmail/trash", response_model=GmailTrashResponse)
async def gmail_trash(
    body: GmailTrashRequest,
    force: bool = Query(default=False, description="Bypass the bulk-threshold gate."),
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> GmailTrashResponse:
    """Trash Gmail messages by query OR explicit ids.

    Layer 0 (#1799): per-agent-name tool-governance gate — `gmail.trash` must
    be granted to the `X-Agent-Role` (if the role is restricted in
    `config.tool_grants`). Fires FIRST so a forbidden role is turned away
    before any OAuth/quota work. Opt-in: unrestricted by default.

    Tier gate (#1859): trash is the `delete` tier — above `read`, so it requires
    an operator-proof AFTER Layer-0. Absent (and the gate ACTIVE) -> 403. The
    gate is DORMANT (fail-open) until OPERATOR_ACTION_KEY is set, so live trash
    flows are unaffected until the operator activates it.

    Flow (FIX-6 #1609 — gate ordering):
      message_ids mode:
        1. Resolve ids from request body (no auth needed).
        2. Bulk-threshold gate (Layer 3) — fires before auth; payload-safety rail.
        3. Auth check — 401 if not authenticated.
        4. Daily-cap gate (Layer 1) for 20 * count units.
        5. Trash loop; audit row written after upstream call (Layer 2).

      query mode:
        1. Auth check — list call requires auth; gate fires after.
        2. Resolve query -> ids via Gmail messages.list (and pay list units).
        3. Bulk-threshold gate (Layer 3) — must fire after list; count unknown before.
        4. Daily-cap gate (Layer 1) for 20 * count units.
        5. Trash loop; audit row written after upstream call (Layer 2).
    """
    # Layer 0 (#1799) — tool-governance gate. 403 on a denied (role, gmail.trash).
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "gmail.trash"
    )

    # Tier gate (#1859) — trash = `delete` tier (above read). Operator-proof
    # required AFTER Layer-0; 403 if absent (when the gate is ACTIVE). Dormant
    # when OPERATOR_ACTION_KEY is unset (fail-open).
    # dry_run is a read-only preview — the operator-proof gate is SKIPPED.
    if not body.dry_run:
        _enforce_operator_tier_or_403(EmailTier.DELETE, operator_proof)

    # FIX-6 (#1609): message_ids mode — bulk-check before auth so the payload
    # safety rail is observable without OAuth setup. query mode still requires
    # auth first because the list call (which produces the count) requires auth.
    if body.message_ids is not None:
        ids = list(body.message_ids)

        # If no ids, exit early — no trash work to do.
        if not ids:
            return GmailTrashResponse(
                trashed_count=0,
                trashed_ids=[],
                errors=[],
                dry_run=body.dry_run,
                would_affect_count=0 if body.dry_run else None,
                would_affect_ids=[] if body.dry_run else None,
            )

        # Layer 3 — bulk threshold fires BEFORE auth in message_ids mode.
        _bulk_check_or_400(len(ids), force)

        # Auth check after bulk gate.
        creds = await _require_creds("gmail", session_project_id, session)
    else:
        # query mode: auth must come first because the list call requires creds.
        creds = await _require_creds("gmail", session_project_id, session)

        # Pay list units before we know the count — this is honest accounting.
        # dry_run still pays list units (the upstream list call happens).
        _cap_check_or_429("gmail", session_project_id, _LIST_UNITS_PER_CALL, "list")
        try:
            ids = await run_in_threadpool(
                gmail_client.list_message_ids,
                creds, body.query, _MAX_LIST_RESULTS,
            )
        except Exception as exc:
            gate.log_audit(
                "gmail", session_project_id, "list", _LIST_UNITS_PER_CALL,
                success=False, error_code=type(exc).__name__,
            )
            logger.warning("gmail list failed: %s", type(exc).__name__)
            raise HTTPException(
                status_code=502,
                detail={"error": "gmail_list_failed", "class": type(exc).__name__},
            ) from exc
        gate.log_audit(
            "gmail", session_project_id, "list", _LIST_UNITS_PER_CALL, success=True,
        )

        # If the query matched nothing, exit early — no trash work to do.
        if not ids:
            return GmailTrashResponse(
                trashed_count=0,
                trashed_ids=[],
                errors=[],
                dry_run=body.dry_run,
                would_affect_count=0 if body.dry_run else None,
                would_affect_ids=[] if body.dry_run else None,
            )

        # Layer 3 — bulk threshold fires AFTER list in query mode (count unknown before).
        _bulk_check_or_400(len(ids), force)

    # dry_run: return preview without moving anything. No trash units charged,
    # no trash_messages call, no _write_action_audit (nothing happened).
    if body.dry_run:
        gate.log_audit(
            "gmail", session_project_id, "trash_dryrun", 0, success=True,
        )
        return GmailTrashResponse(
            trashed_count=0,
            trashed_ids=[],
            errors=[],
            dry_run=True,
            would_affect_count=len(ids),
            would_affect_ids=list(ids),
        )

    # Layer 1 — daily-units cap for the trash workload.
    total_units = _TRASH_UNITS_PER_MESSAGE * len(ids)
    _cap_check_or_429("gmail", session_project_id, total_units, "trash")

    # Execute the trash loop.
    try:
        trashed, errors = await run_in_threadpool(gmail_client.trash_messages, creds, ids)
    except Exception as exc:
        gate.log_audit(
            "gmail", session_project_id, "trash", total_units,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("gmail trash batch failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "gmail_trash_failed", "class": type(exc).__name__},
        ) from exc

    # Audit row — success = at least one message trashed; partial errors
    # surface in the response body for the operator to inspect.
    gate.log_audit(
        "gmail", session_project_id, "trash", total_units,
        success=len(trashed) > 0,
        error_code=None if not errors else "partial_failure",
    )

    # Secretary-action audit (#1585 AC5/AC8) — trash is Tier-2 `delete`
    # (operator_proof approval mode). Logs the action-level row alongside the
    # units-accounting row above.
    _write_action_audit(
        agent_role=agent_role,
        action="trash",
        tier=EmailTier.DELETE,
        message_ids=trashed,
        approval_mode=_resolve_approval_mode(),
        result="success" if len(trashed) > 0 else ("partial" if errors else "noop"),
    )

    return GmailTrashResponse(
        trashed_count=len(trashed),
        trashed_ids=trashed,
        errors=errors,
    )


@router.get("/gmail/usage", response_model=UsageResponse)
async def gmail_usage(
    session_project_id: int = Depends(require_project_id_header),
) -> UsageResponse:
    """Snapshot the daily-units counter for this project (UTC day)."""
    return UsageResponse(**gate.usage(session_project_id))


# ---------------------------------------------------------------------------
# Gmail — Tier-1 modify actions (Kanban #1585: mark read/unread, archive, draft)
# ---------------------------------------------------------------------------
#
# All three are the `modify` tier — OPEN (Layer-0 role-gated + audited, NO
# operator-proof). They MIRROR /gmail/trash's gate ordering exactly:
#   1. Layer-0 (#1799) tool-grant gate  — _enforce_tool_grant_or_403
#   2. Tier gate (#1859)                — _enforce_operator_tier_or_403 (no-op for `modify`)
# then auth -> daily-cap -> upstream call -> gate.log_audit + secretary-action audit.

# Modify-action unit costs (mirrors the Gmail quota reference; users.messages.modify
# is 5 units/call, drafts.create is 10). Charged per message for modify; once for draft.
_MODIFY_UNITS_PER_MESSAGE = 5
_DRAFT_UNITS_PER_CALL = 10


@router.post("/gmail/mark", response_model=GmailModifyResponse)
async def gmail_mark(
    body: GmailMarkRequest,
    force: bool = Query(default=False, description="Bypass the bulk-threshold gate."),
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> GmailModifyResponse:
    """Mark Gmail messages read/unread via label modify (`modify` tier — OPEN).

    Layer 0 (#1799): `gmail.mark` must be granted to the X-Agent-Role (if the
    role is restricted in config.tool_grants). Fires FIRST.

    Tier gate (#1859): `modify` is at/below the open line — no operator-proof
    required (it is NOT in _PROOF_REQUIRED_TIERS). The gate call is still made,
    in the SAME Layer-0 -> tier order as /trash, so the composition stays uniform.

    read=True  -> remove UNREAD (mark read); read=False -> add UNREAD (mark unread).
    """
    # Layer 0 (#1799) — tool-governance gate. 403 on a denied (role, gmail.mark).
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "gmail.mark"
    )
    # Tier gate (#1859) — `modify` is OPEN; this is a no-op but kept in order.
    _enforce_operator_tier_or_403(EmailTier.MODIFY, operator_proof)

    ids = list(body.message_ids)
    # Layer 3 — bulk-threshold gate (mirrors /gmail/trash). Fires after Layer-0/tier,
    # before auth/cap, so the payload-safety rail is observable without OAuth setup.
    _bulk_check_or_400(len(ids), force)
    creds = await _require_creds("gmail", session_project_id, session)

    total_units = _MODIFY_UNITS_PER_MESSAGE * len(ids)
    _cap_check_or_429("gmail", session_project_id, total_units, "mark")

    add_label_ids = [] if body.read else ["UNREAD"]
    remove_label_ids = ["UNREAD"] if body.read else []
    try:
        modified, errors = await run_in_threadpool(
            gmail_client.modify_labels, creds, ids, add_label_ids, remove_label_ids
        )
    except Exception as exc:
        gate.log_audit(
            "gmail", session_project_id, "mark", total_units,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("gmail mark batch failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "gmail_mark_failed", "class": type(exc).__name__},
        ) from exc

    gate.log_audit(
        "gmail", session_project_id, "mark", total_units,
        success=len(modified) > 0,
        error_code=None if not errors else "partial_failure",
    )
    _write_action_audit(
        agent_role=agent_role,
        action="mark_read" if body.read else "mark_unread",
        tier=EmailTier.MODIFY,
        message_ids=modified,
        approval_mode="auto",
        result="success" if len(modified) > 0 else ("partial" if errors else "noop"),
    )
    return GmailModifyResponse(
        modified_count=len(modified), modified_ids=modified, errors=errors
    )


@router.post("/gmail/archive", response_model=GmailModifyResponse)
async def gmail_archive(
    body: GmailArchiveRequest,
    force: bool = Query(default=False, description="Bypass the bulk-threshold gate."),
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> GmailModifyResponse:
    """Archive Gmail messages — remove the INBOX label (`modify` tier — OPEN).

    Gate ordering identical to /gmail/mark: Layer-0 (`gmail.archive`) THEN the
    tier gate (no-op for `modify`), then bulk-threshold gate (Layer 3).
    """
    # Layer 0 (#1799) — tool-governance gate. 403 on a denied (role, gmail.archive).
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "gmail.archive"
    )
    # Tier gate (#1859) — `modify` is OPEN; no-op, kept in Layer-0 -> tier order.
    _enforce_operator_tier_or_403(EmailTier.MODIFY, operator_proof)

    ids = list(body.message_ids)
    # Layer 3 — bulk-threshold gate (mirrors /gmail/trash).
    _bulk_check_or_400(len(ids), force)
    creds = await _require_creds("gmail", session_project_id, session)

    total_units = _MODIFY_UNITS_PER_MESSAGE * len(ids)
    _cap_check_or_429("gmail", session_project_id, total_units, "archive")

    try:
        modified, errors = await run_in_threadpool(
            gmail_client.modify_labels, creds, ids, [], ["INBOX"]
        )
    except Exception as exc:
        gate.log_audit(
            "gmail", session_project_id, "archive", total_units,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("gmail archive batch failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "gmail_archive_failed", "class": type(exc).__name__},
        ) from exc

    gate.log_audit(
        "gmail", session_project_id, "archive", total_units,
        success=len(modified) > 0,
        error_code=None if not errors else "partial_failure",
    )
    _write_action_audit(
        agent_role=agent_role,
        action="archive",
        tier=EmailTier.MODIFY,
        message_ids=modified,
        approval_mode="auto",
        result="success" if len(modified) > 0 else ("partial" if errors else "noop"),
    )
    return GmailModifyResponse(
        modified_count=len(modified), modified_ids=modified, errors=errors
    )


@router.post("/gmail/draft", response_model=GmailDraftResponse)
async def gmail_draft(
    body: GmailDraftRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> GmailDraftResponse:
    """Create a Gmail DRAFT — no send (`modify` tier — OPEN).

    A draft is recoverable: it sits in Drafts until the operator explicitly
    sends it (a higher-tier action that DOES carry operator-proof). Gate ordering
    identical to /gmail/mark: Layer-0 (`gmail.draft`) THEN the tier gate (no-op).
    """
    # Layer 0 (#1799) — tool-governance gate. 403 on a denied (role, gmail.draft).
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "gmail.draft"
    )
    # Tier gate (#1859) — `modify` is OPEN; no-op, kept in Layer-0 -> tier order.
    _enforce_operator_tier_or_403(EmailTier.MODIFY, operator_proof)

    creds = await _require_creds("gmail", session_project_id, session)

    _cap_check_or_429("gmail", session_project_id, _DRAFT_UNITS_PER_CALL, "draft")

    try:
        created = await run_in_threadpool(
            gmail_client.save_draft,
            creds,
            to=body.to,
            subject=body.subject,
            body=body.body,
        )
    except Exception as exc:
        gate.log_audit(
            "gmail", session_project_id, "draft", _DRAFT_UNITS_PER_CALL,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("gmail draft create failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "gmail_draft_failed", "class": type(exc).__name__},
        ) from exc

    draft_id = created.get("draft_id")
    if draft_id is None:
        # Upstream returned an empty/missing id — treat as upstream failure.
        gate.log_audit(
            "gmail", session_project_id, "draft", _DRAFT_UNITS_PER_CALL,
            success=False, error_code="empty_draft_response",
        )
        _write_action_audit(
            agent_role=agent_role,
            action="draft",
            tier=EmailTier.MODIFY,
            message_ids=[],
            approval_mode="auto",
            result="error",
        )
        raise HTTPException(
            status_code=502,
            detail={"error": "empty_draft_response", "hint": "Gmail returned no draft id"},
        )
    gate.log_audit(
        "gmail", session_project_id, "draft", _DRAFT_UNITS_PER_CALL,
        success=True,
    )
    # Secretary-action audit — a draft has no message_ids yet; record the created
    # draft id so the trail still references the artifact.
    _write_action_audit(
        agent_role=agent_role,
        action="draft",
        tier=EmailTier.MODIFY,
        message_ids=[draft_id],
        approval_mode="auto",
        result="success",
    )
    return GmailDraftResponse(
        draft_id=draft_id, message_id=created.get("message_id")
    )


# ---------------------------------------------------------------------------
# Gmail — Tier-3 SEND actions (Kanban #2100: reply / forward / send-internal / external-send)
# ---------------------------------------------------------------------------
#
# These are the ACTUAL fire (users.messages.send) — distinct from draft. Tier map:
#   reply, forward   -> EmailTier.REPLY          (PROOF)
#   send-internal    -> EmailTier.SEND_INTERNAL  (PROOF)
#   external-send    -> EmailTier.EXTERNAL_SEND  (PROOF + out-of-band confirm/202 HALT)
#
# Gate chain mirrors the mutation routes byte-for-byte in ORDER:
#   1. Layer-0 (#1799)  _enforce_tool_grant_or_403(role, "<provider>.<action>")
#   2. Tier gate (#1859) — reply/forward/send-internal: _enforce_operator_tier_or_403;
#      external-send:     _escalate_external_send_or_202 (push + 202 HALT unless proven)
#   then auth -> daily-cap -> upstream send -> gate.log_audit + _write_action_audit
#   + D5 _write_send_audit_task (Kanban action-log row).
#
# users.messages.send is 100 units in Gmail's quota model — the heaviest action;
# we charge it as such so the daily-cap accounting reflects send weight.
_SEND_UNITS_PER_CALL = 100


async def _execute_email_send(
    session: AsyncSession,
    *,
    session_project_id: int,
    agent_role: str | None,
    provider: str,
    action: str,
    units: int,
    tier: EmailTier,
    approval_mode: str,
    client_callable,          # zero-arg callable; returns the client's `sent` dict
    audit_recipient: str,
    audit_subject: str,
    body: str,
    response_cls,
):
    """Shared send-execution tail for the 8 email send handlers (#2682 STEP B).

    The caller performs the gate chain + creds + cap, then hands the bound
    upstream call (as a zero-arg `client_callable`) plus the audit metadata here.
    Behavior is byte-identical to the previous inline tails.
    """
    try:
        sent = await run_in_threadpool(client_callable)
    except Exception as exc:
        gate.log_audit(
            provider, session_project_id, action, units,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("%s %s failed: %s", provider, action, type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": f"{provider}_{action}_failed", "class": type(exc).__name__},
        ) from exc

    msg_id = sent.get("message_id")
    gate.log_audit(provider, session_project_id, action, units, success=True)
    _write_action_audit(
        agent_role=agent_role, action=action, tier=tier,
        message_ids=[msg_id] if msg_id else [], approval_mode=approval_mode,
        result="success",
    )
    await _write_send_audit_task(
        session, session_project_id=session_project_id, provider=provider,
        action=action, recipient=audit_recipient, subject=audit_subject, body=body,
    )
    return response_cls(message_id=msg_id, thread_id=sent.get("thread_id"))


@router.post("/gmail/reply", response_model=GmailSendResponse)
async def gmail_reply(
    body: GmailReplyRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> GmailSendResponse:
    """Reply to a Gmail message in-thread (`reply` tier — PROOF).

    Layer 0 (#1799): `gmail.reply` must be granted to the X-Agent-Role.
    Tier gate (#1859): REPLY is above read — operator-proof required (403 if
    absent + gate ACTIVE; dormant/fail-open until OPERATOR_ACTION_KEY is set).
    """
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "gmail.reply"
    )
    _enforce_operator_tier_or_403(EmailTier.REPLY, operator_proof)

    creds = await _require_creds("gmail", session_project_id, session)
    _cap_check_or_429("gmail", session_project_id, _SEND_UNITS_PER_CALL, "reply")

    return await _execute_email_send(
        session, session_project_id=session_project_id, agent_role=agent_role,
        provider="gmail", action="reply", units=_SEND_UNITS_PER_CALL,
        tier=EmailTier.REPLY, approval_mode=_resolve_approval_mode(),
        client_callable=functools.partial(
            gmail_client.send_reply,
            creds,
            message_id=body.message_id,
            body=body.body,
            thread_id=body.thread_id,
        ),
        audit_recipient=f"(re: {body.message_id})", audit_subject="(reply)",
        body=body.body, response_cls=GmailSendResponse,
    )


@router.post("/gmail/forward", response_model=GmailSendResponse)
async def gmail_forward(
    body: GmailForwardRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> GmailSendResponse:
    """Forward a Gmail message to a new recipient (`reply` tier — PROOF).

    Layer 0 (#1799): `gmail.forward` must be granted to the X-Agent-Role.
    Tier gate (#1859): REPLY tier — operator-proof required.
    """
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "gmail.forward"
    )
    _enforce_operator_tier_or_403(EmailTier.REPLY, operator_proof)

    creds = await _require_creds("gmail", session_project_id, session)
    _cap_check_or_429("gmail", session_project_id, _SEND_UNITS_PER_CALL, "forward")

    return await _execute_email_send(
        session, session_project_id=session_project_id, agent_role=agent_role,
        provider="gmail", action="forward", units=_SEND_UNITS_PER_CALL,
        tier=EmailTier.REPLY, approval_mode=_resolve_approval_mode(),
        client_callable=functools.partial(
            gmail_client.send_forward,
            creds, message_id=body.message_id, to=body.to, body=body.body,
        ),
        audit_recipient=body.to, audit_subject="(forward)",
        body=body.body, response_cls=GmailSendResponse,
    )


@router.post("/gmail/send-internal", response_model=GmailSendResponse)
async def gmail_send_internal(
    body: GmailSendRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> GmailSendResponse:
    """Compose + send a NEW Gmail message, internal recipient (`send_internal` tier — PROOF).

    Layer 0 (#1799): `gmail.send_internal` must be granted to the X-Agent-Role.
    Tier gate (#1859): SEND_INTERNAL — operator-proof required (NO out-of-band
    escalation; that is reserved for external-send).
    """
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "gmail.send_internal"
    )
    _enforce_operator_tier_or_403(EmailTier.SEND_INTERNAL, operator_proof)
    # #2100 WARN-1 — internal/external downgrade rail. Fires AFTER the tier gate
    # and BEFORE any provider call/auth/cap/audit: a send-internal whose
    # recipients aren't all under INTERNAL_EMAIL_DOMAIN is refused here (403)
    # rather than slipping past the external-send confirm. Dormant when unset.
    _enforce_internal_recipients_or_403(to=body.to, cc=body.cc, bcc=body.bcc)

    creds = await _require_creds("gmail", session_project_id, session)
    _cap_check_or_429("gmail", session_project_id, _SEND_UNITS_PER_CALL, "send_internal")

    return await _execute_email_send(
        session, session_project_id=session_project_id, agent_role=agent_role,
        provider="gmail", action="send_internal", units=_SEND_UNITS_PER_CALL,
        tier=EmailTier.SEND_INTERNAL, approval_mode=_resolve_approval_mode(),
        client_callable=functools.partial(
            gmail_client.send_message,
            creds, to=body.to, subject=body.subject, body=body.body,
            cc=body.cc, bcc=body.bcc,
        ),
        audit_recipient=body.to, audit_subject=body.subject,
        body=body.body, response_cls=GmailSendResponse,
    )


@router.post("/gmail/external-send", response_model=GmailSendResponse)
async def gmail_external_send(
    body: GmailSendRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> GmailSendResponse:
    """Compose + send a NEW Gmail message, EXTERNAL recipient (`external_send` tier).

    Highest-blast tier: instead of a bare 403, an absent operator-proof triggers
    `_escalate_external_send_or_202` — a 202 HALT. The operator resumes by
    re-issuing the call carrying a valid X-Operator-Token, which passes the
    helper through and lets the send fire. NO mail is sent on the HALT path
    (the 202 is raised BEFORE any upstream send).
    """
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "gmail.external_send"
    )
    # EXTERNAL_SEND escalation — raises 202 HALT unless proven.
    _escalate_external_send_or_202(
        operator_proof, project_id=session_project_id, summary=f"to {body.to}",
    )

    creds = await _require_creds("gmail", session_project_id, session)
    _cap_check_or_429("gmail", session_project_id, _SEND_UNITS_PER_CALL, "external_send")

    return await _execute_email_send(
        session, session_project_id=session_project_id, agent_role=agent_role,
        provider="gmail", action="external_send", units=_SEND_UNITS_PER_CALL,
        tier=EmailTier.EXTERNAL_SEND, approval_mode="operator_confirm",
        client_callable=functools.partial(
            gmail_client.send_message,
            creds, to=body.to, subject=body.subject, body=body.body,
            cc=body.cc, bcc=body.bcc,
        ),
        audit_recipient=body.to, audit_subject=body.subject,
        body=body.body, response_cls=GmailSendResponse,
    )


# ---------------------------------------------------------------------------
# Gmail — READ actions (Kanban #1939: search + get)
# ---------------------------------------------------------------------------
#
# READ tier — auto-approve: no operator-proof, no _write_action_audit. Gate
# chain: Layer-0 tool-grant → tier gate (no-op for READ) → auth → cap →
# upstream → gate.log_audit (units trail only; NO body/query in audit).
#
# Unit costs — small READ cost to account for upstream API calls:
#   search: 5 units per call (one list + up to max_results metadata fetches;
#           we charge flat 5 to keep it simple, matching _LIST_UNITS_PER_CALL).
#   get:    5 units per call (one full-message fetch).

_SEARCH_UNITS_PER_CALL = 5  # same magnitude as _LIST_UNITS_PER_CALL.
_GET_UNITS_PER_MESSAGE = 5  # one full-message GET.


@router.post("/gmail/search", response_model=GmailSearchResponse)
async def gmail_search(
    body: GmailSearchRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> GmailSearchResponse:
    """Search Gmail and return message metadata (no body). READ tier — auto-approve.

    Layer 0 (#1799): `gmail.search` must be granted to the X-Agent-Role (if the
    role is restricted). Fires first.

    Tier gate (#1859): READ is OPEN — no operator-proof required. The call is
    still made in the same Layer-0 → tier order for uniformity (no-op here).

    PRIVACY: query, subject, sender, snippet MUST NOT appear in gate.log_audit
    or any logger call. Only {provider, action, units, success} are recorded.
    """
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "gmail.search"
    )
    _enforce_operator_tier_or_403(EmailTier.READ, operator_proof)

    creds = await _require_creds("gmail", session_project_id, session)
    _cap_check_or_429("gmail", session_project_id, _SEARCH_UNITS_PER_CALL, "search")

    try:
        items = await run_in_threadpool(
            gmail_client.search_messages, creds, body.query, body.max_results
        )
    except Exception as exc:
        gate.log_audit(
            "gmail", session_project_id, "search", _SEARCH_UNITS_PER_CALL,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("gmail search failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "gmail_search_failed", "class": type(exc).__name__},
        ) from exc

    gate.log_audit(
        "gmail", session_project_id, "search", _SEARCH_UNITS_PER_CALL, success=True,
    )
    search_items = [GmailSearchItem.model_validate(m) for m in items]
    return GmailSearchResponse(results=search_items, count=len(search_items))


@router.post("/gmail/get", response_model=GmailGetResponse)
async def gmail_get(
    body: GmailGetRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> GmailGetResponse:
    """Fetch the full content of a single Gmail message. READ tier — auto-approve.

    Layer 0 (#1799): `gmail.get` must be granted to the X-Agent-Role (if the
    role is restricted). Fires first.

    Tier gate (#1859): READ is OPEN — no operator-proof required.

    PRIVACY: body_text MUST NOT appear in gate.log_audit, any logger call, or
    HTTP error detail. Only type(exc).__name__ is used in error paths.
    """
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "gmail.get"
    )
    _enforce_operator_tier_or_403(EmailTier.READ, operator_proof)

    creds = await _require_creds("gmail", session_project_id, session)
    _cap_check_or_429("gmail", session_project_id, _GET_UNITS_PER_MESSAGE, "get")

    try:
        data = await run_in_threadpool(
            gmail_client.get_message, creds, body.message_id
        )
    except Exception as exc:
        gate.log_audit(
            "gmail", session_project_id, "get", _GET_UNITS_PER_MESSAGE,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("gmail get failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "gmail_get_failed", "class": type(exc).__name__},
        ) from exc

    gate.log_audit(
        "gmail", session_project_id, "get", _GET_UNITS_PER_MESSAGE, success=True,
    )
    return GmailGetResponse.model_validate(data)


# ---------------------------------------------------------------------------
# Gmail — READ extras (Kanban #1940: thread + labels + attachment)
# ---------------------------------------------------------------------------
#
# READ tier — same gate chain as search/get: Layer-0 tool-grant → tier gate
# (no-op for READ) → auth → cap → upstream → gate.log_audit (units only).
# NO _write_action_audit (reads). NO body/filename/data in any log or error.

_THREAD_UNITS_PER_CALL = 5      # rough; mirrors _LIST_UNITS_PER_CALL
_LABELS_UNITS_PER_CALL = 5      # rough; mirrors _LIST_UNITS_PER_CALL
_ATTACHMENT_UNITS_PER_CALL = 5  # rough; mirrors _LIST_UNITS_PER_CALL


@router.post("/gmail/thread", response_model=GmailThreadResponse)
async def gmail_thread(
    body: GmailThreadRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> GmailThreadResponse:
    """Fetch all messages in a Gmail thread by thread id. READ tier — auto-approve.

    Layer 0 (#1799): `gmail.thread` must be granted to the X-Agent-Role (if
    the role is restricted). Fires first.

    Tier gate (#1859): READ is OPEN — no operator-proof required.

    PRIVACY: body_text MUST NOT appear in gate.log_audit, any logger call, or
    HTTP error detail. Only type(exc).__name__ is used in error paths.
    """
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "gmail.thread"
    )
    _enforce_operator_tier_or_403(EmailTier.READ, operator_proof)

    creds = await _require_creds("gmail", session_project_id, session)
    _cap_check_or_429("gmail", session_project_id, _THREAD_UNITS_PER_CALL, "thread")

    try:
        data = await run_in_threadpool(
            gmail_client.get_thread, creds, body.thread_id
        )
    except Exception as exc:
        gate.log_audit(
            "gmail", session_project_id, "thread", _THREAD_UNITS_PER_CALL,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("gmail thread failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "gmail_thread_failed", "class": type(exc).__name__},
        ) from exc

    gate.log_audit(
        "gmail", session_project_id, "thread", _THREAD_UNITS_PER_CALL, success=True,
    )
    messages = [GmailThreadMessage.model_validate(m) for m in data["messages"]]
    return GmailThreadResponse(
        thread_id=data["thread_id"],
        messages=messages,
        count=len(messages),
    )


@router.post("/gmail/labels", response_model=GmailLabelsResponse)
async def gmail_labels(
    body: GmailLabelsRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> GmailLabelsResponse:
    """List all Gmail labels for the authenticated account. READ tier — auto-approve.

    Layer 0 (#1799): `gmail.labels` must be granted to the X-Agent-Role (if
    the role is restricted). Fires first.

    Tier gate (#1859): READ is OPEN — no operator-proof required.

    PRIVACY: label names MUST NOT appear in gate.log_audit or any logger call.
    Only type(exc).__name__ is used in error paths.
    """
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "gmail.labels"
    )
    _enforce_operator_tier_or_403(EmailTier.READ, operator_proof)

    creds = await _require_creds("gmail", session_project_id, session)
    _cap_check_or_429("gmail", session_project_id, _LABELS_UNITS_PER_CALL, "labels")

    try:
        items = await run_in_threadpool(gmail_client.list_labels, creds)
    except Exception as exc:
        gate.log_audit(
            "gmail", session_project_id, "labels", _LABELS_UNITS_PER_CALL,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("gmail labels failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "gmail_labels_failed", "class": type(exc).__name__},
        ) from exc

    gate.log_audit(
        "gmail", session_project_id, "labels", _LABELS_UNITS_PER_CALL, success=True,
    )
    labels = [GmailLabel.model_validate(lbl) for lbl in items]
    return GmailLabelsResponse(labels=labels, count=len(labels))


@router.post("/gmail/attachment", response_model=GmailAttachmentResponse)
async def gmail_attachment(
    body: GmailAttachmentRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> GmailAttachmentResponse:
    """Fetch a Gmail message attachment by message id + attachment id. READ tier.

    Layer 0 (#1799): `gmail.attachment` must be granted to the X-Agent-Role
    (if the role is restricted). Fires first.

    Tier gate (#1859): READ is OPEN — no operator-proof required.

    SIZE CAP: attachments over 10 MB are refused with 413 — data is never
    fetched or returned. Only {error, max_mb} are in the 413 detail body.

    PRIVACY: filename, mime_type, and data_base64 MUST NOT appear in
    gate.log_audit, any logger call, or HTTP error detail. Only
    type(exc).__name__ is used in error paths.
    """
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "gmail.attachment"
    )
    _enforce_operator_tier_or_403(EmailTier.READ, operator_proof)

    creds = await _require_creds("gmail", session_project_id, session)
    _cap_check_or_429("gmail", session_project_id, _ATTACHMENT_UNITS_PER_CALL, "attachment")

    try:
        data = await run_in_threadpool(
            gmail_client.get_attachment, creds, body.message_id, body.attachment_id
        )
    except gmail_client.AttachmentTooLargeError as exc:
        gate.log_audit(
            "gmail", session_project_id, "attachment", _ATTACHMENT_UNITS_PER_CALL,
            success=False, error_code="AttachmentTooLargeError",
        )
        # PRIVACY: do NOT include filename or size in the 413 detail.
        raise HTTPException(
            status_code=413,
            detail={"error": "attachment_too_large", "max_mb": 10},
        ) from exc
    except gmail_client.AttachmentNotFoundError as exc:
        gate.log_audit(
            "gmail", session_project_id, "attachment", _ATTACHMENT_UNITS_PER_CALL,
            success=False, error_code="AttachmentNotFoundError",
        )
        # PRIVACY: do NOT include attachment_id or message_id in the 404 detail.
        raise HTTPException(
            status_code=404,
            detail={"error": "attachment_not_found"},
        ) from exc
    except Exception as exc:
        gate.log_audit(
            "gmail", session_project_id, "attachment", _ATTACHMENT_UNITS_PER_CALL,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("gmail attachment failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "gmail_attachment_failed", "class": type(exc).__name__},
        ) from exc

    gate.log_audit(
        "gmail", session_project_id, "attachment", _ATTACHMENT_UNITS_PER_CALL,
        success=True,
    )
    return GmailAttachmentResponse.model_validate(data)


# >>> #1608 OUTLOOK ROUTES BELOW — append-only zone for parallel dev coordination


# Outlook unit-cost constants — Lead-frozen (research note: Graph publishes no
# per-operation cost; we mirror the same _DAILY_UNITS_CAP via scaled units).
_OUTLOOK_TRASH_UNITS_PER_MESSAGE = 10  # half of Gmail's 20 — see outlook_client docstring.
_OUTLOOK_LIST_UNITS_PER_CALL = 5  # mirrors Gmail's _LIST_UNITS_PER_CALL.
_OUTLOOK_CALLBACK_UNITS = 1  # one AAD token-exchange call.


# ---------------------------------------------------------------------------
# Outlook — OAuth dance
# ---------------------------------------------------------------------------


@router.post("/auth/outlook/start", response_model=OutlookAuthStartResponse)
async def outlook_auth_start(
    session_project_id: int = Depends(require_project_id_header),
) -> OutlookAuthStartResponse:
    """Begin the Outlook OAuth flow. Returns the URL to open in a browser."""
    try:
        auth_url = outlook_client.auth_start(session_project_id)
    except RuntimeError as exc:
        # Missing env vars — log internally, do not leak path/env-var hints to caller.
        logger.warning("outlook oauth start: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="oauth_not_configured; check server logs",
        ) from exc
    return OutlookAuthStartResponse(auth_url=auth_url)


@router.get("/auth/outlook/callback", response_model=OutlookCallbackResponse)
async def outlook_auth_callback(
    code: str = Query(..., min_length=1, max_length=2048),
    state: str = Query(..., min_length=1, max_length=512),
    session: AsyncSession = Depends(get_session),
) -> OutlookCallbackResponse:
    """Exchange the OAuth code for tokens and store them.

    Note: like the Gmail callback, this endpoint does NOT require X-Project-Id
    — Microsoft's redirect won't carry custom headers. The project binding is
    recovered from the `state` value (set by auth_start, validated server-side).

    Audit: we log 1 unit even though no cap-check is gated (callback is part of
    the OAuth dance, not user-issued bulk work) so the audit trail captures
    every token exchange.
    """
    try:
        project_id, creds = await run_in_threadpool(outlook_client.auth_callback, code, state)
    except ValueError as exc:
        # Don't leak the upstream error_description (msal may include hints
        # about the secret in some error paths).
        logger.warning("outlook oauth callback failed: %s", str(exc).split(":")[0])
        raise HTTPException(
            status_code=400,
            detail=(
                "oauth_callback_failed; restart at "
                "POST /api/tools/email/auth/outlook/start"
            ),
        ) from exc
    except Exception as exc:
        logger.warning("outlook oauth callback failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=400,
            detail=(
                "oauth_callback_failed; restart at "
                "POST /api/tools/email/auth/outlook/start"
            ),
        ) from exc

    await token_store.put("outlook", project_id, creds, session)
    gate.log_audit(
        "outlook", project_id, "auth_callback", _OUTLOOK_CALLBACK_UNITS, success=True,
    )
    summary = outlook_client.creds_summary(creds)
    return OutlookCallbackResponse(
        project_id=project_id,
        authenticated=True,
        email=summary.get("email"),
    )


@router.get("/auth/outlook/status", response_model=AuthStatusResponse)
async def outlook_auth_status(
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> AuthStatusResponse:
    """Return current auth status for Outlook on this project."""
    st = await token_store.status("outlook", session_project_id, session)
    return AuthStatusResponse(**st)


# ---------------------------------------------------------------------------
# Outlook — trash (move to Deleted Items)
# ---------------------------------------------------------------------------



@router.post("/outlook/trash", response_model=OutlookTrashResponse)
async def outlook_trash(
    body: OutlookTrashRequest,
    force: bool = Query(default=False, description="Bypass the bulk-threshold gate."),
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> OutlookTrashResponse:
    """Move Outlook messages to Deleted Items by query OR explicit ids.

    Layer 0 (#1799): per-agent-name tool-governance gate — `outlook.trash` must
    be granted to the `X-Agent-Role` (if the role is restricted in
    `config.tool_grants`). Fires FIRST so a forbidden role is turned away
    before any OAuth/quota work. Opt-in: unrestricted by default.

    Tier gate (#1859): trash is the `delete` tier — above `read`, so it requires
    an operator-proof AFTER Layer-0. Absent (and the gate ACTIVE) -> 403. The
    gate is DORMANT (fail-open) until OPERATOR_ACTION_KEY is set, so live trash
    flows are unaffected until the operator activates it.

    Flow (FIX-6 #1609 — gate ordering):
      message_ids mode:
        1. Resolve ids from request body (no auth needed).
        2. Bulk-threshold gate (Layer 3) — fires BEFORE auth; payload-safety rail.
        3. Auth check — 401 if not authenticated.
        4. Daily-cap gate (Layer 1) for 10 * count units.
        5. Move loop; audit row written after upstream call (Layer 2).

      query mode (Graph $search — mirrors Gmail query flow):
        1. Auth check — list call requires auth; gate fires after.
        2. Resolve query -> ids via Graph $search (and pay list units).
        3. Bulk-threshold gate (Layer 3) — must fire after list; count unknown before.
        4. Daily-cap gate (Layer 1) for 10 * count units.
        5. Move loop; audit row written after upstream call (Layer 2).
    """
    # Layer 0 (#1799) — tool-governance gate. 403 on a denied (role, outlook.trash).
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "outlook.trash"
    )

    # Tier gate (#1859) — trash = `delete` tier (above read). Operator-proof
    # required AFTER Layer-0; 403 if absent (when the gate is ACTIVE). Dormant
    # when OPERATOR_ACTION_KEY is unset (fail-open).
    # dry_run is a read-only preview — the operator-proof gate is SKIPPED.
    if not body.dry_run:
        _enforce_operator_tier_or_403(EmailTier.DELETE, operator_proof)

    if body.message_ids is not None:
        ids = list(body.message_ids)

        if not ids:
            return OutlookTrashResponse(
                trashed_count=0,
                trashed_ids=[],
                errors=[],
                dry_run=body.dry_run,
                would_affect_count=0 if body.dry_run else None,
                would_affect_ids=[] if body.dry_run else None,
            )

        # FIX-6 (#1609): Layer 3 — bulk threshold fires BEFORE auth in message_ids mode.
        _bulk_check_or_400(len(ids), force)

        # Auth check after bulk gate.
        creds = await _require_creds("outlook", session_project_id, session)
    else:
        # query mode: auth must come first because the list call requires creds.
        creds = await _require_creds("outlook", session_project_id, session)

        # Pay list units before we know the count — honest accounting.
        # dry_run still pays list units (the upstream list call happens).
        _cap_check_or_429("outlook", session_project_id, _OUTLOOK_LIST_UNITS_PER_CALL, "list")
        try:
            ids = await run_in_threadpool(
                outlook_client.list_message_ids,
                creds, body.query, _MAX_LIST_RESULTS,
            )
        except Exception as exc:
            gate.log_audit(
                "outlook", session_project_id, "list", _OUTLOOK_LIST_UNITS_PER_CALL,
                success=False, error_code=type(exc).__name__,
            )
            logger.warning("outlook list failed: %s", type(exc).__name__)
            raise HTTPException(
                status_code=502,
                detail={"error": "outlook_list_failed", "class": type(exc).__name__},
            ) from exc
        gate.log_audit(
            "outlook", session_project_id, "list", _OUTLOOK_LIST_UNITS_PER_CALL, success=True,
        )

        if not ids:
            return OutlookTrashResponse(
                trashed_count=0,
                trashed_ids=[],
                errors=[],
                dry_run=body.dry_run,
                would_affect_count=0 if body.dry_run else None,
                would_affect_ids=[] if body.dry_run else None,
            )

        # Layer 3 — bulk threshold fires AFTER list in query mode (count unknown before).
        _bulk_check_or_400(len(ids), force)

    # dry_run: return preview without moving anything. No trash units charged,
    # no trash_messages call, no _write_action_audit (nothing happened).
    if body.dry_run:
        gate.log_audit(
            "outlook", session_project_id, "trash_dryrun", 0, success=True,
        )
        return OutlookTrashResponse(
            trashed_count=0,
            trashed_ids=[],
            errors=[],
            dry_run=True,
            would_affect_count=len(ids),
            would_affect_ids=list(ids),
        )

    # Layer 1 — daily-units cap for the trash workload.
    total_units = _OUTLOOK_TRASH_UNITS_PER_MESSAGE * len(ids)
    _cap_check_or_429("outlook", session_project_id, total_units, "trash")

    # Execute the move loop.
    # FIX-1 (#1609): outlook_client.trash_messages is sync and calls time.sleep
    # on Graph 429. Wrap in run_in_threadpool so the async event loop is not
    # blocked during retry sleeps.
    try:
        trashed, errors = await run_in_threadpool(outlook_client.trash_messages, creds, ids)
    except Exception as exc:
        gate.log_audit(
            "outlook", session_project_id, "trash", total_units,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("outlook trash batch failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "outlook_trash_failed", "class": type(exc).__name__},
        ) from exc

    # Audit row — success = at least one message moved; partial errors surface
    # in the response body for the operator to inspect.
    gate.log_audit(
        "outlook", session_project_id, "trash", total_units,
        success=len(trashed) > 0,
        error_code=None if not errors else "partial_failure",
    )
    _write_action_audit(
        agent_role=agent_role,
        action="trash",
        tier=EmailTier.DELETE,
        message_ids=trashed,
        approval_mode=_resolve_approval_mode(),
        result="success" if len(trashed) > 0 else ("partial" if errors else "noop"),
    )

    return OutlookTrashResponse(
        trashed_count=len(trashed),
        trashed_ids=trashed,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Outlook — Tier-1 modify actions (Kanban #1917: mark read/unread, archive, draft)
# ---------------------------------------------------------------------------
# Gate chain (byte-for-byte same ORDER as Gmail Tier-1):
#   Layer-0 _enforce_tool_grant_or_403 → tier gate _enforce_operator_tier_or_403(MODIFY)
#   → (_bulk_check_or_400 for mark/archive) → _require_creds("outlook", …)
#   → _cap_check_or_429("outlook", …) → execute → gate.log_audit("outlook"…)
#   → _write_action_audit(…)
# Both MODIFY_UNITS_PER_MESSAGE and _DRAFT_UNITS_PER_CALL are shared with Gmail
# (provider-agnostic constants defined above the Gmail routes).


@router.post("/outlook/mark", response_model=OutlookModifyResponse)
async def outlook_mark(
    body: OutlookMarkRequest,
    force: bool = Query(default=False, description="Bypass the bulk-threshold gate."),
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> OutlookModifyResponse:
    """Mark Outlook messages read/unread via isRead PATCH (`modify` tier — OPEN).

    Layer 0 (#1799): `outlook.mark` must be granted to the X-Agent-Role.
    Tier gate (#1859): `modify` is OPEN — no operator-proof required.

    read=True  -> isRead=true (mark read); read=False -> isRead=false (mark unread).
    """
    # Layer 0 (#1799) — tool-governance gate.
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "outlook.mark"
    )
    # Tier gate (#1859) — `modify` is OPEN; no-op, kept in Layer-0 -> tier order.
    _enforce_operator_tier_or_403(EmailTier.MODIFY, operator_proof)

    ids = list(body.message_ids)
    # Layer 3 — bulk-threshold gate.
    _bulk_check_or_400(len(ids), force)
    creds = await _require_creds("outlook", session_project_id, session)

    total_units = _MODIFY_UNITS_PER_MESSAGE * len(ids)
    _cap_check_or_429("outlook", session_project_id, total_units, "mark")

    try:
        modified, errors = await run_in_threadpool(
            outlook_client.mark_read, creds, ids, body.read
        )
    except Exception as exc:
        gate.log_audit(
            "outlook", session_project_id, "mark", total_units,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("outlook mark batch failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "outlook_mark_failed", "class": type(exc).__name__},
        ) from exc

    gate.log_audit(
        "outlook", session_project_id, "mark", total_units,
        success=len(modified) > 0,
        error_code=None if not errors else "partial_failure",
    )
    _write_action_audit(
        agent_role=agent_role,
        action="mark_read" if body.read else "mark_unread",
        tier=EmailTier.MODIFY,
        message_ids=modified,
        approval_mode="auto",
        result="success" if len(modified) > 0 else ("partial" if errors else "noop"),
    )
    return OutlookModifyResponse(
        modified_count=len(modified), modified_ids=modified, errors=errors
    )


@router.post("/outlook/archive", response_model=OutlookModifyResponse)
async def outlook_archive(
    body: OutlookArchiveRequest,
    force: bool = Query(default=False, description="Bypass the bulk-threshold gate."),
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> OutlookModifyResponse:
    """Archive Outlook messages — move to well-known 'archive' folder (`modify` tier — OPEN).

    Gate ordering identical to /outlook/mark: Layer-0 (`outlook.archive`) THEN
    the tier gate (no-op for `modify`), then bulk-threshold gate (Layer 3).
    """
    # Layer 0 (#1799) — tool-governance gate.
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "outlook.archive"
    )
    # Tier gate (#1859) — `modify` is OPEN; no-op, kept in Layer-0 -> tier order.
    _enforce_operator_tier_or_403(EmailTier.MODIFY, operator_proof)

    ids = list(body.message_ids)
    # Layer 3 — bulk-threshold gate.
    _bulk_check_or_400(len(ids), force)
    creds = await _require_creds("outlook", session_project_id, session)

    total_units = _MODIFY_UNITS_PER_MESSAGE * len(ids)
    _cap_check_or_429("outlook", session_project_id, total_units, "archive")

    try:
        modified, errors = await run_in_threadpool(
            outlook_client.archive, creds, ids
        )
    except Exception as exc:
        gate.log_audit(
            "outlook", session_project_id, "archive", total_units,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("outlook archive batch failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "outlook_archive_failed", "class": type(exc).__name__},
        ) from exc

    gate.log_audit(
        "outlook", session_project_id, "archive", total_units,
        success=len(modified) > 0,
        error_code=None if not errors else "partial_failure",
    )
    _write_action_audit(
        agent_role=agent_role,
        action="archive",
        tier=EmailTier.MODIFY,
        message_ids=modified,
        approval_mode="auto",
        result="success" if len(modified) > 0 else ("partial" if errors else "noop"),
    )
    return OutlookModifyResponse(
        modified_count=len(modified), modified_ids=modified, errors=errors
    )


@router.post("/outlook/draft", response_model=OutlookDraftResponse)
async def outlook_draft(
    body: OutlookDraftRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> OutlookDraftResponse:
    """Create an Outlook DRAFT — no send (`modify` tier — OPEN).

    A draft is recoverable: it sits in Drafts until the operator explicitly
    sends it (a higher-tier action that DOES carry operator-proof). Gate ordering
    identical to /outlook/mark: Layer-0 (`outlook.draft`) THEN the tier gate (no-op).
    """
    # Layer 0 (#1799) — tool-governance gate.
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "outlook.draft"
    )
    # Tier gate (#1859) — `modify` is OPEN; no-op, kept in Layer-0 -> tier order.
    _enforce_operator_tier_or_403(EmailTier.MODIFY, operator_proof)

    creds = await _require_creds("outlook", session_project_id, session)

    _cap_check_or_429("outlook", session_project_id, _DRAFT_UNITS_PER_CALL, "draft")

    try:
        created = await run_in_threadpool(
            outlook_client.save_draft,
            creds,
            to=body.to,
            subject=body.subject,
            body=body.body,
        )
    except Exception as exc:
        gate.log_audit(
            "outlook", session_project_id, "draft", _DRAFT_UNITS_PER_CALL,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("outlook draft create failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "outlook_draft_failed", "class": type(exc).__name__},
        ) from exc

    draft_id = created.get("draft_id")
    if draft_id is None:
        # Upstream returned an empty/missing id — treat as upstream failure.
        gate.log_audit(
            "outlook", session_project_id, "draft", _DRAFT_UNITS_PER_CALL,
            success=False, error_code="empty_draft_response",
        )
        _write_action_audit(
            agent_role=agent_role,
            action="draft",
            tier=EmailTier.MODIFY,
            message_ids=[],
            approval_mode="auto",
            result="error",
        )
        raise HTTPException(
            status_code=502,
            detail={"error": "empty_draft_response", "hint": "Outlook Graph returned no message id"},
        )
    gate.log_audit(
        "outlook", session_project_id, "draft", _DRAFT_UNITS_PER_CALL,
        success=True,
    )
    _write_action_audit(
        agent_role=agent_role,
        action="draft",
        tier=EmailTier.MODIFY,
        message_ids=[draft_id],
        approval_mode="auto",
        result="success",
    )
    return OutlookDraftResponse(
        draft_id=draft_id, message_id=created.get("message_id")
    )


# ---------------------------------------------------------------------------
# Outlook — READ actions (Kanban #1939: search + get)
# ---------------------------------------------------------------------------
#
# READ tier — auto-approve: no operator-proof, no _write_action_audit. Gate
# chain mirrors Gmail read routes byte-for-byte but uses the outlook provider
# + _cap_check_or_429("outlook", …) + _require_creds("outlook", …).
#
# Unit costs mirror the Gmail READ constants (provider-agnostic cost parity).
_OUTLOOK_SEARCH_UNITS_PER_CALL = 5  # one Graph $search call.
_OUTLOOK_GET_UNITS_PER_MESSAGE = 5  # one full-message GET.


@router.post("/outlook/search", response_model=OutlookSearchResponse)
async def outlook_search(
    body: OutlookSearchRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> OutlookSearchResponse:
    """Search Outlook and return message metadata (no body). READ tier — auto-approve.

    Layer 0 (#1799): `outlook.search` must be granted to the X-Agent-Role (if the
    role is restricted). Fires first.

    Tier gate (#1859): READ is OPEN — no operator-proof required.

    PRIVACY: query, subject, sender, snippet MUST NOT appear in gate.log_audit
    or any logger call. Only {provider, action, units, success} are recorded.
    """
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "outlook.search"
    )
    _enforce_operator_tier_or_403(EmailTier.READ, operator_proof)

    creds = await _require_creds("outlook", session_project_id, session)
    _cap_check_or_429("outlook", session_project_id, _OUTLOOK_SEARCH_UNITS_PER_CALL, "search")

    try:
        items = await run_in_threadpool(
            outlook_client.search_messages, creds, body.query, body.max_results
        )
    except Exception as exc:
        gate.log_audit(
            "outlook", session_project_id, "search", _OUTLOOK_SEARCH_UNITS_PER_CALL,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("outlook search failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "outlook_search_failed", "class": type(exc).__name__},
        ) from exc

    gate.log_audit(
        "outlook", session_project_id, "search", _OUTLOOK_SEARCH_UNITS_PER_CALL, success=True,
    )
    search_items = [OutlookSearchItem.model_validate(m) for m in items]
    return OutlookSearchResponse(results=search_items, count=len(search_items))


@router.post("/outlook/get", response_model=OutlookGetResponse)
async def outlook_get(
    body: OutlookGetRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> OutlookGetResponse:
    """Fetch the full content of a single Outlook message. READ tier — auto-approve.

    Layer 0 (#1799): `outlook.get` must be granted to the X-Agent-Role (if the
    role is restricted). Fires first.

    Tier gate (#1859): READ is OPEN — no operator-proof required.

    PRIVACY: body_text MUST NOT appear in gate.log_audit, any logger call, or
    HTTP error detail. Only type(exc).__name__ is used in error paths.
    """
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "outlook.get"
    )
    _enforce_operator_tier_or_403(EmailTier.READ, operator_proof)

    creds = await _require_creds("outlook", session_project_id, session)
    _cap_check_or_429("outlook", session_project_id, _OUTLOOK_GET_UNITS_PER_MESSAGE, "get")

    try:
        data = await run_in_threadpool(
            outlook_client.get_message, creds, body.message_id
        )
    except Exception as exc:
        gate.log_audit(
            "outlook", session_project_id, "get", _OUTLOOK_GET_UNITS_PER_MESSAGE,
            success=False, error_code=type(exc).__name__,
        )
        logger.warning("outlook get failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"error": "outlook_get_failed", "class": type(exc).__name__},
        ) from exc

    gate.log_audit(
        "outlook", session_project_id, "get", _OUTLOOK_GET_UNITS_PER_MESSAGE, success=True,
    )
    return OutlookGetResponse.model_validate(data)


# ---------------------------------------------------------------------------
# Outlook — Tier-3 SEND actions (Kanban #2100: reply / forward / send-internal / external-send)
# ---------------------------------------------------------------------------
#
# Mirrors the Gmail Tier-3 send block byte-for-byte in gate ORDER + audit shape;
# uses the outlook provider + _cap_check_or_429("outlook", …) + _require_creds("outlook", …).
# Graph send actions (reply/forward/sendMail) return 202 with no body, so
# message_id is typically None in the response (see OutlookSendResponse).
#
# Send cost mirrors Gmail's _SEND_UNITS_PER_CALL (provider-agnostic send weight).
_OUTLOOK_SEND_UNITS_PER_CALL = 100


@router.post("/outlook/reply", response_model=OutlookSendResponse)
async def outlook_reply(
    body: OutlookReplyRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> OutlookSendResponse:
    """Reply to an Outlook message in-conversation (`reply` tier — PROOF).

    Layer 0 (#1799): `outlook.reply` must be granted to the X-Agent-Role.
    Tier gate (#1859): REPLY — operator-proof required (dormant/fail-open until
    OPERATOR_ACTION_KEY is set).
    """
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "outlook.reply"
    )
    _enforce_operator_tier_or_403(EmailTier.REPLY, operator_proof)

    creds = await _require_creds("outlook", session_project_id, session)
    _cap_check_or_429("outlook", session_project_id, _OUTLOOK_SEND_UNITS_PER_CALL, "reply")

    return await _execute_email_send(
        session, session_project_id=session_project_id, agent_role=agent_role,
        provider="outlook", action="reply", units=_OUTLOOK_SEND_UNITS_PER_CALL,
        tier=EmailTier.REPLY, approval_mode=_resolve_approval_mode(),
        client_callable=functools.partial(
            outlook_client.send_reply, creds, message_id=body.message_id, body=body.body,
        ),
        audit_recipient=f"(re: {body.message_id})", audit_subject="(reply)",
        body=body.body, response_cls=OutlookSendResponse,
    )


@router.post("/outlook/forward", response_model=OutlookSendResponse)
async def outlook_forward(
    body: OutlookForwardRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> OutlookSendResponse:
    """Forward an Outlook message to a new recipient (`reply` tier — PROOF).

    Layer 0 (#1799): `outlook.forward` must be granted to the X-Agent-Role.
    Tier gate (#1859): REPLY tier — operator-proof required.
    """
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "outlook.forward"
    )
    _enforce_operator_tier_or_403(EmailTier.REPLY, operator_proof)

    creds = await _require_creds("outlook", session_project_id, session)
    _cap_check_or_429("outlook", session_project_id, _OUTLOOK_SEND_UNITS_PER_CALL, "forward")

    return await _execute_email_send(
        session, session_project_id=session_project_id, agent_role=agent_role,
        provider="outlook", action="forward", units=_OUTLOOK_SEND_UNITS_PER_CALL,
        tier=EmailTier.REPLY, approval_mode=_resolve_approval_mode(),
        client_callable=functools.partial(
            outlook_client.send_forward, creds, message_id=body.message_id, to=body.to, body=body.body,
        ),
        audit_recipient=body.to, audit_subject="(forward)",
        body=body.body, response_cls=OutlookSendResponse,
    )


@router.post("/outlook/send-internal", response_model=OutlookSendResponse)
async def outlook_send_internal(
    body: OutlookSendRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> OutlookSendResponse:
    """Compose + send a NEW Outlook message, internal recipient (`send_internal` tier — PROOF).

    Layer 0 (#1799): `outlook.send_internal` must be granted to the X-Agent-Role.
    Tier gate (#1859): SEND_INTERNAL — operator-proof required (no escalation).
    """
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "outlook.send_internal"
    )
    _enforce_operator_tier_or_403(EmailTier.SEND_INTERNAL, operator_proof)
    # #2100 WARN-1 — internal/external downgrade rail (mirrors Gmail send-internal).
    _enforce_internal_recipients_or_403(to=body.to, cc=body.cc, bcc=body.bcc)

    creds = await _require_creds("outlook", session_project_id, session)
    _cap_check_or_429("outlook", session_project_id, _OUTLOOK_SEND_UNITS_PER_CALL, "send_internal")

    return await _execute_email_send(
        session, session_project_id=session_project_id, agent_role=agent_role,
        provider="outlook", action="send_internal", units=_OUTLOOK_SEND_UNITS_PER_CALL,
        tier=EmailTier.SEND_INTERNAL, approval_mode=_resolve_approval_mode(),
        client_callable=functools.partial(
            outlook_client.send_message,
            creds, to=body.to, subject=body.subject, body=body.body, cc=body.cc, bcc=body.bcc,
        ),
        audit_recipient=body.to, audit_subject=body.subject,
        body=body.body, response_cls=OutlookSendResponse,
    )


@router.post("/outlook/external-send", response_model=OutlookSendResponse)
async def outlook_external_send(
    body: OutlookSendRequest,
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
    session: AsyncSession = Depends(get_session),
) -> OutlookSendResponse:
    """Compose + send a NEW Outlook message, EXTERNAL recipient (`external_send` tier).

    Highest-blast tier: an absent operator-proof triggers
    `_escalate_external_send_or_202` (202 HALT). The operator resumes by
    re-issuing with a valid X-Operator-Token. NO mail is sent on the HALT
    path (the 202 fires BEFORE any upstream send).
    """
    await _enforce_tool_grant_or_403(
        session, session_project_id, agent_role, "outlook.external_send"
    )
    _escalate_external_send_or_202(
        operator_proof, project_id=session_project_id, summary=f"to {body.to}",
    )

    creds = await _require_creds("outlook", session_project_id, session)
    _cap_check_or_429("outlook", session_project_id, _OUTLOOK_SEND_UNITS_PER_CALL, "external_send")

    return await _execute_email_send(
        session, session_project_id=session_project_id, agent_role=agent_role,
        provider="outlook", action="external_send", units=_OUTLOOK_SEND_UNITS_PER_CALL,
        tier=EmailTier.EXTERNAL_SEND, approval_mode="operator_confirm",
        client_callable=functools.partial(
            outlook_client.send_message,
            creds, to=body.to, subject=body.subject, body=body.body, cc=body.cc, bcc=body.bcc,
        ),
        audit_recipient=body.to, audit_subject=body.subject,
        body=body.body, response_cls=OutlookSendResponse,
    )
