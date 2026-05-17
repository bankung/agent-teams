#!/usr/bin/env bash
# agent-teams — full reset for macOS / Linux / WSL.
#
# Tears down the stack AND deletes the Postgres volume, then re-runs install.sh.
# DESTRUCTIVE: every row in the DB is gone after this.
#
# Bypass the confirmation with: AGENT_TEAMS_RESET_YES=1 ./bin/reset.sh
#   OR pass --yes as the first argument.

set -euo pipefail

EXPECTED_PROJECT="agent-teams"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Refuse to run from a worktree — the wipe must target the main checkout's
# compose project specifically (L13 prevention).
if [[ "$REPO_ROOT" == *".claude/worktrees/"* ]]; then
  echo "ERROR: refusing to run from a worktree ($REPO_ROOT)."
  echo "       cd to the main repo checkout first."
  exit 1
fi

# Confirm cwd is actually a compose project root.
if [ ! -f "docker-compose.yml" ]; then
  echo "ERROR: docker-compose.yml not found in $REPO_ROOT."
  echo "       reset.sh must run from the main repo root."
  exit 1
fi

# Accept --yes flag as an alternative to the env-var bypass.
FLAG_YES=0
if [ "${1:-}" = "--yes" ]; then
  FLAG_YES=1
fi

if [ "${AGENT_TEAMS_RESET_YES:-0}" != "1" ] && [ "$FLAG_YES" != "1" ]; then
  cat <<EOF
This will:
  - Stop all agent-teams containers (compose project: ${EXPECTED_PROJECT}).
  - DELETE the Postgres volume (every project, task, and history row is gone).
  - Re-build and re-seed from scratch.

Type 'WIPE' to continue, anything else to abort.
EOF
  read -r answer
  if [ "$answer" != "WIPE" ]; then
    echo "Aborted."
    exit 0
  fi
fi

echo "==> docker compose -p ${EXPECTED_PROJECT} down -v"
docker compose -p "${EXPECTED_PROJECT}" down -v

echo "==> Re-running installer..."
exec "$SCRIPT_DIR/install.sh"
