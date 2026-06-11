"""tool_calls audit writer (Kanban #980).

Single public entrypoint: `record_tool_call(...)`. The langgraph
specialist tool-use loop (wired by #981) calls this synchronously
before / after each tool invocation; the audit row is committed
BEFORE the LLM sees the tool result (Q9 → A lock: synchronous audit
writes block the loop until the row is durable). Latency cost
~5 ms; crash mid-tool means no audit but the task halts anyway.

Truncation rules (Q10 → A lock):

- `output_summary` = first 256 chars of `result.output` (raw cut).
  UTF-8 mid-character risk accepted because this is a SUMMARY for
  the timeline UI, not a transcript. Full output reconstruction is
  not a goal of the audit table.
- `error_msg` = first 1024 chars of `result.error_msg` (raw cut).
  Mirrors the 1 KB cap noted in the migration docstring.

The writer is robust to None / empty values: a missing `output` or
`error_msg` lands as NULL (the columns are nullable); an empty
string lands as "" (preserved verbatim — not coerced to NULL).
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.tool_call import ToolCall

# Locked at #949 Q10 → A. Raw byte cut.
_OUTPUT_SUMMARY_MAX_CHARS = 256
_ERROR_MSG_MAX_CHARS = 1024

# Lead-activity summary cap (#2320) + #2136 non-printable strip. The summary is
# Lead-supplied and lands on an LLM-facing surface (the activity rail + future
# auditor mining). Mirror the tools_email.py #2136 convention: keep
# ASCII-printable (\x20-\x7E) + the Thai block (U+0E00-U+0E7F), replace the rest
# with '?'. Applied BEFORE the cap so the cap counts post-sanitize chars.
_LEAD_SUMMARY_MAX_CHARS = 2000
_NON_PRINTABLE_RE = re.compile(r"[^\x20-\x7E฀-๿]")


def _truncate(value: str | None, limit: int) -> str | None:
    """Raw cut to `limit` characters. None passes through. Empty string is
    preserved verbatim (NOT coerced to None — callers may want to record
    the absence of output as distinct from "tool did not set output").
    """
    if value is None:
        return None
    if len(value) <= limit:
        return value
    return value[:limit]


async def record_tool_call(
    task_id: int,
    tool_name: str,
    tier: str,
    input_args: dict[str, Any],
    result: dict[str, Any],
    permission_decision: str,
    db: AsyncSession,
) -> ToolCall:
    """Write one audit row synchronously and return the persisted ToolCall.

    Args:
        task_id: Kanban task id that owns this tool call. Required (NOT
            NULL on the DB column).
        tool_name: registered tool name (e.g. 'file_edit', 'http_get').
        tier: 'read' / 'write' / 'network' / 'destructive'. Free-form on
            the wire — no validation here; the tool itself is the source
            of truth.
        input_args: the tool's validated input args (dict). Stored as-is
            in JSONB so a future replay/forensic audit can reconstruct
            the exact call.
        result: serialized `ToolCallResult` dict (post-filter — the
            langgraph-side `retry_safe` is dropped before reaching here).
            Expected keys: success (bool), error_code (str|None),
            error_msg (str|None), output (str|None), duration_ms (int).
            Missing keys are treated defensively (success defaults False,
            duration_ms defaults 0, the rest default None).
        permission_decision: 'auto_allow' / 'halt' / 'reject'.
        db: AsyncSession the caller owns. The writer flushes + commits;
            the caller is responsible for rolling back its own transaction
            on exception.

    Returns:
        The persisted ToolCall row (with `id` populated by the DB).

    The function is intentionally NOT exception-swallowing — if the audit
    row fails to write, the loop's invariant ("invocation has a paired
    audit row") is broken and the caller MUST halt the task. Re-raise.
    """
    row = ToolCall(
        task_id=task_id,
        tool_name=tool_name,
        tier=tier,
        input_json=input_args or {},
        success=bool(result.get("success", False)),
        error_code=result.get("error_code"),
        error_msg=_truncate(result.get("error_msg"), _ERROR_MSG_MAX_CHARS),
        output_summary=_truncate(
            result.get("output"), _OUTPUT_SUMMARY_MAX_CHARS
        ),
        duration_ms=int(result.get("duration_ms") or 0),
        permission_decision=permission_decision,
    )
    db.add(row)
    # Synchronous commit — blocks until the row is durable. Per Q9 → A
    # lock: latency cost ~5 ms acceptable; the alternative (fire-and-
    # forget queue) lets a crash-mid-tool drop the audit row silently.
    await db.commit()
    await db.refresh(row)
    return row


async def record_lead_activity(
    task_id: int,
    kind: str,
    summary: str,
    success: bool,
    tool_name: str | None,
    db: AsyncSession,
) -> ToolCall:
    """Write one Lead report-back checkpoint to the activity rail (#2320).

    A lead row fills source='lead' + kind + summary; the engine-only columns
    (tier / input_json / duration_ms / permission_decision) stay NULL. The
    summary is sanitized (#2136 non-printable strip) then capped at 2000 chars
    BEFORE persist — the value is Lead-supplied and surfaces on the rail UI and
    the future improvement-auditor mining query.

    Args:
        task_id: Kanban task id that owns this checkpoint.
        kind: lead-row taxonomy value (validated by LeadActivityCreate).
        summary: human-readable evidence (sanitize + cap here).
        success: False marks a failure/blocker checkpoint.
        tool_name: optional free label (e.g. the agent name on kind='spawn').
        db: AsyncSession the caller owns; the writer commits.

    Returns:
        The persisted ToolCall row (with `id` populated by the DB).
    """
    safe_summary = _NON_PRINTABLE_RE.sub("?", summary)[:_LEAD_SUMMARY_MAX_CHARS]
    row = ToolCall(
        task_id=task_id,
        source="lead",
        kind=kind,
        summary=safe_summary,
        # Engine-only columns left NULL for lead rows; tool_name is an optional
        # free label (NOT NULL on the column, but defaulted to '' when absent so
        # the existing NOT-NULL tool_name contract is preserved).
        tool_name=tool_name or "",
        success=bool(success),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row
