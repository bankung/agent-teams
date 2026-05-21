#!/usr/bin/env bash
# Per-machine tier toggle for .claude/agents/*.md model defaults.
#
# Usage: bin/agent-teams-tier-set.sh max|l2|pro|free [--dry-run]
#
# Tiers:
#   max       Operator's full-Opus preset. Restores .claude/agents/ to the
#             committed baseline (git checkout HEAD). Routine agents default to
#             whatever model: is committed — currently Sonnet for dev-* and most
#             content/seo/ads roles, implicit-Opus for general/secretary/novel-*.
#
#   l2 / pro  Pro pilot preset. Routine roles that were implicit-Opus get an
#             explicit model: sonnet; content-writer drops from opus to sonnet;
#             thai-proofreader drops from sonnet to haiku.
#             sr-* + strategists (bi-analyst/sem-campaign-lead/seo-strategist)
#             stay Opus regardless of tier.
#
#   free      Same as l2 (Free plan has similar quota constraints).
#
# After switching, restart your Claude Code session to pick up changes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

TIER="${1:-}"
DRY_RUN=0
FORCE=0

# Parse flags — allow --dry-run / --force anywhere in the argument list
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --force|-f) FORCE=1 ;;
  esac
done

# Normalise aliases before the main dispatch
case "$TIER" in
  pro)
    echo "(treating as L2 preset — Pro plan alias)"
    TIER="l2"
    ;;
  free)
    echo "(treating as L2 preset — Free plan has similar quota constraints)"
    TIER="l2"
    ;;
esac

# guard_agents_dirty — exits 1 if .claude/agents/ has uncommitted edits and the
# user declines to discard them.  Skipped when FORCE=1 or --dry-run mode.
guard_agents_dirty() {
  # Nothing to guard in dry-run; the checkout won't happen.
  [ "$DRY_RUN" -eq 1 ] && return 0
  [ "$FORCE"   -eq 1 ] && return 0

  local dirty_files
  dirty_files=$(git status --porcelain .claude/agents/ 2>/dev/null | grep -v '^$' || true)
  [ -z "$dirty_files" ] && return 0

  echo "WARNING: The following .claude/agents/ files have uncommitted edits:"
  echo "$dirty_files"
  echo ""

  # Non-interactive stdin (pipe / CI) → default to abort; require --force to override.
  if [ ! -t 0 ]; then
    echo "ERROR: Non-interactive session detected. Pass --force / -f to discard edits." >&2
    exit 1
  fi

  # Interactive: prompt the user.
  printf "Discard uncommitted edits in .claude/agents/? [y/N] "
  read -r _answer </dev/tty
  case "$_answer" in
    y|Y) return 0 ;;
    *)
      echo "Aborted. No files changed."
      exit 1
      ;;
  esac
}

case "$TIER" in
  max)
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "[dry-run] Would: git checkout HEAD -- .claude/agents/"
      exit 0
    fi
    guard_agents_dirty
    echo "Applying TIER MAX (operator's committed baseline)..."
    # .claude/agents/ is version-controlled; reverting restores the MAX baseline
    # including any implicit-Opus agents (those without a model: line).
    git checkout HEAD -- .claude/agents/
    echo "Done. Restart your Claude Code session to pick up changes."
    ;;

  l2)
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "[dry-run] Would: bash bin/tier-presets/apply-l2.sh"
      exit 0
    fi
    echo "Applying TIER L2 (Pro pilot preset)..."
    bash "$REPO_ROOT/bin/tier-presets/apply-l2.sh"
    # apply-l2-tier.sh prints its own "Restart" message.
    ;;

  ""|-h|--help)
    cat <<'EOF'
Usage: bin/agent-teams-tier-set.sh max|l2|pro|free [--dry-run] [--force|-f]

  max        Operator's full preset — restores .claude/agents/ to the committed
             baseline via `git checkout HEAD`. Agents with no explicit model: line
             default to Opus at the harness layer (Claude Code Max plan behavior).

  l2 / pro   Pro plan preset — routine agents downgraded to Sonnet.
               content-writer     opus       -> sonnet
               thai-proofreader   sonnet     -> haiku
               general            (implicit) -> sonnet  (adds model: line)
               secretary          (implicit) -> sonnet  (adds model: line)
               novel-writer       (implicit) -> sonnet  (adds model: line)
               novel-editor       (implicit) -> sonnet  (adds model: line)

  free       Same as l2 (Free plan has similar quota constraints).

  --dry-run  Print what would be executed without making any changes.
  --force/-f Skip the dirty-file prompt and discard any uncommitted edits in
             .claude/agents/ without asking. Required in non-interactive (CI/hook)
             environments when uncommitted edits should be discarded.

Stays Opus regardless of tier:
  dev-sr-backend, dev-sr-frontend  (sr-* new-surface design)
  bi-analyst, sem-campaign-lead, seo-strategist  (strategist roles)

Stays Haiku regardless of tier:
  dev-documentor, general-researcher
  secretary-email-triage, secretary-job-scout

Restart your Claude Code session after switching tiers to pick up the new
.claude/agents/*.md model defaults.
EOF
    exit 0
    ;;

  *)
    echo "Unknown tier: $TIER. Use max|l2|pro|free." >&2
    exit 1
    ;;
esac
