"""Agent-frontmatter validator (Kanban #1016).

Validates every ``.claude/agents/*.md`` file against the locked frontmatter
contract and returns a flat list of diagnostics
(``{file, line, field, message, severity}``). One service function backs both
frontends:

  * ``GET /api/agents/validate``  (``routers/agent_validation.py``)
  * ``python -m scripts.validate_agents``  (``scripts/validate_agents.py``)

so the endpoint and the CLI can never disagree (single implementation).

What counts as an agent file
----------------------------
Every ``*.md`` in the agents directory EXCEPT underscore-prefixed includes
(e.g. ``_dev-shared.md``). Claude Code does not treat ``_``-prefixed files as
agents — they are shared substrate injected into other agents' prompts and have
no frontmatter of their own. Validating them would (correctly) flag
"missing frontmatter", which would break the ground-truth zero-errors
calibration gate. They are skipped, not errored. (Calibration finding,
2026-06-12.)

Severity model (contract §2)
----------------------------
Every diagnostic carries ``severity`` ∈ {``"error"``, ``"warning"``}.

  * ERROR — the file would fail to load as an agent: missing frontmatter,
    malformed YAML, missing/blank required key, bad ``name`` regex, duplicate
    ``name`` across files, unknown ``model`` value, wrong-typed ``tools`` /
    ``hooks``.
  * WARNING — the file loads, but something is off-spec and worth surfacing:
    an unknown top-level key (real files carry custom keys like
    ``email_actions``), or an unknown tool NAME inside ``tools`` (the tool
    universe drifts).

Line numbers (contract §6)
--------------------------
  * YAML parse errors → the mark line from the PyYAML exception, offset by +1
    for the opening ``---`` fence so it points at the real source line.
  * Semantic errors → the line of the offending key, found by a cheap raw-text
    scan (``_find_key_line``). When the key cannot be located cheaply, the
    diagnostic falls back to line 1. We deliberately do NOT build a
    position-tracking YAML loader.

Path / security
---------------
The endpoint passes a FIXED directory (``<repo_root>/.claude/agents``); there is
no client-supplied path (contract §4 dropped the POST-body variant to avoid an
arbitrary-path read primitive). The ``file`` field emitted in every diagnostic
is the BASENAME only — absolute paths never leave this module.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import yaml

from src.schemas.agent_metadata import (
    AGENT_NAME_PATTERN,
    AGENT_NAME_RE,
    ALL_TOOLS_LITERAL,
    KNOWN_TOOLS,
    MODEL_TIERS,
)

logger = logging.getLogger(__name__)

# Static sub-paths under /api/agents/ — route-order makes these unreachable as
# agent names.  RESERVED_AGENT_NAMES backstops the router-order guarantee so a
# file named ``validate.md`` is surfaced as invalid rather than silently
# shadowed.
RESERVED_AGENT_NAMES: frozenset[str] = frozenset({"validate"})

# Frontmatter fence: the block between the first ``---`` line and the next
# ``---`` line (contract §7).
_FENCE = "---"


def _make_diag(
    file: str, line: int, field: str, message: str, severity: str
) -> dict[str, object]:
    """Build one diagnostic dict (the on-the-wire shape)."""
    return {
        "file": file,
        "line": line,
        "field": field,
        "message": message,
        "severity": severity,
    }


def _extract_frontmatter(text: str) -> tuple[str | None, int]:
    """Return ``(frontmatter_text, body_start_line)`` or ``(None, 0)``.

    The frontmatter is the block between the FIRST ``---`` line and the next
    ``---`` line. ``body_start_line`` is the 1-based source line of the opening
    fence (always 1 when a fence exists) — used to offset YAML mark lines.

    Returns ``(None, 0)`` when the file does not open with a ``---`` fence OR
    has an opening fence with no closing fence (both = "no usable frontmatter").
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        return None, 0
    for idx in range(1, len(lines)):
        if lines[idx].strip() == _FENCE:
            # frontmatter body = lines[1:idx]; opening fence is source line 1.
            return "\n".join(lines[1:idx]), 1
    # Opening fence but no closing fence → not a usable frontmatter block.
    return None, 0


class _FrontmatterError(Exception):
    """A frontmatter parse error carrying a 1-based source line number.

    ``line`` is the line WITHIN the frontmatter body (1-based); the caller adds
    the fence offset to get the source line.
    """

    def __init__(self, message: str, line: int) -> None:
        super().__init__(message)
        self.message = message
        self.line = line


def _parse_frontmatter_block(fm_text: str) -> dict[str, object]:
    """Parse a Claude Code agent frontmatter block into a mapping.

    Claude Code frontmatter is NOT strict YAML: a top-level scalar value runs to
    end-of-line and may contain colons (real ``description`` values say things
    like ``"Read-only: never executes DML. Success metric: ..."``). Feeding the
    whole block to ``yaml.safe_load`` rejects those valid files with
    "mapping values are not allowed here" — so we parse line-by-line, faithful
    to how the harness actually reads the block, and only invoke YAML for the
    two structured cases:

      * a ``[...]`` flow value (``tools: [Read, Grep]``)  → parsed as YAML, and
      * a key with NO inline value followed by an indented block
        (``hooks:`` → nested mapping)                     → parsed as YAML.

    A plain scalar value is taken LITERALLY (its colons are data, not syntax).

    Genuine malformed YAML inside a flow value or an indented sub-block still
    raises ``_FrontmatterError`` with the offending line. This is deliberately a
    simple line scanner — NOT a position-tracking YAML loader (contract §6).

    Calibration: validating the 38 real agent files this way yields ZERO errors
    (the strict-YAML approach produced 8 false "mapping values" errors on
    description lines with mid-sentence colons). 2026-06-12, #1016.
    """
    result: dict[str, object] = {}
    lines = fm_text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        # Blank / comment lines between top-level keys are ignored.
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        # Only top-level (column-0, unindented) keys are structural here. An
        # indented line at this position without a preceding block-key is
        # malformed.
        if raw[0] in (" ", "\t"):
            raise _FrontmatterError(
                "unexpected indentation (no top-level key to attach it to)", i + 1
            )
        if ":" not in raw:
            raise _FrontmatterError(
                f"expected 'key: value' on line: {raw.strip()!r}", i + 1
            )
        key, _, inline = raw.partition(":")
        key = key.strip()
        inline = inline.strip()

        # A block-scalar indicator (``>``, ``|`` and their chomp/keep variants)
        # introduces a multi-line folded/literal STRING whose value lives on the
        # following indented lines (real ``description: >`` blocks use this).
        is_block_scalar = inline in (">", "|", ">-", "|-", ">+", "|+")

        if inline == "" or is_block_scalar:
            # Collect the following blank / more-indented lines as this key's
            # block: either a nested mapping (``hooks:``) or a block scalar
            # (``description: >``).
            block_lines: list[str] = []
            j = i + 1
            while j < n and (lines[j].strip() == "" or lines[j][:1] in (" ", "\t")):
                block_lines.append(lines[j])
                j += 1
            if is_block_scalar:
                # Re-attach the indicator and let YAML fold/keep the scalar. The
                # value is a plain string — colons inside it are data.
                try:
                    result[key] = yaml.safe_load(
                        f"{key}: {inline}\n" + "\n".join(block_lines)
                    )[key]
                except yaml.YAMLError as exc:
                    mark = getattr(exc, "problem_mark", None)
                    sub_line = (mark.line if mark is not None else 0) + (i + 1)
                    raise _FrontmatterError(
                        f"malformed block scalar for {key!r}: {exc}", sub_line
                    ) from exc
            elif not any(b.strip() for b in block_lines):
                # ``key:`` with nothing under it → null value.
                result[key] = None
            else:
                try:
                    sub = yaml.safe_load("\n".join(block_lines))
                except yaml.YAMLError as exc:
                    mark = getattr(exc, "problem_mark", None)
                    sub_line = (mark.line if mark is not None else 0) + (i + 1) + 1
                    raise _FrontmatterError(
                        f"malformed nested block under {key!r}: {exc}", sub_line
                    ) from exc
                result[key] = sub
            i = j
            continue

        # Inline value.
        if inline.startswith("[") or inline.startswith("{"):
            # Flow collection → parse as YAML so e.g. ``tools: [Read, Grep]``
            # becomes a real list. A broken flow value surfaces with its line.
            try:
                result[key] = yaml.safe_load(inline)
            except yaml.YAMLError as exc:
                raise _FrontmatterError(
                    f"malformed value for {key!r}: {exc}", i + 1
                ) from exc
        else:
            # Plain scalar — taken LITERALLY (colons are data). Strip matching
            # surrounding quotes so ``tools: "All tools"`` yields the bare
            # string, matching YAML scalar semantics.
            if len(inline) >= 2 and inline[0] == inline[-1] and inline[0] in ("'", '"'):
                result[key] = inline[1:-1]
            else:
                result[key] = inline
        i += 1

    return result


def _find_key_line(frontmatter_text: str, key: str, fence_offset: int) -> int:
    """Best-effort source line (1-based) of a top-level ``key:`` in the block.

    Cheap raw-text scan: the first line whose stripped form starts with
    ``"<key>:"``. ``fence_offset`` accounts for the opening ``---`` fence
    (frontmatter body starts on source line ``fence_offset + 1``). Returns 1
    when the key is not found on its own line (contract §6 fallback).
    """
    for i, raw in enumerate(frontmatter_text.splitlines()):
        if raw.strip().startswith(f"{key}:"):
            return fence_offset + 1 + i
    return 1


def _validate_one_file(
    path: Path, name_to_file: dict[str, str]
) -> list[dict[str, object]]:
    """Validate a single agent file; return its diagnostics.

    ``name_to_file`` maps an already-seen ``name`` → the basename that first
    declared it; used for cross-file duplicate detection (mutated here as a
    side effect so the first declarer wins and later duplicates are flagged).
    """
    basename = path.name
    diags: list[dict[str, object]] = []

    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        diags.append(
            _make_diag(
                basename,
                1,
                "file",
                f"could not read file: {exc.strerror} (errno {exc.errno})",
                "error",
            )
        )
        return diags

    fm_text, fence_offset = _extract_frontmatter(text)
    if fm_text is None:
        diags.append(
            _make_diag(
                basename,
                1,
                "frontmatter",
                "missing frontmatter (no '---' delimited block at the top of the file)",
                "error",
            )
        )
        return diags

    # --- Frontmatter parse (line-oriented; faithful to the harness, NOT strict
    # YAML — see _parse_frontmatter_block for why). ---
    try:
        data = _parse_frontmatter_block(fm_text)
    except _FrontmatterError as exc:
        diags.append(
            _make_diag(
                basename,
                exc.line + fence_offset,
                "yaml",
                f"malformed YAML frontmatter: {exc.message}",
                "error",
            )
        )
        return diags

    if not data:
        diags.append(
            _make_diag(
                basename, 1, "frontmatter", "frontmatter is empty", "error"
            )
        )
        return diags

    # --- name (required, regex, unique) ---
    name_line = _find_key_line(fm_text, "name", fence_offset)
    name = data.get("name")
    if "name" not in data:
        diags.append(
            _make_diag(basename, 1, "name", "name is required", "error")
        )
    elif not isinstance(name, str) or not name.strip():
        diags.append(
            _make_diag(
                basename, name_line, "name", "name must be a non-empty string", "error"
            )
        )
    elif not AGENT_NAME_RE.fullmatch(name):
        diags.append(
            _make_diag(
                basename,
                name_line,
                "name",
                f"name {name!r} must match {AGENT_NAME_PATTERN}",
                "error",
            )
        )
    else:
        # Valid name shape — check reserved names before registering.
        if name in RESERVED_AGENT_NAMES:
            diags.append(
                _make_diag(
                    basename,
                    name_line,
                    "name",
                    f"name {name!r} is reserved by the /api/agents/validate route",
                    "error",
                )
            )
        else:
            # Register / check uniqueness across the directory.
            prior = name_to_file.get(name)
            if prior is not None:
                diags.append(
                    _make_diag(
                        basename,
                        name_line,
                        "name",
                        f"duplicate name {name!r} — also declared in {prior}",
                        "error",
                    )
                )
            else:
                name_to_file[name] = basename

    # --- description (required, non-empty) ---
    desc_line = _find_key_line(fm_text, "description", fence_offset)
    if "description" not in data:
        diags.append(
            _make_diag(
                basename, 1, "description", "description is required", "error"
            )
        )
    else:
        desc = data.get("description")
        if not isinstance(desc, str) or not desc.strip():
            diags.append(
                _make_diag(
                    basename,
                    desc_line,
                    "description",
                    "description must be a non-empty string",
                    "error",
                )
            )

    # --- model (optional; if present must be a known tier) ---
    if "model" in data:
        model_line = _find_key_line(fm_text, "model", fence_offset)
        model = data.get("model")
        if model not in MODEL_TIERS:
            diags.append(
                _make_diag(
                    basename,
                    model_line,
                    "model",
                    f"model {model!r} must be one of {MODEL_TIERS} (or omit to inherit default)",
                    "error",
                )
            )

    # --- tools (optional; list of strings OR the literal "All tools") ---
    if "tools" in data:
        tools_line = _find_key_line(fm_text, "tools", fence_offset)
        tools = data.get("tools")
        if isinstance(tools, str):
            if tools != ALL_TOOLS_LITERAL:
                diags.append(
                    _make_diag(
                        basename,
                        tools_line,
                        "tools",
                        f"tools string must be {ALL_TOOLS_LITERAL!r}; "
                        f"got {tools!r} (use a YAML list for individual tools)",
                        "error",
                    )
                )
        elif isinstance(tools, list):
            for i, tool in enumerate(tools):
                if not isinstance(tool, str):
                    diags.append(
                        _make_diag(
                            basename,
                            tools_line,
                            f"tools[{i}]",
                            f"tool entries must be strings; got {type(tool).__name__}",
                            "error",
                        )
                    )
                elif tool not in KNOWN_TOOLS:
                    # Unknown tool NAME → WARNING, never error (universe drifts).
                    diags.append(
                        _make_diag(
                            basename,
                            tools_line,
                            f"tools[{i}]",
                            f"unknown tool {tool!r} (not in the known tool set)",
                            "warning",
                        )
                    )
        else:
            diags.append(
                _make_diag(
                    basename,
                    tools_line,
                    "tools",
                    f"tools must be a YAML list or the literal {ALL_TOOLS_LITERAL!r}; "
                    f"got {type(tools).__name__}",
                    "error",
                )
            )

    # --- hooks (optional; presence + mapping-type only, no deep validation) ---
    if "hooks" in data:
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            hooks_line = _find_key_line(fm_text, "hooks", fence_offset)
            diags.append(
                _make_diag(
                    basename,
                    hooks_line,
                    "hooks",
                    f"hooks must be a mapping; got {type(hooks).__name__}",
                    "error",
                )
            )

    # --- scope (optional string) ---
    if "scope" in data:
        scope = data.get("scope")
        if not isinstance(scope, str):
            scope_line = _find_key_line(fm_text, "scope", fence_offset)
            diags.append(
                _make_diag(
                    basename,
                    scope_line,
                    "scope",
                    f"scope must be a string; got {type(scope).__name__}",
                    "error",
                )
            )

    # --- unknown top-level keys → WARNING (real files carry custom keys) ---
    _known_keys = {"name", "description", "model", "tools", "hooks", "scope"}
    for key in data:
        if key not in _known_keys:
            key_line = _find_key_line(fm_text, str(key), fence_offset)
            diags.append(
                _make_diag(
                    basename,
                    key_line,
                    str(key),
                    f"unknown frontmatter key {key!r} (not part of the agent schema)",
                    "warning",
                )
            )

    return diags


def _is_agent_file(path: Path) -> bool:
    """True for a real agent file: ``*.md`` not underscore-prefixed.

    Underscore-prefixed files (``_dev-shared.md``) are shared includes, not
    agents — they have no frontmatter and are skipped (see module docstring).
    """
    return path.suffix == ".md" and not path.name.startswith("_")


def validate_agents_dir(agents_dir: Path) -> dict[str, object]:
    """Validate every agent file in ``agents_dir``; return the result dict.

    Result shape (contract §4)::

        {
          "files_scanned": int,
          "diagnostics": [ {file, line, field, message, severity}, ... ],
          "error_count": int,
          "warning_count": int,
        }

    ``diagnostics`` is ordered by ``(file, line)`` for stable output. Files are
    visited in sorted-name order so duplicate-name detection is deterministic
    (the alphabetically-first file is the "first declarer", later files flag the
    duplicate). A missing directory yields zero files and zero diagnostics
    (not an error) — the caller decides whether that is surprising.
    """
    diagnostics: list[dict[str, object]] = []
    name_to_file: dict[str, str] = {}

    try:
        candidates = sorted(
            (p for p in agents_dir.iterdir() if _is_agent_file(p)),
            key=lambda p: p.name,
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        logger.warning(
            "agent_validation: agents dir %r is not readable; scanned 0 files",
            str(agents_dir),
        )
        candidates = []

    for path in candidates:
        diagnostics.extend(_validate_one_file(path, name_to_file))

    diagnostics.sort(key=lambda d: (d["file"], d["line"]))

    error_count = sum(1 for d in diagnostics if d["severity"] == "error")
    warning_count = sum(1 for d in diagnostics if d["severity"] == "warning")

    return {
        "files_scanned": len(candidates),
        "diagnostics": diagnostics,
        "error_count": error_count,
        "warning_count": warning_count,
    }


def default_agents_dir(repo_root: Path) -> Path:
    """Resolve the canonical agents directory under the repo root.

    Mirrors the ``repo_root`` resolution used by ``services/task_outputs.py``
    (the caller passes ``get_settings().repo_root``). Inside the api container
    this is ``/repo/.claude/agents``.
    """
    return repo_root / ".claude" / "agents"


# ===========================================================================
# Agent gallery (Kanban #1017) — listing + detail.
#
# Built ON TOP of the validator above: every gallery row reuses the same
# frontmatter parse + per-file diagnostics, so an invalid file STILL appears
# (with valid=false + its error diagnostics) instead of being dropped.
# ===========================================================================

# Domain is a presentation HEURISTIC, not a real frontmatter field — agents
# carry no `domain` key. We derive it from the agent NAME prefix. The table is
# ordered longest/most-specific prefix first so e.g. `platform-ads-*` wins over
# a hypothetical `platform-*` and the ads families map to `sem`. Anything that
# matches no prefix falls through to `other`. Documented as a heuristic on the
# wire (the `domain` field) — callers must not treat it as authoritative.
#
# Each entry is (matcher, domain). `matcher` is matched against the agent name
# with the rule in the third tuple slot: "prefix" = name.startswith(matcher),
# "exact-or-prefix" = name == matcher OR name.startswith(matcher) (used for the
# families like `secretary` / `general` that appear both bare and hyphenated).
_DOMAIN_RULES: tuple[tuple[str, str, str], ...] = (
    ("dev-", "dev", "prefix"),
    ("novel-", "novel", "prefix"),
    ("content-", "content", "prefix"),
    ("secretary", "secretary", "exact-or-prefix"),
    ("sem-", "sem", "prefix"),
    ("seo-", "seo", "prefix"),
    ("google-ads-", "sem", "prefix"),
    ("meta-ads-", "sem", "prefix"),
    ("platform-ads-", "sem", "prefix"),
    ("bi-", "data", "prefix"),
    ("dashboard-", "data", "prefix"),
    ("sql-", "data", "prefix"),
    ("analytics-", "data", "prefix"),
    ("data-", "data", "prefix"),
    ("general", "general", "exact-or-prefix"),
)


def _domain_for_name(name: str) -> str:
    """Derive the presentation ``domain`` from an agent name prefix (heuristic).

    First matching rule wins (see ``_DOMAIN_RULES`` ordering). Returns
    ``"other"`` when nothing matches. A non-string / empty name (e.g. an
    unparseable file) also yields ``"other"``.
    """
    if not isinstance(name, str) or not name:
        return "other"
    for matcher, domain, kind in _DOMAIN_RULES:
        if kind == "prefix":
            if name.startswith(matcher):
                return domain
        elif name == matcher or name.startswith(matcher):
            return domain
    return "other"


def _count_hooks(hooks: object) -> int:
    """Count hook matcher entries across all top-level event keys in ``hooks:``.

    Frontmatter ``hooks:`` is a mapping of EVENT NAME (``PreToolUse``,
    ``PostToolUse``, ...) → a LIST of matcher entries. We sum the list lengths
    across every top-level event key:

        hooks:
          PreToolUse:
            - matcher: "Bash"      # 1 entry
              hooks: [...]
        →  hook_count == 1

    Rules (kept deliberately simple, per the contract):
      * ``hooks`` absent / not a mapping        → 0
      * a value that is a list                  → + len(list)
      * a value that is a non-list, non-null    → + 1 (a lone entry)
      * a null value                            → + 0
    The nested ``hooks:`` list INSIDE one matcher entry is NOT recursed into —
    we count matcher entries, not individual command hooks.
    """
    if not isinstance(hooks, dict):
        return 0
    total = 0
    for value in hooks.values():
        if isinstance(value, list):
            total += len(value)
        elif value is not None:
            total += 1
    return total


def _summarize_tools(data: dict[str, object]) -> tuple[str, int | None]:
    """Return ``(tools_summary, tool_count)`` from a parsed frontmatter dict.

    ``"All tools"`` + ``None`` when ``tools`` is absent or the literal
    ``"All tools"``; otherwise ``"N tools"`` + ``N`` for an explicit list. A
    malformed ``tools`` value (string that is not the literal, or a non-list /
    non-string) is treated as "all tools" for the SUMMARY — the validator
    already emits the ERROR diagnostic, so the gallery does not double-report;
    it just shows a non-misleading placeholder.
    """
    if "tools" not in data:
        return ALL_TOOLS_LITERAL, None
    tools = data.get("tools")
    if isinstance(tools, list):
        n = len(tools)
        return f"{n} tools", n
    # Either the literal "All tools" or an off-spec value (validator flagged).
    return ALL_TOOLS_LITERAL, None


def _summarize_one_file(
    path: Path, name_to_file: dict[str, str]
) -> dict[str, object]:
    """Build one gallery summary dict for a single agent file.

    Reuses :func:`_validate_one_file` (so the SAME diagnostics drive the
    gallery and ``/validate``) plus a best-effort re-parse for the display
    fields. A file that fails to parse still yields a row — ``description=""``,
    ``model=None``, all-tools summary — with its error diagnostics attached.

    ``name_to_file`` is the running first-declarer map for cross-file duplicate
    detection (mutated by ``_validate_one_file``); the caller threads the same
    dict across all files so duplicate-name diagnostics match ``/validate``.
    """
    basename = path.name

    # Diagnostics first (single source of truth, mutates name_to_file).
    diagnostics = _validate_one_file(path, name_to_file)
    has_error = any(d["severity"] == "error" for d in diagnostics)

    # Best-effort re-parse for the DISPLAY fields. Wrapped defensively: a file
    # that already produced ERROR diagnostics may not parse cleanly — we never
    # let that raise (the row must still render).
    raw_frontmatter = ""
    full_description = ""
    name = basename[:-3] if basename.endswith(".md") else basename
    model: str | None = None
    tools_summary, tool_count = ALL_TOOLS_LITERAL, None
    hook_count = 0
    # Detail-only: structured tools (list / "All tools" / None) + raw body text.
    tools_structured: list[str] | str | None = None
    body_text = ""

    try:
        text = path.read_text(encoding="utf-8-sig")
        fm_text, fence_offset = _extract_frontmatter(text)
        if fm_text is not None:
            raw_frontmatter = fm_text
            try:
                data = _parse_frontmatter_block(fm_text)
            except _FrontmatterError:
                data = {}
            if isinstance(data, dict):
                fm_name = data.get("name")
                if isinstance(fm_name, str) and fm_name.strip():
                    name = fm_name
                desc = data.get("description")
                if isinstance(desc, str):
                    full_description = desc
                fm_model = data.get("model")
                if fm_model in MODEL_TIERS:
                    model = fm_model  # type: ignore[assignment]  # mypy cannot narrow via 'in MODEL_TIERS'; guarded above. #1017
                tools_summary, tool_count = _summarize_tools(data)
                hook_count = _count_hooks(data.get("hooks"))
                # Structured tools for the detail/edit pre-fill (Part D, #2481).
                raw_tools = data.get("tools")
                if isinstance(raw_tools, list):
                    tools_structured = [str(t) for t in raw_tools]
                elif isinstance(raw_tools, str) and raw_tools == ALL_TOOLS_LITERAL:
                    tools_structured = ALL_TOOLS_LITERAL
                # else: absent → stays None (= all tools, not set explicitly)

            # Body = everything after the closing '---' fence.
            lines = text.splitlines()
            # fence_offset is 1 (= the opening fence source line, 1-based).
            # The closing fence is the second '---' line; body starts after it.
            fence_count = 0
            body_start = None
            for i, line in enumerate(lines):
                if line.strip() == "---":
                    fence_count += 1
                    if fence_count == 2:
                        body_start = i + 1
                        break
            if body_start is not None:
                body_text = "\n".join(lines[body_start:]).lstrip("\n")
    except OSError:
        # Unreadable file — _validate_one_file already emitted the read error.
        pass

    return {
        "name": name,
        "description": full_description,
        "model": model,
        "tools_summary": tools_summary,
        "tool_count": tool_count,
        "hook_count": hook_count,
        "source_file": basename,
        "domain": _domain_for_name(name),
        "valid": not has_error,
        "validation_errors": diagnostics,
        # Detail-only fields (ignored by the AgentSummary serializer on the
        # listing endpoint; consumed by AgentDetail on the detail endpoint).
        "raw_frontmatter": raw_frontmatter,
        "full_description": full_description,
        "tools": tools_structured,
        "body": body_text,
    }


def list_agents(agents_dir: Path) -> list[dict[str, object]]:
    """Build the gallery listing for ``GET /api/agents`` (contract §1).

    Returns one summary dict per agent file (underscore-prefixed includes
    skipped, same rule as the validator), SORTED BY ``name``. Invalid files are
    included (``valid=false`` + diagnostics). A missing directory yields ``[]``.

    Each dict carries the detail-only keys too (``raw_frontmatter`` /
    ``full_description``); the listing serializer (``AgentSummary``) simply
    ignores them.
    """
    try:
        candidates = sorted(
            (p for p in agents_dir.iterdir() if _is_agent_file(p)),
            key=lambda p: p.name,
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        logger.warning(
            "agent_gallery: agents dir %r is not readable; listed 0 agents",
            str(agents_dir),
        )
        return []

    # Visit in sorted-FILENAME order so duplicate-name detection is
    # deterministic (matches /validate), then re-sort the OUTPUT by agent name
    # for the response (contract §1: "sorted by name").
    name_to_file: dict[str, str] = {}
    rows = [_summarize_one_file(p, name_to_file) for p in candidates]
    rows.sort(key=lambda r: r["name"])
    return rows


def get_agent_summary(agents_dir: Path, name: str) -> dict[str, object] | None:
    """Return the single gallery row whose agent ``name`` matches, or ``None``.

    Resolves by matching the SCANNED listing (never joins ``name`` onto a
    filesystem path — the router validates the regex first, but this is the
    second line of defense: we only ever return a row that the directory scan
    actually produced). The match is on the derived ``name`` field (the
    frontmatter name when parseable, else the filename stem).

    Returns ``None`` immediately for reserved names (e.g. ``"validate"``) so
    the gallery router 404s cleanly — the validator route owns that path.
    """
    if name in RESERVED_AGENT_NAMES:
        return None
    for row in list_agents(agents_dir):
        if row["name"] == name:
            return row
    return None


# ===========================================================================
# Agent WRITE (Kanban #2481) — gated create/edit of `.claude/agents/*.md`.
#
# Built ON TOP of the validator above: the candidate file is run through the
# SAME `validate_agents_dir` (in an isolated tmp dir) so a write can never
# persist a file the read endpoints would flag as invalid. The byte-write path
# is path-confined + atomic; the operator-proof gate lives in the ROUTER (this
# module is pure filesystem + validation, no HTTP / no auth).
# ===========================================================================


class AgentPathError(Exception):
    """The requested agent ``name`` cannot be confined to the agents dir.

    Raised by :func:`confine_agent_path` when ``name`` fails the agent-name
    regex OR the resolved target escapes ``agents_dir`` (traversal). The router
    maps this to the gallery's posture (422/404) BEFORE any filesystem write.
    Carrying a dedicated exception (rather than returning ``None``) keeps the
    "never write outside the sandbox" decision in ONE place that callers cannot
    accidentally ignore.
    """


def confine_agent_path(agents_dir: Path, name: str) -> Path:
    """Resolve ``<agents_dir>/<name>.md`` and assert it stays INSIDE agents_dir.

    Two independent guards, in order (defense-in-depth — either alone would
    block the obvious attacks, but both are cheap and the combination is
    auditable):

      1. ``AGENT_NAME_RE.fullmatch(name)`` — a name that is not a valid agent
         name (contains ``/``, ``\\``, ``..``, ``.``, upper-case, etc.) is
         rejected before it is ever joined onto a path. This mirrors the
         gallery's regex-first posture.
      2. Realpath confinement — resolve both the agents dir and the candidate
         target and assert the target is the dir itself or a descendant via
         ``os.path.commonpath`` (NOT ``startswith`` — that has the
         ``/agents-evil`` sibling-prefix bug). This backstops guard 1 against
         any future regex loosening and against symlink games.

    Returns the confined ``Path`` on success; raises :class:`AgentPathError`
    otherwise. NEVER touches the filesystem beyond resolving paths (no read, no
    write, no mkdir) — resolution of a not-yet-existing target is fine.
    """
    if not AGENT_NAME_RE.fullmatch(name):
        raise AgentPathError(
            f"name {name!r} is not a valid agent name (must match "
            f"{AGENT_NAME_PATTERN})"
        )

    target = agents_dir / f"{name}.md"

    # Confinement: resolve the dir + the candidate and require the candidate to
    # live under the dir. We resolve the PARENT of the target (the agents dir)
    # to a realpath and compare against the realpath of the target's parent —
    # the filename component (already regex-clean) is appended back. Using
    # `os.path.realpath` (string-level) avoids requiring the target file to
    # exist (Path.resolve(strict=False) would also work; realpath matches the
    # task_outputs/sandbox precedents in this repo).
    dir_real = os.path.realpath(str(agents_dir))
    target_real = os.path.realpath(str(target))
    try:
        common = os.path.commonpath([dir_real, target_real])
    except ValueError:
        # Different drives on Windows (no common path) → definitively outside.
        raise AgentPathError(
            f"resolved target {target_real!r} is not under {dir_real!r}"
        ) from None
    if common != dir_real or os.path.dirname(target_real) != dir_real:
        # Either the target is not under the dir at all, or it resolved to a
        # nested location (a symlink/`..` that climbs out and back into a
        # subdir). Agent files live DIRECTLY in agents_dir — reject anything
        # whose parent is not exactly the dir.
        raise AgentPathError(
            f"resolved target {target_real!r} is not directly inside "
            f"{dir_real!r}"
        )
    return target


def _serialize_frontmatter(fields: dict[str, object]) -> str:
    """Serialize the present frontmatter ``fields`` into a YAML block (no fences).

    Only keys present in ``fields`` are emitted, in a STABLE order
    (name, description, model, tools, hooks, scope) so a round-trip is
    deterministic. Each top-level key is serialized to be readable by the #1016
    LINE-ORIENTED parser (``services.agent_validation._parse_frontmatter_block``),
    which is NOT strict YAML — it handles, per top-level key, only: a plain
    scalar (literal to EOL), a ``[...]``/``{...}`` flow value, an indented nested
    mapping, and a block scalar. It does NOT handle a YAML BLOCK sequence
    (``tools:`` then ``- Read`` at column 0). So:

      * scalars (``name``/``description``/``model``/``scope``) → one
        ``yaml.safe_dump`` line. A ``description`` with a colon is quoted by
        PyYAML and the parser strips the matching surrounding quotes on read
        (round-trips).
      * ``tools`` LIST → forced to a FLOW list (``tools: [Read, Grep]``) via
        ``default_flow_style=True`` — the parser's ``[...]`` branch reads it back
        as a real list. (A block sequence would break the parser; calibration
        caught this — 2026-06-18, #2481.) A ``tools`` string (the ``"All tools"``
        literal) is emitted as a quoted scalar.
      * ``hooks`` MAPPING → block style so it renders as an indented sub-block
        the parser feeds to YAML.

    ``allow_unicode=True`` keeps non-ASCII (e.g. Thai in a description) intact.
    ``sort_keys=False`` preserves our explicit order. The result NEVER includes
    the leading/trailing ``---`` fences — the caller wraps it (see
    :func:`assemble_agent_file`).
    """
    ordered_keys = ("name", "description", "model", "tools", "hooks", "scope")
    chunks: list[str] = []
    for key in ordered_keys:
        if key not in fields:
            continue
        value = fields[key]
        if key == "tools" and isinstance(value, list):
            # Emit `tools: [a, b, c]` — a FLOW list the line-parser's `[...]`
            # branch reads back as a real list. We flow-dump the LIST VALUE
            # ALONE (not `{tools: [...]}`, which yaml renders as a flow MAPPING
            # `{tools: [...]}` and breaks the parser — caught 2026-06-18, #2481).
            # safe_dump quotes any odd tool name (e.g. one containing a colon).
            flow_list = yaml.safe_dump(
                value, allow_unicode=True, default_flow_style=True
            ).strip()
            chunks.append(f"tools: {flow_list}")
        else:
            # Scalars + the hooks mapping + the "All tools" string dump cleanly
            # in the default block style, one top-level key at a time.
            dumped = yaml.safe_dump(
                {key: value},
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
            )
            chunks.append(dumped.rstrip("\n"))
    return "\n".join(chunks)


def assemble_agent_file(fields: dict[str, object], body: str) -> str:
    """Build the full ``.md`` text: frontmatter fence + serialized fields + body.

    Shape (matches how real agent files + the #1016 ``_extract_frontmatter``
    expect it): an opening ``---`` line, the serialized frontmatter, a closing
    ``---`` line, a blank separator line, then the body. The body is written
    VERBATIM (it is the operator's markdown); a single trailing newline is
    ensured so the file is POSIX-clean.
    """
    frontmatter = _serialize_frontmatter(fields)
    body_text = body.rstrip("\n")
    if body_text:
        return f"---\n{frontmatter}\n---\n\n{body_text}\n"
    # Body empty → frontmatter-only file (structurally valid). Still emit the
    # trailing blank line + newline for a clean file.
    return f"---\n{frontmatter}\n---\n"


# Claude Code hook event names that are structurally accepted on the WRITE path.
# Derived from the real agent files (all 14 hook-bearing agents use PreToolUse)
# and from .claude/settings.json (PreToolUse, PostToolUse, Notification,
# SubagentStop, PreCompact, SessionEnd in active use as of 2026-06-18).
# Stop / UserPromptSubmit / SessionStart are in the Claude Code spec but absent
# from existing files — included to avoid breaking agents that legitimately need
# them.  Adding a new CC event only requires extending this set (not a migration).
KNOWN_HOOK_EVENTS: frozenset[str] = frozenset(
    {
        "PreToolUse",
        "PostToolUse",
        "UserPromptSubmit",
        "Stop",
        "SubagentStop",
        "Notification",
        "SessionStart",
        "SessionEnd",
        "PreCompact",
    }
)

# Accepted ``type`` values for a single hook entry.  ``"command"`` is the only
# type Claude Code defines today; the allowlist is intentionally narrow — an
# unknown type would silently do nothing and indicates a typo / future type that
# needs an explicit extension here.
_KNOWN_HOOK_TYPES: frozenset[str] = frozenset({"command"})


def validate_hooks_structure(hooks: dict | None) -> list[str]:
    """Structurally validate a ``hooks`` mapping from the WRITE path.

    Returns a list of human-readable error strings (empty = valid).  Enforces:

      * Every top-level key is a known Claude Code hook event name
        (``KNOWN_HOOK_EVENTS``).
      * Each event value is a list of mappings (the list of matcher entries).
      * Each matcher mapping may carry an optional ``matcher`` string.
      * Each matcher mapping MUST carry a ``hooks`` list of
        ``{type, command}`` entries where:
          - ``type`` is one of ``_KNOWN_HOOK_TYPES``
          - ``command`` is a string

    ``None`` / absent hooks is accepted (returns ``[]``).

    Residual: a well-formed hook can still carry any shell command string.
    The operator-proof gate is the authorization control for that (out of scope
    to sanitize command contents here — that is a human-only decision).
    """
    if hooks is None:
        return []

    errors: list[str] = []

    for event_key, event_val in hooks.items():
        if event_key not in KNOWN_HOOK_EVENTS:
            errors.append(
                f"hooks: unknown event key {event_key!r}; "
                f"must be one of {sorted(KNOWN_HOOK_EVENTS)}"
            )
            continue  # Don't deep-validate under an unknown event key.

        if not isinstance(event_val, list):
            errors.append(
                f"hooks.{event_key}: value must be a list of matcher entries; "
                f"got {type(event_val).__name__}"
            )
            continue

        for i, entry in enumerate(event_val):
            prefix = f"hooks.{event_key}[{i}]"
            if not isinstance(entry, dict):
                errors.append(
                    f"{prefix}: each matcher entry must be a mapping; "
                    f"got {type(entry).__name__}"
                )
                continue

            # Optional ``matcher`` must be a string if present.
            if "matcher" in entry and not isinstance(entry["matcher"], str):
                errors.append(
                    f"{prefix}.matcher: must be a string; "
                    f"got {type(entry['matcher']).__name__}"
                )

            # ``hooks`` sub-list is required and must contain {type, command}.
            sub_hooks = entry.get("hooks")
            if sub_hooks is None:
                errors.append(f"{prefix}: missing required 'hooks' list")
                continue
            if not isinstance(sub_hooks, list):
                errors.append(
                    f"{prefix}.hooks: must be a list; got {type(sub_hooks).__name__}"
                )
                continue
            for j, cmd_entry in enumerate(sub_hooks):
                cp = f"{prefix}.hooks[{j}]"
                if not isinstance(cmd_entry, dict):
                    errors.append(
                        f"{cp}: each hook entry must be a mapping; "
                        f"got {type(cmd_entry).__name__}"
                    )
                    continue
                h_type = cmd_entry.get("type")
                if h_type not in _KNOWN_HOOK_TYPES:
                    errors.append(
                        f"{cp}.type: {h_type!r} not in allowed set "
                        f"{sorted(_KNOWN_HOOK_TYPES)}"
                    )
                h_cmd = cmd_entry.get("command")
                if not isinstance(h_cmd, str):
                    errors.append(
                        f"{cp}.command: must be a string; "
                        f"got {type(h_cmd).__name__}"
                    )

    return errors


def validate_candidate_agent_file(name: str, file_text: str) -> list[dict[str, object]]:
    """Validate a candidate agent file IN ISOLATION; return its ERROR diagnostics.

    Writes ``file_text`` as ``<tmp>/<name>.md`` into a throwaway temp directory
    and runs the real ``validate_agents_dir`` over it, then returns ONLY the
    error-severity diagnostics (warnings — unknown tool names / custom keys — do
    NOT block a write, matching the gallery's ``valid`` rule). The temp dir is
    removed before returning.

    Isolation rationale: validating in the REAL agents dir would (a) require
    writing the candidate first (the very thing we are gate-checking) and (b)
    drag in cross-file duplicate-name diagnostics from unrelated files. The
    contract is "this proposed agent is itself valid", so we validate it alone.
    Reserved-name (``validate``) and every structural/field rule still fire —
    they are single-file checks.

    ``name`` is assumed already path-safe (the router calls
    :func:`confine_agent_path` first); here it only forms the temp filename.
    """
    diags: list[dict[str, object]] = []
    # Hoist candidate BEFORE the try so the finally can always reference it (NIT-3).
    tmp_dir = tempfile.mkdtemp(prefix="agent-candidate-")
    candidate = Path(tmp_dir) / f"{name}.md"
    try:
        candidate.write_text(file_text, encoding="utf-8")
        result = validate_agents_dir(Path(tmp_dir))
        diags = [d for d in result["diagnostics"] if d["severity"] == "error"]
    finally:
        # Best-effort cleanup; a leaked tmp dir is harmless but we tidy up.
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
    return diags


def write_agent_file_atomic(target: Path, file_text: str) -> None:
    """Write ``file_text`` to ``target`` ATOMICALLY (temp file + ``os.replace``).

    A partial/garbled file must NEVER be observable at ``target`` — a reader
    (the gallery scan, Claude Code at session start) either sees the old file or
    the fully-written new one, never a half-written one. We write to a temp file
    IN THE SAME DIRECTORY (so ``os.replace`` is a same-filesystem atomic rename,
    not a cross-device copy) then replace.

    ``target`` is assumed already confined (the caller runs
    :func:`confine_agent_path`); this function does the bytes only. Synchronous
    by design — the router offloads it via ``anyio.to_thread.run_sync`` (same
    discipline as the gallery scan).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.stem}-", suffix=".md.tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(file_text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, str(target))
    except BaseException:
        # On any failure leave the original target untouched and clean the temp.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
