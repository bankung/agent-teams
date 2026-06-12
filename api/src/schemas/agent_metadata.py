"""Pydantic schemas for the agent-frontmatter validator (Kanban #1016).

`.claude/agents/*.md` files carry a YAML frontmatter block that Claude Code
parses at session start. A malformed block surfaces only as "agent doesn't
exist" with no diagnostic. This module defines the *contract* that frontmatter
must satisfy, plus the diagnostic wire shape.

Two schemas:

  * ``AgentMetadata`` — the canonical Pydantic v2 model describing a VALID
    agent frontmatter block. It encodes the locked rules (name regex,
    description non-empty, model enum, tools shape, hooks mapping, scope). The
    validator service (``services/agent_validation.py``) derives its checks from
    these same rules so there is a single source of truth for "what a valid
    file looks like".

    NOTE: the service does NOT round-trip every file through
    ``AgentMetadata.model_validate`` for the error path, because Pydantic's
    ``ValidationError`` collapses field problems into one severity (error) and
    carries no source line numbers. The validator needs per-field
    ``error``/``warning`` severity AND a line number, so it walks the rules
    field-by-field and emits ``AgentDiagnostic`` objects. ``AgentMetadata`` is
    still the authoritative declaration of the contract (and is exercised in
    tests) — the service mirrors it.

  * ``AgentDiagnostic`` — one finding ``{file, line, field, message,
    severity}``. ``severity`` is ``"error"`` or ``"warning"``. ``file`` is the
    basename only — absolute paths never go on the wire.

  * ``AgentValidationResponse`` — the ``GET /api/agents/validate`` response.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Locked name regex (contract §1): lower-case alphanumeric segments joined by
# single hyphens. Exported so the service reuses the exact same pattern (single
# source of truth — a drift here would desync the endpoint from the schema).
AGENT_NAME_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
AGENT_NAME_RE = re.compile(AGENT_NAME_PATTERN)

# Known model tiers (contract §1). ``model`` absent = inherit the session
# default (NOT an error). A present-but-unknown value IS an error.
MODEL_TIERS: tuple[str, ...] = ("opus", "sonnet", "haiku")
ModelTierLiteral = Literal["opus", "sonnet", "haiku"]

# The literal string a ``tools`` value may carry instead of a YAML list to mean
# "all tools available" (contract §1). Case-sensitive per the brief.
ALL_TOOLS_LITERAL = "All tools"

# Tool universe used across the real agent files (Read, Grep, Glob, Bash,
# Write, Edit, WebFetch, WebSearch as of 2026-06-12). A tool name NOT in this
# set is a WARNING, never an error — the tool universe drifts (contract §2).
KNOWN_TOOLS: frozenset[str] = frozenset(
    {
        "Read",
        "Grep",
        "Glob",
        "Bash",
        "Write",
        "Edit",
        "WebFetch",
        "WebSearch",
        "NotebookEdit",
        "Agent",
        "Task",
    }
)

DiagnosticSeverity = Literal["error", "warning"]


class AgentDiagnostic(BaseModel):
    """One validation finding for an agent file.

    ``file`` is the basename only (e.g. ``secretary.md``) — absolute paths are
    never serialized. ``line`` is 1-based and points at the offending key when
    cheaply known from the raw text, else 1 (see service docstring for the
    line-number limitation). ``field`` is the frontmatter key the finding is
    about (``name``, ``model``, ``tools[2]``, ...) or a pseudo-field like
    ``frontmatter`` / ``yaml`` for structural problems.
    """

    model_config = ConfigDict(from_attributes=True)

    file: str
    line: int
    field: str
    message: str
    severity: DiagnosticSeverity


class AgentValidationResponse(BaseModel):
    """Response for ``GET /api/agents/validate`` (contract §4)."""

    files_scanned: int
    diagnostics: list[AgentDiagnostic]
    error_count: int
    warning_count: int


class AgentMetadata(BaseModel):
    """Canonical schema for a VALID agent frontmatter block (contract §1).

    Unknown keys are ALLOWED here (``extra="allow"``) because real files carry
    custom keys today (``email_actions`` on secretary.md). The validator service
    surfaces unknown keys as WARNINGS, not errors — so this model must accept
    them rather than reject them, otherwise a round-trip would 422 on a file the
    contract considers valid-with-warnings.

    Fields:
      * ``name`` — required, must match ``AGENT_NAME_PATTERN``. Uniqueness across
        the directory is a cross-file rule enforced by the service, not here.
      * ``description`` — required, non-empty after strip.
      * ``model`` — optional; when present must be one of ``MODEL_TIERS``.
        Absent = inherit default (not an error).
      * ``tools`` — optional; a list of tool-name strings OR the literal
        ``"All tools"`` OR absent (= all tools). Unknown tool NAMES inside a
        list are warnings (service-level), not errors, so this model does not
        constrain the membership.
      * ``hooks`` — optional nested mapping; presence + mapping-type only (v1
        does not deep-validate hook internals).
      * ``scope`` — optional string.
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    model: ModelTierLiteral | None = None
    tools: list[str] | Literal["All tools"] | None = None
    hooks: dict | None = None
    scope: str | None = None

    @field_validator("name")
    @classmethod
    def _name_matches_pattern(cls, v: str) -> str:
        if not AGENT_NAME_RE.fullmatch(v):
            raise ValueError(f"name must match {AGENT_NAME_PATTERN}")
        return v

    @field_validator("description")
    @classmethod
    def _description_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("description must be non-empty")
        return v
