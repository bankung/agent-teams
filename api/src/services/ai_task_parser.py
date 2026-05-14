"""LLM-backed task parser for POST /api/tasks/ai-parse (Kanban #856).

Provider-agnostic factory keyed on `LANGGRAPH_LLM_PROVIDER` (parity with
the langgraph service's `llm.py` factory — same env-var contract). API scope
is anthropic + openai only; ollama lands as a follow-up.

Pipeline:
1. Resolve provider from env (default 'anthropic').
2. Resolve model from per-provider env-var override or fall back to defaults.
3. Build provider-specific structured-output call:
   - Anthropic: tool-use API with the proposal schema as the tool input.
   - OpenAI: JSON schema response_format with strict=true.
4. Wrap inside asyncio.wait_for(_, 10s) — the timeout converts to a 504 at
   the router edge.
5. Return a validated `ProposedTask` instance — the caller never sees SDK
   types or raw JSON.

Errors map to:
- `MissingApiKey` → 503 (provider not configured)
- `AiCallFailed` → 502 (network / 5xx from provider)
- `AiCallTimeout` → 504 (exceeded 10s wall)
- `AiUnparseable` → 422 (LLM returned shape that fails Pydantic validation)

Cost tracking is NOT integrated with the cost_usage aggregation (out of
scope #856; that aggregation tracks task RUNS, not one-off parse calls).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Literal

from pydantic import ValidationError

from src.schemas.ai_task import ProposedTask

logger = logging.getLogger(__name__)

# Mirror langgraph/llm.py defaults so ops can flip ANTHROPIC_MODEL /
# OPENAI_MODEL once and both services pick up the new value.
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-4o"

# 10s soft wall for the LLM call. Mirrored in the router as the wait_for
# budget; surfaces as a 504 to the client.
AI_PARSE_TIMEOUT_SECONDS = 10.0

# Provider scope for the api service. Note: langgraph/llm.py also supports
# 'ollama'; api scope is intentionally narrower (#856 brief) — follow-up.
ProviderName = Literal["anthropic", "openai"]
_SUPPORTED_PROVIDERS: tuple[str, ...] = ("anthropic", "openai")


# =============================================================================
# Exceptions — map to HTTP codes in the router.
# =============================================================================


class AiTaskParserError(Exception):
    """Base for ai-parser-specific failures."""


class MissingApiKey(AiTaskParserError):
    """Selected provider's API key env var is unset/empty."""


class AiCallFailed(AiTaskParserError):
    """Provider call failed (network, 5xx, malformed response)."""


class AiCallTimeout(AiTaskParserError):
    """Provider call exceeded `AI_PARSE_TIMEOUT_SECONDS`."""


class AiUnparseable(AiTaskParserError):
    """LLM returned output that does not pass `ProposedTask` validation."""


# =============================================================================
# Provider resolution
# =============================================================================


def resolve_provider() -> ProviderName:
    """Read LANGGRAPH_LLM_PROVIDER from env; validate against api scope.

    Note the env var name is shared with the langgraph service so ops sets
    it ONCE for the whole stack. The api scope is narrower (anthropic +
    openai); 'ollama' is rejected here with a clear actionable error.
    """
    raw = os.getenv("LANGGRAPH_LLM_PROVIDER", "anthropic").strip().lower()
    if raw not in _SUPPORTED_PROVIDERS:
        raise MissingApiKey(
            f"LANGGRAPH_LLM_PROVIDER={raw!r} is not supported by the api "
            f"service (expected one of {list(_SUPPORTED_PROVIDERS)}). "
            "ollama is supported only by the langgraph service in this "
            "release."
        )
    return raw  # type: ignore[return-value]


def _resolve_model(provider: ProviderName) -> str:
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL).strip()
    return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip()


def _require_api_key(provider: ProviderName) -> str:
    env_var = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
    key = os.getenv(env_var, "").strip()
    if not key:
        raise MissingApiKey(
            f"AI provider not configured (LANGGRAPH_LLM_PROVIDER={provider} "
            f"but {env_var} is empty)"
        )
    return key


# =============================================================================
# System prompt
# =============================================================================


_SYSTEM_PROMPT_TEMPLATE = """\
You are a task triage assistant for a Kanban backend. Your job: parse the \
user's free-text request into a structured task proposal so the user can \
confirm it via a pre-fill form.

Project context: {project_context}

Output discipline:
- Use ONLY the tool / structured-output channel. No chain-of-thought, no \
markdown, no commentary in the response.
- Be conservative: when a field is uncertain, use the safest default \
(see field rules below) rather than guessing.

Field rules:
- title: short noun phrase (under ~80 chars). Strip filler words like \
"please add", "I want to". Do not start with a verb if the user described \
a thing rather than an action.
- description: the user's free text, lightly cleaned (fix typos, expand \
unclear acronyms only if obvious). Echo the original if the input is \
already concise.
- task_type: one of {{bug, feature, chore, docs, refactor}}. Use 'bug' \
when the user mentions a crash, defect, error, regression, or "broken". \
Use 'feature' for new capabilities. Use 'chore' for housekeeping / config \
/ dependency updates. Use 'docs' for documentation. Use 'refactor' for \
internal restructure with no behavior change. Default to 'feature' if \
uncertain.
- priority: integer code 1..4. 1=low, 2=normal, 3=high, 4=urgent. \
"high priority" in the user text maps to 3 (not 4). 4=urgent is reserved \
for production blockers / outages / "everything is on fire". Default to 2.
- assigned_role: integer code 1..5 OR null. 1=frontend (UI, browser, \
client, page, component, button, form), 2=backend (API, server, database, \
endpoint, query, migration, auth), 3=devops (deploy, infra, docker, CI, \
pipeline, build), 4=qa (testing, verification, test plan), 5=reviewer \
(code review). Return null when the text gives no clear signal.
- blocked_by: integer task ID OR null. ONLY non-null when the user \
explicitly mentions a task number that blocks this one (e.g., "blocked \
by #123", "depends on task 45"). Otherwise null.
"""


def _build_system_prompt(project_id: int) -> str:
    return _SYSTEM_PROMPT_TEMPLATE.format(
        project_context=f"project_id={project_id}"
    )


# =============================================================================
# Anthropic branch — tool-use API
# =============================================================================


_ANTHROPIC_TOOL_NAME = "propose_task"


def _anthropic_tool_spec() -> dict[str, Any]:
    """Tool definition for the Anthropic Messages API.

    The tool's `input_schema` is derived from ProposedTask's JSON schema so
    the LLM is forced into the exact wire shape the FE will consume.
    """
    return {
        "name": _ANTHROPIC_TOOL_NAME,
        "description": (
            "Emit the structured task proposal. Call this tool exactly "
            "once with the parsed fields."
        ),
        "input_schema": ProposedTask.model_json_schema(),
    }


async def _call_anthropic(
    *, text: str, project_id: int, model: str, api_key: str
) -> ProposedTask:
    """Call Anthropic with forced tool-use; parse the tool input.

    Imported lazily so an OpenAI-only deployment doesn't pay the SDK import
    cost at module load.
    """
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key)
    system = _build_system_prompt(project_id)

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=1024,
            system=system,
            tools=[_anthropic_tool_spec()],
            tool_choice={"type": "tool", "name": _ANTHROPIC_TOOL_NAME},
            messages=[{"role": "user", "content": text}],
        )
    except Exception as exc:  # noqa: BLE001 — wrap + hide provider details
        logger.warning("ai-parse: Anthropic call failed: %s", exc, exc_info=True)
        raise AiCallFailed(f"Anthropic call failed: {exc}") from exc

    # Find the tool_use block. Forced tool_choice guarantees one exists in
    # the happy path; we defensively raise AiCallFailed if not.
    tool_input: dict[str, Any] | None = None
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            raw_input = getattr(block, "input", None)
            if isinstance(raw_input, dict):
                tool_input = raw_input
                break

    if tool_input is None:
        raise AiCallFailed(
            "Anthropic response did not include a tool_use block"
        )

    try:
        return ProposedTask.model_validate(tool_input)
    except ValidationError as exc:
        raise AiUnparseable(
            f"AI returned an unparseable proposal: {exc.errors()}"
        ) from exc


# =============================================================================
# OpenAI branch — JSON schema structured output
# =============================================================================


def _openai_response_format() -> dict[str, Any]:
    """response_format spec for OpenAI Chat Completions.

    Newer GA structured-output API: `json_schema` mode with `strict: true`
    forces the model to emit valid JSON matching the schema, with no
    additional properties. Models that don't support strict can fall back
    to function-calling, but we don't add that fallback here — the
    AiUnparseable path catches any drift via Pydantic validation.
    """
    schema = ProposedTask.model_json_schema()
    # OpenAI strict mode requires additionalProperties=false at every object
    # level — Pydantic already emits this for `extra='forbid'`, but the
    # top-level safety net stays.
    schema.setdefault("additionalProperties", False)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "proposed_task",
            "schema": schema,
            "strict": True,
        },
    }


async def _call_openai(
    *, text: str, project_id: int, model: str, api_key: str
) -> ProposedTask:
    """Call OpenAI Chat Completions with json_schema response_format."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)
    system = _build_system_prompt(project_id)

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            response_format=_openai_response_format(),
            max_tokens=1024,
        )
    except Exception as exc:  # noqa: BLE001 — wrap + hide provider details
        logger.warning("ai-parse: OpenAI call failed: %s", exc, exc_info=True)
        raise AiCallFailed(f"OpenAI call failed: {exc}") from exc

    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError) as exc:
        raise AiCallFailed(
            "OpenAI response did not include a message content"
        ) from exc

    if not content:
        raise AiCallFailed("OpenAI returned empty message content")

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AiUnparseable(
            f"AI returned an unparseable proposal: not valid JSON ({exc})"
        ) from exc

    try:
        return ProposedTask.model_validate(payload)
    except ValidationError as exc:
        raise AiUnparseable(
            f"AI returned an unparseable proposal: {exc.errors()}"
        ) from exc


# =============================================================================
# Public entry point
# =============================================================================


async def parse_task_text(*, text: str, project_id: int) -> ProposedTask:
    """Parse free-text into a `ProposedTask`. Provider chosen from env.

    Provider call is wrapped in `asyncio.wait_for` with a 10s budget; on
    timeout raises `AiCallTimeout`. Caller (router) maps exceptions to HTTP
    codes per the table in the module docstring.
    """
    provider = resolve_provider()
    api_key = _require_api_key(provider)
    model = _resolve_model(provider)

    if provider == "anthropic":
        coro = _call_anthropic(
            text=text, project_id=project_id, model=model, api_key=api_key
        )
    else:
        coro = _call_openai(
            text=text, project_id=project_id, model=model, api_key=api_key
        )

    try:
        return await asyncio.wait_for(coro, timeout=AI_PARSE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise AiCallTimeout(
            f"AI provider timeout after {AI_PARSE_TIMEOUT_SECONDS}s"
        ) from exc
