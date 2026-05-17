"""Pydantic schemas for the cross-project next-action recommender (Kanban #1010).

Returned by `GET /api/user/next-action?limit=N`. USER-scoped, NOT
project-scoped — the endpoint walks every active project the user owns and
returns the top-N highest-impact pending interactions (questions / decisions).

Wire shape per the locked spec:

    {
      "items": [
        {
          "task_id": 998,
          "project_id": 599,
          "project_name": "secretary",
          "title": "...",
          "reason": "oldest in inbox (5h) and blocking 2 downstream tasks",
          "score": 0.84
        }
      ],
      "fallback_hint": null   # or "No action needed - 3 tasks running, 7 completed today"
    }

Mobile/digest/inbox consumers (#1009 digest section 5, #1003 home tile,
#1000 inbox empty-state). Small payload, no embedded HTML.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class NextActionItem(BaseModel):
    """One ranked action surfaced to the operator.

    `score` is the weighted ranker output in [0, 1] inclusive — see
    `services/next_action_ranker.py` for the weights (aging 40 / downstream 30 /
    priority 20 / budget 10) and the dominant-factor reason picker. `reason` is
    a 1-line human-readable string suitable for mobile-tile / digest rendering
    (no markdown, no HTML).
    """

    model_config = ConfigDict(extra="forbid")

    task_id: int
    project_id: int
    project_name: str
    title: str
    reason: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)


class NextActionResponse(BaseModel):
    """Top-level response.

    `items` is empty when no actionable tasks exist; `fallback_hint` carries a
    one-line summary in that case (cross-project running / completed-today
    counts). When `items` is non-empty, `fallback_hint` is None.

    The endpoint NEVER 500s on the fallback path — if the underlying counts
    query also fails, the hint degrades to the bare `"No action needed."`
    string. Behavior pinned by test_user_next_action.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[NextActionItem] = Field(default_factory=list)
    fallback_hint: str | None = None
