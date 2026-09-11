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

Kanban #2833 (MED) fixed two defects in the v1 design above:

  - Emptied buckets were never removed from ``_WINDOWS``, so every distinct
    (project_id, tag) pair ever seen left a permanent dict entry behind —
    unbounded memory growth. ``check_and_consume`` now deletes a key the
    moment its deque drains to empty after eviction (both the per-tag bucket
    below and the new per-project one).
  - ``tag`` is caller-controlled (URL path param on the public ingest
    endpoint), so varying it produced a fresh 60/min bucket per tag,
    bypassing the intended aggregate cap. Fixed by ADDING a per-project
    aggregate bucket (``_PROJECT_WINDOWS``) capped at ``limit_per_minute *
    WEBHOOK_RATE_LIMIT_PROJECT_MULTIPLIER`` (env-tunable, default 5x) rather
    than collapsing the key to project_id alone — an existing test
    (``test_webhook_rate_limit_resets_between_project_tag_pairs``) locks
    per-(project, tag) isolation as intentional (distinct tags are distinct
    legitimate integrations, e.g. calendly vs github_issue, and must not
    share one tiny bucket). The per-tag bucket is unchanged; the aggregate
    ceiling is additive on top of it.
"""

from __future__ import annotations

import os
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Final, Literal

# Default — 60 hits per (project_id, tag) per minute. The original Kanban
# description called for a 2-tier scheme (60/min soft + 600/min hard); we
# simplified to a single hard cap for v1 per the brief.
WEBHOOK_RATE_LIMIT_PER_MIN_ENV: Final[str] = "WEBHOOK_RATE_LIMIT_PER_MIN"
WEBHOOK_RATE_LIMIT_PER_MIN_DEFAULT: Final[int] = 60

# Aggregate per-project ceiling = limit_per_minute * this multiplier (Kanban
# #2833 — closes the tag-variation bypass). 5x means a project can spread its
# effective cap across roughly 5 distinct tags' worth of traffic before the
# aggregate kicks in; tune via env if that's too tight/loose in practice.
WEBHOOK_RATE_LIMIT_PROJECT_MULTIPLIER_ENV: Final[str] = "WEBHOOK_RATE_LIMIT_PROJECT_MULTIPLIER"
WEBHOOK_RATE_LIMIT_PROJECT_MULTIPLIER_DEFAULT: Final[int] = 5

_WINDOW_SECONDS: Final[int] = 60

# Module-level state — per-process. Keys are (project_id, tag) tuples; values
# are deques of UTC datetimes (most recent on the right; we ``popleft`` to
# expire old entries on each check).
_WINDOWS: dict[tuple[int, str], deque[datetime]] = defaultdict(deque)

# Aggregate per-project state (Kanban #2833) — same shape, keyed on
# project_id alone. Every accepted hit is recorded here IN ADDITION TO
# ``_WINDOWS`` so the total across all of a project's tags can be capped.
_PROJECT_WINDOWS: dict[int, deque[datetime]] = defaultdict(deque)


class RateLimitError(Exception):
    """Raised by ``check_and_consume`` when either the per-(project, tag) bucket
    or the per-project aggregate bucket (Kanban #2833) is full.

    Carries the configured limit, the elapsed-window length, and which bucket
    tripped (``scope``) so the router can render a useful 429 detail string
    and log which cap was hit. Module-level (NOT subclass of HTTPException) so
    unit tests on this service don't depend on FastAPI.
    """

    def __init__(self, limit_per_minute: int, *, scope: Literal["tag", "project"]) -> None:
        self.limit_per_minute = limit_per_minute
        self.scope = scope
        if scope == "tag":
            message = f"rate limit exceeded: {limit_per_minute}/min per (project, tag)"
        else:
            message = (
                f"rate limit exceeded: {limit_per_minute}/min per project "
                "(aggregate across tags)"
            )
        super().__init__(message)


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


def _resolved_project_multiplier() -> int:
    """Read the per-project aggregate multiplier from env on every call.

    Mirrors ``_resolved_limit``'s guard shape: invalid / non-int / negative
    env value falls back silently to the default.
    """
    raw = os.environ.get(WEBHOOK_RATE_LIMIT_PROJECT_MULTIPLIER_ENV)
    if not raw:
        return WEBHOOK_RATE_LIMIT_PROJECT_MULTIPLIER_DEFAULT
    try:
        v = int(raw)
        return v if v > 0 else WEBHOOK_RATE_LIMIT_PROJECT_MULTIPLIER_DEFAULT
    except ValueError:
        return WEBHOOK_RATE_LIMIT_PROJECT_MULTIPLIER_DEFAULT


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

    Two buckets gate every hit (Kanban #2833):
      1. The per-(project_id, tag) bucket — unchanged v1 behavior.
      2. A per-project_id aggregate bucket, capped at ``limit_per_minute *
         WEBHOOK_RATE_LIMIT_PROJECT_MULTIPLIER`` — bounds the TOTAL hits a
         project can rack up across every tag, so spraying distinct tag
         values can't multiply the effective cap.
    Both buckets evict-then-delete-if-empty on every check, so neither
    leaks a permanent dict entry once its entries fully expire.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if limit_per_minute is None:
        limit_per_minute = _resolved_limit()

    cutoff = now - timedelta(seconds=_WINDOW_SECONDS)

    # ----- 1. Per-(project, tag) bucket ------------------------------------
    key = (project_id, tag)
    bucket = _WINDOWS[key]
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if not bucket:
        # Reclaim memory (Kanban #2833) — a bucket that's drained to empty
        # gets no permanent dict entry. Safe even though `bucket` still
        # locally references the (now-orphaned) empty deque: `len(bucket)`
        # below is unaffected by dict membership.
        del _WINDOWS[key]

    if len(bucket) >= limit_per_minute:
        raise RateLimitError(limit_per_minute, scope="tag")

    # ----- 2. Per-project aggregate bucket (bypass fix, Kanban #2833) ------
    project_limit = limit_per_minute * _resolved_project_multiplier()
    project_bucket = _PROJECT_WINDOWS[project_id]
    while project_bucket and project_bucket[0] < cutoff:
        project_bucket.popleft()
    if not project_bucket:
        del _PROJECT_WINDOWS[project_id]

    if len(project_bucket) >= project_limit:
        raise RateLimitError(project_limit, scope="project")

    # ----- 3. Both checks passed -> consume in both buckets ----------------
    # Re-fetch via defaultdict rather than reusing the `bucket` /
    # `project_bucket` locals: either may have just been `del`-ed above (when
    # it drained to empty), and that local would then be an orphaned deque no
    # longer reachable through the dict — appending to it would silently
    # lose the hit on the next lookup.
    _WINDOWS[key].append(now)
    _PROJECT_WINDOWS[project_id].append(now)


def reset() -> None:
    """Wipe ALL buckets — test-fixture hook.

    Conftest's per-test reset fixture calls this so a test that exercises
    the limit doesn't leak counts into the next test. Clears both the
    per-(project, tag) buckets and the per-project aggregate buckets
    (Kanban #2833).
    """
    _WINDOWS.clear()
    _PROJECT_WINDOWS.clear()
