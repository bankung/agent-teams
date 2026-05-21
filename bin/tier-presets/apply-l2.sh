#!/usr/bin/env bash
# Apply Tier L2 (Pro pilot) preset to .claude/agents/*.md
# Lead generated this from #1360 audit; operator runs after review.
#
# What changes:
#   content-writer     opus        -> sonnet
#   thai-proofreader   sonnet      -> haiku
#   general            (implicit)  -> sonnet  (adds model: line)
#   secretary          (implicit)  -> sonnet  (adds model: line)
#   novel-writer       (implicit)  -> sonnet  (adds model: line)
#   novel-editor       (implicit)  -> sonnet  (adds model: line)
#
# What stays:
#   dev-sr-backend / dev-sr-frontend  Opus (sr-* new-surface design)
#   bi-analyst / sem-campaign-lead / seo-strategist  Opus (strategist roles)
#   dev-documentor / general-researcher / secretary-email-triage
#     / secretary-job-scout  Haiku (cheap-model roles)
#   All other agents already at Sonnet — no change.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

DIFF_DIR="bin/tier-presets/l2"

for diff in "$DIFF_DIR"/*.md.diff; do
  agent=$(basename "$diff" .md.diff)
  echo "Applying L2 to $agent..."
  git apply --whitespace=fix "$diff"
done

echo ""
echo "L2 tier applied to 6 agents."
echo "Restart your Claude Code session to pick up new model: defaults."
