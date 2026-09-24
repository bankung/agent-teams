"""Zero-config orchestration scaffolder (MVP-A — Kanban #792, parent #789).

Live-snapshot template engine: copies the agent-teams repo's own orchestration
files (`.claude/`, `context/standards/`, `context/teams/<team>/`) into a
target project path so a freshly-created project can run with the same agent
harness as agent-teams itself, no manual file shuffling.

`CLAUDE.md` is the one exception (Kanban #3321): its CONTENT is never copied
from the repo's own CLAUDE.md — that file names agent-teams-specific compose
projects / DB names / source paths and links into `.claude/docs/**` and
`.claude/teams/**`, which this manifest never ships, so a verbatim copy is
wrong for every scaffolded project. `render_project_claude_stub` renders a
small per-project pointer stub instead; `"CLAUDE.md"` stays in
`_UNIVERSAL_FILES` so the path still lands, but both call sites (the on-disk
path in routers/projects.py and the HTTP manifest in routers/scaffold.py)
overwrite/substitute its bytes with the rendered stub.

Three rules, in priority order:

1. **Live snapshot.** Source files are read from `agent_teams_root` at call
   time. Editing source files between calls is the expected way to roll out
   harness improvements — there's no compiled template bundle. Source files
   are NEVER written to by this service.
2. **Idempotent-add only.** If a destination file already exists, skip it and
   record it under `skipped`. NEVER overwrite. MVP-B will own refresh modes;
   MVP-A is purely additive.
3. **Best-effort.** A failure on one file (permission, missing source, etc.)
   is recorded in `errors` and the scan continues. Caller decides what to do
   with partial scaffolds; this service does not raise mid-walk.

The team-specific file set is convention-derived from `constants.TEAM_ROSTERS`
(Kanban #1620, 2026-05-28): for team T the manifest is
`.claude/agents/<role>.md` for every role in `TEAM_ROSTERS[T]`, plus
`.claude/teams/<T>.md` if that playbook exists, plus the `context/teams/<T>/**`
glob, on top of the universal files/globs that land on every team. Adding a team
needs NO edit here — drop the agent .md + roster line in constants.py. A missing
playbook (e.g. `.claude/teams/content.md` does not exist today) is SKIPPED, not
an error. Universal files land on every team; adding a universal file = edit the
constants below.

Path-traversal guard rejects `target_path` that resolves to or under
`agent_teams_root` so a misconfigured `working_path = "."` can never overwrite
the harness itself.

`.claude/settings.json` is copied verbatim in MVP-A. MVP-B (#793 / follow-up)
will substitute per-project name/path tokens before write.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from src.constants import TEAM_ROSTERS

logger = logging.getLogger(__name__)


# Kanban #793 / #795 — substrings we strip from a scaffolded settings.json before
# write. Shared between the POST /api/projects auto-scaffold path (#793 in
# routers/projects.py) and the GET /api/scaffold/{team}/files endpoint (#795 in
# routers/scaffold.py). One implementation, two call sites.
#
# Template-tweak-light: drop agent-teams self-references, leave the rest verbatim.
# Future projects can hand-tune their own allow list.
_AGENT_TEAMS_ALLOW_DROP_SUBSTRINGS: tuple[str, ...] = (
    "by-name/agent-teams",
    "/api/projects/1/",
    '/api/projects/1"',
    "/context/projects/agent-teams/",
    # Kanban #3324 — an allow/ask entry pinned to `-p agent-teams` (this repo's
    # own compose project) is an agent-teams self-reference by definition: it
    # aims a pre-approved command at agent-teams' stack, useless (and a hazard)
    # from a different project's session.
    "-p agent-teams",
)


def substitute_settings_json(
    content: bytes, project_name: str, project_id: int
) -> bytes:
    """Filter agent-teams self-references out of a settings.json byte blob.

    Pure function: takes the source settings.json bytes, returns filtered bytes.
    No filesystem I/O — callers handle read/write so the same helper can serve
    both the on-disk scaffold path (#793) and the HTTP manifest endpoint (#795).

    Drops any `permissions.allow` / `permissions.ask` entry that contains a
    hard-coded reference to the agent-teams project (its name via
    `by-name/agent-teams`, its id=1 via `/api/projects/1/` or `/api/projects/1"`,
    its on-disk `/context/projects/agent-teams/` path, or its compose project
    via `-p agent-teams`). Everything else is left verbatim — the new project
    can hand-tune its own allow list later.

    Unparseable JSON → return input unchanged + log a warning. The scaffold
    pipeline is best-effort; a malformed settings.json should not blow up the
    project create flow (DB row is the source of truth per #793 contract).

    `project_name` and `project_id` are reserved for future per-project token
    substitution (e.g., inserting the new project's id into a new allow rule).
    Today they're unused — the filter is purely subtractive — but the signature
    is locked so callers don't churn when substitution lands.
    """
    # Mark args as intentionally accepted but currently unused. Future:
    # per-project token substitution lands here.
    del project_name, project_id

    try:
        data = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning("substitute_settings_json: failed to parse JSON: %s", e)
        return content

    perms = data.get("permissions")
    if not isinstance(perms, dict):
        # Nothing to filter; return input unchanged.
        return content

    def _filter(entries: object) -> list:
        if not isinstance(entries, list):
            return entries  # type: ignore[return-value]
        return [
            e
            for e in entries
            if not (
                isinstance(e, str)
                and any(s in e for s in _AGENT_TEAMS_ALLOW_DROP_SUBSTRINGS)
            )
        ]

    if "allow" in perms:
        perms["allow"] = _filter(perms["allow"])
    if "ask" in perms:
        perms["ask"] = _filter(perms["ask"])

    return json.dumps(data, indent=2).encode("utf-8")


# Kanban #3321 — the rendered per-project CLAUDE.md stub. Locked template text
# from `_scratch/3321-stub-spec.md` (Decision 2); `<name>` / `<team>` are the
# only substitution points. Contains zero `](` markdown-link sequences by
# construction — that's acceptance criterion 4, checked mechanically by tests
# and by bin/agent-teams-init.smoke.ps1.
_PROJECT_CLAUDE_STUB_TEMPLATE = """# <name> — Claude Code instructions

This project does not carry the agent-teams Lead harness. The harness lives in the
agent-teams repo and loads only in a session started there.

## Normal way to work on this project

Start Claude Code in the agent-teams repo, then bind this project:

    /zb-bind <name>

The universal Lead rules, the team playbook (team=<team>) and this project's own rules
all load from there.

## If you are reading this from inside this folder

No Lead harness is loaded, so the universal rules are not in context. Read
`shared/README.md` in this project before doing anything. Its
"Project facts (compose + tests)" section is the source of truth for this project's
compose project name, test command and test database. If that file or section does not
exist yet, create it before running any build or test command.

## Scope of this file

Pointer only. Harness rules belong in the agent-teams repo; this project's own rules
belong in `shared/`. Do not grow this file — a copy of the universal rules is what this
stub exists to prevent.

<!-- Generated by the agent-teams scaffold. Regenerating is safe; editing is not useful. -->
"""


def render_project_claude_stub(project_name: str, team: str) -> bytes:
    """Render the per-project CLAUDE.md pointer stub (Kanban #3321).

    Pure function, same shape as `substitute_settings_json`: no filesystem
    I/O — callers handle read/write so the same helper serves both the
    on-disk scaffold path (routers/projects.py) and the HTTP manifest
    endpoint (routers/scaffold.py).

    Replaces the previous behavior of shipping the agent-teams repo's own
    CLAUDE.md verbatim into every scaffolded project, which was wrong for
    every target: that file names agent-teams-specific compose projects / DB
    names / source paths and links into `.claude/docs/**` + `.claude/teams/**`,
    neither of which this manifest ships (MorAI measured 26 relative-link
    occurrences, 24 dead).

    `<name>` and `<team>` are substituted verbatim into the locked template;
    everything else ships as-is. Encoded UTF-8.
    """
    text = _PROJECT_CLAUDE_STUB_TEMPLATE.replace("<name>", project_name).replace(
        "<team>", team
    )
    return text.encode("utf-8")


# ---------------------------------------------------------------------------
# File manifest — hard-coded per team. Glob entries end with `/**` and are
# walked recursively; bare paths are single files. Paths are relative to
# agent_teams_root on the source side and target_path on the destination side.
# ---------------------------------------------------------------------------

_UNIVERSAL_FILES: tuple[str, ...] = (
    "CLAUDE.md",
    ".claude/hooks/block-raw-sql-dml.ps1",
    ".claude/hooks/auto-approve-safe-writes.ps1",
    ".claude/hooks/auto-approve-safe-writes.smoke.ps1",
    ".claude/agents/dev-analyst.md",
    ".claude/agents/dev-spec-reviewer.md",
    ".claude/settings.json",
)

_UNIVERSAL_GLOBS: tuple[str, ...] = ("context/standards/**",)


@dataclass
class ScaffoldReport:
    """Result of a scaffold_orchestration call.

    All path lists are relative to `target_path` (i.e., the same shape callers
    pass to copy-this-file logs).
    """

    target_path: Path
    project_name: str
    team: str
    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


def _resolve_manifest(
    team: str, agent_teams_root: Path
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Convention-derive (files, globs) for the given team (Kanban #1620).

    Team-specific files = `.claude/agents/<role>.md` for every role in
    `TEAM_ROSTERS[team]`, plus `.claude/teams/<team>.md` IF that playbook exists
    under `agent_teams_root` (missing playbook → skipped, not an error — e.g.
    `.claude/teams/content.md` does not exist today). Team-specific glob =
    `context/teams/<team>/**` (missing dir → empty via `_expand_glob`). Universal
    files/globs are always prepended.

    Unknown team (not in TEAM_ROSTERS) → raise KeyError. The router 422 gate +
    the constants.py import-time invariant prevent this reaching here; raising
    loud beats silently scaffolding the wrong team's harness (the pre-#1620
    behavior was a dev fallback).
    """
    if team not in TEAM_ROSTERS:
        raise KeyError(
            f"zero_config_scaffold: unknown team {team!r} not in TEAM_ROSTERS "
            f"(valid: {sorted(TEAM_ROSTERS)})"
        )

    files = list(_UNIVERSAL_FILES)
    globs = list(_UNIVERSAL_GLOBS)

    files += [f".claude/agents/{role}.md" for role in TEAM_ROSTERS[team]]

    playbook_rel = f".claude/teams/{team}.md"
    if (agent_teams_root / playbook_rel).is_file():
        files.append(playbook_rel)
    else:
        logger.info(
            "zero_config_scaffold: no playbook %s — skipping (team %r scaffolds "
            "agents + standards only)",
            playbook_rel,
            team,
        )

    globs.append(f"context/teams/{team}/**")

    return tuple(files), tuple(globs)


def _expand_glob(agent_teams_root: Path, pattern: str) -> list[str]:
    """Walk a `<dir>/**` pattern under agent_teams_root and return relative
    paths (POSIX-style) of every file beneath. Missing dir → empty list (no
    error — novel team's source dir may legitimately not exist yet).
    """
    if not pattern.endswith("/**"):
        logger.warning(
            "zero_config_scaffold: glob %r does not end in /** — treating as literal",
            pattern,
        )
        return [pattern]
    base_rel = pattern[: -len("/**")]
    base_abs = agent_teams_root / base_rel
    if not base_abs.exists() or not base_abs.is_dir():
        return []
    out: list[str] = []
    for p in base_abs.rglob("*"):
        if p.is_file():
            rel = p.relative_to(agent_teams_root).as_posix()
            out.append(rel)
    out.sort()
    return out


def _copy_one(
    agent_teams_root: Path,
    target_path: Path,
    rel: str,
    report: ScaffoldReport,
) -> None:
    """Copy a single relative path. Idempotent-add: skip if destination exists.
    Logs errors to report.errors and returns (never raises out of here)."""
    src = agent_teams_root / rel
    dest = target_path / rel
    try:
        if dest.exists():
            report.skipped.append(rel)
            return
        if not src.exists():
            report.errors.append((rel, f"source not found: {src}"))
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        report.copied.append(rel)
    except Exception as e:  # pragma: no cover — exercised via mock in tests
        report.errors.append((rel, f"{type(e).__name__}: {e}"))


def scaffold_orchestration(
    target_path: Path,
    project_name: str,
    team: str,
    agent_teams_root: Path,
) -> ScaffoldReport:
    """Scaffold an orchestration harness into target_path. See module docstring
    for the full contract.

    Raises:
        ValueError: if target_path resolves to or under agent_teams_root
            (path-traversal guard against `working_path = "."`).
    """
    target_abs = Path(target_path).resolve()
    root_abs = Path(agent_teams_root).resolve()

    # Path-traversal guard. `is_relative_to` returns True if equal too, so a
    # single check covers both "target IS root" and "target is inside root".
    if target_abs == root_abs or target_abs.is_relative_to(root_abs):
        raise ValueError(
            f"target_path {target_abs!s} resolves to or under "
            f"agent_teams_root {root_abs!s} — refusing to overwrite the "
            f"harness source repo"
        )

    report = ScaffoldReport(
        target_path=target_abs,
        project_name=project_name,
        team=team,
    )

    target_abs.mkdir(parents=True, exist_ok=True)

    files, globs = _resolve_manifest(team, root_abs)

    # Bare files first (deterministic-ish ordering for predictable reports).
    for rel in files:
        _copy_one(root_abs, target_abs, rel, report)

    # Then walk each glob. Sorted inside _expand_glob; duplicate paths across
    # globs are tolerated (the second hit becomes a skip via dest.exists).
    for pattern in globs:
        for rel in _expand_glob(root_abs, pattern):
            _copy_one(root_abs, target_abs, rel, report)

    return report
