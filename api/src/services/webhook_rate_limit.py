"""Per-(project_id, tag) sliding-window rate limit for webhook ingest (Kanban #1328 M4b).

Lean v1: in-memory ``dict[(project_id, tag), deque[datetime]]``. The window is
a strict 60-second slide; entries older than the cutoff are popped lazily on
each check. The state is process-local — restart resets all buckets. That's
acceptable for the single-instance docker-compose deployment we target today;
a multi-replica deployment would swap this for a Redis-backed limiter (slowapi
+ moving-window backend), preserving the same ``check_and_consume`` signature.

We picked an in-house deque (rather than reusing the existing ``slowapi``
limiter on ``api/src/middleware/rate_limit.py``) because:

  - slowapi's ``key_func`` resolves at decoration time and receives the FastAPI
    request, so keying on ``(project_id, tag)`` from the URL path requires a
    custom key_func — workable, but the limit decorator is then opaque to
    tests that want to reset per-(project, tag) state granularly.
  - The webhook surface needs a SECOND bucket independent of the projects-POST
    bucket, with its own override env, its own reset hook for tests, and a
    different key shape. A second ``Limiter()`` instance is fine, but the
    explicit module-level deque is simpler and easier to reason about for v1.

If/when we need Redis-backed scale-out, replace the ``_WINDOWS`` dict with a
Redis sorted-set per key and the API surface stays the same.

The router calls ``check_and_consume(project_id, tag, datetime.now(timezone.utc))``
inside the request handler — raise ``RateLimitError`` on overflow; the router
catches and re-raises as HTTPException(429).
"""

from __future__ import annotations

import os
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Final

# Default — 60 hits per (project_id, tag) per minute. The original Kanban
# description called for a 2-tier scheme (60/min soft + 600/min hard); we
# simplified to a single hard cap for v1 per the brief.
WEBHOOK_RATE_LIMIT_PER_MIN_ENV: Final[str] = "WEBHOOK_RATE_LIMIT_PER_MIN"
WEBHOOK_RATE_LIMIT_PER_MIN_DEFAULT: Final[int] = 60

_WINDOW_SECONDS: Final[int] = 60

# Module-level state — per-process. Keys are (project_id, tag) tuples; values
# are deques of UTC datetimes (most recent on the right; we ``popleft`` to
# expire old entries on each check).
_WINDOWS: dict[tuple[int, str], deque[datetime]] = defaultdict(deque)


class RateLimitError(Exception):
    """Raised by ``check_and_consume`` when the per-(project, tag) bucket is full.

    Carries the configured limit + the elapsed-window length so the router can
    render a useful 429 detail string. Module-level (NOT subclass of HTTPException)
    so unit tests on this service don't depend on FastAPI.
    """

    def __init__(self, limit_per_minute: int) -> None:
        self.limit_per_minute = limit_per_minute
        super().__init__(
            f"rate limit exceeded: {limit_per_minute}/min per (project, tag)"
        )


def _resolved_limit() -> int:
    """Read the per-minute cap from env on every call so tests can monkeypatch.

    Invalid / non-int / negative env value → fall back silently to the default.
    The router does not block on a misconfigured env — it just applies the
    documented safe default.
    """
    raw = os.environ.get(WEBHOOK_RATE_LIMIT_PER_MIN_ENV)
    if not raw:
        return WEBHOOK_RATE_LIMIT_PER_MIN_DEFAULT
    try:
        v = int(raw)
        return v if v > 0 else WEBHOOK_RATE_LIMIT_PER_MIN_DEFAULT
    except ValueError:
        return WEBHOOK_RATE_LIMIT_PER_MIN_DEFAULT


def check_and_consume(
    project_id: int,
    tag: str,
    now: datetime | None = None,
    *,
    limit_per_minute: int | None = None,
) -> None:
    """Allow this hit, or raise ``RateLimitError`` if the window is saturated.

    Sliding window: every call evicts entries older than 60s before checking
    the count. ``limit_per_minute`` overrides the env-resolved limit (test hook).

    ``now`` defaults to UTC now; tests inject a fixed clock to verify the
    expiry-eviction path without sleeping.

    On success the current timestamp is appended to the deque AFTER the
    check — so the (N+1)th hit within the window raises BEFORE consuming
    the slot. This is the intuitive "60/min means exactly 60 succeed" shape.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if limit_per_minute is None:
        limit_per_minute = _resolved_limit()

    key = (project_id, tag)
    bucket = _WINDOWS[key]

    # Evict expired entries (oldest first; deque is ordered).
    cutoff = now - timedelta(seconds=_WINDOW_SECONDS)
    while bucket and bucket[0] < cutoff:
        bucket.popleft()

    if len(bucket) >= limit_per_minute:
        raise RateLimitError(limit_per_minute)

    bucket.append(now)


def reset() -> None:
    """Wipe ALL buckets — test-fixture hook.

    Conftest's per-test reset fixture calls this so a test that exercises
    the limit doesn't leak counts into the next test.
    """
    _WINDOWS.clear()
