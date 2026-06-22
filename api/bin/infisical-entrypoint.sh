#!/bin/sh
# Soft-fallback Infisical entrypoint for the agent-teams api (Kanban #2488, Phase 1, DRAFT v3).
# Decision §0.4: NEVER hard-fail. If Infisical is configured AND reachable, inject secrets via
# `infisical run`; otherwise fall back to the .env baseline (the only path for non-Infisical
# installs). Place at api/bin/infisical-entrypoint.sh + chmod +x.
#
# v3 (dev-devops review 2026-06-23, Lead-verified): token via INFISICAL_TOKEN env (NOT --token
# flag — keeps it out of ps/proc); INFISICAL_DOMAIN (verified current name) + explicit --domain;
# explicit if-branch (no fragile word-split); skip CLI update check.
set -e

INFISICAL_ENV="${INFISICAL_ENV:-dev}"
export INFISICAL_DISABLE_UPDATE_CHECK="${INFISICAL_DISABLE_UPDATE_CHECK:-true}"
# INFISICAL_TOKEN is read automatically from the environment by the CLI — do NOT pass --token
# (it would be visible in `ps aux` / /proc/<pid>/cmdline).

start_uvicorn() { exec uvicorn src.main:app --host 0.0.0.0 --port 8456 --reload; }

if [ -n "$INFISICAL_TOKEN" ] && command -v infisical >/dev/null 2>&1; then
  if [ -n "$INFISICAL_DOMAIN" ]; then
    # Self-host: --domain takes highest precedence. Probe (-- true) before exec so we can fall back.
    if infisical run --domain "$INFISICAL_DOMAIN" --projectId="$INFISICAL_PROJECT_ID" --env="$INFISICAL_ENV" -- true >/dev/null 2>&1; then
      echo "[entrypoint] Infisical reachable — injecting secrets via 'infisical run' (env=$INFISICAL_ENV, domain=$INFISICAL_DOMAIN)." >&2
      exec infisical run --domain "$INFISICAL_DOMAIN" --projectId="$INFISICAL_PROJECT_ID" --env="$INFISICAL_ENV" \
        -- uvicorn src.main:app --host 0.0.0.0 --port 8456 --reload
    fi
  else
    # No INFISICAL_DOMAIN -> CLI default target (Infisical Cloud). Only valid for cloud, not self-host.
    if infisical run --projectId="$INFISICAL_PROJECT_ID" --env="$INFISICAL_ENV" -- true >/dev/null 2>&1; then
      echo "[entrypoint] Infisical reachable (cloud default) — injecting secrets via 'infisical run'." >&2
      exec infisical run --projectId="$INFISICAL_PROJECT_ID" --env="$INFISICAL_ENV" \
        -- uvicorn src.main:app --host 0.0.0.0 --port 8456 --reload
    fi
  fi
  echo "[entrypoint] WARNING: INFISICAL_TOKEN set but Infisical unreachable — falling back to .env values." >&2
else
  echo "[entrypoint] Infisical not configured (no token/CLI) — using .env values." >&2
fi

start_uvicorn
