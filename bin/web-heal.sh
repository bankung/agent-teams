#!/usr/bin/env bash
# agent-teams — heal the web container after a .next corruption (hot-reload race).
#
# Default action: docker compose -p agent-teams restart web  (~6s fix).
# --clean flag:   stop web, remove host .next/, then bring web back up.
#
# Usage:
#   ./bin/web-heal.sh           # fast restart
#   ./bin/web-heal.sh --clean   # full rebuild of .next

set -euo pipefail

COMPOSE_PROJECT="agent-teams"
POLL_TIMEOUT=60   # seconds to wait for HTTP 200 after restart
WEB_URL="http://localhost:5431"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

CLEAN=0
if [ "${1:-}" = "--clean" ]; then
  CLEAN=1
fi

if [ "$CLEAN" = "1" ]; then
  echo "==> [web-heal] --clean: stopping web container"
  docker compose -p "${COMPOSE_PROJECT}" stop web

  NEXT_DIR="$REPO_ROOT/web/.next"
  if [ -d "$NEXT_DIR" ]; then
    echo "==> [web-heal] removing $NEXT_DIR (best-effort)"
    rm -rf "$NEXT_DIR" || echo "    (removal failed or directory in use — continuing)"
  else
    echo "==> [web-heal] $NEXT_DIR not found — skipping removal"
  fi

  echo "==> [web-heal] bringing web back up"
  docker compose -p "${COMPOSE_PROJECT}" up -d web
else
  echo "==> [web-heal] restarting web container"
  docker compose -p "${COMPOSE_PROJECT}" restart web
fi

# Poll until HTTP 200 or timeout.
echo "==> [web-heal] polling $WEB_URL (timeout ${POLL_TIMEOUT}s)..."
ELAPSED=0
STATUS=""
while [ "$ELAPSED" -lt "$POLL_TIMEOUT" ]; do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$WEB_URL" 2>/dev/null || true)
  if [ "$HTTP_CODE" = "200" ]; then
    STATUS="200"
    break
  fi
  sleep 2
  ELAPSED=$((ELAPSED + 2))
done

if [ "$STATUS" = "200" ]; then
  echo "==> [web-heal] SUCCESS — $WEB_URL returned HTTP 200"
else
  echo "==> [web-heal] FAIL — $WEB_URL did not return 200 within ${POLL_TIMEOUT}s (last code: ${HTTP_CODE:-none})"
  exit 1
fi
