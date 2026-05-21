#!/usr/bin/env bash
# Per-machine tier toggle for .claude/agents/*.md model defaults.
#
# Usage: bin/agent-teams-tier-set.sh max|l2
#
# Tiers:
#   max  Operator's full-Opus preset. Restores .claude/agents/ to the
#        committed baseline (git checkout HEAD). Routine agents default to
#        whatever model: is committed — currently Sonnet for dev-* and most
#        content/seo/ads roles, implicit-Opus for general/secretary/novel-*.
#
#   l2   Pro pilot preset. Routine roles that were implicit-Opus get an
#        explicit model: sonnet; content-writer drops from opus to sonnet;
#        thai-proofreader drops from sonnet to haiku.
#        sr-* + strategists (bi-analyst/sem-campaign-lead/seo-strategist)
#        stay Opus regardless of tier.
#
# After switching, restart your Claude Code session to pick up changes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

TIER="${1:-}"

case "$TIER" in
  max)
    echo "Applying TIER MAX (operator's committed baseline)..."
    # .claude/agents/ is version-controlled; reverting restores the MAX baseline
    # including any implicit-Opus agents (those without a model: line).
    git checkout HEAD -- .claude/agents/
    echo "Done. Restart your Claude Code session to pick up changes."
    ;;

  l2)
    echo "Applying TIER L2 (Pro pilot preset)..."
    bash "$REPO_ROOT/bin/tier-presets/apply-l2.sh"
    # apply-l2-tier.sh prints its own "Restart" message.
    ;;

  ""|-h|--help)
    cat <<'EOF'
Usage: bin/agent-teams-tier-set.sh max|l2

  max  Operator's full preset — restores .claude/agents/ to the committed
       baseline via `git checkout HEAD`. Agents with no explicit model: line
       default to Opus at the harness layer (Claude Code Max plan behavior).

  l2   Pro pilot preset. Downgrade routine agents to conserve Opus quota:
         content-writer     opus       -> sonnet
         thai-proofreader   sonnet     -> haiku
         general            (implicit) -> sonnet  (adds model: line)
         secretary          (implicit) -> sonnet  (adds model: line)
         novel-writer       (implicit) -> sonnet  (adds model: line)
         novel-editor       (implicit) -> sonnet  (adds model: line)

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
    echo "Unknown tier: $TIER. Use max|l2." >&2
    exit 1
    ;;
esac
