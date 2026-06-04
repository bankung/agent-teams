"""Stale-doc curator (Kanban #1222).

Reads context/ tree (read-only) and writes a stale-docs report to
_scratch/auditor/stale-docs-<YYYY-MM-DD>.md for operator review (HITL gate).

Target directories (AC1):
- context/standards/  — all .md files recursively
- context/teams/      — all .md files recursively
- context/projects/*/shared/decisions.md — one per active project (skips .deleted/)

File-age source: filesystem mtime (Path.stat().st_mtime).
Reason: `git` is NOT available inside the API container
(exec: "git": executable file not found in PATH — confirmed 2026-06-04).

Threshold (AC2): configurable via env var STALE_DOC_DAYS (default = 60 days).

Contradiction heuristic (AC3):
  For each decisions.md, scan entries that contain the tokens:
    REPLACES / SUPERSEDES / CANCELLED / changed-to
  Any file referenced (by filename stem or path fragment) in such entries
  is flagged as a candidate contradiction if that file still exists.

Write-confinement (AC5):
- _WRITE_ROOT = _scratch/auditor/   — all output files must be inside.
- Two guards on every write:
    1. Path-escape guard: resolved target must be relative to _WRITE_ROOT.
    2. Forbidden-prefix guard: target must not start with .claude/ or context/.

Integration (AC6):
  Called from digest.py alongside run_skill_stub_detector — same soft-fail
  pattern (exception never blocks the digest send).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config / path constants
# ---------------------------------------------------------------------------

_DEFAULT_STALE_DAYS: int = 60

# api/src/services/stale_doc_curator.py  ->  api/ -> repo root (4 levels up)
_REPO_ROOT: Path = Path(__file__).resolve().parents[3]
_SCRATCH_AUDITOR: Path = _REPO_ROOT / "_scratch" / "auditor"

# Known-safe write root; every write is inside this subtree.
_WRITE_ROOT: Path = _SCRATCH_AUDITOR

# Read-only scan targets (AC1).
_STANDARDS_ROOT: Path = _REPO_ROOT / "context" / "standards"
_TEAMS_ROOT: Path = _REPO_ROOT / "context" / "teams"
_PROJECTS_ROOT: Path = _REPO_ROOT / "context" / "projects"

# Contradiction tokens (AC3).
_CONTRADICTION_TOKENS: tuple[str, ...] = (
    "REPLACES",
    "SUPERSEDES",
    "CANCELLED",
    "changed-to",
)

# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass
class StaleDocCuratorResult:
    """Summary returned to the digest pipeline (AC6).

    report_path : absolute path of the written report, or None when nothing was
                  flagged or the write was skipped.
    stale_count : number of docs flagged as stale (age > threshold).
    contradiction_count : docs flagged by the contradiction heuristic.
    threshold_days : threshold used for this run.
    scanned_count : total docs scanned.
    """

    report_path: str | None = None
    stale_count: int = 0
    contradiction_count: int = 0
    threshold_days: int = _DEFAULT_STALE_DAYS
    scanned_count: int = 0


# ---------------------------------------------------------------------------
# Internal data types
# ---------------------------------------------------------------------------


class DocRecord(NamedTuple):
    path: Path
    age_days: float
    is_stale: bool
    is_contradiction: bool
    contradiction_note: str  # human-readable detail if is_contradiction


# ---------------------------------------------------------------------------
# Scanning helpers
# ---------------------------------------------------------------------------


def _collect_target_files() -> list[Path]:
    """Return the list of .md files to audit (AC1).

    - context/standards/  — all *.md recursively
    - context/teams/      — all *.md recursively
    - context/projects/*/shared/decisions.md (skips .deleted/ subtree)
    """
    targets: list[Path] = []

    if _STANDARDS_ROOT.exists():
        targets.extend(_STANDARDS_ROOT.rglob("*.md"))

    if _TEAMS_ROOT.exists():
        targets.extend(_TEAMS_ROOT.rglob("*.md"))

    if _PROJECTS_ROOT.exists():
        for decisions_file in _PROJECTS_ROOT.rglob("decisions.md"):
            # Skip soft-deleted projects (.deleted/ subtree).
            parts = decisions_file.parts
            if ".deleted" in parts:
                continue
            # Only include the shared/decisions.md canonical shape.
            if decisions_file.parent.name == "shared":
                targets.append(decisions_file)

    return sorted(set(targets))


def _file_age_days(path: Path) -> float:
    """Return age of `path` in days based on filesystem mtime."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return 0.0
    now = datetime.now(timezone.utc).timestamp()
    return max(0.0, (now - mtime) / 86400.0)


def _extract_contradiction_hints(
    decisions_file: Path,
    all_paths_by_stem: dict[str, Path],
) -> list[tuple[Path, str]]:
    """Read a decisions.md and return (referenced_path, note) pairs where the
    entry contains a contradiction token and a known-file stem.

    Conservative: we extract bare stems (filename without suffix) mentioned
    after a contradiction token on the same line or the same 200-char window.
    """
    try:
        content = decisions_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    results: list[tuple[Path, str]] = []
    # Build a joined-token regex for speed.
    token_pattern = re.compile(
        r"(" + "|".join(re.escape(t) for t in _CONTRADICTION_TOKENS) + r")",
    )

    for match in token_pattern.finditer(content):
        token = match.group(1)
        # Look in a window of 200 chars after the token for a known stem.
        window_start = match.start()
        window_end = min(len(content), match.end() + 200)
        window = content[window_start:window_end]
        # Grab all word-like tokens (potential file stems) in the window.
        candidate_stems = re.findall(r"[\w\-]+", window)
        for stem in candidate_stems:
            if stem in all_paths_by_stem:
                target_path = all_paths_by_stem[stem]
                try:
                    df_rel = decisions_file.relative_to(_REPO_ROOT)
                except ValueError:
                    df_rel = decisions_file
                note = (
                    f"Contradiction token '{token}' in "
                    f"{df_rel} "
                    f"references stem '{stem}'"
                )
                results.append((target_path, note))

    return results


def _build_stem_index(paths: list[Path]) -> dict[str, Path]:
    """Map filename stem -> path for the contradiction heuristic lookup."""
    index: dict[str, Path] = {}
    for p in paths:
        stem = p.stem
        # Last-wins for duplicate stems (conservative; different dirs may share a name).
        index[stem] = p
    return index


# ---------------------------------------------------------------------------
# Write helpers (mirroring skill_stub_detector.py pattern)
# ---------------------------------------------------------------------------


def _assert_write_safe(target: Path) -> None:
    """Raise ValueError if `target` escapes _WRITE_ROOT or hits a forbidden prefix.

    Two-layer defense (AC5):
    1. Path-escape guard: resolved target must be relative to _WRITE_ROOT.
    2. Forbidden-prefix guard: must not start with .claude/ or context/.
    """
    try:
        target.resolve().relative_to(_WRITE_ROOT.resolve())
    except ValueError as e:
        raise ValueError(
            f"stale_doc_curator: write path {target!r} escapes "
            f"_WRITE_ROOT={_WRITE_ROOT!r}; refusing to write. "
            f"Original error: {e}"
        ) from e

    resolved = str(target.resolve())
    _forbidden = [
        str((_REPO_ROOT / ".claude").resolve()),
        str((_REPO_ROOT / "context").resolve()),
    ]
    for forbidden_prefix in _forbidden:
        if resolved.startswith(forbidden_prefix):
            raise ValueError(
                f"stale_doc_curator: SAFETY VIOLATION — write path {resolved!r} "
                f"is inside forbidden prefix {forbidden_prefix!r}. Refusing."
            )


def _write_report(
    records: list[DocRecord],
    threshold_days: int,
    today: date,
    report_path: Path,
) -> None:
    """Write the stale-docs Markdown report to report_path.

    Validates write is within _WRITE_ROOT before touching the filesystem.
    """
    _assert_write_safe(report_path)

    stale = [r for r in records if r.is_stale]
    contradictions = [r for r in records if r.is_contradiction]

    lines: list[str] = [
        f"# Stale-doc audit — {today.isoformat()}",
        "",
        f"**Generated by:** stale_doc_curator (Kanban #1222)  ",
        f"**Threshold:** {threshold_days} days (via `STALE_DOC_DAYS` env or default)  ",
        f"**File-age source:** filesystem mtime (git not available in API container)  ",
        f"**Scanned:** {len(records)} docs  ",
        f"**Stale (age > threshold):** {len(stale)}  ",
        f"**Contradiction flags:** {len(contradictions)}  ",
        "",
        "---",
        "",
    ]

    if stale:
        lines.append("## Stale documents (age > threshold)")
        lines.append("")
        lines.append("| Path | Age (days) | Contradiction |")
        lines.append("|------|-----------|---------------|")
        for r in sorted(stale, key=lambda x: x.age_days, reverse=True):
            try:
                rel = str(r.path.relative_to(_REPO_ROOT))
            except ValueError:
                rel = str(r.path)
            contra = "yes" if r.is_contradiction else "no"
            lines.append(f"| `{rel}` | {r.age_days:.1f} | {contra} |")
        lines.append("")
    else:
        lines.append("## Stale documents")
        lines.append("")
        lines.append("No documents exceed the age threshold.")
        lines.append("")

    if contradictions:
        lines.append("## Contradiction flags")
        lines.append("")
        lines.append(
            "These documents are referenced by a `REPLACES` / `SUPERSEDES` / "
            "`CANCELLED` / `changed-to` entry in a later decisions.md."
        )
        lines.append("")
        for r in contradictions:
            try:
                rel = str(r.path.relative_to(_REPO_ROOT))
            except ValueError:
                rel = str(r.path)
            lines.append(f"- `{rel}`: {r.contradiction_note}")
        lines.append("")

    lines += [
        "---",
        f"*Report written: {datetime.now(timezone.utc).isoformat()}*",
        "*Operator review required — this file is auto-generated and must not "
        "be committed as a source-of-truth.*",
    ]

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_stale_doc_curator(
    *,
    threshold_days: int | None = None,
    today: date | None = None,
) -> StaleDocCuratorResult:
    """Scan context/ tree and write a stale-docs report to _scratch/auditor/.

    Pure filesystem operation — no DB session needed (reads mtime, reads
    decisions.md text, writes _scratch/).

    Called by the digest pipeline (see src/routers/digest.py) — same soft-fail
    pattern as run_skill_stub_detector: any exception caught by the caller so
    curator failure never blocks the digest send.

    Parameters
    ----------
    threshold_days:
        Override for stale threshold in days (default: env STALE_DOC_DAYS or 60).
    today:
        Override the date used for the report filename (default: UTC today).

    Returns
    -------
    StaleDocCuratorResult summarising the run.
    """
    if threshold_days is None:
        env_val = os.environ.get("STALE_DOC_DAYS", "")
        try:
            threshold_days = int(env_val)
        except (ValueError, TypeError):
            threshold_days = _DEFAULT_STALE_DAYS

    if today is None:
        today = datetime.now(timezone.utc).date()

    result = StaleDocCuratorResult(threshold_days=threshold_days)

    # Step 1: collect all target files (AC1).
    all_paths = _collect_target_files()
    result.scanned_count = len(all_paths)

    if not all_paths:
        logger.info("stale_doc_curator: no target files found; nothing to audit")
        return result

    # Step 2: build stem index for contradiction heuristic lookup (AC3).
    stem_index = _build_stem_index(all_paths)

    # Step 3: collect decisions.md paths for contradiction scanning.
    decisions_files = [p for p in all_paths if p.name == "decisions.md"]

    # Step 4: for each decisions.md, extract contradiction hints.
    contradiction_map: dict[Path, list[str]] = {}  # path -> list of notes
    for df in decisions_files:
        hints = _extract_contradiction_hints(df, stem_index)
        for referenced_path, note in hints:
            contradiction_map.setdefault(referenced_path, []).append(note)

    # Step 5: score each file.
    records: list[DocRecord] = []
    for path in all_paths:
        age = _file_age_days(path)
        is_stale = age > threshold_days
        notes = contradiction_map.get(path, [])
        is_contradiction = len(notes) > 0
        contradiction_note = "; ".join(notes) if notes else ""
        records.append(
            DocRecord(
                path=path,
                age_days=age,
                is_stale=is_stale,
                is_contradiction=is_contradiction,
                contradiction_note=contradiction_note,
            )
        )

    stale_docs = [r for r in records if r.is_stale]
    contra_docs = [r for r in records if r.is_contradiction]

    result.stale_count = len(stale_docs)
    result.contradiction_count = len(contra_docs)

    # Step 6: write report only when there is something to flag (AC4).
    if stale_docs or contra_docs:
        report_path = _SCRATCH_AUDITOR / f"stale-docs-{today.isoformat()}.md"
        try:
            _write_report(records, threshold_days, today, report_path)
            result.report_path = str(report_path)
            logger.info(
                "stale_doc_curator: report written to %s "
                "(stale=%d, contradictions=%d, scanned=%d)",
                report_path,
                result.stale_count,
                result.contradiction_count,
                result.scanned_count,
            )
        except (ValueError, OSError) as exc:
            logger.error(
                "stale_doc_curator: failed to write report: %s", exc
            )
    else:
        logger.info(
            "stale_doc_curator: all %d docs are fresh and contradiction-free",
            result.scanned_count,
        )

    return result
