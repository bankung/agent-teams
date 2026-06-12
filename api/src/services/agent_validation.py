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

    try:
        text = path.read_text(encoding="utf-8-sig")
        fm_text, _fence = _extract_frontmatter(text)
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
