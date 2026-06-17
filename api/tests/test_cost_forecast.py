"""Kanban #1304 — pre-task cost forecast (service unit + endpoint integration).

Two layers:

1. Pure-unit on `forecast_task_cost` (no DB / no HTTP) — SimpleNamespace fakes
   for the task + pinned resources. The provider env vars are cleared per test
   so the resolved model is deterministically the opus-4-8 default regardless of
   the container's `LANGGRAPH_LLM_PROVIDER` (the api image sets it to `ollama`).

2. HTTP integration on `POST /api/tasks/{id}/cost-forecast` via the ASGI client.
   Resources are inserted directly through the `db_session` fixture (the file
   upload route needs python-multipart, which is not in the running container);
   link/JSON resources work but carry no `est_cost_if_full`, so a direct insert
   is the cleanest way to drive the tagged/untagged/size-null branches.

NOTE on confidence for an EMPTY/text-only task: the locked confidence rules
("high = every file resource tagged; med = some untagged; low = a file with
size_bytes NULL OR unknown model") yield "high" when there are NO file resources
and the model is known — there is nothing untagged to drag it down. The
role-brief constant is ALWAYS summed, so an empty task is priced > $0 (role-brief
only), never $0. The only true-$0 paths are an unknown model or an ollama
provider. See the #1304 report's deviation note.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.constants import RecordStatus, ResourceKind
from src.models.project_resource import ProjectResource
from src.services.task_cost_estimator import (
    OUTPUT_TOKEN_RATIO,
    ROLE_BRIEF_TOKEN_ESTIMATE,
    forecast_task_cost,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"cost-forecast fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


def _fake_task(**kw) -> SimpleNamespace:
    base = dict(
        title=None, description=None, acceptance_criteria=None, model_override=None
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _fake_resource(kind: str, size_bytes, tags) -> SimpleNamespace:
    return SimpleNamespace(kind=kind, size_bytes=size_bytes, tags=tags)


@pytest.fixture
def _opus_default_env(monkeypatch):
    """Clear provider env so resolve_provider_model() lands the opus-4-8 default.

    The api container sets LANGGRAPH_LLM_PROVIDER=ollama; without this the pure
    forecast would price at $0 (ollama). Deleting the vars makes the unit cost
    assertions deterministic on the documented anthropic/opus-4-8 default.
    """
    monkeypatch.delenv("LANGGRAPH_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)


# ---------------------------------------------------------------------------
# Pure-unit: forecast_task_cost
# ---------------------------------------------------------------------------


def test_forecast_empty_task_role_brief_only(_opus_default_env) -> None:
    """Empty task: prompt+resources=0, only the role-brief term remains.

    estimated_tokens == ROLE_BRIEF_TOKEN_ESTIMATE; cost is the role-brief priced
    at opus-4-8 (> $0 — role_brief is always summed). Confidence is "high": no
    file resources + known model = nothing untagged. (NOT $0/low — see module
    docstring + #1304 report.)
    """
    r = forecast_task_cost(_fake_task(), [])
    assert r["breakdown"]["prompt"] == 0
    assert r["breakdown"]["attached_resources"] == 0
    assert r["breakdown"]["role_brief"] == ROLE_BRIEF_TOKEN_ESTIMATE
    assert r["estimated_tokens"] == ROLE_BRIEF_TOKEN_ESTIMATE
    assert r["breakdown"]["completion"] == int(
        ROLE_BRIEF_TOKEN_ESTIMATE * OUTPUT_TOKEN_RATIO
    )
    assert r["model"] == "claude-opus-4-8"
    # POSITIVE: role-brief priced > $0 at opus rates.
    assert r["estimated_usd"] > Decimal("0.0000")
    # NEGATIVE (lock): role-brief term means it is NEVER zero for the empty task.
    assert r["estimated_usd"] != Decimal("0.0000")
    assert r["confidence"] == "high"


def test_forecast_text_only_task_priced_at_opus(_opus_default_env) -> None:
    """Text-only task: prompt tokens + role_brief, priced at opus-4-8, high."""
    task = _fake_task(
        title="Add a login endpoint",
        description="Build a FastAPI login route returning a JWT. " * 5,
        acceptance_criteria=[
            {"text": "returns 200 on valid credentials"},
            {"text": "returns 401 on bad password"},
        ],
    )
    r = forecast_task_cost(task, [])
    assert r["breakdown"]["prompt"] > 0
    assert r["breakdown"]["attached_resources"] == 0
    # estimated_tokens is the sum of the three INPUT buckets.
    assert (
        r["estimated_tokens"]
        == r["breakdown"]["prompt"]
        + r["breakdown"]["role_brief"]
        + r["breakdown"]["attached_resources"]
    )
    assert r["breakdown"]["completion"] == int(
        r["estimated_tokens"] * OUTPUT_TOKEN_RATIO
    )
    assert r["model"] == "claude-opus-4-8"
    assert r["provider"] == "anthropic"
    assert r["estimated_usd"] > Decimal("0.0000")
    assert r["confidence"] == "high"


def test_forecast_tagged_resource_summed_high(_opus_default_env) -> None:
    """A tagged file resource contributes approx_tokens; confidence stays high."""
    task = _fake_task(title="Summarize the CSV", description="process attached data")
    tagged = _fake_resource(
        ResourceKind.FILE,
        5_000,
        {"est_cost_if_full": {"approx_tokens": 12_000, "usd": 0.06}},
    )
    r = forecast_task_cost(task, [tagged])
    assert r["breakdown"]["attached_resources"] == 12_000
    # The resource tokens fold into the input total (and thus the cost).
    assert r["estimated_tokens"] == (
        r["breakdown"]["prompt"] + r["breakdown"]["role_brief"] + 12_000
    )
    assert r["confidence"] == "high"
    assert r["estimated_usd"] > Decimal("0.0000")


def test_forecast_untagged_resource_downgrades_to_med(_opus_default_env) -> None:
    """A file resource with no est_cost_if_full tag -> confidence "med"."""
    task = _fake_task(title="x", description="y")
    tagged = _fake_resource(
        ResourceKind.FILE, 1_000, {"est_cost_if_full": {"approx_tokens": 4_000}}
    )
    untagged = _fake_resource(ResourceKind.FILE, 800, {"format_detected": "pdf"})
    r = forecast_task_cost(task, [tagged, untagged])
    # Only the tagged one contributes tokens; the untagged adds nothing.
    assert r["breakdown"]["attached_resources"] == 4_000
    assert r["confidence"] == "med"


def test_forecast_file_size_null_forces_low(_opus_default_env) -> None:
    """A file resource not fully uploaded (size_bytes NULL) -> "low" (overrides)."""
    task = _fake_task(title="x", description="y")
    tagged = _fake_resource(
        ResourceKind.FILE, 1_000, {"est_cost_if_full": {"approx_tokens": 4_000}}
    )
    not_uploaded = _fake_resource(
        ResourceKind.FILE, None, {"est_cost_if_full": {"approx_tokens": 3_000}}
    )
    r = forecast_task_cost(task, [tagged, not_uploaded])
    # size_bytes NULL beats the otherwise-high tagged state.
    assert r["confidence"] == "low"


def test_forecast_unknown_model_override_low_zero(_opus_default_env) -> None:
    """An unknown model_override -> cost $0 AND confidence forced "low"."""
    task = _fake_task(
        title="x", description="y", model_override="totally-unknown-model-xyz"
    )
    r = forecast_task_cost(task, [])
    assert r["estimated_usd"] == Decimal("0.0000")
    assert r["confidence"] == "low"
    # Token counts are still preserved (partial signal beats none): prompt +
    # role_brief (no resources). The 'x'/'y' text contributes a few prompt tokens
    # on top of the flat role-brief term, so the total exceeds the role-brief alone.
    assert r["breakdown"]["attached_resources"] == 0
    assert (
        r["estimated_tokens"]
        == r["breakdown"]["prompt"] + ROLE_BRIEF_TOKEN_ESTIMATE
    )
    assert r["estimated_tokens"] >= ROLE_BRIEF_TOKEN_ESTIMATE


def test_forecast_link_resource_does_not_gate_confidence(_opus_default_env) -> None:
    """Links carry no est_cost_if_full and never drag confidence down."""
    task = _fake_task(title="x", description="y")
    tagged = _fake_resource(
        ResourceKind.FILE, 1_000, {"est_cost_if_full": {"approx_tokens": 4_000}}
    )
    link = _fake_resource(ResourceKind.LINK, None, {"url_scheme": "https"})
    r = forecast_task_cost(task, [tagged, link])
    assert r["confidence"] == "high"


# ---------------------------------------------------------------------------
# HTTP integration: POST /api/tasks/{id}/cost-forecast
# ---------------------------------------------------------------------------


async def _make_project_and_task(client, scaffold_cleanup, slug: str):
    name = scaffold_cleanup(_unique_name(slug))
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "forecast me",
            "description": "A sufficiently long description for non-zero tokens. " * 3,
            "acceptance_criteria": [{"text": "the endpoint returns a forecast"}],
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    return project_id, headers, create.json()["id"]


@pytest.mark.asyncio
async def test_forecast_endpoint_returns_contract_and_persists(
    client, scaffold_cleanup, db_session
) -> None:
    """POST -> 200 with all 4 contract keys; tasks.forecast_cost_usd persisted."""
    project_id, headers, task_id = await _make_project_and_task(
        client, scaffold_cleanup, "cf-contract"
    )
    try:
        # Pin a tagged file resource directly (multipart upload unavailable).
        res = ProjectResource(
            project_id=project_id,
            task_id=task_id,
            kind=ResourceKind.FILE,
            filename="data.csv",
            size_bytes=4_096,
            tags={"est_cost_if_full": {"approx_tokens": 8_000, "usd": 0.04}},
            status=RecordStatus.ACTIVE,
        )
        db_session.add(res)
        await db_session.commit()

        resp = await client.post(
            f"/api/tasks/{task_id}/cost-forecast", headers=headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # All four contract keys present.
        assert set(body.keys()) == {
            "estimated_usd",
            "estimated_tokens",
            "breakdown",
            "confidence",
        }, body
        assert set(body["breakdown"].keys()) == {
            "prompt",
            "role_brief",
            "attached_resources",
            "completion",
        }, body["breakdown"]
        # The tagged resource's tokens flowed through.
        assert body["breakdown"]["attached_resources"] == 8_000
        assert body["confidence"] in ("low", "med", "high")
        # Tagged file + (in the container) ollama provider -> confidence high,
        # cost may be $0 under ollama; the persistence check below is the lock.

        # POSITIVE: the forecast persisted to the task row.
        got = await client.get(f"/api/tasks/{task_id}", headers=headers)
        assert got.status_code == 200, got.text
        persisted = got.json()["forecast_cost_usd"]
        assert persisted is not None, got.json()
        # Round-trips to the same value the endpoint returned.
        assert Decimal(str(persisted)) == Decimal(str(body["estimated_usd"]))
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_forecast_endpoint_404_missing_task(
    client, scaffold_cleanup
) -> None:
    """A non-existent task id -> 404."""
    name = scaffold_cleanup(_unique_name("cf-404"))
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    try:
        resp = await client.post(
            "/api/tasks/99999999/cost-forecast", headers=headers
        )
        assert resp.status_code == 404, resp.text
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_forecast_endpoint_400_soft_deleted_task(
    client, scaffold_cleanup
) -> None:
    """POST cost-forecast on a soft-deleted task -> 400; forecast_cost_usd unchanged.

    M1 fix: the not-active guard fires before the resource query / DB write, so
    a soft-deleted task cannot receive a forecast write.
    """
    project_id, headers, task_id = await _make_project_and_task(
        client, scaffold_cleanup, "cf-deleted"
    )
    try:
        # Soft-delete the task (flips status=0).
        del_resp = await client.delete(f"/api/tasks/{task_id}", headers=headers)
        assert del_resp.status_code == 204, del_resp.text

        resp = await client.post(
            f"/api/tasks/{task_id}/cost-forecast", headers=headers
        )
        # NEGATIVE: soft-deleted task must be rejected.
        assert resp.status_code == 400, resp.text
        assert "not active" in resp.json().get("detail", "").lower()

        # POSITIVE: forecast_cost_usd must remain NULL (no write happened).
        # The detail GET endpoint returns the row regardless of soft-delete status.
        got = await client.get(f"/api/tasks/{task_id}", headers=headers)
        assert got.status_code == 200, got.text
        assert got.json()["forecast_cost_usd"] is None
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_forecast_endpoint_404_wrong_project(
    client, scaffold_cleanup
) -> None:
    """A task that belongs to another project -> 404 (same guard as PUT)."""
    # Project A owns the task.
    pa, headers_a, task_id = await _make_project_and_task(
        client, scaffold_cleanup, "cf-wp-a"
    )
    # Project B is a different session-bound project.
    name_b = scaffold_cleanup(_unique_name("cf-wp-b"))
    proj_b = await client.post(
        "/api/projects", json=_project_create_payload(name_b)
    )
    pb = proj_b.json()["id"]
    headers_b = {"X-Project-Id": str(pb)}
    try:
        resp = await client.post(
            f"/api/tasks/{task_id}/cost-forecast", headers=headers_b
        )
        # The session-project guard (assert_task_belongs_to_session) raises 400
        # when the task belongs to project A but the header is project B.
        # The guard fires before the endpoint can return 404 (task "not found"
        # from the session's perspective is surfaced as 400 per Kanban #695).
        assert resp.status_code == 400, resp.text
    finally:
        await client.delete(f"/api/projects/{pa}")
        await client.delete(f"/api/projects/{pb}")
