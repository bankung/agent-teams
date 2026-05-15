#!/usr/bin/env bash
# agent-teams — print the Tailscale MagicDNS URL for this host.
#
# Reads the local Tailscale state via `tailscale status --json` and prints
# the `http://<this-machine>.<tailnet>.ts.net:<port>` URL that other tailnet
# devices use to reach the agent-teams stack on this host.
#
# Honors WEB_PORT (default 5431) — `WEB_PORT=8080 ./bin/remote-url.sh` overrides.
#
# Companion: bin/remote-url.ps1 (native Windows).
# Setup guide: readme_remote-access.md.
#
# Exit codes:
#   0  URL printed
#   1  tailscale not on PATH
#   2  tailscale daemon not reachable, or DNSName missing from JSON

set -euo pipefail

WEB_PORT="${WEB_PORT:-5431}"

err() { printf 'ERROR: %s\n' "$*" >&2; }

if ! command -v tailscale >/dev/null 2>&1; then
  err "tailscale is not installed (or not on PATH)."
  err "See readme_remote-access.md for the full setup."
  exit 1
fi

# Capture status JSON; daemon-unreachable exits non-zero.
if ! status_json="$(tailscale status --json 2>/dev/null)"; then
  err "Tailscale daemon is not reachable, or this host is not logged in."
  err "Try:  sudo tailscale up"
  exit 2
fi

# Prefer `jq` if available (clean parse); fall back to a small Python one-liner
# (present on every macOS / mainstream Linux). If neither is present, last-ditch
# grep for the DNSName field. The fallback chain is intentional — `bin/` should
# work on barebones systems without forcing the user to install jq.
host=""
if command -v jq >/dev/null 2>&1; then
  host="$(printf '%s' "$status_json" | jq -r '.Self.DNSName // empty')"
elif command -v python3 >/dev/null 2>&1; then
  host="$(printf '%s' "$status_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Self",{}).get("DNSName","") or "")')"
else
  # Grep fallback. `tailscale status --json` formats Self.DNSName as
  # `"DNSName": "<host>.<tailnet>.ts.net."` somewhere near the top of the doc.
  host="$(printf '%s' "$status_json" | grep -o '"DNSName"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*"DNSName"[[:space:]]*:[[:space:]]*"([^"]*)"/\1/')"
fi

# Strip trailing dot from the FQDN.
host="${host%.}"

if [ -z "$host" ]; then
  err "Tailscale status JSON missing Self.DNSName — is MagicDNS enabled?"
  err "See readme_remote-access.md ('MagicDNS — turn it on')."
  exit 2
fi

printf 'http://%s:%s/p/agent-teams\n' "$host" "$WEB_PORT"
exit 0
