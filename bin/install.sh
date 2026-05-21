#!/usr/bin/env bash
# agent-teams — one-shot installer for macOS / Linux / WSL.
#
# What it does (idempotent):
#   1. Verify Docker is installed AND the daemon is responsive.
#   2. docker compose up -d --build  (builds on first run, cache after).
#   3. Wait for the API to answer 200 on http://localhost:8456/api/projects.
#   4. Run the seed (docker compose exec — no host Python required).
#   5. Print the Kanban URL and optionally open it.
#
# Companion: bin/install.ps1 (native Windows). Reset: bin/reset.sh.
#
# Exit codes:
#   0  success
#   1  docker missing OR daemon unreachable
#   2  docker compose up failed
#   3  API healthy-wait timed out
#   4  seed failed
#   5  schema migration failed

set -euo pipefail

# Resolve repo root from this script's location so the script works from any cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

API_PORT="${API_PORT:-8456}"
WEB_PORT="${WEB_PORT:-5431}"
PROJECT_URL="http://localhost:${WEB_PORT}/p/agent-teams"
HEALTH_URL="http://localhost:${API_PORT}/api/projects"
WAIT_TIMEOUT_SEC=60
WAIT_INTERVAL_SEC=5

# ---- helpers ----------------------------------------------------------------
log()  { printf '==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
err()  { printf 'ERROR: %s\n' "$*" >&2; }

open_url() {
  local url="$1"
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
  fi
}

# ---- 1. Docker check --------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  err "Docker is not installed (or not on PATH)."
  err "Install Docker Desktop / Docker Engine: https://docs.docker.com/get-docker/"
  exit 1
fi

# `docker info` exits non-zero when the daemon isn't reachable.
if ! docker info >/dev/null 2>&1; then
  err "Docker is installed but the daemon is not responding."
  err "Start Docker Desktop (or 'sudo systemctl start docker' on Linux) and retry."
  err "Install / troubleshooting: https://docs.docker.com/get-docker/"
  exit 1
fi
log "Docker daemon OK."

# ---- 2. docker compose up ---------------------------------------------------
log "Building and starting services (docker compose up -d --build)..."
if ! docker compose up -d --build; then
  err "docker compose up failed. Inspect the output above."
  exit 2
fi

# ---- 2b. Schema migration (live-DB guard bypass) ----------------------------
# The L10 guard in api/alembic/env.py refuses non-_test DBs without
# MIGRATION_TARGET=live. Same for L11 in scripts/seed.py / SEED_TARGET=production.
# Both are SAFE to bypass on a fresh install — there's no data to lose. Subsequent
# re-runs of this installer are no-ops (alembic reports 'no new revisions';
# seed is idempotent). The guards remain in force for any other code path.
log "First-time install: bypassing live-DB guards (MIGRATION_TARGET=live + SEED_TARGET=production) for the initial schema + seed."
log "  This is safe on a fresh DB. Subsequent re-runs are no-ops (alembic no-op + seed idempotent)."
log "Running schema migration (docker compose exec -T -e MIGRATION_TARGET=live api alembic upgrade head)..."
if ! docker compose exec -T -e MIGRATION_TARGET=live api alembic upgrade head; then
  err "Schema migration failed. Check logs: docker compose logs api"
  exit 5
fi

# ---- 3. Wait for API healthy ------------------------------------------------
log "Waiting for API at ${HEALTH_URL} (cap ${WAIT_TIMEOUT_SEC}s)..."
elapsed=0
healthy=0
while [ "$elapsed" -lt "$WAIT_TIMEOUT_SEC" ]; do
  # -f makes curl exit non-zero on HTTP >=400; -s silences progress; -o/dev/null drops body.
  if curl -fsS -o /dev/null "$HEALTH_URL" 2>/dev/null; then
    healthy=1
    break
  fi
  printf '    ...still waiting (%ds elapsed)\n' "$elapsed"
  sleep "$WAIT_INTERVAL_SEC"
  elapsed=$((elapsed + WAIT_INTERVAL_SEC))
done

if [ "$healthy" -ne 1 ]; then
  err "API did not become healthy within ${WAIT_TIMEOUT_SEC}s."
  err "Check logs: docker compose logs api"
  exit 3
fi
log "API healthy."

# ---- 4. Seed ----------------------------------------------------------------
# Seed is idempotent — re-runs print 'already seeded' and exit 0.
# Use -T to disable pseudo-TTY (safe in non-interactive CI / scripts).
log "Running seed (docker compose exec -T -e SEED_TARGET=production api python -m scripts.seed)..."
if ! docker compose exec -T -e SEED_TARGET=production api python -m scripts.seed; then
  err "Seed failed. Check logs: docker compose logs api"
  exit 4
fi

# ---- 5. Claude Code plan → tier preset -------------------------------------
# Non-interactive safe: if stdin is not a TTY (CI / --non-interactive / piped),
# or if the NON_INTERACTIVE env var is set, skip the prompt and default to max.
TIER_CHOICE="max"
if [ -z "${NON_INTERACTIVE:-}" ] && [ -t 0 ]; then
  printf '\n'
  printf 'Claude Code plan? [m]ax / [p]ro  (default: max, Enter to skip): '
  read -r _plan_input || _plan_input=""
  case "${_plan_input}" in
    p|P|pro|Pro|PRO) TIER_CHOICE="l2" ;;
    *)               TIER_CHOICE="max" ;;
  esac
else
  log "Non-interactive mode — defaulting to TIER MAX."
fi

if [ "$TIER_CHOICE" = "l2" ]; then
  log "Pro plan selected — applying TIER L2 preset..."
  if [ -f "$REPO_ROOT/bin/agent-teams-tier-set.sh" ]; then
    bash "$REPO_ROOT/bin/agent-teams-tier-set.sh" l2
  else
    warn "bin/agent-teams-tier-set.sh not found — skipping tier apply. Run it manually."
  fi
  log "TIER L2 active. Restart your Claude Code session to pick up new model defaults."
else
  log "TIER MAX active (operator default — no agent file changes)."
fi

# ---- 6. Next steps + friendly banner ----------------------------------------
cat <<'EOF'

=========================================================================
✓ agent-teams is installed and running.

Next steps:
  1. Open http://localhost:5431 in your browser.
  2. Click the 'demo-tour' project. Try a task. (5 min walkthrough.)
  3. Read QUICKSTART.md (at the repo root) for the full intro.

Need help? See README.md or run `bin/agent-teams-tier-set.sh --help`
to switch Claude Code Pro/Max tier presets.
=========================================================================

EOF

open_url "$PROJECT_URL"
exit 0
