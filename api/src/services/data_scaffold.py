"""Filesystem scaffold for data-analytics starter folders (Kanban #1308).

Called from POST /api/projects, gated on `project.team ==
ProjectTeam.DATA_ANALYTICS`, immediately after `scaffold_orchestration` and
still inside that call's `if target_is_dir:` block (see routers/projects.py).
Copies the static bundled template tree at `src/templates/data_analytics/`
into the project's `working_path`:

    data/raw/sample_sales.csv    (~1000-row synthetic starter dataset)
    data/raw/README.md
    data/cleaned/README.md
    analysis/outputs/README.md   (path convention matches services/task_outputs.py)
    README.md                    (top-level project quick-start)

Distinct module from its two siblings rather than a function bolted onto
either: `zero_config_scaffold.py` live-snapshots the AGENT harness (CLAUDE.md,
.claude/, context/standards/) straight from the running agent-teams repo, and
`project_scaffold.py` populates the in-repo `context/projects/<name>/`
role-state tree (working_path=null projects only). This module copies a
STATIC bundled template tree into `working_path` and is the only one of the
three gated on team — a genuinely separate concern.

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

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def _templates_dir() -> Path:
    """Resolve the bundled data-analytics template tree inside the api package."""
    # services/data_scaffold.py -> services/ -> src/ -> src/templates/data_analytics/
    return Path(__file__).resolve().parent.parent / "templates" / "data_analytics"


@dataclass
class DataScaffoldReport:
    """Result of a `scaffold_data_analytics` call.

    All path lists are relative to `target_path` (same shape as
    `zero_config_scaffold.ScaffoldReport`).
    """

    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


def scaffold_data_analytics(
    target_path: Path,
    agent_teams_root: Path,
) -> DataScaffoldReport:
    """Copy the data-analytics starter tree into `target_path`. Idempotent, best-effort.

    Raises:
        ValueError: if target_path resolves to or under agent_teams_root
            (path-traversal guard, mirrors `scaffold_orchestration`).

    Never raises for any OTHER failure — a missing template dir or a single
    file's copy error is logged/recorded into the returned report instead.
    """
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

    report = DataScaffoldReport()
    templates = _templates_dir()
    if not templates.is_dir():
        logger.error("data_scaffold: template dir missing at %s", templates)
        return report

    target_abs.mkdir(parents=True, exist_ok=True)

    for src in sorted(templates.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(templates).as_posix()
        dest = target_abs / rel
        try:
            if dest.exists():
                report.skipped.append(rel)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            report.copied.append(rel)
        except Exception as e:  # pragma: no cover — defensive, mirrors _copy_one
            report.errors.append((rel, f"{type(e).__name__}: {e}"))

    return report
