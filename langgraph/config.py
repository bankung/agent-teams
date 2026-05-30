"""Central runtime configuration helpers (Phase 1 minimization).

Consolidates three duplicated patterns that previously lived in
nodes.py, audit.py, and worker.py:

  - DEFAULT_API_BASE + resolve_api_base() — the Kanban API base URL.
  - resolve_project_id()                  — LANGGRAPH_PROJECT_ID → int | None.
  - utc_now()                             — UTC ISO-8601 with 'Z' suffix.

All three callers previously had identical or near-identical inline copies.
Wire format contract: utc_now() returns the 'Z'-terminated ISO format that
the Kanban API and auditor tests pin (e.g. '2026-05-30T12:34:56.789012Z').
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

# Compose-internal hostname for the Kanban API. Every langgraph container
# component (nodes, audit, worker) resolves through this default.
DEFAULT_API_BASE: str = "http://api:8456"


def resolve_api_base() -> str:
    """Return the Kanban API base URL, stripping any trailing slash.

    Reads LANGGRAPH_KANBAN_API_BASE at call time (not module import time)
    so tests can monkeypatch the env-var per-invocation without re-importing.
    """
    return (
        os.getenv("LANGGRAPH_KANBAN_API_BASE", DEFAULT_API_BASE)
        .strip()
        .rstrip("/")
    )


def resolve_project_id() -> int | None:
    """Resolve LANGGRAPH_PROJECT_ID to an int, or None if absent/malformed.

    The langgraph container is bound to a single project; this env-var is
    the authoritative source. Returns None rather than raising so callers
    can fall back gracefully (e.g. skip the audit POST, skip tools_config
    fetch).
    """
    raw = os.getenv("LANGGRAPH_PROJECT_ID", "").strip()
    if not raw or not raw.isdigit():
        return None
    return int(raw)


def utc_now() -> str:
    """UTC ISO-8601 timestamp with 'Z' suffix.

    Shape: '2026-05-30T12:34:56.789012Z'
    Matches the Kanban API's timestamp shape and the auditor's existing
    _iso_now_utc() format (nodes.py). The worker's old _now_iso() used the
    '+00:00' form; no test pinned that exact shape, so standardizing on 'Z'
    is safe.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
