#!/usr/bin/env bash
# agent-teams — after-pull / fresh-machine bring-up for macOS / Linux / WSL / Git-Bash.
#
# What it does (thin wrapper around install.sh — idempotent, does NOT wipe):
#   1. Refuse if the working tree is dirty (uncommitted/untracked changes).
#      Pass --force as the first argument to skip this check.
#   2. git pull --ff-only  (fast-forward only; aborts on diverged history / no upstream).
#   3. Print the resulting short HEAD SHA.
#   4. Delegate to bin/install.sh which handles:
#        - docker compose up -d --build
#        - alembic upgrade head  (with MIGRATION_TARGET=live — L10 guard bypass)
#        - scripts/seed           (with SEED_TARGET=production — L11 guard bypass)
#        - wait-for-healthy + friendly banner
#
# Companion: bin/bring-up.ps1 (Windows PowerShell). Launcher: bin/bring-up.cmd.
# To wipe and rebuild from scratch: bin/reset.sh / bin/reset.ps1 (destructive).
#
# Exit codes:
#   0  success (install.sh completed)
#   1  dirty working tree (without --force) OR git pull failed
#   2+ forwarded from install.sh (docker / migrate / seed failures)

set -euo pipefail

# Resolve repo root from this script's location so the script works from any cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# ---- helpers ----------------------------------------------------------------
log()  { printf '==> %s\n' "$*"; }
err()  { printf 'ERROR: %s\n' "$*" >&2; }

# ---- args -------------------------------------------------------------------
FORCE=0
if [ "${1:-}" = "--force" ]; then
  FORCE=1
fi

# ---- dirty-tree check -------------------------------------------------------
DIRTY="$(git status --porcelain 2>/dev/null)"
if [ -n "$DIRTY" ] && [ "$FORCE" -ne 1 ]; then
  err "Working tree has uncommitted or untracked changes:"
  printf '%s\n' "$DIRTY" >&2
  err "Commit or stash your changes first, or re-run with --force to skip this check."
  exit 1
fi

# ---- git pull --ff-only -----------------------------------------------------
log "Pulling latest changes (git pull --ff-only)..."
if ! git pull --ff-only; then
  err "git pull --ff-only failed."
  err "Possible causes: diverged history, no upstream branch, or merge conflict."
  err "Resolve manually (git fetch + git log + git merge/rebase), then retry."
  exit 1
fi

# ---- print resulting HEAD ---------------------------------------------------
HEAD_SHORT="$(git rev-parse --short HEAD)"
log "Now at: $HEAD_SHORT"

# ---- delegate to install.sh -------------------------------------------------
log "Delegating to bin/install.sh (build + migrate + seed)..."
exec "$SCRIPT_DIR/install.sh"
