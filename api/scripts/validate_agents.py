"""Agent-frontmatter validator CLI (Kanban #1016).

Usage:
    python -m scripts.validate_agents

Scans ``<repo_root>/.claude/agents/*.md`` and prints one human-friendly line
per diagnostic::

    agents/foo.md:3 [error] name: name 'Foo Bar' must match ^[a-z0-9]+(-[a-z0-9]+)*$
    agents/bar.md:1 [warning] email_actions: unknown frontmatter key 'email_actions' ...

Exit code:
    1  if ANY diagnostic has severity "error"
    0  otherwise (clean, or warnings-only)

This is the CLI frontend of the SAME service the ``GET /api/agents/validate``
endpoint uses (``services.agent_validation.validate_agents_dir``) — one
implementation, two frontends, so they can never disagree.

No DB access (pure filesystem scan), so — unlike ``scripts/seed.py`` — imports
do not need to be deferred to dodge the engine-binding race; this script never
touches ``src.db``. ``repo_root`` is read from settings exactly as the endpoint
does.
"""

from __future__ import annotations

import sys
from pathlib import Path

from src.services.agent_validation import default_agents_dir, validate_agents_dir
from src.settings import get_settings


def run() -> int:
    """Validate the agents dir; print diagnostics; return the exit code."""
    repo_root = Path(get_settings().repo_root)
    agents_dir = default_agents_dir(repo_root)
    result = validate_agents_dir(agents_dir)

    diagnostics = result["diagnostics"]
    for d in diagnostics:
        # Prefix the basename with "agents/" for a readable, repo-relative
        # location token (the service only knows the basename — paths never
        # leave the service).
        print(
            f"agents/{d['file']}:{d['line']} "
            f"[{d['severity']}] {d['field']}: {d['message']}"
        )

    error_count = result["error_count"]
    warning_count = result["warning_count"]
    print(
        f"\nScanned {result['files_scanned']} agent file(s): "
        f"{error_count} error(s), {warning_count} warning(s).",
        file=sys.stderr,
    )

    return 1 if error_count else 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
