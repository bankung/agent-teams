"""L17 prevention layer — pickup-time content scan for destructive intent.

The LAST safety net BEFORE the LLM agent runs against task content. Per the
red-team Phase 7 attack chain step 6: if all earlier layers fail (L14
creation-time tag, L15 template confirm, L16 resume-context sanitize) and a
task with destructive content (e.g., `TRUNCATE tasks_history`,
`DROP TABLE projects`, `rm -rf /`) makes it to the worker's auto-pickup path,
the LLM agent SHOULD refuse per CLAUDE.md golden rules — but that's
prompt-layer discipline only. L17 hard-codes the refusal at the worker layer.

Scope (intentionally narrow): a static regex pass over `title + description +
acceptance_criteria[*].text`. Matches surface as a list of pattern names so the
worker can stamp `halt_reason='destructive_intent_detected'` + a
`status_change_reason` listing the matched patterns, then PATCH BLOCKED
WITHOUT invoking the LLM (zero token spend, zero side effects).

Patterns cover both raw SQL DDL/DML (DROP TABLE/DATABASE/SCHEMA, TRUNCATE,
DELETE FROM, ALTER TABLE ... DISABLE/DROP TRIGGER/CONSTRAINT) and the most
dangerous shell escapes (`rm -rf`, `dropdb`, `docker volume rm`). The regex
list is intentionally short — false positives here permanently lock a task
until a human reviews, so the bar is "uncommon enough in legitimate task text
that the cost of a manual unblock is acceptable."

L14 sibling note: the spec calls for a shared `api/.../content_moderation`
module. L14 hasn't shipped yet (P2 batch); this file inlines its own scanner
so L17 doesn't block on L14. When L14 lands it should refactor langgraph to
import from the shared module — keeping two copies of the pattern list in
sync is the kind of drift that causes a "we thought we patched it" incident.

Incident reference: context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Pattern name → compiled regex. Case-insensitive across the board (operators
# write `DROP TABLE` in caps; LLM-drafted task descriptions write `drop table`
# in flowing prose). \b word-boundaries keep `DELETE FROM` from matching e.g.
# `selete from-bottom` or other accidental substrings inside identifier names.
_DESTRUCTIVE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("DROP_TABLE", re.compile(r"(?i)\bDROP\s+(TABLE|DATABASE|SCHEMA)\b")),
    ("TRUNCATE", re.compile(r"(?i)\bTRUNCATE\b")),
    ("DELETE_FROM", re.compile(r"(?i)\bDELETE\s+FROM\b")),
    (
        "ALTER_TRIGGER",
        re.compile(
            r"(?i)\bALTER\s+TABLE\b.*\b(DISABLE|DROP)\s+(TRIGGER|CONSTRAINT)\b"
        ),
    ),
    ("SHELL_RM", re.compile(r"(?i)\b(rm\s+-rf|dropdb|docker\s+volume\s+rm)\b")),
]


def scan_task_content(
    title: str | None,
    description: str | None,
    acceptance_criteria: Iterable[Any] | None = None,
) -> list[str]:
    """Return a list of matched destructive pattern names. Empty = clean.

    Concatenates title + description + each AC's `text` field (when present)
    into a single haystack and runs every pattern from `_DESTRUCTIVE_PATTERNS`
    against it. The return preserves pattern-list order (deterministic for
    test assertions + audit logs); duplicates are not de-duped beyond the
    "one entry per pattern that matched" granularity (we don't care if
    `TRUNCATE` appears twice).

    AC items may be dicts (`{"text": "...", "status": "..."}`) per the JSONB
    column shape, or Pydantic-model instances with a `.text` attribute. Both
    are accepted; anything else is silently skipped. Halt-reason / status-
    change-reason strings are deliberately NOT scanned — the worker stamps
    those itself when it halts, and re-scanning them would create a self-
    triggering loop on re-pickup (the halt body contains the matched pattern
    name, which would re-match on next poll).

    See module docstring for the LLM-bypass contract this enables.
    """
    matched: list[str] = []
    haystacks: list[str] = []
    if title:
        haystacks.append(title)
    if description:
        haystacks.append(description)
    if acceptance_criteria:
        for ac in acceptance_criteria:
            text: Any = None
            if isinstance(ac, dict):
                text = ac.get("text")
            else:
                text = getattr(ac, "text", None)
            if text:
                haystacks.append(str(text))
    if not haystacks:
        return matched
    full = "\n".join(haystacks)
    for name, regex in _DESTRUCTIVE_PATTERNS:
        if regex.search(full):
            matched.append(name)
    return matched
