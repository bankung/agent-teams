"""Tests for POST /api/tasks/ai-parse (Kanban #856).

Coverage:
1. POSITIVE — smoke: "high priority backend bug for the login crash"
   maps to task_type='bug', priority=3, assigned_role=2 for BOTH providers.
2. NEGATIVE — empty text returns 422 (Pydantic min_length=1).
3. NEGATIVE — provider configured but API key missing returns 503.
4. INVARIANT — ai-parse does NOT create a tasks row (AC 2).
5. POSITIVE — provider switch: LANGGRAPH_LLM_PROVIDER=openai hits OpenAI,
   not Anthropic (AC 5).

All HTTP calls to provider endpoints are stubbed via respx — no real network.
The `_reset_engine_pool_per_test` fixture in conftest.py handles event-loop
binding; we just declare async tests and use the `client` fixture.
"""

from __future__ import annotations

import httpx
import pytest
import respx

# Provider HTTPS endpoints stubbed by respx.
_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

# Seed-bound project id (mirrors the seed's default agent-teams project).
_PROJECT_ID = 1


# =============================================================================
# Stub response builders
# =============================================================================


def _anthropic_tool_response(
    *,
    title: str = "Login crash",
    description: str = "high priority backend bug for the login crash",
    task_type: str = "bug",
    priority: int = 3,
    assigned_role: int | None = 2,
    blocked_by: int | None = None,
    tool_name: str = "propose_task",
) -> dict:
    """Build a fake Anthropic Messages response with a single tool_use block."""
    tool_input: dict = {
        "title": title,
        "description": description,
        "task_type": task_type,
        "priority": priority,
        "assigned_role": assigned_role,
        "blocked_by": blocked_by,
    }
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_test",
                "name": tool_name,
                "input": tool_input,
            }
        ],
        "model": "claude-sonnet-4-6",
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


def _openai_chat_response(
    *,
    title: str = "Login crash",
    description: str = "high priority backend bug for the login crash",
    task_type: str = "bug",
    priority: int = 3,
    assigned_role: int | None = 2,
    blocked_by: int | None = None,
) -> dict:
    """Build a fake OpenAI Chat Completion response with JSON content."""
    import json as _json

    proposal = {
        "title": title,
        "description": description,
        "task_type": task_type,
        "priority": priority,
        "assigned_role": assigned_role,
        "blocked_by": blocked_by,
    }
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": _json.dumps(proposal),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


# =============================================================================
# Helpers
# =============================================================================


async def _count_tasks(client) -> int:
    """Count active tasks for project 1 via the public list endpoint."""
    resp = await client.get(
        "/api/tasks",
        params={"limit": 500, "include_cancelled": True},
        headers={"X-Project-Id": str(_PROJECT_ID)},
    )
    assert resp.status_code == 200, resp.text
    return len(resp.json())


# =============================================================================
# Schema-level
# =============================================================================


def test_parse_request_rejects_empty_text() -> None:
    from pydantic import ValidationError

    from src.schemas.ai_task import ParseRequest

    with pytest.raises(ValidationError):
        ParseRequest(text="")


def test_parse_request_rejects_oversized_text() -> None:
    from pydantic import ValidationError

    from src.schemas.ai_task import ParseRequest

    with pytest.raises(ValidationError):
        ParseRequest(text="x" * 2001)


def test_proposed_task_rejects_invalid_priority() -> None:
    from pydantic import ValidationError

    from src.schemas.ai_task import ProposedTask

    with pytest.raises(ValidationError):
        ProposedTask(
            title="t",
            description="d",
            task_type="bug",
            priority=5,  # type: ignore[arg-type]
            assigned_role=None,
        )


# =============================================================================
# HTTP — POSITIVE smoke for both providers
# =============================================================================


@pytest.mark.asyncio
async def test_ai_parse_smoke_anthropic(client, monkeypatch) -> None:
    """AC 3: 'high priority backend bug for the login crash' →
    task_type='bug', priority=3, assigned_role=2 (anthropic provider)."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    with respx.mock(assert_all_called=True) as router:
        router.post(_ANTHROPIC_MESSAGES_URL).mock(
            return_value=httpx.Response(200, json=_anthropic_tool_response())
        )

        resp = await client.post(
            "/api/tasks/ai-parse",
            json={"text": "high priority backend bug for the login crash"},
            headers={"X-Project-Id": str(_PROJECT_ID)},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "proposed" in body
    proposed = body["proposed"]
    assert proposed["task_type"] == "bug"
    assert proposed["priority"] == 3
    assert proposed["assigned_role"] == 2
    assert proposed["blocked_by"] is None
    assert proposed["title"]
    assert proposed["description"]


@pytest.mark.asyncio
async def test_ai_parse_smoke_openai(client, monkeypatch) -> None:
    """AC 5: same smoke input parses correctly through the OpenAI branch."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    # assert_all_called=False because the anthropic route is registered as
    # a negative guard (we ASSERT it is NOT called below).
    with respx.mock(assert_all_called=False) as router:
        openai_route = router.post(_OPENAI_CHAT_URL).mock(
            return_value=httpx.Response(200, json=_openai_chat_response())
        )
        # Anthropic must NOT be called when provider=openai.
        anthropic_route = router.post(_ANTHROPIC_MESSAGES_URL).mock(
            return_value=httpx.Response(500, json={"error": "should not be called"})
        )

        resp = await client.post(
            "/api/tasks/ai-parse",
            json={"text": "high priority backend bug for the login crash"},
            headers={"X-Project-Id": str(_PROJECT_ID)},
        )

    assert resp.status_code == 200, resp.text
    assert openai_route.called
    assert not anthropic_route.called  # AC 5: provider switch works
    body = resp.json()
    proposed = body["proposed"]
    assert proposed["task_type"] == "bug"
    assert proposed["priority"] == 3
    assert proposed["assigned_role"] == 2


# =============================================================================
# HTTP — NEGATIVE error paths
# =============================================================================


@pytest.mark.asyncio
async def test_ai_parse_empty_text_returns_422(client, monkeypatch) -> None:
    """AC 4: empty text fails Pydantic min_length=1 with 422."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    resp = await client.post(
        "/api/tasks/ai-parse",
        json={"text": ""},
        headers={"X-Project-Id": str(_PROJECT_ID)},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_ai_parse_missing_api_key_returns_503(client, monkeypatch) -> None:
    """When LANGGRAPH_LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is unset,
    the endpoint returns 503 with a clear actionable detail (not 500)."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    resp = await client.post(
        "/api/tasks/ai-parse",
        json={"text": "make a thing"},
        headers={"X-Project-Id": str(_PROJECT_ID)},
    )
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert "ANTHROPIC_API_KEY" in body["detail"]
    assert "anthropic" in body["detail"].lower()


@pytest.mark.asyncio
async def test_ai_parse_missing_header_returns_400(client, monkeypatch) -> None:
    """X-Project-Id is required for all /api/tasks/* endpoints (Kanban #695)."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    resp = await client.post(
        "/api/tasks/ai-parse",
        json={"text": "make a thing"},
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_ai_parse_provider_5xx_maps_to_502(client, monkeypatch) -> None:
    """Provider 5xx → 502 at the wire (don't leak provider details, don't 500)."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    with respx.mock(assert_all_called=True) as router:
        router.post(_ANTHROPIC_MESSAGES_URL).mock(
            return_value=httpx.Response(503, json={"error": {"message": "down"}})
        )

        resp = await client.post(
            "/api/tasks/ai-parse",
            json={"text": "high priority backend bug for the login crash"},
            headers={"X-Project-Id": str(_PROJECT_ID)},
        )

    assert resp.status_code == 502, resp.text


@pytest.mark.asyncio
async def test_ai_parse_unparseable_proposal_returns_422(client, monkeypatch) -> None:
    """LLM returns valid-but-out-of-range field → 422 AiUnparseable."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    # priority=99 fails the Literal[1,2,3,4] guard in ProposedTask.
    bad_response = _anthropic_tool_response()
    bad_response["content"][0]["input"]["priority"] = 99

    with respx.mock(assert_all_called=True) as router:
        router.post(_ANTHROPIC_MESSAGES_URL).mock(
            return_value=httpx.Response(200, json=bad_response)
        )

        resp = await client.post(
            "/api/tasks/ai-parse",
            json={"text": "high priority backend bug for the login crash"},
            headers={"X-Project-Id": str(_PROJECT_ID)},
        )

    assert resp.status_code == 422, resp.text
    assert "unparseable" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_ai_parse_rejects_ollama_provider(client, monkeypatch) -> None:
    """API scope is anthropic + openai only. ollama → 503 with actionable detail."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "ollama")

    resp = await client.post(
        "/api/tasks/ai-parse",
        json={"text": "make a thing"},
        headers={"X-Project-Id": str(_PROJECT_ID)},
    )
    assert resp.status_code == 503, resp.text


# =============================================================================
# HTTP — INVARIANT: ai-parse does NOT create a tasks row (AC 2)
# =============================================================================


@pytest.mark.asyncio
async def test_ai_parse_does_not_create_task_row(client, monkeypatch) -> None:
    """AC 2: ai-parse is read-only. Task count must be unchanged after call."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    before = await _count_tasks(client)

    with respx.mock(assert_all_called=True) as router:
        router.post(_ANTHROPIC_MESSAGES_URL).mock(
            return_value=httpx.Response(200, json=_anthropic_tool_response())
        )
        resp = await client.post(
            "/api/tasks/ai-parse",
            json={"text": "high priority backend bug for the login crash"},
            headers={"X-Project-Id": str(_PROJECT_ID)},
        )
    assert resp.status_code == 200, resp.text

    after = await _count_tasks(client)
    assert after == before, (
        f"ai-parse leaked a task row (before={before}, after={after})"
    )
