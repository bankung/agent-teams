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
