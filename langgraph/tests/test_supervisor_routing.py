"""Unit tests for the supervisor's conditional-edge function.

Pinned mapping per `nodes.ROLE_*` constants — mirror of
`api/src/constants.py::TaskRole`. The test exists so drift between the two
codebases surfaces here (and the duplication note in nodes.py points back).
"""

from __future__ import annotations

import pytest

from nodes import route_from_supervisor


@pytest.mark.parametrize(
    "role,expected",
    [
        (1, "frontend"),
        (2, "backend"),
        (3, "devops"),
        (4, "tester"),
        (5, "reviewer"),
        (None, "general"),
        (99, "general"),  # defensive default — unknown int falls through to general
    ],
)
def test_route_from_supervisor(role: int | None, expected: str) -> None:
    state = {"assigned_role": role, "brief": "test", "task_id": 1}
    assert route_from_supervisor(state) == expected


def test_route_from_supervisor_missing_role_key() -> None:
    """state may not have `assigned_role` at all (TypedDict total=False).
    Treat as None → general."""
    state = {"brief": "test", "task_id": 1}
    assert route_from_supervisor(state) == "general"
