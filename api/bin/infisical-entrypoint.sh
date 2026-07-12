#!/bin/sh
# Soft-fallback Infisical entrypoint for the agent-teams api (Kanban #2488, Phase 1, DRAFT v3).
# Decision §0.4: NEVER hard-fail. If Infisical is configured AND reachable, inject secrets via
# `infisical run`; otherwise fall back to the .env baseline (the only path for non-Infisical
# installs). Place at api/bin/infisical-entrypoint.sh + chmod +x.
#
# v3 (dev-devops review 2026-06-23, Lead-verified): token via INFISICAL_TOKEN env (NOT --token
# flag — keeps it out of ps/proc); INFISICAL_DOMAIN (verified current name) + explicit --domain;
# explicit if-branch (no fragile word-split); skip CLI update check.
# v4 (Kanban #2529): UVICORN_RELOAD switch, default = NO reload (secure default — this entrypoint
# previously always passed --reload, leaking the dev reloader into any prod+secrets combo).
# docker-compose.dev.yml sets UVICORN_RELOAD=true so the documented dev+secrets combo keeps
# hot-reload; every other overlay leaves it unset/false. Explicit if/else per call site (not a
# shared "$FLAG" var expanded unquoted) to match this file's existing no-word-splitting convention.
set -e

INFISICAL_ENV="${INFISICAL_ENV:-dev}"
UVICORN_RELOAD="${UVICORN_RELOAD:-false}"
export INFISICAL_DISABLE_UPDATE_CHECK="${INFISICAL_DISABLE_UPDATE_CHECK:-true}"
# INFISICAL_TOKEN is read automatically from the environment by the CLI — do NOT pass --token
# (it would be visible in `ps aux` / /proc/<pid>/cmdline).

start_uvicorn() {
  if [ "$UVICORN_RELOAD" = "true" ]; then
    exec uvicorn src.main:app --host 0.0.0.0 --port 8456 --reload
  else
    exec uvicorn src.main:app --host 0.0.0.0 --port 8456
  fi
}

if [ -n "$INFISICAL_TOKEN" ] && command -v infisical >/dev/null 2>&1; then
  if [ -z "$INFISICAL_DOMAIN" ]; then
    # Self-host requires the domain. NEVER fall back to Infisical Cloud (would send the token off-box).
    echo "[entrypoint] WARNING: INFISICAL_TOKEN set but INFISICAL_DOMAIN empty — refusing cloud fallback; using .env values." >&2
  elif [ -z "$INFISICAL_PROJECT_ID" ]; then
    echo "[entrypoint] WARNING: INFISICAL_TOKEN set but INFISICAL_PROJECT_ID empty — using .env values." >&2
  elif timeout 5 infisical run --domain "$INFISICAL_DOMAIN" --projectId="$INFISICAL_PROJECT_ID" --env="$INFISICAL_ENV" -- true >/dev/null 2>&1; then
    # Probe (-- true, 5s cap) before exec so an unreachable/slow backend falls back instead of hanging.
    echo "[entrypoint] Infisical reachable — injecting secrets via 'infisical run' (env=$INFISICAL_ENV, domain=$INFISICAL_DOMAIN)." >&2
    if [ "$UVICORN_RELOAD" = "true" ]; then
      exec infisical run --domain "$INFISICAL_DOMAIN" --projectId="$INFISICAL_PROJECT_ID" --env="$INFISICAL_ENV" \
        -- uvicorn src.main:app --host 0.0.0.0 --port 8456 --reload
    else
      exec infisical run --domain "$INFISICAL_DOMAIN" --projectId="$INFISICAL_PROJECT_ID" --env="$INFISICAL_ENV" \
        -- uvicorn src.main:app --host 0.0.0.0 --port 8456
    fi
  else
    echo "[entrypoint] WARNING: INFISICAL_TOKEN set but Infisical unreachable/timed out — falling back to .env values." >&2
  fi
else
  echo "[entrypoint] Infisical not configured (no token/CLI) — using .env values." >&2
fi

start_uvicorn
