"""Skill/runbook stub proposal detector (Kanban #1223).

Reads task history from the DB, detects recurring task patterns, and PROPOSES
skill/runbook stub drafts to _scratch/auditor/ for operator review (HITL gate).

Design constraints:
- ZERO writes to .claude/ or context/ — ALL output goes to _scratch/auditor/.
- READS only: no INSERT/UPDATE/DELETE via ORM or raw SQL.
- Callable from the existing digest pipeline (same pattern as
  `fetch_open_audit_flags` in digest_template.py).
- Threshold is configurable; defaults to MIN_GROUP_SIZE = 3.

Pattern detection algorithm (AC1):
1. Load recent DONE tasks (process_status=5) across all projects.
2. Normalize each task title to a "prefix slug": lowercase, strip special chars,
   take first N words (default 5) — this groups titles like
   "[GOV2] audit X" and "[GOV2] audit Y" under the same prefix.
3. For subagent_models sequences: extract the first 2 distinct agent/model pairs
   from the list (ordered by the `at` field) to form the "top-2-step prefix".
   Group tasks that share both a title prefix AND a top-2-step prefix.
4. Groups with >= threshold tasks AND no existing skill/runbook match = new proposal.

Dedup check (AC2): scan for existing skill/runbook slugs in:
- .claude/agents/<slug>.md
- context/ skill/runbook markdown files
The file-existence check uses the same `_scratch/auditor/proposed-stubs-*/` dir
to avoid re-proposing on the SAME run. Cross-run dedup via stub path existence.

Output (AC3): _scratch/auditor/proposed-stubs-<YYYY-MM-DD>/<pattern-slug>.md
"""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus, TaskStatus
from src.models.task import Task

logger = logging.getLogger(__name__)

# Minimum tasks per group to qualify as a pattern (configurable via env).
_DEFAULT_MIN_GROUP_SIZE: int = 3
# Number of leading words used to form the title prefix slug.
_TITLE_PREFIX_WORDS: int = 5
# Number of top subagent-model steps used to form the sequence prefix.
_TOP_STEPS: int = 2

# Root of the agent-teams repo — derive from this file's location.
# api/src/services/skill_stub_detector.py → api/src/ → api/ → repo root
_REPO_ROOT: Path = Path(__file__).resolve().parents[3]
_SCRATCH_AUDITOR: Path = _REPO_ROOT / "_scratch" / "auditor"

# Known-safe write root; every write is inside this subtree.
_WRITE_ROOT: Path = _SCRATCH_AUDITOR

# Candidate paths for existing skills/runbooks (read-only reference checks).
_EXISTING_SKILL_DIRS: list[Path] = [
    _REPO_ROOT / ".claude" / "agents",
]
_EXISTING_CONTEXT_DIRS: list[Path] = [
    _REPO_ROOT / "context",
]


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass
class SkillStubDetectorResult:
    """Summary returned to the digest pipeline (AC5).

    proposed_count : number of new stubs written to _scratch/auditor/.
    stub_dir       : absolute path to today's proposed-stubs directory (or
                     None when proposed_count == 0).
    groups_found   : total pattern groups >= threshold (including already-
                     proposed).
    skipped_dedup  : groups skipped because a slug already existed.
    """

    proposed_count: int = 0
    stub_dir: str | None = None
    groups_found: int = 0
    skipped_dedup: int = 0
    threshold_used: int = _DEFAULT_MIN_GROUP_SIZE


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_title_prefix(title: str, n_words: int = _TITLE_PREFIX_WORDS) -> str:
    """Lowercase + strip special chars + take first N words.

    Examples:
        "[GOV2] daily audit project #5" → "gov2 daily audit project 5"  (5 words)
        "[auditor] Auto-PROPOSE skill stubs" → "auditor autopropose skill stubs"
    """
    # Strip bracketed prefixes like [GOV2] or [auditor] as separate tokens.
    text = title.lower()
    # Remove punctuation except word boundaries; keep alphanumerics + spaces.
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    words = text.split()
    return " ".join(words[:n_words])


def _slugify(text: str) -> str:
    """Convert a title prefix to a filesystem-safe slug.

    "gov2 daily audit project" → "gov2-daily-audit-project"
    """
    return re.sub(r"\s+", "-", text.strip()).strip("-")[:80]


def _top2_step_key(subagent_models: list[dict[str, Any]]) -> str:
    """Extract top-2-step sequence key from subagent_models JSONB list.

    Each entry shape: {agent: str, model: str, at: str (ISO datetime)}.
    Sort by `at`, take first _TOP_STEPS, join as "agent:model|agent:model".
    Entries missing `at` are kept at original list order (stable).
    """
    if not subagent_models:
        return ""

    def _sort_key(entry: dict[str, Any]) -> str:
        at = entry.get("at") or ""
        return at if isinstance(at, str) else ""

    sorted_entries = sorted(subagent_models, key=_sort_key)
    steps = sorted_entries[:_TOP_STEPS]
    parts = [f"{e.get('agent', '?')}:{e.get('model', '?')}" for e in steps]
    return "|".join(parts)


def _group_key(title: str, subagent_models: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (title_prefix, step_sequence) tuple for grouping."""
    return _normalize_title_prefix(title), _top2_step_key(subagent_models or [])


def _slug_exists(slug: str, today_dir: Path) -> bool:
    """True if a stub with this slug already exists anywhere under _WRITE_ROOT.

    Checks:
    1. today's proposed-stubs dir (same run — idempotent re-runs).
    2. Any previous day's proposed-stubs dir under _SCRATCH_AUDITOR.
    3. Existing agent/skill files in .claude/agents/.

    Does NOT check context/ (too broad — we'd get false positives on any
    file that happens to match a word in the slug).
    """
    # Check today's dir first (fast path).
    if (today_dir / f"{slug}.md").exists():
        return True

    # Check previous days' stubs under _SCRATCH_AUDITOR.
    if _SCRATCH_AUDITOR.exists():
        for stub_dir in _SCRATCH_AUDITOR.glob("proposed-stubs-*"):
            if stub_dir == today_dir:
                continue
            if (stub_dir / f"{slug}.md").exists():
                return True

    # Check .claude/agents/<slug>.md.
    for agent_dir in _EXISTING_SKILL_DIRS:
        if (agent_dir / f"{slug}.md").exists():
            return True

    return False


def _write_stub(
    slug: str,
    title_prefix: str,
    step_key: str,
    task_ids: list[int],
    project_ids: list[int],
    today_dir: Path,
    today: date,
) -> Path:
    """Write a single proposed-stub .md file under today_dir.

    Validates write is within _WRITE_ROOT before touching the filesystem.
    Raises ValueError if path escapes _WRITE_ROOT (defense-in-depth).
    """
    target = today_dir / f"{slug}.md"

    # Safety invariant: target must be inside _WRITE_ROOT.
    try:
        target.resolve().relative_to(_WRITE_ROOT.resolve())
    except ValueError as e:
        raise ValueError(
            f"skill_stub_detector: write path {target!r} escapes "
            f"_WRITE_ROOT={_WRITE_ROOT!r}; refusing to write. "
            f"Original error: {e}"
        ) from e

    # Also assert it is NOT under .claude/ or context/ (AC4 hard safety).
    resolved = str(target.resolve())
    _forbidden = [
        str((_REPO_ROOT / ".claude").resolve()),
        str((_REPO_ROOT / "context").resolve()),
    ]
    for forbidden_prefix in _forbidden:
        if resolved.startswith(forbidden_prefix):
            raise ValueError(
                f"skill_stub_detector: SAFETY VIOLATION — write path {resolved!r} "
                f"is inside forbidden prefix {forbidden_prefix!r}. Refusing."
            )

    today_dir.mkdir(parents=True, exist_ok=True)

    # Format frontmatter + skeleton (AC3).
    unique_projects = sorted(set(project_ids))
    source_ids_str = ", ".join(f"#{t}" for t in sorted(set(task_ids)))

    content = f"""---
generated_by: skill_stub_detector
kanban: "#1223"
date: {today.isoformat()}
pattern_slug: {slug}
source_task_ids: [{", ".join(str(t) for t in sorted(set(task_ids)))}]
source_project_ids: [{", ".join(str(p) for p in unique_projects)}]
step_sequence: "{step_key}"
status: proposed
---

# Proposed skill/runbook: {title_prefix}

**Status:** PROPOSED — operator review required before adding to .claude/agents/ or context/

**Pattern detected:** {len(set(task_ids))} tasks share this title prefix + step sequence.

**Source task IDs:** {source_ids_str}

**Step sequence (top-{_TOP_STEPS} subagent steps):** `{step_key or "(none recorded)"}`

## Role / purpose

<!-- TODO: Fill in what this skill/runbook does. -->

## When to invoke

<!-- TODO: Describe the trigger condition. -->

## Workflow steps

<!-- TODO: List the steps this skill/runbook runs. -->

## References

- Source tasks: {source_ids_str}
- Generated: {datetime.now(timezone.utc).isoformat()}

---
*This stub was auto-proposed by the Kanban #1223 skill_stub_detector. Review and
 promote to `.claude/agents/<slug>.md` if the pattern is genuinely recurring and
 warrants a reusable runbook. Delete this file to dismiss.*
"""

    target.write_text(content, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_skill_stub_detector(
    session: AsyncSession,
    *,
    min_group_size: int | None = None,
    today: date | None = None,
) -> SkillStubDetectorResult:
    """Detect recurring task patterns and write proposed-stub drafts to _scratch.

    Called by the digest pipeline (see src/routers/digest.py) — same pattern
    as fetch_open_audit_flags.  Pure side-effects: reads DB, writes _scratch/.

    Parameters
    ----------
    session:
        AsyncSession bound to the test or live DB.
    min_group_size:
        Override for the minimum task count per group (default: env
        SKILL_STUB_MIN_GROUP_SIZE or 3).
    today:
        Override the date used for the output directory name (default: UTC today).

    Returns
    -------
    SkillStubDetectorResult summarizing the run.
    """
    if min_group_size is None:
        env_val = os.environ.get("SKILL_STUB_MIN_GROUP_SIZE", "")
        try:
            min_group_size = int(env_val)
        except (ValueError, TypeError):
            min_group_size = _DEFAULT_MIN_GROUP_SIZE

    if today is None:
        today = datetime.now(timezone.utc).date()

    today_dir = _SCRATCH_AUDITOR / f"proposed-stubs-{today.isoformat()}"

    result = SkillStubDetectorResult(threshold_used=min_group_size)

    # Load DONE tasks across all active projects (read-only).
    stmt = (
        select(Task)
        .where(
            Task.status == RecordStatus.ACTIVE,
            Task.process_status == TaskStatus.DONE,
            Task.is_template.is_(False),
        )
        .order_by(Task.id.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        logger.info("skill_stub_detector: no DONE tasks found; nothing to detect")
        return result

    # Group tasks by (title_prefix, step_sequence).
    groups: dict[tuple[str, str], list[Task]] = defaultdict(list)
    for task in rows:
        key = _group_key(task.title or "", task.subagent_models or [])
        groups[key].append(task)

    qualifying = {k: v for k, v in groups.items() if len(v) >= min_group_size}
    result.groups_found = len(qualifying)

    if not qualifying:
        logger.info(
            "skill_stub_detector: %d groups checked; none reached threshold=%d",
            len(groups),
            min_group_size,
        )
        return result

    for (title_prefix, step_key), tasks in qualifying.items():
        slug = _slugify(title_prefix)
        if not slug:
            logger.warning(
                "skill_stub_detector: empty slug for title_prefix=%r; skipping",
                title_prefix,
            )
            continue

        if _slug_exists(slug, today_dir):
            logger.info(
                "skill_stub_detector: slug=%r already proposed; skipping (dedup)",
                slug,
            )
            result.skipped_dedup += 1
            continue

        task_ids = [t.id for t in tasks]
        project_ids = [t.project_id for t in tasks]

        try:
            stub_path = _write_stub(
                slug=slug,
                title_prefix=title_prefix,
                step_key=step_key,
                task_ids=task_ids,
                project_ids=project_ids,
                today_dir=today_dir,
                today=today,
            )
            result.proposed_count += 1
            if result.stub_dir is None:
                result.stub_dir = str(today_dir)
            logger.info(
                "skill_stub_detector: proposed stub %s (source_tasks=%d)",
                stub_path,
                len(task_ids),
            )
        except (ValueError, OSError) as exc:
            logger.error(
                "skill_stub_detector: failed to write stub for slug=%r: %s",
                slug,
                exc,
            )

    return result
