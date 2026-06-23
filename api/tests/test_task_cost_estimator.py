"""Kanban #944 — per-task LLM-cost estimation on done-flip.

The PATCH handler computes estimated_input_tokens / estimated_output_tokens /
estimated_cost_usd when a task transitions process_status from <5 to 5 (DONE).
Idempotent re-flip preserves the first-close values.

Coverage (the 4 cases from AC #4):
1. test_first_close_writes_non_null_fields    — happy path, fields populate.
2. test_re_close_does_not_overwrite           — done → cancelled → done preserves estimate.
3. test_ollama_provider_yields_zero_cost      — local inference = $0; tokens still counted.
4. test_thai_text_uses_2_chars_per_token      — script-ratio detector picks 2 cpt.

Plus a couple of pure-unit tests on the estimator function itself (no HTTP).
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.services.task_cost_estimator import (
    chars_per_token,
    estimate_task_cost,
    resolve_provider_model,
)


# ---------------------------------------------------------------------------
# Helpers (project + task fixtures via the public API)
# ---------------------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


# ---------------------------------------------------------------------------
# Pure-unit tests on the estimator function (no DB / no HTTP)
# ---------------------------------------------------------------------------


def test_chars_per_token_ascii_returns_4() -> None:
    assert chars_per_token("hello world this is a test") == 4


def test_chars_per_token_thai_dominant_returns_2() -> None:
    # All Thai chars (~30 chars Thai, 0 ASCII) → 100% dense → 2 cpt.
    thai = "สวัสดีครับ ทดสอบระบบประเมินค่าใช้จ่าย"
    assert chars_per_token(thai) == 2


def test_chars_per_token_mixed_below_threshold_stays_4() -> None:
    # ~10 Thai chars in an 80-char string = 12.5% < 30% → ASCII bucket.
    mixed = "Hello world this is a long English sentence อาทิตย์ ok"
    assert chars_per_token(mixed) == 4


def test_chars_per_token_empty_returns_4_default() -> None:
    assert chars_per_token("") == 4


def test_estimate_task_cost_heuristic_uses_2_cpt_for_thai() -> None:
    """Thai-dominant description divides by 2, not 4 — load-bearing for AC #4."""
    # Build a task with a clearly Thai-dominant description.
    thai_desc = "ทดสอบการประเมินค่าใช้จ่ายของงาน " * 4  # 4 copies → ~120 chars dense
    task = SimpleNamespace(
        title="t",  # 1 char ASCII (negligible vs 120+ Thai)
        description=thai_desc,
        status_change_reason=None,
    )
    result = estimate_task_cost(task, runs=None)
    # Expected: tokens_in == (len(title) + len(thai_desc)) // 2.
    expected = (len(task.title) + len(thai_desc)) // 2
    assert result["tokens_in"] == expected, (
        f"Thai dominant must use 2 cpt; got {result['tokens_in']} expected {expected}"
    )
    # Cross-check vs the /4 path so the test really pins the divisor.
    if expected > 0:
        assert result["tokens_in"] != (len(task.title) + len(thai_desc)) // 4


def test_estimate_task_cost_empty_task_returns_zero() -> None:
    task = SimpleNamespace(title="", description=None, status_change_reason=None)
    result = estimate_task_cost(task, runs=None)
    assert result == {"tokens_in": 0, "tokens_out": 0, "cost_usd": Decimal("0.0000")}


def test_estimate_task_cost_real_metering_branch_sums_runs() -> None:
    """When runs are provided, totals come from runs (not heuristic)."""
    runs = [
        SimpleNamespace(
            total_input_tokens=1000,
            total_output_tokens=200,
            total_cost_usd=Decimal("0.5000"),
        ),
        SimpleNamespace(
            total_input_tokens=2000,
            total_output_tokens=300,
            total_cost_usd=Decimal("1.2500"),
        ),
    ]
    # Task description should be IGNORED when runs are present.
    task = SimpleNamespace(
        title="x" * 1000,
        description="y" * 5000,
        status_change_reason="z" * 200,
    )
    result = estimate_task_cost(task, runs=runs)
    assert result["tokens_in"] == 3000
    assert result["tokens_out"] == 500
    assert result["cost_usd"] == Decimal("1.7500")


def test_estimate_task_cost_ollama_provider_zero_cost(monkeypatch) -> None:
    """Pure-unit: LANGGRAPH_LLM_PROVIDER=ollama → cost=0 but tokens>0."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "ollama")
    task = SimpleNamespace(
        title="hello",
        description="some english description with enough length",
        status_change_reason="closing reason text",
    )
    result = estimate_task_cost(task, runs=None)
    assert result["tokens_in"] > 0, "expected non-zero tokens_in even for ollama"
    assert result["tokens_out"] > 0, "expected non-zero tokens_out even for ollama"
    assert result["cost_usd"] == Decimal("0.0000")


def test_resolve_provider_model_defaults_to_anthropic_opus(monkeypatch) -> None:
    # Kanban #1304: default bumped sonnet-4-6 -> opus-4-8 (the model interactive
    # Lead sessions actually run; aligned with langgraph/llm.py).
    monkeypatch.delenv("LANGGRAPH_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    assert resolve_provider_model() == ("anthropic", "claude-opus-4-8")


def test_resolve_provider_model_openai_branch(monkeypatch) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    assert resolve_provider_model() == ("openai", "gpt-4o")


# ---------------------------------------------------------------------------
# Integration tests via the HTTP API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_close_writes_non_null_fields(client, scaffold_cleanup) -> None:
    """AC #4 case 1: first close populates the 3 estimate fields."""
    name = _unique_name("cost-first-close")
    scaffold_cleanup(name)
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        create = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "cost-first-close",
                "description": (
                    "A sufficiently long English description so the heuristic "
                    "yields non-zero token counts. " * 4
                ),
            },
            headers=headers,
        )
        assert create.status_code == 201, create.text
        body = create.json()
        task_id = body["id"]
        # Pre-close — all three fields NULL.
        assert body["estimated_input_tokens"] is None
        assert body["estimated_output_tokens"] is None
        assert body["estimated_cost_usd"] is None

        # Close: ps=5 + reason for output-char counting.
        patch = await client.patch(
            f"/api/tasks/{task_id}",
            json={
                "process_status": 5,
                "status_change_reason": "closing — finished the work",
            },
            headers=headers,
        )
        assert patch.status_code == 200, patch.text
        closed = patch.json()
        assert closed["process_status"] == 5
        assert closed["estimated_input_tokens"] is not None, (
            f"expected non-null estimated_input_tokens; got {closed!r}"
        )
        assert closed["estimated_output_tokens"] is not None
        assert closed["estimated_cost_usd"] is not None
        assert int(closed["estimated_input_tokens"]) > 0
        assert int(closed["estimated_output_tokens"]) > 0
        # USD cost > 0 only when provider rates are non-zero (anthropic default).
        # We check >= 0 to be robust to tiny rounding; the field-present check
        # above already pins the non-null contract.
        assert Decimal(str(closed["estimated_cost_usd"])) >= Decimal("0.0000")

        # GET round-trip — DB persisted the values.
        got = await client.get(f"/api/tasks/{task_id}", headers=headers)
        assert got.status_code == 200
        got_body = got.json()
        assert got_body["estimated_input_tokens"] == closed["estimated_input_tokens"]
        assert got_body["estimated_output_tokens"] == closed["estimated_output_tokens"]
        assert (
            Decimal(str(got_body["estimated_cost_usd"]))
            == Decimal(str(closed["estimated_cost_usd"]))
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_re_close_does_not_overwrite(client, scaffold_cleanup) -> None:
    """AC #4 case 2: close → cancel → close again preserves first-close values."""
    name = _unique_name("cost-reclose")
    scaffold_cleanup(name)
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        create = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "cost-reclose",
                "description": "Initial description for first-close estimate. " * 3,
            },
            headers=headers,
        )
        assert create.status_code == 201, create.text
        task_id = create.json()["id"]

        # First close — write the values.
        first = await client.patch(
            f"/api/tasks/{task_id}",
            json={"process_status": 5, "status_change_reason": "first close"},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        first_in = first.json()["estimated_input_tokens"]
        first_out = first.json()["estimated_output_tokens"]
        first_cost = first.json()["estimated_cost_usd"]
        assert first_in is not None and first_in > 0
        assert first_out is not None and first_out > 0
        assert first_cost is not None

        # Cancel (ps=6) — re-open path. Note: also PATCH a much-longer
        # description so the heuristic would yield DIFFERENT numbers if it
        # ever re-ran on re-close. This pins the no-overwrite contract.
        cancel = await client.patch(
            f"/api/tasks/{task_id}",
            json={
                "process_status": 6,
                "description": ("Way longer description. " * 100),
                "status_change_reason": "cancelled mid-flight",
            },
            headers=headers,
        )
        assert cancel.status_code == 200, cancel.text
        # Cancel does NOT trigger estimation (it's a <5 → 6 transition,
        # NOT <5 → 5). Existing values still in place from first close.
        assert cancel.json()["estimated_input_tokens"] == first_in
        assert cancel.json()["estimated_output_tokens"] == first_out

        # Re-close: ps=6 → ps=5 (not <5 → 5, the guard skips). Even if the
        # guard misfired, the idempotent `estimated_cost_usd IS NOT NULL`
        # check would still suppress the write. Both belts hold here.
        reclose = await client.patch(
            f"/api/tasks/{task_id}",
            json={"process_status": 5, "status_change_reason": "second close"},
            headers=headers,
        )
        assert reclose.status_code == 200, reclose.text
        body = reclose.json()
        assert body["estimated_input_tokens"] == first_in, (
            f"re-close must not overwrite; first={first_in} got={body['estimated_input_tokens']}"
        )
        assert body["estimated_output_tokens"] == first_out, (
            f"re-close must not overwrite; first={first_out} got={body['estimated_output_tokens']}"
        )
        assert (
            Decimal(str(body["estimated_cost_usd"]))
            == Decimal(str(first_cost))
        ), "re-close must not overwrite cost"

        # GET round-trip confirms DB persisted the no-overwrite contract.
        got = await client.get(f"/api/tasks/{task_id}", headers=headers)
        assert got.json()["estimated_input_tokens"] == first_in
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_ollama_provider_yields_zero_cost(
    client, scaffold_cleanup, monkeypatch
) -> None:
    """AC #4 case 3: LANGGRAPH_LLM_PROVIDER=ollama → cost=0, tokens still > 0."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "ollama")
    name = _unique_name("cost-ollama")
    scaffold_cleanup(name)
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        create = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "cost-ollama",
                "description": "English description to ensure non-zero token count. " * 3,
            },
            headers=headers,
        )
        task_id = create.json()["id"]

        patch = await client.patch(
            f"/api/tasks/{task_id}",
            json={"process_status": 5, "status_change_reason": "closing on ollama"},
            headers=headers,
        )
        assert patch.status_code == 200, patch.text
        body = patch.json()
        assert int(body["estimated_input_tokens"]) > 0, (
            "tokens must still count under ollama"
        )
        assert int(body["estimated_output_tokens"]) > 0
        # Ollama = local = $0.
        assert Decimal(str(body["estimated_cost_usd"])) == Decimal("0.0000")
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_thai_text_uses_2_chars_per_token(client, scaffold_cleanup) -> None:
    """AC #4 case 4: Thai-dominant description uses /2 not /4 in the heuristic."""
    name = _unique_name("cost-thai")
    scaffold_cleanup(name)
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    # Predominantly Thai description.
    thai_desc = "ทดสอบการประเมินค่าใช้จ่ายของงานในระบบ Kanban นี้ " * 4
    title = "ภาษาไทย"  # also Thai → keeps the script ratio overwhelmingly Thai

    try:
        create = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": title,
                "description": thai_desc,
            },
            headers=headers,
        )
        assert create.status_code == 201, create.text
        task_id = create.json()["id"]

        patch = await client.patch(
            f"/api/tasks/{task_id}",
            json={"process_status": 5, "status_change_reason": "ปิดงานแล้ว"},
            headers=headers,
        )
        assert patch.status_code == 200, patch.text
        body = patch.json()

        # Expected: tokens_in = (len(title) + len(description)) // 2.
        # If the script-ratio detector misfired and used /4, the count would
        # be HALF this value — the assertion below pins the right divisor.
        expected_in = (len(title) + len(thai_desc)) // 2
        actual_in = int(body["estimated_input_tokens"])
        assert actual_in == expected_in, (
            f"Thai dominant input must divide by 2; got {actual_in}, expected {expected_in} "
            f"(if /4 it would be {(len(title) + len(thai_desc)) // 4})"
        )

        # status_change_reason is also Thai, so output should also be /2.
        expected_out = len("ปิดงานแล้ว") // 2
        assert int(body["estimated_output_tokens"]) == expected_out
    finally:
        await client.delete(f"/api/projects/{project_id}")
