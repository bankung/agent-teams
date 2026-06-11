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
import re
import time
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

# Kanban #2215 — Mode-B fs-tool guard.
#
# Harness-internal scratch dir. When a project has NO working_path configured
# (working_path NULL), a write under <repo_root>/_scratch is allowed THROUGH
# the destination-legitimacy guard so the regression pack (S5) + the harness's
# own temp writes still work. The NORMAL tier gate then still applies — a
# write-tier tool under /repo/_scratch still HALTs for review exactly as before.
_SCRATCH_SUBDIR: str = "_scratch"

# Error code emitted on the NULL-working_path illegitimate-destination case.
# The specialist node keys on this exact string to convert the destination
# violation into a HALT (ask-where-to-save), mirroring the write-tier gate.
WORKING_PATH_UNSET_CODE: str = "working_path_unset"

# Kanban #2136 budgeted-truncation convention. The LLM-supplied `raw_path` is
# attacker-influenced (a drifting agent controls it) so it must be sanitized +
# length-capped before it lands in any LLM-facing error string, the audit row,
# or halt text. Mirrors nodes.py:1517 (strip non-printable, keep ASCII-printable
# + the Thai block) then caps to a fixed budget.
_RAW_PATH_CAP: int = 500
_NON_PRINTABLE_RE = re.compile(r"[^\x20-\x7E฀-๿]")


def _safe_raw_path(raw_path: str) -> str:
    """Sanitize + length-cap an LLM-supplied path for safe embedding.

    Strips non-printable / control characters (so an injected control sequence
    can't pollute a log consumer or the LLM context) and caps to `_RAW_PATH_CAP`
    chars with an explicit elision marker. Used in every `_*_result` constructor
    before the path is interpolated into an error message.
    """
    cleaned = _NON_PRINTABLE_RE.sub("?", raw_path)
    if len(cleaned) > _RAW_PATH_CAP:
        return cleaned[:_RAW_PATH_CAP] + "...[trunc]"
    return cleaned

# Operator allowlist file (resolved-path prefixes). A target under any
# allowlisted prefix is allowed regardless of working_path (SET-outside-subtree
# OR NULL). Read with a tiny TTL so operator edits land WITHOUT a container
# restart. Missing file → empty allowlist (no crash).
_ALLOWLIST_PATH: str = "/repo/_runtime/write-allowlist.txt"
_ALLOWLIST_TTL_SEC: float = 5.0
# M1 (#2215 review): keyed by `path` arg — {path: (timestamp, prefixes)}. The
# prior time-only key collided across distinct paths (a test reading a temp
# allowlist would serve the prod cache and vice-versa). Module-level so tests
# can reset it.
_allowlist_cache: dict[str, tuple[float, list[str]]] = {}


def _allowlist_cache_clear() -> None:
    """Test hook — drop the cached allowlist(s) so the next read re-parses."""
    _allowlist_cache.clear()


def _read_allowlist(path: str = _ALLOWLIST_PATH) -> list[str]:
    """Return the resolved-path prefixes from the operator allowlist file.

    Format: one path prefix per line; blank lines and `#` comments ignored.
    Each line is `os.path.realpath()`-resolved so a comparison against an
    already-resolved candidate is apples-to-apples. Missing/unreadable file →
    empty list (the allowlist is purely additive; absence means "no override").

    Cached for `_ALLOWLIST_TTL_SEC` PER `path` so a hot operator edit is picked
    up without a worker restart, while not re-reading the file on every single
    tool call. Keying by path means a non-default path (tests) never serves the
    default-path cache, and vice-versa (M1 #2215).
    """
    now = time.monotonic()
    cached = _allowlist_cache.get(path)
    if cached is not None and (now - cached[0]) < _ALLOWLIST_TTL_SEC:
        return cached[1]

    prefixes: list[str] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                prefixes.append(os.path.realpath(line))
    except FileNotFoundError:
        prefixes = []
    except OSError as exc:
        # A permissions / IO error must NOT crash the guard. Treat as empty
        # (no override) and log so ops can grep.
        logger.warning("write-allowlist read failed (%s): %r", path, exc)
        prefixes = []

    _allowlist_cache[path] = (now, prefixes)
    return prefixes


def _is_under(resolved: str, prefix: str) -> bool:
    """True iff `resolved` is `prefix` itself or a descendant of it.

    Uses `os.path.commonpath` so `/repo/_scratchX` is NOT treated as under
    `/repo/_scratch` (a naive `startswith` would mis-match that sibling).
    """
    try:
        return os.path.commonpath([prefix, resolved]) == prefix
    except ValueError:
        # Mismatched drives on Windows etc. — never under.
        return False


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
    """Destination-legitimacy guard for fs write tools (Kanban #2215).

    Runs BEFORE the tier gate (rule 6): there is no point asking the operator
    to authorize a write to an illegitimate destination. Returns `None` when
    the destination is legitimate (tier gate then proceeds exactly as before),
    or a `ToolResult(success=False, ...)` carrying one of these error codes:

      - ``fs_boundary``            — working_path SET, target OUTSIDE the
                                     subtree (and not allowlisted). LLM-facing
                                     actionable error (existing behaviour).
      - ``working_path_unmounted`` — working_path SET but the resolved boundary
                                     is not an existing directory in this
                                     container (e.g. an unmounted Windows host
                                     path). HARD fail — NEVER silently allow a
                                     /repo write instead.
      - ``working_path_unset``     — working_path NULL and the target is NOT
                                     under <repo_root>/_scratch and NOT
                                     allowlisted. The node converts this into a
                                     HALT (HITL "where should this project save
                                     files?"). retry_safe=False.

    Only WRITE/DESTRUCTIVE tools that declare a `path` field are gated; reads
    and path-less write tools (git_commit, shell_run) skip. The allowlist
    (`/repo/_runtime/write-allowlist.txt`) overrides every rejection in both
    the SET and NULL cases.
    """
    if not _tool_writes_to_path_arg(tool):
        return None

    raw_path = args.get("path")
    if raw_path is None or not isinstance(raw_path, str) or raw_path == "":
        # Pydantic input validation will reject this BEFORE the sandbox runs.
        # If we somehow got here with no path, defer the error to the tool.
        return None

    repo_root = os.path.realpath(ctx.repo_root or "/repo")

    if ctx.working_path:
        # --- working_path SET ------------------------------------------------
        boundary = os.path.realpath(ctx.working_path)
        # Relative paths anchor at the boundary (today's behaviour); absolute
        # paths resolve as-is. realpath() collapses `..` and follows symlinks.
        candidate = (
            raw_path if os.path.isabs(raw_path) else os.path.join(boundary, raw_path)
        )
        resolved = os.path.realpath(candidate)

        # rule 5 — allowlist override applies first, even when the boundary is
        # unmounted or the target is outside the subtree.
        if _allowlisted(resolved):
            return None

        # rule 3 — boundary configured but NOT an existing directory in this
        # container → the host path was never bind-mounted here. Fail HARD
        # (checked BEFORE the subtree-membership allow: a target that "looks"
        # inside an unmounted boundary must still hard-fail, never silently
        # fall through to allowing the write).
        if not os.path.isdir(boundary):
            return _unmounted_result(ctx.working_path, boundary)

        if _is_under(resolved, boundary):
            # rule 2 — inside the (mounted) subtree, allowed (unchanged).
            return None

        # rule 2 (negative) — boundary mounted, target genuinely outside.
        return _outside_subtree_result(raw_path, resolved, boundary)

    # --- working_path NULL ---------------------------------------------------
    candidate = (
        raw_path if os.path.isabs(raw_path) else os.path.join(repo_root, raw_path)
    )
    resolved = os.path.realpath(candidate)

    scratch_root = os.path.realpath(os.path.join(repo_root, _SCRATCH_SUBDIR))
    if _is_under(resolved, scratch_root):
        # rule 4a — harness-internal exception. Allowed THROUGH to the tier
        # gate (which still HALTs write-tier tools — preserves S5).
        return None

    if _allowlisted(resolved):
        # rule 5 — allowlist override in the NULL case too.
        return None

    # rule 4b — NULL working_path, target somewhere illegitimate → ask the
    # operator where this project saves files. The node turns this into a HALT.
    return _unset_result(raw_path, resolved, scratch_root)


def _allowlisted(resolved: str) -> bool:
    """True iff `resolved` falls under any allowlist prefix."""
    return any(_is_under(resolved, prefix) for prefix in _read_allowlist())


def _outside_subtree_result(raw_path: str, resolved: str, boundary: str) -> ToolResult:
    """rule 2 (negative) — target outside a mounted working_path subtree.

    The configured `boundary` (this project's own working_path) is named so the
    LLM can self-correct to a legal path — that is the agent's sanctioned
    sandbox, not a privilege-escalation recipe. The operator override mechanism
    (the write-allowlist) is deliberately NOT named here (see W-1 #2215).
    `raw_path`/`resolved` are sanitized + capped before embedding (N-1 #2136).
    """
    safe_raw = _safe_raw_path(raw_path)
    safe_resolved = _safe_raw_path(resolved)
    return ToolResult(
        success=False,
        error_code="fs_boundary",
        error_msg=(
            f"Path {safe_raw!r} (resolved: {safe_resolved!r}) is outside the "
            f"sandbox working_path {boundary!r}. The specialist may only "
            "read/write inside this project's working_path. Use a path "
            "relative to the working_path, or absolute paths that resolve "
            f"inside it. For temp files, use {boundary!r}/_scratch/."
        ),
        retry_safe=True,
    )


def _unmounted_result(configured: str, boundary: str) -> ToolResult:
    """rule 3 — working_path SET but not mounted in this container."""
    return ToolResult(
        success=False,
        error_code="working_path_unmounted",
        error_msg=(
            f"This project's configured working_path {configured!r} (resolved: "
            f"{boundary!r}) is not mounted inside the worker container, so the "
            "specialist cannot write there. Fix one of: (1) bind-mount that "
            "path into the langgraph service in docker-compose.yml, or "
            "(2) correct projects.working_path to a path that IS mounted in the "
            "container. The harness will NOT silently write to /repo instead."
        ),
        retry_safe=False,
    )


def _unset_result(raw_path: str, resolved: str, scratch_root: str) -> ToolResult:
    """rule 4b — NULL working_path, illegitimate destination → ask operator.

    W-1 (#2215): the exact allowlist file path is NOT printed back to the model
    — naming it would teach a drifting agent the self-grant recipe. The message
    points the model at the operator and references "the write-allowlist (see
    ops docs)" generically. The actual path lives in ops docs / the operator's
    head, not in the LLM-facing string. `raw_path`/`resolved` sanitized + capped
    before embedding (N-1 #2136).
    """
    safe_raw = _safe_raw_path(raw_path)
    safe_resolved = _safe_raw_path(resolved)
    return ToolResult(
        success=False,
        error_code="working_path_unset",
        error_msg=(
            f"This project has no working_path configured, so the harness does "
            f"not know where it should save files. The requested write to "
            f"{safe_raw!r} (resolved: {safe_resolved!r}) is outside the only "
            f"unconfigured-project exception ({scratch_root!r}). Ask the "
            "operator to set the project's working_path, or to grant this "
            "destination via the write-allowlist (see ops docs); then retry "
            "the task."
        ),
        retry_safe=False,
    )


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
