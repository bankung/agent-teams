"""Per-project sliding-window rate limit for POST /api/usage/events (Kanban #2355).

Mirrors the webhook_rate_limit.py pattern (Kanban #1328 M4b) — an in-memory
deque keyed by project_id. A runaway hook loop (SubagentStop firing repeatedly
with no backoff) would otherwise flood the ingest ledger with duplicate cost
rows.

Default: 60 requests per 10 seconds per project. That comfortably covers normal
capture traffic (a SubagentStop + a few PreCompact/SessionEnd POSTs per minute)
while stopping a tight loop. The window is intentionally SHORT (10s) rather than
the webhook's 60s because:

  - We want fast recovery after the loop breaks — 10s clears, not 60s.
  - The idempotent dedup_key path already handles exact-duplicate retries.
  - 60 hits / 10s = 360/min burst headroom; normal traffic is <10/min.

Override via env ``USAGE_EVENTS_RATE_LIMIT_PER_10S`` (int, > 0).

State is process-local — restart resets all buckets. Fine for single-instance
docker-compose deployment; a multi-replica deployment would swap to Redis-backed
storage preserving the same ``check_and_consume`` signature.
"""

from __future__ import annotations

import os
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Final

USAGE_EVENTS_RATE_LIMIT_ENV: Final[str] = "USAGE_EVENTS_RATE_LIMIT_PER_10S"
USAGE_EVENTS_RATE_LIMIT_DEFAULT: Final[int] = 60

_WINDOW_SECONDS: Final[int] = 10

# Keys are project_id ints; values are deques of UTC datetimes.
_WINDOWS: dict[int, deque[datetime]] = defaultdict(deque)


class RateLimitError(Exception):
    """Raised when the per-project bucket is full."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"rate limit exceeded: {limit} requests per {_WINDOW_SECONDS}s")


def _resolved_limit() -> int:
    """Read the per-10s cap from env on every call so tests can monkeypatch."""
    raw = os.environ.get(USAGE_EVENTS_RATE_LIMIT_ENV)
    if not raw:
        return USAGE_EVENTS_RATE_LIMIT_DEFAULT
    try:
        v = int(raw)
        return v if v > 0 else USAGE_EVENTS_RATE_LIMIT_DEFAULT
    except ValueError:
        return USAGE_EVENTS_RATE_LIMIT_DEFAULT


def check_and_consume(
    project_id: int,
    now: datetime | None = None,
    *,
    limit: int | None = None,
) -> None:
    """Allow this hit, or raise ``RateLimitError`` if the window is saturated.

    Sliding window: evicts entries older than ``_WINDOW_SECONDS`` before
    checking. ``limit`` overrides the env-resolved cap (test hook). ``now``
    defaults to UTC now; tests inject a fixed clock to verify expiry.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if limit is None:
        limit = _resolved_limit()

    bucket = _WINDOWS[project_id]
    cutoff = now - timedelta(seconds=_WINDOW_SECONDS)
    while bucket and bucket[0] < cutoff:
        bucket.popleft()

    if len(bucket) >= limit:
        raise RateLimitError(limit)

    bucket.append(now)


def reset() -> None:
    """Wipe ALL per-project buckets — test-fixture hook."""
    _WINDOWS.clear()
