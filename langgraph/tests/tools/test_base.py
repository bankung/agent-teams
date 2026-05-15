"""Tool base-class invariants — contract every Tool subclass must satisfy.

Covers (Kanban #977 AC1):
- ToolResult envelope shape (Pydantic forbids extra fields; defaults present).
- Tier enum values + str-coercion (so it serializes cleanly to JSON for audit).
- Tool subclass attribute enforcement: name/tier/input_schema must be set,
  with the right types, or class-definition raises.
- `invoke()` returns ToolResult even when the inner `_run` raises (catches
  internal errors into `error_code='internal_error'`).
- `invoke()` returns ToolResult with `error_code='invalid_input'` on bad input.
"""

from __future__ import annotations

import pytest

from tools.base import InvokeContext, Tier, Tool, ToolInput, ToolResult


class _DummyInput(ToolInput):
    value: int


class _DummyTool(Tool):
    name = "dummy"
    description = "dummy tool for tests"
    tier = Tier.READ
    input_schema = _DummyInput

    async def _run(self, input_obj, context):  # type: ignore[override]
        return ToolResult(success=True, output=f"value={input_obj.value}")


def test_toolresult_minimal_success_shape():
    r = ToolResult(success=True)
    assert r.success is True
    assert r.error_code is None
    assert r.error_msg is None
    assert r.output is None
    assert r.retry_safe is True
    assert r.duration_ms == 0


def test_toolresult_forbids_extra_fields():
    """Extra fields are silently lost when shape changes — forbid catches that
    at construction time so a typo on the producer side (e.g. `output_text`
    instead of `output`) raises rather than disappearing into the audit log."""
    with pytest.raises(Exception):  # pydantic.ValidationError
        ToolResult(success=True, foo="bar")  # type: ignore[call-arg]


def test_tier_enum_values():
    assert Tier.READ.value == "read"
    assert Tier.WRITE.value == "write"
    assert Tier.NETWORK.value == "network"
    assert Tier.DESTRUCTIVE.value == "destructive"


def test_subclass_missing_name_raises():
    """Define a Tool with empty name → __init_subclass__ raises immediately."""
    with pytest.raises(TypeError, match="name must be a non-empty string"):

        class Broken(Tool):
            description = "no name"
            tier = Tier.READ
            input_schema = _DummyInput

            async def _run(self, input_obj, context):
                return ToolResult(success=True)


def test_subclass_wrong_tier_type_raises():
    """tier must be a Tier instance, not a raw string."""
    with pytest.raises(TypeError, match="tier must be a Tier value"):

        class Broken(Tool):
            name = "broken"
            description = "x"
            tier = "read"  # type: ignore[assignment]  -- intentional bug
            input_schema = _DummyInput

            async def _run(self, input_obj, context):
                return ToolResult(success=True)


def test_subclass_wrong_input_schema_raises():
    with pytest.raises(TypeError, match="input_schema"):

        class Broken(Tool):
            name = "broken"
            description = "x"
            tier = Tier.READ
            input_schema = dict  # type: ignore[assignment]  -- intentional bug

            async def _run(self, input_obj, context):
                return ToolResult(success=True)


async def test_invoke_validates_input_returns_error_result():
    """Bad input → ToolResult with error_code='invalid_input', not raised."""
    tool = _DummyTool()
    result = await tool.invoke({"value": "not-an-int"})
    assert result.success is False
    assert result.error_code == "invalid_input"
    assert "validation" in (result.error_msg or "").lower()


async def test_invoke_catches_internal_exceptions():
    """If `_run` raises, invoke() converts it to ToolResult(internal_error)."""

    class Bomb(Tool):
        name = "bomb"
        description = "always raises"
        tier = Tier.READ
        input_schema = _DummyInput

        async def _run(self, input_obj, context):
            raise RuntimeError("kaboom")

    result = await Bomb().invoke({"value": 1})
    assert result.success is False
    assert result.error_code == "internal_error"
    assert "kaboom" in (result.error_msg or "")


async def test_invoke_happy_path_returns_envelope():
    tool = _DummyTool()
    result = await tool.invoke({"value": 42})
    assert result.success is True
    assert result.output == "value=42"
    assert result.duration_ms >= 0


async def test_invoke_default_context_is_used_when_none_passed():
    """Calling invoke() without an explicit InvokeContext must not raise —
    base supplies a default one. Useful for unit tests + ad-hoc REPL use."""
    tool = _DummyTool()
    result = await tool.invoke({"value": 1}, context=None)
    assert result.success is True


def test_invokecontext_defaults():
    """InvokeContext fields default to None/string so a bare InvokeContext()
    is valid (used by the public `invoke()` when caller passes None)."""
    ctx = InvokeContext()
    assert ctx.task_id is None
    assert ctx.project_id is None
    assert ctx.repo_root == "/repo"
    assert ctx.working_path is None
