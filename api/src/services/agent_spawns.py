"""Cross-project spawn history for the agent gallery detail view (Kanban #1017).

This is the ONLY DB touch in the agent-gallery feature — a single read-only
query against ``tasks.subagent_models`` (a JSONB array of ``{agent, model, at}``
appended per task by the Lead spawn log, Kanban #887). The filesystem-backed
gallery (``services/agent_validation.py``) stays DB-free; this sibling module
owns the one query so that separation is explicit.

Why a parametrized ``text()`` query instead of the ORM
-----------------------------------------------------
We need to (a) UNNEST the JSONB array per task, (b) keep only elements whose
``agent`` equals the requested name, (c) order by the element's ``at`` (falling
back to ``tasks.updated_at`` when the element omits it), (d) cap at 20, and
(e) join the owning project for a human-readable name — all in ONE round trip
(no N+1). ``jsonb_array_elements`` is a set-returning function used as a LATERAL
join; expressing that through the async ORM is awkward and error-prone, so we
use a single hand-written, fully-parametrized statement. The agent name is the
only user input and is bound as a parameter (never string-interpolated).

The ``@>`` containment pre-filter (``subagent_models @> '[{"agent": :name}]'``)
lets PostgreSQL use a GIN index on ``subagent_models`` if one exists and prunes
the task set before the LATERAL unnest; the per-element ``WHERE elem->>'agent'``
is still required because a task may have spawned several agents and we only
want this one's elements.
"""

from __future__ import annotations

import logging

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Cap on returned spawn rows (contract §2: "cap 20").
_SPAWN_CAP = 20

# One query. `elem` is each {agent, model, at} object unnested from the task's
# subagent_models JSONB array. We order newest-first by the element's `at`,
# falling back to the task's updated_at when the element omits `at`. status=1
# (RecordStatus.ACTIVE) excludes soft-deleted tasks. The `@>` containment
# pre-filter prunes tasks that never spawned this agent before the LATERAL
# unnest runs.
_SPAWNS_SQL = text(
    """
    SELECT
        t.id            AS task_id,
        t.project_id    AS project_id,
        p.name          AS project_name,
        elem ->> 'model' AS model,
        elem ->> 'at'    AS at
    FROM tasks AS t
    JOIN projects AS p ON p.id = t.project_id
    CROSS JOIN LATERAL jsonb_array_elements(t.subagent_models) AS elem
    WHERE t.status = 1
      AND t.subagent_models @> :containment
      AND elem ->> 'agent' = :name
    ORDER BY COALESCE(
        NULLIF(elem ->> 'at', ''),
        to_char(t.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    ) DESC, t.id DESC
    LIMIT :cap
    """
).bindparams(bindparam("containment", type_=JSONB))


async def fetch_agent_spawns(
    session: AsyncSession, name: str
) -> list[dict[str, object]]:
    """Return up to 20 recent cross-project spawns of ``name``, newest first.

    Each dict: ``{task_id, project_id, project_name, model, at}`` matching the
    ``AgentSpawn`` wire schema. ``model`` / ``at`` may be ``None`` when the log
    entry omitted them. Read-only; never mutates. Soft-deleted tasks
    (``status=0``) are excluded.

    The ``@>`` containment value is a JSON array with a single
    ``{"agent": <name>}`` object — PostgreSQL matches a task whose
    ``subagent_models`` contains an element with that agent (the per-element
    ``WHERE`` then keeps only this agent's elements). It is passed as a Python
    object and serialized ONCE by the ``JSONB`` bindparam type (passing a
    pre-``json.dumps``'d string would double-encode it into a JSON *string*).
    """
    result = await session.execute(
        _SPAWNS_SQL,
        {"name": name, "containment": [{"agent": name}], "cap": _SPAWN_CAP},
    )
    return [
        {
            "task_id": row.task_id,
            "project_id": row.project_id,
            "project_name": row.project_name,
            "model": row.model,
            "at": row.at,
        }
        for row in result
    ]


# ===========================================================================
# Kanban #1020 — per-agent, per-project cost-estimate rollup.
#
# Same ``@>`` containment pre-filter as ``_SPAWNS_SQL`` above (same GIN index,
# ``ix_tasks_subagent_models_gin``) but scoped to ONE project and a 30-day
# completed_at window, and aggregated rather than listed row-by-row. This is a
# COUNT + SUM over `tasks.estimated_cost_usd` (the #944 done-flip heuristic
# estimate) — see the router for why this is estimated-basis, not
# usage_events-basis, in v1.
#
# dev-reviewer #1020 fix 2: a naive ``COUNT(*) FROM tasks`` undercounts —
# ``subagent_models`` can list the SAME agent twice on one task (routine
# accumulation across two spawns within the task's lifetime), and
# ``spawn_count_last_30d`` must count SPAWNS (elements), not tasks. Cost
# attribution stays task-level: mirroring ``_SPAWNS_SQL``'s LATERAL unnest for
# the per-element COUNT, but a task's ``estimated_cost_usd`` is summed ONCE
# per distinct task (not once per element) via the ``task_costs`` CTE below —
# otherwise a task listing the agent twice would double-count its own cost.
# This means a multi-agent task's full cost attributes to EACH agent it
# lists (v1 heuristic; see the router docstring).
# ===========================================================================

_COST_ROLLUP_SQL = text(
    """
    WITH matching_elems AS (
        SELECT t.id AS task_id
        FROM tasks AS t
        CROSS JOIN LATERAL jsonb_array_elements(t.subagent_models) AS elem
        WHERE t.status = 1
          AND t.project_id = :project_id
          AND t.subagent_models @> :containment
          AND t.completed_at >= now() - interval '30 days'
          AND elem ->> 'agent' = :name
    ),
    task_costs AS (
        SELECT DISTINCT t.id, t.estimated_cost_usd
        FROM tasks AS t
        WHERE t.id IN (SELECT task_id FROM matching_elems)
    )
    SELECT
        (SELECT COUNT(*) FROM matching_elems)  AS spawn_count,
        (SELECT SUM(estimated_cost_usd) FROM task_costs) AS total_cost
    """
).bindparams(bindparam("containment", type_=JSONB))


async def fetch_agent_cost_rollup(
    session: AsyncSession, name: str, project_id: int
) -> tuple[int, object | None]:
    """Return ``(spawn_count_last_30d, total_cost_usd)`` for one agent.

    Scoped to completed (``completed_at`` set), non-soft-deleted tasks of
    ``project_id`` whose ``subagent_models`` contains an element with
    ``agent == name``, within the last 30 days.

    ``spawn_count_last_30d`` is a per-ELEMENT count (``matching_elems``) — a
    task that lists this agent twice in ``subagent_models`` contributes 2, not
    1. ``total_cost_usd`` is a per-TASK sum (``task_costs`` — deduplicated by
    task id via ``DISTINCT``/the ``IN`` membership test) so that same
    twice-listed task contributes its ``estimated_cost_usd`` ONCE, not twice.
    ``SUM()`` skips NULLs (SQL-standard behavior), so a project with zero
    costed matching tasks yields ``total_cost_usd is None`` while
    ``spawn_count`` still reflects every matching element (costed or not).
    Returns ``(0, None)`` when no task matches at all.

    The router derives ``avg_cost_per_spawn = total_cost_usd / spawn_count``
    and ``projected_monthly_usd = total_cost_usd`` directly (self-consistent:
    ``avg * count == total`` by construction — see fix 2 in the router's
    cost-estimate endpoint).
    """
    result = await session.execute(
        _COST_ROLLUP_SQL,
        {"project_id": project_id, "containment": [{"agent": name}], "name": name},
    )
    row = result.one()
    return (row.spawn_count, row.total_cost)
