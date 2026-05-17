"""L14 prevention layer — API content moderation for destructive intent.

Per the red-team Phase 7 sleeper-attack chain: API previously had ZERO content
moderation on task fields. A task body containing `TRUNCATE tasks_history` or
`DROP TABLE projects` was indistinguishable from a benign description. Combined
with auto-headless run_mode + a stale-cron recurrence template, that's the
end-to-end sleeper destruction path.

This module is the CANONICAL scanner for destructive intent across the
codebase. The langgraph worker's `langgraph/content_safety.py` (L17) was shipped
earlier as a temporary inline copy because L14 hadn't landed yet; that file
should refactor to import from here in a follow-up task.

## Scope (intentionally narrow)

A static regex pass over the task fields that a human / agent author can
populate freely: `title`, `description`, each `acceptance_criteria[*].text`,
`halt_reason`, `status_change_reason`. Patterns cover the SQL DDL / DML keywords
that an LLM has no business writing into a task description (and that, if it
DOES, mean the operator should review before any agent picks the task up).

The scanner DOES NOT BLOCK — it TAGS. A match sets `tasks.requires_human_review
= true`, which the auto-headless gate refuses to flip to `auto_headless` until
the reviewer explicitly clears the flag. Operators can still legitimately FILE
a destructive task (quarterly archive purge, schema rotation, etc.) — they
just can't accidentally auto-run it without one explicit human ack.

## Pattern philosophy

False positives here cost a manual unblock per task — annoying but tractable.
False negatives let the sleeper through. So the bar is "uncommon in legitimate
task text, common in destructive intent": `DROP TABLE foo`, `TRUNCATE bar`,
`ALTER TABLE x DISABLE TRIGGER`, etc. Plain English like "Add cleanup migration
for archived rows" does NOT match — the `DELETE_FROM` pattern requires `DELETE`
adjacent to `FROM`, not just the word "cleanup".

Pattern list is kept in lockstep with `langgraph/content_safety.py` for now;
the L17 follow-up will collapse them to a single import.

## What this module deliberately does NOT do

- **No semantic understanding.** "Wipe the database" is not flagged unless the
  author writes the literal `TRUNCATE` / `DROP` keyword. That's a known gap —
  semantic detection is L19+ scope.
- **No blocking.** Per the spec, the scanner TAGS only. The auto-headless gate
  in `routers/tasks.py` is what stops the destruction chain.
- **No re-scan on resume_context.** `resume_context` is server-written
  partial-work state, not user-author content — scanning would produce
  self-triggering loops when the worker stores a halted task's
  status_change_reason ("destructive_intent_detected: matched fields...")
  into its own resume context.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Compiled regex patterns. Case-insensitive — operators write `DROP TABLE` in
# caps, LLM-drafted task prose writes `drop table` in flowing sentences. The
# \b word-boundary anchors keep e.g. `selete from-bottom` or `delete-from-list`
# (a hyphenated identifier) from false-matching on the `DELETE_FROM` pattern.
#
# Order is significant only for test assertion stability (the scanner returns
# matched field names, not pattern names — but the field-name list preserves
# insertion order, so a deterministic pattern-list order keeps the audit log
# stable across runs).
DESTRUCTIVE_PATTERNS: list[re.Pattern[str]] = [
    # DDL drops on whole-relation objects. NOT matched: column drops on a
    # specific table — that's narrower than catastrophic and already gated by
    # the ALTER TABLE pattern below.
    re.compile(
        r"\bDROP\s+(TABLE|DATABASE|SCHEMA|INDEX|TRIGGER|FUNCTION)\b",
        re.IGNORECASE,
    ),
    # TRUNCATE on any object. The `(TABLE\s+)?` makes `TRUNCATE TABLE foo` and
    # the shorter `TRUNCATE foo` both match; `\w+` requires an identifier to
    # follow so the bare word "truncate" in prose doesn't fire.
    re.compile(r"\bTRUNCATE\s+(TABLE\s+)?\w+", re.IGNORECASE),
    # DELETE without a WHERE clause is the unbounded-DML case the hammer test
    # actually catches; we match the looser DELETE FROM because the WHERE
    # variant ALSO deserves a human review when authored as task content.
    # NOTE: `Add cleanup migration for archived rows` does NOT match — there
    # is no `DELETE` keyword in that sentence at all.
    re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE),
    # ALTER TABLE ... DISABLE/DROP TRIGGER/CONSTRAINT/COLUMN. The DOTALL flag
    # lets the ".*" between `ALTER TABLE` and `DISABLE TRIGGER` span newlines
    # — multi-line SQL pasted into a description still matches.
    re.compile(
        r"\bALTER\s+TABLE\b.*\b(DISABLE|DROP)\s+(TRIGGER|CONSTRAINT|COLUMN)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    # Permission grants — privilege escalation via task content is the same
    # sleeper class. `GRANT ALL ON tasks TO public` is the textbook example.
    re.compile(r"\bGRANT\b.*\bON\b.*\bTO\b", re.IGNORECASE),
    re.compile(r"\bREVOKE\b.*\bON\b", re.IGNORECASE),
    # Shell escape into Docker — most dangerous variants only. `docker compose
    # down -v` wipes the volume; `docker exec db dropdb` / `psql` lets an
    # agent reach the live DB outside the FastAPI gate.
    re.compile(
        r"\bdocker\s+(compose\s+)?(down\s+-v|exec\s+\S*db\s+(psql|dropdb))\b",
        re.IGNORECASE,
    ),
]


def contains_destructive_intent(text: str | None) -> bool:
    """Return True if `text` matches any destructive pattern. None / empty
    string returns False (the calling field is simply not authored).

    Cheap (single-string sweep over the compiled patterns) — safe to call on
    every POST / PATCH path without measurable latency cost.
    """
    if not text:
        return False
    return any(p.search(text) for p in DESTRUCTIVE_PATTERNS)


def scan_task_payload(
    *,
    title: str | None,
    description: str | None = None,
    acceptance_criteria: Iterable[Any] | None = None,
    halt_reason: str | None = None,
    status_change_reason: str | None = None,
) -> list[str]:
    """Scan every author-supplied task field. Returns the list of matched
    field names (empty list = clean).

    Field names returned use dotted-path notation for AC items so the operator
    UI can highlight the exact element: `acceptance_criteria[3].text` rather
    than the bare `acceptance_criteria` (which would imply the whole list).

    `acceptance_criteria` is accepted as both list-of-dicts (the JSONB stored
    shape) and list-of-Pydantic-models (the validated TaskCreate shape) — the
    router calls this from BOTH positions: after Pydantic parses the body but
    before the model_dump for JSONB write. Anything in the list that lacks a
    `text` field is silently skipped (defensive; should not occur post-Pydantic).

    Keyword-only signature (note the `*` in the def) — every field is optional,
    and positional ordering would silently corrupt the field-name list if a
    future caller forgets which slot is which.
    """
    matched: list[str] = []
    if contains_destructive_intent(title):
        matched.append("title")
    if contains_destructive_intent(description):
        matched.append("description")
    if acceptance_criteria:
        for i, ac in enumerate(acceptance_criteria):
            text: Any = None
            if isinstance(ac, dict):
                text = ac.get("text")
            else:
                text = getattr(ac, "text", None)
            if contains_destructive_intent(text):
                matched.append(f"acceptance_criteria[{i}].text")
    if contains_destructive_intent(halt_reason):
        matched.append("halt_reason")
    if contains_destructive_intent(status_change_reason):
        matched.append("status_change_reason")
    return matched


# ----------------------------------------------------------------------------
# L23 prevention layer — agent-output sanitizer (Kanban #1126, 2026-05-17)
# ----------------------------------------------------------------------------
#
# DIFFERENT CONTRACT FROM THE SCANNER ABOVE. The L14 scanner above TAGS author
# content for human review (sticky flag, doesn't block); this sanitizer REFUSES
# to forward an extracted command — caller treats None as "halt + escalate".
#
# Motivating incident: Phase 9B Ollama red-team finding (2026-05-17). When the
# operator asked a local LLM to execute a destructive task, 2 of 3 Ollama
# models REFUSED but echoed the dangerous SQL string verbatim inside their
# refusal explanation:
#
#   "...the task contains a destructive SQL command
#    (`DELETE FROM tasks WHERE process_status = 5;`) which can potentially
#    delete data..."
#
# If a downstream extractor pulls "what to do next" out of agent free-form
# output (e.g., a recommender that greps "the exact shell command" from the
# LLM's reply, or any code that forwards `final_result` into an executable
# context), it would pick up the SQL string and execute it — even though the
# agent was refusing. That's the L23 hole this function plugs.
#
# Pattern set is intentionally TIGHTER than the L14 scanner: only the
# truly-cannot-be-forwarded keywords (DROP relation, TRUNCATE, DELETE FROM,
# ALTER TABLE DISABLE/DROP TRIGGER/CONSTRAINT). The L14 scanner's broader
# fence (GRANT/REVOKE, docker shell escapes) deliberately stays out — those
# matter for AUTHOR-time intent flagging but a refusal text legitimately
# discussing "we should not GRANT ALL on tasks to public" should still be
# forwardable. The trade is per-layer false-positive cost: L14 false-positive
# = one human ack to unblock; L23 false-positive = halt the task. The L23
# bar is higher.
DANGEROUS_IN_ACTION = re.compile(
    r"(?i)\b(DROP\s+(TABLE|DATABASE|SCHEMA|TRIGGER|FUNCTION)"
    r"|TRUNCATE\b"
    r"|DELETE\s+FROM\b"
    r"|ALTER\s+TABLE\b.*\b(DISABLE|DROP)\s+(TRIGGER|CONSTRAINT))",
    re.DOTALL,
)


def sanitize_agent_action(text: str | None) -> str | None:
    """Return None if `text` contains destructive SQL — caller must escalate to a human.

    Intended use: extracting an actionable command / next-step / status message
    from agent free-form output BEFORE it lands in an executable or
    operator-trusted context. Returning None signals "the agent echoed
    something we refuse to forward; halt the task and surface to a human."

    Contract:
      - None / empty string in → same value out (no extraction happened).
      - Clean text in → text out unchanged.
      - Text containing a destructive SQL pattern → None.

    None is the explicit halt signal so callers MUST branch on it; the caller
    decides whether to (a) replace the field with a safe placeholder, (b) drop
    the field entirely, or (c) PATCH a halt_reason. This module does NOT make
    that policy call.

    See incident 2026-05-17 Phase 9B (Ollama refused but echoed SQL) and the
    L23 spec at _scratch/pending-kanban-2026-05-17/27-p2-bug-L23-agent-output-sanitizer.md.
    """
    if not text:
        return text
    if DANGEROUS_IN_ACTION.search(text):
        return None
    return text
