"""Filesystem scaffold for team starter folders (Kanban #1308, generalized #1319).

Called from POST /api/projects, invoked unconditionally immediately after
`scaffold_orchestration` and still inside that call's `if target_is_dir:`
block (see routers/projects.py). Copies the static bundled template tree at
`src/templates/<team>/` — `team` used VERBATIM (e.g. `data-analytics`,
`social`) — into the project's `working_path`. A team with no bundled
template dir (e.g. `dev`, `novel`) is a silent no-op: the
`templates/<team>/`-existence check IS the per-team gate now, so the caller
no longer needs an `if project.team == ...` conditional.

Originally `scaffold_data_analytics` (Kanban #1308), hardcoded to
`templates/data_analytics/`. Generalized here (#1319) so a second team
(`social`) — and any future team that ships a `templates/<team>/` tree —
reuses this one service instead of a parallel near-duplicate module.

Distinct module from its two siblings rather than a function bolted onto
either: `zero_config_scaffold.py` live-snapshots the AGENT harness (CLAUDE.md,
.claude/, context/standards/) straight from the running agent-teams repo, and
`project_scaffold.py` populates the in-repo `context/projects/<name>/`
role-state tree (working_path=null projects only). This module copies a
STATIC bundled template tree into `working_path` and is the only one of the
three keyed on team — a genuinely separate concern.

Idempotent-add + best-effort + path-traversal guard, mirroring
`zero_config_scaffold._copy_one` / `scaffold_orchestration` exactly: a
destination file that already exists is left untouched (recorded under
`skipped`), a per-file copy failure is recorded under `errors` and the walk
continues (never raises mid-walk — the caller's DB-row commit must not roll
back), and `target_path` resolving to/under `agent_teams_root` raises
`ValueError` (the one exception the caller is expected to catch, same as
`scaffold_orchestration`).
"""

from __future__ import annotations

import filecmp
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from src.constants import ProjectTeam

logger = logging.getLogger(__name__)


def _templates_dir(team: str) -> Path:
    """Resolve the bundled starter template tree for `team` inside the api package.

    `team` is used VERBATIM as the directory name under `src/templates/`
    (e.g. `data-analytics`, `social`). Callers only ever reach this helper
    with a `team` already checked against `ProjectTeam.ALL` —
    `scaffold_team_starter` self-guards before calling here (see below), so
    an unknown/unvalidated team never reaches path resolution. A *valid*
    team with no bundled tree simply resolves to a path that doesn't exist;
    the caller treats that as a no-op too.
    """
    # services/team_starter_scaffold.py -> services/ -> src/ -> src/templates/<team>/
    return Path(__file__).resolve().parent.parent / "templates" / team


@dataclass
class StarterScaffoldReport:
    """Result of a `scaffold_team_starter` call.

    All path lists are relative to `target_path` (same shape as
    `zero_config_scaffold.ScaffoldReport`). Kept as a distinct type from that
    module's `ScaffoldReport` even though the shape matches — different
    caller, different lifecycle.
    """

    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


def scaffold_team_starter(
    target_path: Path,
    agent_teams_root: Path,
    team: str,
) -> StarterScaffoldReport:
    """Copy `team`'s starter tree into `target_path`. Idempotent, best-effort.

    `team` is checked against `ProjectTeam.ALL` before it ever reaches path
    resolution — an unknown/unvalidated team (e.g. a traversal payload like
    `".."`) is rejected with a warning and an empty report, so this function
    stays safe even if a future caller forgets to pre-validate. A *valid*
    team with no bundled `templates/<team>/` dir (e.g. `dev`, `novel`) is
    also a silent no-op returning an empty report — this IS the per-team
    gate; there is no `if team == ...` conditional in the caller.

    Raises:
        ValueError: if target_path resolves to or under agent_teams_root
            (path-traversal guard, mirrors `scaffold_orchestration`).

    Never raises for any OTHER failure — an unknown team, a missing template
    dir, or a single file's copy error is logged/recorded into the returned
    report instead.
    """
    # Unknown/unvalidated team must not reach path resolution (MAJOR
    # trust-boundary finding, #1319 review): mirrors the target_path
    # traversal guard below as defense-in-depth for the team-derived SOURCE
    # path. The sole current caller already validates team in
    # ProjectTeam.ALL, but this module is documented as reusable — self-guard
    # rather than trust every future caller.
    if team not in ProjectTeam.ALL:
        logger.warning(
            "team_starter_scaffold: rejected unknown/unvalidated team=%r (no-op)",
            team,
        )
        return StarterScaffoldReport()

    target_abs = Path(target_path).resolve()
    root_abs = Path(agent_teams_root).resolve()

    # Path-traversal guard. `is_relative_to` returns True if equal too, so a
    # single check covers both "target IS root" and "target is inside root".
    if target_abs == root_abs or target_abs.is_relative_to(root_abs):
        raise ValueError(
            f"target_path {target_abs!s} resolves to or under "
            f"agent_teams_root {root_abs!s} — refusing to write into the "
            f"harness source repo"
        )

    report = StarterScaffoldReport()
    templates = _templates_dir(team)
    if not templates.is_dir():
        # Expected for every team without a bundled starter tree (most teams)
        # — INFO not ERROR, since this fires on every such project creation.
        logger.info(
            "team_starter_scaffold: no template dir for team=%r at %s (no-op)",
            team,
            templates,
        )
        return report

    target_abs.mkdir(parents=True, exist_ok=True)

    for src in sorted(templates.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(templates).as_posix()
        dest = target_abs / rel
        try:
            if dest.exists():
                if not dest.is_file():
                    # Non-file collision (e.g. a dir at this rel path) — a real
                    # conflict, not an idempotent re-scaffold: surface as error.
                    report.errors.append(
                        (rel, f"destination exists and is not a regular file: {dest}")
                    )
                    continue
                # Existing file left untouched (idempotent-add). WARN if its
                # content differs from the template — distinguishes a benign
                # re-scaffold from a working_path reused across teams (a
                # `%d skipped` count can't). filecmp short-circuits on a size
                # mismatch and reads in bounded chunks, so a large pre-existing
                # dest is never slurped into memory. Best-effort; see decisions.md #1319.
                try:
                    if not filecmp.cmp(src, dest, shallow=False):
                        logger.warning(
                            "team_starter_scaffold: %s already exists with "
                            "content differing from the %s template — left "
                            "untouched (working_path reused across teams?)",
                            rel,
                            team,
                        )
                except OSError:
                    pass  # visibility only; a read error must not abort the walk
                report.skipped.append(rel)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            report.copied.append(rel)
        except Exception as e:  # pragma: no cover — defensive, mirrors _copy_one
            report.errors.append((rel, f"{type(e).__name__}: {e}"))

    return report
