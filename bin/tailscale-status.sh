#!/usr/bin/env bash
# agent-teams — Tailscale status helper for macOS / Linux / WSL.
#
# Wraps `tailscale status` with a friendly header. Exits non-zero if
# Tailscale isn't installed or the daemon isn't reachable.
#
# Companion: bin/tailscale-status.ps1 (native Windows).
# Setup guide: readme_remote-access.md.
#
# Exit codes:
#   0  Tailscale running and connected
#   1  tailscale not on PATH (not installed)
#   2  tailscale installed but daemon not reachable / not logged in

set -euo pipefail

log() { printf '==> %s\n' "$*"; }
err() { printf 'ERROR: %s\n' "$*" >&2; }

log "agent-teams — Tailscale status"
echo ""

if ! command -v tailscale >/dev/null 2>&1; then
  err "tailscale is not installed (or not on PATH)."
  err "Install: curl -fsSL https://tailscale.com/install.sh | sh"
  err "See readme_remote-access.md for the full setup."
  exit 1
fi

# `tailscale status` exits non-zero when the daemon isn't responding or the
# host isn't logged in. We still want the user to see whatever output it
# produces, so don't silence it — just inspect the exit code.
if ! tailscale status; then
  echo ""
  err "Tailscale daemon is not reachable, or this host is not logged in."
  err "Try:  sudo tailscale up"
  err "See readme_remote-access.md for the full setup."
  exit 2
fi

exit 0
