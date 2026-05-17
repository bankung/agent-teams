"""L16 prevention layer — langgraph-side mirror of the API canonical sanitizer.

Kanban #1123 (2026-05-17). The CANONICAL source lives at
`api/src/services/agent_context_sanitizer.py`. This file is an intentional
copy because the langgraph container does NOT import the api package (separate
pyproject + container; same precedent as `langgraph/content_safety.py` for
L17). Keep the two copies in lockstep — drift here would mean an attacker
payload caught by one layer leaks past the other.

Pattern set: DROP / TRUNCATE / DELETE / ALTER / GRANT / REVOKE / EXEC / EXECUTE
(word-boundary, case-insensitive). Cap: 500 chars per field after redaction.

When the api package becomes importable from this container (e.g., a future
shared `agent-teams-common` package), this file should re-export from the
canonical source — flagged in `langgraph/content_safety.py`'s module docstring
as a known cross-cutting refactor.
"""

from __future__ import annotations

import re

# Keep in lockstep with api/src/services/agent_context_sanitizer.py
# (Kanban #1123). The set covers SQL DDL/DML keywords that, when redacted,
# prevent the LLM from interpreting an attacker-controlled halt_reason or
# status_change_reason as a literal destructive instruction.
_DESTRUCTIVE_KEYWORDS = re.compile(
    r"\b(DROP|TRUNCATE|DELETE|ALTER|GRANT|REVOKE|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)

# 500 chars — half the L18 wire-contract cap of 1000 (TaskCreate / TaskUpdate
# halt_reason / status_change_reason max_length). Defense in depth: the
# narrower agent-context cap leaves even less room for attacker-controlled
# padding than the DB column allows.
_MAX_AGENT_CONTEXT_LEN = 500


def sanitize_for_agent_context(text: str | None) -> str:
    """Return `text` with destructive keywords redacted and capped at 500 chars.

    Returns "" (empty string, NOT None) for None / empty input — callers can
    safely f-string the result without a guard. The literal token `[REDACTED]`
    (brackets, all caps) is wire contract pinned by tests + by Kanban #1123 AC4.

    Mirror of api/src/services/agent_context_sanitizer.py. Keep in lockstep.
    """
    if not text:
        return ""
    redacted = _DESTRUCTIVE_KEYWORDS.sub("[REDACTED]", text)
    return redacted[:_MAX_AGENT_CONTEXT_LEN]
