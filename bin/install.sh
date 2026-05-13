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
log "Running seed (docker compose exec -T api python -m scripts.seed)..."
if ! docker compose exec -T api python -m scripts.seed; then
  err "Seed failed. Check logs: docker compose logs api"
  exit 4
fi

# ---- 5. URL + help ----------------------------------------------------------
cat <<EOF

================================================================================
agent-teams is ready.

  Kanban UI : ${PROJECT_URL}
  API base  : http://localhost:${API_PORT}

Helpful commands:
  Stop      : docker compose down
  Restart   : docker compose up -d            (or rerun ./bin/install.sh)
  Reset DB  : ./bin/reset.sh                  (or 'docker compose down -v')
  Tail logs : docker compose logs -f api web

EOF

open_url "$PROJECT_URL"
exit 0
