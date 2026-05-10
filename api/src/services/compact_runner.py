"""Compact runner — Haiku 4.5 LLM-summarize Recent Activity (CTX-4, Kanban #719).

Pipeline (`run_compact`):
1.  Atomic status lock: `UPDATE sessions SET status='compacting' WHERE id=:sid
    AND status='active' RETURNING id`. Zero rows → 404/400/409 (caller
    distinguishes by re-reading the row).
2.  Read `## Recent Activity` + `## Compacted History` via CTX-2.
3.  Build Anthropic prompt (system + single user message). System carries the
    summarization brief; user carries PRIOR COMPACTED HISTORY + NEW ACTIVITY.
4.  Call `claude-haiku-4-5-20251001`, max_tokens=4096. respx stubs this in tests.
5.  Compute `before_tokens` (Recent Activity input via CTX-3 `count_tokens`),
    `after_tokens` (LLM summary text via CTX-3), and `compact_cost_usd` from
    SDK-reported `usage.input_tokens` / `usage.output_tokens` via CTX-3
    `compute_cost`. Falls back to chars/4 estimate if SDK usage missing.
6.  Pick next archive ordinal — scan `_sessions/<sid>/archive/compact_*.md`,
    take max+1, zero-pad to 3 digits.
7.  Write `_sessions/<sid>/archive/compact_NNN.md` (header + original Recent
    Activity body + LLM summary footer for audit).
8.  REPLACE `## Compacted History` with the LLM summary (the LLM already had
    the prior compacted history as context — it decides what to merge). RESET
    `## Recent Activity` to empty body.
9.  INSERT `session_compacts` row.
10. Mark `sessions.status='active'` (lock release). On any failure between
    steps 4-9, the `try/finally` lock-release in `run_compact` flips status
    back to 'active' so a stuck row is impossible.

Design notes:
- Compacted History strategy: REPLACE (not concat). Spec says "rebuild";
  the LLM is given the prior compacted history in its prompt and is
  expected to fold-in what it wants. Concat would double-count.
- Recent Activity reset: `replace_section(... "")` produces a single
  newline body (CTX-2 enforces trailing newline). Tests assert this shape.
- Lock semantics: V1 single-process FastAPI — DB row's status field IS
  the lock. Multi-process gunicorn would need advisory locks (not in scope).
- Status flip back to 'active' on failure: `try/finally` block. The runner
  never raises with status stuck at 'compacting'.
- Anthropic key: `ANTHROPIC_API_KEY` from env. Missing key surfaces as 503
  via `MissingApiKey` exception caught at the router; the runner itself
  refuses to instantiate the client without a key.
- Provider error: any exception from `messages.create` is wrapped as
  `AnthropicCallFailed` so the router can return a uniform 502 without
  leaking provider error details to the caller.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import SessionCompactTrigger
from src.models.session import Session as SessionModel
from src.models.session import SessionCompact
from src.schemas.session import SessionCompactTriggerLiteral as CompactTriggerKind
from src.services.cost_tracker import compute_cost
from src.services.session_store import (
    SECTION_COMPACTED_HISTORY,
    SECTION_RECENT_ACTIVITY,
    get_section_text,
    replace_section,
)
from src.services.token_counter import count_tokens

logger = logging.getLogger(__name__)


# Public constants — pinned so router + tests reference one source of truth.
COMPACT_MODEL = "claude-haiku-4-5-20251001"
COMPACT_PROVIDER = "anthropic"
COMPACT_MAX_TOKENS = 4096
COMPACT_API_KEY_ENV = "ANTHROPIC_API_KEY"


# Source-text-locked detail strings — referenced by router constants. Tests
# assert byte-equality.
_COMPACT_SYSTEM_PROMPT = (
    "Summarize the following kanban session activity into a compact, dense "
    "markdown summary. Preserve concrete decisions, errors, and tasks "
    "involved. Drop conversational filler. Target ~3000 tokens. Output ONLY "
    "the markdown summary, no preamble."
)


# =============================================================================
# Exceptions
# =============================================================================


class CompactRunnerError(Exception):
    """Base for compact-runner-specific failures."""


class SessionNotFound(CompactRunnerError):
    """Session id does not exist."""


class SessionClosed(CompactRunnerError):
    """Session is closed; cannot compact."""


class SessionAlreadyCompacting(CompactRunnerError):
    """Another compact is in flight on this session."""


class MissingApiKey(CompactRunnerError):
    """ANTHROPIC_API_KEY env var is unset/empty."""


class AnthropicCallFailed(CompactRunnerError):
    """The Anthropic API call raised. Underlying exception is logged, not
    re-exposed to the HTTP client (security)."""


# =============================================================================
# Helpers
# =============================================================================


_ARCHIVE_NAME_RE = re.compile(r"^compact_(\d{3})\.md$")


def _next_archive_ordinal(session_id: int, repo_root: Path) -> int:
    """Scan `_sessions/<sid>/archive/` for `compact_NNN.md` and return max+1.

    Empty / missing dir → 1. Files that don't match the strict pattern
    (e.g. user-dropped notes) are ignored.
    """
    archive_dir = Path(repo_root) / "_sessions" / str(session_id) / "archive"
    if not archive_dir.is_dir():
        return 1
    max_seen = 0
    for entry in archive_dir.iterdir():
        m = _ARCHIVE_NAME_RE.match(entry.name)
        if m is not None:
            n = int(m.group(1))
            if n > max_seen:
                max_seen = n
    return max_seen + 1


def _build_user_message(*, prior_compacted: str, recent_activity: str) -> str:
    """Construct the user-side message body. Two sections, byte-stable."""
    return (
        "PRIOR COMPACTED HISTORY:\n"
        f"{prior_compacted.strip() or '(none)'}\n"
        "\n"
        "NEW ACTIVITY TO COMPACT:\n"
        f"{recent_activity.strip() or '(none)'}\n"
    )


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_archive(
    *,
    ordinal: int,
    trigger_kind: str,
    prior_compacted: str,
    recent_body: str,
    summary: str,
) -> str:
    """Build the audit archive file body.

    Three sections after the header — captures BOTH inputs the LLM saw
    (prior Compacted History + original Recent Activity) plus the output
    (LLM Summary). The prior Compacted History is critical: `run_compact`
    REPLACES `## Compacted History` with the LLM output, so without this
    section a poor summary would permanently lose the prior context.
    """
    header = f"# Compact {ordinal:03d} — {_utc_now_iso()} — trigger={trigger_kind}\n"
    return (
        f"{header}"
        "\n"
        "## Prior Compacted History (verbatim — input context to this compact)\n"
        f"{prior_compacted.rstrip() or '(none)'}\n"
        "\n"
        "## Original Recent Activity (verbatim)\n"
        f"{recent_body.rstrip()}\n"
        "\n"
        "## LLM Summary\n"
        f"{summary.rstrip()}\n"
    )


# =============================================================================
# Status lock (atomic UPDATE)
# =============================================================================


async def _acquire_compact_lock(db: AsyncSession, session_id: int) -> None:
    """Atomic flip 'active' -> 'compacting'. Raise typed exception on conflict.

    Uses `RETURNING id` so a 0-row update is unambiguous. On conflict, a
    follow-up SELECT distinguishes 404 (no row) from 400 (closed) from 409
    (already compacting).
    """
    stmt = (
        update(SessionModel)
        .where(SessionModel.id == session_id, SessionModel.status == "active")
        .values(status="compacting", updated_at=text("now()"))
        .returning(SessionModel.id)
    )
    result = await db.execute(stmt)
    locked_id = result.scalar_one_or_none()
    if locked_id is not None:
        await db.commit()
        return

    # 0 rows updated — re-read to classify.
    sess = await db.get(SessionModel, session_id)
    if sess is None:
        raise SessionNotFound(f"Session id={session_id} not found")
    if sess.status == "closed":
        raise SessionClosed(f"Session id={session_id} is closed; cannot compact")
    if sess.status == "compacting":
        raise SessionAlreadyCompacting(
            f"Session id={session_id} is already compacting"
        )
    # Defensive: any other state (shouldn't happen with current CHECK).
    raise CompactRunnerError(
        f"Session id={session_id} not in lockable state (status={sess.status!r})"
    )


async def _release_compact_lock(db: AsyncSession, session_id: int) -> None:
    """Flip 'compacting' -> 'active' unconditionally. Best-effort."""
    try:
        await db.execute(
            update(SessionModel)
            .where(
                SessionModel.id == session_id, SessionModel.status == "compacting"
            )
            .values(status="active", updated_at=text("now()"))
        )
        await db.commit()
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "compact runner: failed to release lock for session_id=%d",
            session_id,
        )


# =============================================================================
# Anthropic call
# =============================================================================


async def _call_anthropic(
    *, system_prompt: str, user_message: str
) -> tuple[str, int, int]:
    """Invoke the Haiku model. Returns `(summary_text, input_tokens, output_tokens)`.

    Raises `MissingApiKey` if env var is unset, `AnthropicCallFailed` for any
    error from the SDK / network. Token counts come from `usage` on the
    response when present; otherwise fall back to chars/4.
    """
    api_key = os.environ.get(COMPACT_API_KEY_ENV)
    if not api_key:
        raise MissingApiKey(
            f"compact runner unavailable: {COMPACT_API_KEY_ENV} not configured"
        )

    # Imported lazily so test environments without the SDK installed can still
    # import the module (the import error would otherwise mask the cleaner
    # MissingApiKey signal).
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key)
    try:
        response = await client.messages.create(
            model=COMPACT_MODEL,
            max_tokens=COMPACT_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:  # noqa: BLE001 — log + wrap, hide details from client
        logger.warning(
            "compact runner: Anthropic API call failed: %s", exc, exc_info=True
        )
        raise AnthropicCallFailed("compact runner: Anthropic API call failed") from exc

    # Extract first text block. Defensive — content may be empty if the model
    # produced only tool_use blocks (we don't pass tools, so this shouldn't
    # happen in practice).
    summary = ""
    for block in getattr(response, "content", []) or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            summary = getattr(block, "text", "") or ""
            break

    usage = getattr(response, "usage", None)
    if usage is not None:
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    else:  # pragma: no cover — every real response carries usage.
        input_tokens = count_tokens(user_message + system_prompt)
        output_tokens = count_tokens(summary)

    return summary, input_tokens, output_tokens


# =============================================================================
# Public entry point
# =============================================================================


async def run_compact(
    session_id: int,
    *,
    trigger_kind: CompactTriggerKind,
    db: AsyncSession,
    repo_root: Path,
) -> SessionCompact:
    """Compact a session's Recent Activity into Compacted History via Haiku.

    See module docstring for the 12-step pipeline. Returns the inserted
    `SessionCompact` audit row.
    """
    if trigger_kind not in SessionCompactTrigger.ALL:
        # Defensive — Pydantic guards this on the router, but the runner is
        # also called by the (future) automation path.
        raise ValueError(f"unknown trigger_kind {trigger_kind!r}")

    # Step 1 — atomic lock acquire.
    await _acquire_compact_lock(db, session_id)

    try:
        # Steps 2-3 — read sections + build prompt.
        recent_body = get_section_text(
            session_id, SECTION_RECENT_ACTIVITY, repo_root
        )
        prior_compacted = get_section_text(
            session_id, SECTION_COMPACTED_HISTORY, repo_root
        )
        before_tokens = count_tokens(recent_body)

        user_message = _build_user_message(
            prior_compacted=prior_compacted, recent_activity=recent_body
        )

        # Step 4 — call Anthropic. Wrapping exceptions surface to router.
        summary, input_tokens, output_tokens = await _call_anthropic(
            system_prompt=_COMPACT_SYSTEM_PROMPT, user_message=user_message
        )

        # Step 5 — compute audit metrics.
        after_tokens = count_tokens(summary)
        try:
            cost_usd: Decimal = compute_cost(
                COMPACT_PROVIDER, COMPACT_MODEL, input_tokens, output_tokens
            )
        except ValueError:
            # Unknown model — shouldn't happen for the pinned model, but
            # don't block the compact on a missing price-card entry.
            logger.warning(
                "compact runner: cost lookup failed for model=%r; storing 0.0",
                COMPACT_MODEL,
            )
            cost_usd = Decimal("0")

        # Steps 6-7 — pick ordinal + write archive.
        ordinal = _next_archive_ordinal(session_id, repo_root)
        archive_dir = (
            Path(repo_root) / "_sessions" / str(session_id) / "archive"
        )
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_filename = f"compact_{ordinal:03d}.md"
        archive_path = archive_dir / archive_filename
        archive_path.write_text(
            _format_archive(
                ordinal=ordinal,
                trigger_kind=trigger_kind,
                prior_compacted=prior_compacted,
                recent_body=recent_body,
                summary=summary,
            ),
            encoding="utf-8",
        )

        # Step 8 — replace sections.
        # Compacted History strategy: REPLACE with LLM output. The LLM saw
        # prior compacted history as input and is expected to fold-in what
        # it wants. Concat would double-count.
        replace_section(
            session_id, SECTION_COMPACTED_HISTORY, summary, repo_root
        )
        # Recent Activity reset: empty body — CTX-2 enforces trailing newline,
        # so the body becomes "\n" (single blank line under the heading).
        replace_section(
            session_id, SECTION_RECENT_ACTIVITY, "", repo_root
        )

        # Step 9 — audit row insert. Path stored relative to repo_root for
        # operator-readable display + portability across env-specific roots.
        rel_path = (
            f"_sessions/{session_id}/archive/{archive_filename}"
        )
        compact_row = SessionCompact(
            session_id=session_id,
            trigger_kind=trigger_kind,
            archive_path=rel_path,
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            compact_model=COMPACT_MODEL,
            compact_cost_usd=cost_usd,
        )
        db.add(compact_row)
        await db.commit()
        await db.refresh(compact_row)

        # Step 10 — release the lock (status -> active).
        await _release_compact_lock(db, session_id)

        return compact_row

    except Exception:
        # Any failure in steps 2-9: release the lock so the session is not
        # stuck in 'compacting' forever. Re-raise so the router maps to the
        # right HTTP code.
        await _release_compact_lock(db, session_id)
        raise
