"""Specialist tool layer — base classes (Kanban #977, foundation of #949).

This module defines the contract every specialist tool implements:

- `Tier` — permission-tier enum (read/write/network/destructive). Each tool
  declares its tier so the per-project permission gate (#979) can decide
  auto-allow vs halt-for-review vs hard-block. THIS slice (#977) just declares
  the tier; the gate isn't wired yet.
- `ToolResult` — Pydantic envelope every `invoke()` returns. Carries
  success/error_code/error_msg/output/retry_safe/duration_ms. Serializable to
  the LangChain `ToolMessage.content` slot.
- `Tool` — abstract base. Subclasses set class attributes `name`, `description`,
  `tier`, `input_schema` (Pydantic model class), and implement `async invoke()`.
  `to_langchain()` adapts the tool into a langchain `StructuredTool` so the
  specialist node can wire `model.bind_tools(registry.all_tools_as_langchain())`.

Design notes locked in `_scratch/specialist-tools-design.md` (§2, §7) +
Lead/user review (#949). Out of scope here: permission gate flow, audit table,
global sandbox enforcement (subprocess timeout + fs boundary + output cap are
deferred to #981). Per-tool `dry_run` is implemented in each tool's input
schema; we do NOT enforce a global dry-run mode at the engine level (Kanban
#949b).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


class Tier(str, Enum):
    """Permission tier — classifies a tool's blast radius.

    READ = no state change (git status/diff, file read).
    WRITE = modifies local state, reversible (file_edit, file_write, git_commit).
    NETWORK = external calls, may have side effects (http_get/post — #978).
    DESTRUCTIVE = cannot be safely undone (shell_run lands here because
        arbitrary shell can do anything; #979 will hard-block this tier by
        default, with the per-command allowlist/denylist providing the only
        path through).
    """

    READ = "read"
    WRITE = "write"
    NETWORK = "network"
    DESTRUCTIVE = "destructive"


class ToolResult(BaseModel):
    """Standard return envelope for all tools.

    The shape is locked by the design doc §7. All fields are present in every
    return — even on success `error_code`/`error_msg` exist as `None`. This
    rigidity makes the audit log (#980) trivial to serialize and lets the LLM
    see a uniform tool-response shape regardless of which tool ran.

    `retry_safe` defaults to True (most read+idempotent-write tools); tools
    like `git_commit` and future `http_post` (#978) override to False so the
    engine knows not to auto-retry on transient failure.
    """

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(..., description="True if the tool's operation succeeded.")
    error_code: str | None = Field(
        None,
        description=(
            "Machine-readable failure code (e.g. 'permission_denied', 'timeout', "
            "'not_found', 'match_ambiguous', 'blocked_command'). None on success."
        ),
    )
    error_msg: str | None = Field(
        None, description="Human-readable error message; surfaced to the LLM."
    )
    output: str | None = Field(
        None,
        description=(
            "Tool-specific output — stdout, file diff, API response, etc. "
            "Truncated to the per-project output cap (#981) when applicable."
        ),
    )
    retry_safe: bool = Field(
        True,
        description=(
            "True iff the engine can safely retry the call. False for POST and "
            "git_commit-like non-idempotent operations."
        ),
    )
    duration_ms: int = Field(
        0, ge=0, description="Wall-clock duration of the invocation (milliseconds)."
    )


class ToolInput(BaseModel):
    """Base class for tool input schemas. Subclasses define per-tool fields.

    `extra='forbid'` is critical — the LLM occasionally hallucinates extra args
    that don't exist on the schema. With forbid, those calls raise a
    ValidationError that the engine catches + reports back as a tool error
    rather than silently dropping the extra arg.
    """

    model_config = ConfigDict(extra="forbid")


class InvokeContext(BaseModel):
    """Metadata passed to every `Tool.invoke()` call.

    Fields here are READ-only inside tools. The context carries task_id +
    project_id for audit logging (#980 will wire this), `working_path` for the
    fs boundary check (#981 will enforce), and `repo_root` for git ops that
    need to know where the git directory lives.

    For #977 the context is intentionally minimal — `permission_config` and
    `audit_logger` are #979/#980 territory. Tools that need a working dir use
    `repo_root` as the default cwd if a path is relative.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: int | None = Field(None, description="Kanban task that invoked this tool.")
    project_id: int | None = Field(None, description="Project the task belongs to.")
    repo_root: str = Field(
        "/repo",
        description=(
            "Filesystem root for git/shell ops. In the langgraph container "
            "the host worktree is bind-mounted here (see docker-compose.yml). "
            "Used as cwd for git_diff / git_status / shell_run when not "
            "explicitly overridden."
        ),
    )
    working_path: str | None = Field(
        None,
        description=(
            "Optional per-task fs boundary (e.g. /repo/_sessions/task-123/). "
            "Tools MAY narrow their cwd to this path; enforcement of the "
            "boundary check moves to #981."
        ),
    )


class Tool(ABC):
    """Abstract base for all specialist tools.

    Subclasses set these class attributes (NOT instance attrs — the registry
    instantiates each tool once and reuses):

    - `name`: snake_case, LLM-facing, unique across the registry.
    - `description`: docstring-style description shown to the LLM in
      `model.bind_tools()`. Be concrete about inputs, outputs, failure modes.
    - `tier`: a `Tier` value.
    - `input_schema`: subclass of `ToolInput` (Pydantic v2 BaseModel).
    - `timeout_sec` (optional): per-tool default; #981 enforces.

    Concrete classes implement `async _run(input_obj, context)` which returns
    a `ToolResult`. The public `invoke()` wraps `_run` with timing + the
    Pydantic input parsing dance.
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    tier: ClassVar[Tier] = Tier.READ
    input_schema: ClassVar[type[ToolInput]]
    timeout_sec: ClassVar[int] = 30

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Enforce that every concrete Tool subclass declares the contract.

        Catches dev errors early (missing name, missing input_schema, mis-typed
        tier) at class-definition time, not at `registry.register()` time —
        the stack trace is more informative this way.
        """
        super().__init_subclass__(**kwargs)
        # `__abstractmethods__` is set by ABCMeta AFTER __init_subclass__ runs,
        # so we approximate "is this concrete?" by checking whether `_run`
        # is still the abstract version from `Tool`. Intermediate abstract
        # subclasses can skip the contract check by leaving `_run` abstract.
        run_method = cls.__dict__.get("_run")
        if run_method is None or getattr(run_method, "__isabstractmethod__", False):
            return
        if not cls.name:
            raise TypeError(f"{cls.__name__}.name must be a non-empty string.")
        if not isinstance(cls.tier, Tier):
            raise TypeError(
                f"{cls.__name__}.tier must be a Tier value, got {cls.tier!r}."
            )
        schema = getattr(cls, "input_schema", None)
        if schema is None or not (
            isinstance(schema, type) and issubclass(schema, ToolInput)
        ):
            raise TypeError(
                f"{cls.__name__}.input_schema must be a ToolInput subclass, "
                f"got {schema!r}."
            )

    @abstractmethod
    async def _run(self, input_obj: ToolInput, context: InvokeContext) -> ToolResult:
        """Concrete tools implement this. Receives a validated input + context.

        MUST return a ToolResult — never raise for tool-internal failures (use
        `ToolResult(success=False, error_code=...)` instead). Raising is
        reserved for harness errors (invalid input schema, registry lookup
        failed) which the specialist node catches and halts on.
        """
        ...

    async def invoke(
        self, input_dict: dict[str, Any], context: InvokeContext | None = None
    ) -> ToolResult:
        """Public entrypoint. Validates the input, times the call, returns a ToolResult.

        Tool-internal exceptions (anything `_run` raises) are caught here and
        wrapped in `ToolResult(success=False, error_code='internal_error', ...)`
        so the engine never sees a raw exception from a tool.
        """
        if context is None:
            context = InvokeContext()
        start = time.monotonic()
        try:
            input_obj = self.input_schema.model_validate(input_dict)
        except Exception as exc:  # ValidationError or unexpected
            return ToolResult(
                success=False,
                error_code="invalid_input",
                error_msg=f"Input validation failed: {exc}",
                retry_safe=True,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        try:
            result = await self._run(input_obj, context)
        except Exception as exc:
            result = ToolResult(
                success=False,
                error_code="internal_error",
                error_msg=f"Tool {self.name} raised: {exc!r}",
                retry_safe=False,
            )
        # Always stamp duration; respect a tool that already set it.
        if not result.duration_ms:
            object.__setattr__(
                result,
                "duration_ms",
                int((time.monotonic() - start) * 1000),
            )
        return result

    def to_langchain(self) -> Any:
        """Adapt this tool into a langchain `StructuredTool`.

        The langchain side uses the tool's `name`, `description`, and
        `input_schema` (Pydantic) directly — no schema duplication. The
        coroutine `_lc_coroutine` bridges langchain's call into our async
        `invoke()` using the per-call context the specialist node injects via
        a closure pattern (#977 defers that wiring to the node; here we just
        bind a default InvokeContext so unit tests work standalone).
        """
        # Lazy import — keeps `langchain_core` off the import path for code
        # that just inspects the registry (e.g. CLI listing tools).
        from langchain_core.tools import StructuredTool

        async def _lc_coroutine(**kwargs: Any) -> str:
            result = await self.invoke(kwargs)
            return result.model_dump_json()

        return StructuredTool.from_function(
            coroutine=_lc_coroutine,
            name=self.name,
            description=self.description,
            args_schema=self.input_schema,
        )
