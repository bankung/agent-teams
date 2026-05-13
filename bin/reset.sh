#!/usr/bin/env bash
# agent-teams — full reset for macOS / Linux / WSL.
#
# Tears down the stack AND deletes the Postgres volume, then re-runs install.sh.
# DESTRUCTIVE: every row in the DB is gone after this.
#
# Bypass the confirmation with: AGENT_TEAMS_RESET_YES=1 ./bin/reset.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [ "${AGENT_TEAMS_RESET_YES:-0}" != "1" ]; then
  cat <<EOF
This will:
  - Stop all agent-teams containers.
  - DELETE the Postgres volume (every project, task, and history row is gone).
  - Re-build and re-seed from scratch.

Type 'yes' to continue, anything else to abort.
EOF
  read -r answer
  if [ "$answer" != "yes" ]; then
    echo "Aborted."
    exit 0
  fi
fi

echo "==> docker compose down -v"
docker compose down -v

echo "==> Re-running installer..."
exec "$SCRIPT_DIR/install.sh"
