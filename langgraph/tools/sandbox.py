"""Global sandbox guards (Kanban #981).

Three pure functions wrapped around every specialist-tool invocation by the
loop in `nodes.backend_specialist_node`:

  - `fs_boundary_check(ctx, args)`     — reject write/destructive paths
                                          outside the resolved working_path.
  - `apply_output_cap(result)`          — system-wide 100KB cap on
                                          ToolResult.output (applied AFTER
                                          the tool's own truncation).
  - `check_hard_kill_drift(tool, result, requested_timeout_s)`
                                        — verify the per-tool timeout fired
                                          (force-mark failure if not).

Design notes (locked):

- **fs-boundary**: enforced ONLY for tools at `Tier.WRITE` and
  `Tier.DESTRUCTIVE` whose input_schema declares a `path` field (file_edit,
  file_write). git_commit is WRITE-tier but writes only inside the repo
  (no `path` arg) so the boundary skip is by-design — the underlying
  git invocation is already cwd-scoped to `ctx.repo_root`. shell_run is
  DESTRUCTIVE-tier and doesn't declare a `path` either; its own
  allowlist + denylist is the sandbox (see shell_run.py).

- **realpath resolution**: per the design lock (#949 Q1 → A), symlinks
  are resolved ONCE — the caller passes the already-resolved
  `ctx.working_path`. We re-resolve the input `path` here via
  `os.path.realpath()` to defend against a path like
  `<working_path>/../etc/passwd` or a symlink-pointing-outward.

- **output cap**: 100KB system cap. The tool may have already truncated
  to a smaller value (shell_run truncates stdout to 100KB internally);
  the system cap covers tools that don't truncate (file_edit's diff,
  http_get's body — both could exceed 100KB on a long file or a heavy
  response). The cap appends a `\n... [system-cap]\n` marker so the LLM
  sees a clear signal that output was elided.

- **hard-kill drift**: belt-and-suspenders. The per-tool timeout
  (`asyncio.wait_for(..., timeout=...)`) fires in normal cases. If the
  tool ran for `> timeout_s * 1.5` and returned success, that's a
  symptom of a stuck subprocess that escaped the wait_for (e.g. exec'd
  child outlived the parent). We log a structured WARNING and force
  `success=False` so the audit trail shows the anomaly + the LLM is
  told to back off.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .base import InvokeContext, Tier, Tool, ToolResult

logger = logging.getLogger("langgraph.tools.sandbox")

# Locked at #949 Q4 → A. System-wide cap, in BYTES (UTF-8 encoded).
# Applied AFTER any tool-side truncation. The marker is verbose enough that
# the LLM cannot mistake it for legitimate output ending in "...".
OUTPUT_CAP_BYTES: int = 100_000
OUTPUT_CAP_MARKER: str = "\n... [system-cap]\n"

# Hard-kill drift threshold (multiplier on declared timeout). A tool that
# reports duration_ms > timeout_s * 1000 * 1.5 silently escaped its timeout
# — that's a sandbox failure, not a tool failure, so we force-mark it.
_HARD_KILL_DRIFT_MULT: float = 1.5


# ---------------------------------------------------------------------------
# fs-boundary
# ---------------------------------------------------------------------------


def _tool_writes_to_path_arg(tool: Tool) -> bool:
    """True if `tool` mutates the filesystem at an LLM-controlled `path`.

    The boundary check applies only to tools that:
      1. are WRITE or DESTRUCTIVE tier, AND
      2. declare a `path` field on their input_schema.

    Both file_edit and file_write meet this; git_commit (WRITE, no `path`)
    and shell_run (DESTRUCTIVE, no `path`) do not. http_get/http_post
    (NETWORK tier) are filtered out by the tier check.
    """
    if tool.tier not in (Tier.WRITE, Tier.DESTRUCTIVE):
        return False
    schema = getattr(tool, "input_schema", None)
    if schema is None:
        return False
    return "path" in getattr(schema, "model_fields", {})


def fs_boundary_check(
    tool: Tool, ctx: InvokeContext, args: dict[str, Any]
) -> ToolResult | None:
    """Return a ToolResult on boundary violation, None on pass.

    Resolves both `ctx.working_path` and `args['path']` with
    `os.path.realpath()` so a symlink pointing outside the boundary is
    caught. `ctx.working_path = None` disables the check (test default;
    in production the worker injects the project's resolved working_path).
    """
    if not _tool_writes_to_path_arg(tool):
        return None
    if not ctx.working_path:
        # No boundary configured for this invocation. Tests rely on this
        # default — the production specialist-node wiring is responsible for
        # injecting working_path from the project's `working_path` column.
        return None

    raw_path = args.get("path")
    if raw_path is None or not isinstance(raw_path, str) or raw_path == "":
        # Pydantic input validation will reject this BEFORE the sandbox runs.
        # If we somehow got here with no path, defer the error to the tool.
        return None

    boundary = os.path.realpath(ctx.working_path)
    # Resolve the candidate path against the boundary's parent if relative.
    # Most LLMs emit absolute paths, but a relative path means "relative to
    # the working_path" — the boundary is the natural anchor.
    candidate = raw_path if os.path.isabs(raw_path) else os.path.join(boundary, raw_path)
    resolved = os.path.realpath(candidate)

    # The candidate is inside the boundary iff its resolved form equals
    # boundary OR is a subpath of boundary. `commonpath` returns boundary
    # exactly when `resolved` is `<boundary>` or `<boundary>/...`.
    try:
        common = os.path.commonpath([boundary, resolved])
    except ValueError:
        # Mismatched drives on Windows etc. — always outside.
        common = ""

    if common != boundary:
        return ToolResult(
            success=False,
            error_code="fs_boundary",
            error_msg=(
                f"Path {raw_path!r} (resolved: {resolved!r}) is outside the "
                f"sandbox working_path {boundary!r}. The specialist may only "
                "read/write inside this project's working_path. Use a path "
                "relative to the working_path, or absolute paths that resolve "
                "inside it."
            ),
            retry_safe=True,
        )
    return None


# ---------------------------------------------------------------------------
# output cap
# ---------------------------------------------------------------------------


def apply_output_cap(result: ToolResult, cap_bytes: int = OUTPUT_CAP_BYTES) -> ToolResult:
    """Truncate `result.output` to `cap_bytes` UTF-8 bytes if oversized.

    Returns a NEW ToolResult (Pydantic models are immutable enough that
    mutating in-place via `__setattr__` is brittle). Preserves every other
    field. No-op for `result.output is None` or output that fits the cap.

    Truncation is conservative on UTF-8: encode → slice → decode with
    `errors='replace'` so we never emit a partial multibyte sequence.
    """
    if result.output is None:
        return result
    encoded = result.output.encode("utf-8")
    if len(encoded) <= cap_bytes:
        return result
    sliced = encoded[:cap_bytes].decode("utf-8", errors="replace")
    truncated = sliced + OUTPUT_CAP_MARKER
    # Build a new ToolResult via model_copy(update=...). model_dump + roundtrip
    # would also work but is heavier.
    return result.model_copy(update={"output": truncated})


# ---------------------------------------------------------------------------
# hard-kill drift detection
# ---------------------------------------------------------------------------


def check_hard_kill_drift(
    tool: Tool,
    result: ToolResult,
    requested_timeout_s: int | None = None,
) -> ToolResult:
    """Detect a tool that escaped its declared timeout.

    Compares `result.duration_ms` against `requested_timeout_s` (or the
    tool's class-level `timeout_sec` if not supplied). If duration exceeds
    `timeout * 1.5 * 1000` ms AND `success=True`, that's a runaway: the
    per-tool wait_for didn't fire. Logs a WARNING (so ops can grep) and
    force-marks `success=False` with `error_code='hard_kill_drift'`.

    No-op for tools that don't declare a timeout (most read-tier tools)
    or for results that already failed.
    """
    timeout_s = requested_timeout_s
    if timeout_s is None:
        timeout_s = getattr(tool, "timeout_sec", 0) or 0
    if timeout_s <= 0:
        return result
    if not result.success:
        return result

    limit_ms = int(timeout_s * 1000 * _HARD_KILL_DRIFT_MULT)
    if result.duration_ms <= limit_ms:
        return result

    # Drift detected.
    logger.warning(
        "hard_kill_drift: tool=%s declared_timeout=%ds actual_duration_ms=%d "
        "(> %dms cap); force-marking failure",
        tool.name,
        timeout_s,
        result.duration_ms,
        limit_ms,
    )
    return result.model_copy(
        update={
            "success": False,
            "error_code": "hard_kill_drift",
            "error_msg": (
                f"Tool {tool.name!r} reported success but ran for "
                f"{result.duration_ms}ms — more than {limit_ms}ms "
                f"(1.5x the declared timeout of {timeout_s}s). Sandbox "
                "force-marked this as a failure to surface the anomaly."
            ),
            "retry_safe": False,
        }
    )


# ---------------------------------------------------------------------------
# convenience wrapper — the canonical "apply all three guards" entry point
# ---------------------------------------------------------------------------


def apply_sandbox(
    tool: Tool,
    result: ToolResult,
    requested_timeout_s: int | None = None,
) -> ToolResult:
    """Apply output-cap + hard-kill-drift to a tool result, in that order.

    The fs-boundary check runs BEFORE the tool fires (it's a pre-flight
    check), so it's not part of this post-flight bundle. Callers do:

        violation = fs_boundary_check(tool, ctx, args)
        if violation is not None:
            return violation  # never invoke the tool
        result = await tool.invoke(args, ctx)
        result = apply_sandbox(tool, result, requested_timeout_s=t)

    The two-step pattern keeps the pre/post distinction explicit at the
    call site rather than hiding it in a single mega-wrapper.
    """
    result = apply_output_cap(result)
    result = check_hard_kill_drift(tool, result, requested_timeout_s)
    return result
