#!/usr/bin/env bash
# DRAFT (Kanban #1269 AC4) — POSIX twin of draft-sem-spend-cap-gate.ps1.
# PreToolUse on Edit + Write for sem-campaign-lead / google-ads-specialist /
# meta-ads-specialist / platform-ads-coordinator. Emits requires-attention when
# proposed budget exceeds a hardcoded daily/monthly threshold.
#
# DRAFT ONLY — do NOT install. Lead handles agent file + .codex/hooks/ placement
# per feedback_codex_dir_humans_only.md.
#
# Registration snippet (Lead writes into .codex/agents/<agent>.md frontmatter):
#   hooks:
#     PreToolUse:
#       - matcher: Edit|Write
#         hooks:
#           - type: command
#             command: bash .codex/hooks/sem-spend-cap-gate.sh
#
# Fail-open on any internal error.

set +e

DAILY_CAP_USD=5000
MONTHLY_CAP_USD=50000

emit_allow() {
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}\n'
}

emit_attention() {
    local reason="$1"
    jq -n --arg r "$reason" \
        '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"requires-attention",permissionDecisionReason:$r}}' \
        2>/dev/null
}

fail_open() {
    echo "WARN: sem-spend-cap-gate: $1 ; failing open (allow)" >&2
    emit_allow
    exit 0
}

payload="$(cat 2>/dev/null)"
if [ -z "$payload" ]; then emit_allow; exit 0; fi

if ! command -v jq >/dev/null 2>&1; then fail_open "jq not installed"; fi

tool_name="$(echo "$payload" | jq -r '.tool_name // empty' 2>/dev/null)"
if [ "$tool_name" != "Edit" ] && [ "$tool_name" != "Write" ]; then emit_allow; exit 0; fi

# Agent-name scope (multi-key fallback).
agent_name="$(echo "$payload" | jq -r '.agent_name // .subagent_type // .agent // .agentName // empty' 2>/dev/null)"
if [ -n "$agent_name" ]; then
    case "$agent_name" in
        sem-campaign-lead|google-ads-specialist|meta-ads-specialist|platform-ads-coordinator) ;;
        *) emit_allow; exit 0 ;;
    esac
fi

# Extract content (Write.content or Edit.new_string).
if [ "$tool_name" = "Write" ]; then
    content="$(echo "$payload" | jq -r '.tool_input.content // empty' 2>/dev/null)"
else
    content="$(echo "$payload" | jq -r '.tool_input.new_string // empty' 2>/dev/null)"
fi

if [ -z "$content" ]; then emit_allow; exit 0; fi

# ---------------------------------------------------------------------------
# Budget extraction — heuristic. We compute daily_total and monthly_total
# via awk so we can sum across multiple matches.
# ---------------------------------------------------------------------------
parse_sum() {
    # Strip $ , whitespace, USD suffix from $1; print as float; else nothing.
    echo "$1" | sed -E 's/[ \t$,]//g; s/[Uu][Ss][Dd]//g'
}

# Monthly (scoped) — capture group is the amount.
monthly_total="$(echo "$content" \
    | grep -oiE '(monthly[_ ]budget|monthly_usd)[:= ]+\$?[0-9][0-9,]*(\.[0-9]+)?( *USD)?' \
    | grep -oE '[0-9][0-9,]*(\.[0-9]+)?' \
    | awk '{gsub(",",""); s+=$1} END {printf "%g", s+0}')"

# Daily (scoped).
daily_scoped="$(echo "$content" \
    | grep -oiE '(daily[_ ]budget|daily_usd|campaign[_ ]budget)[:= ]+\$?[0-9][0-9,]*(\.[0-9]+)?( *USD)?' \
    | grep -oE '[0-9][0-9,]*(\.[0-9]+)?' \
    | awk '{gsub(",",""); s+=$1} END {printf "%g", s+0}')"

# Unscoped bare $-amounts / N USD — strip scoped matches from content first.
residue="$(echo "$content" \
    | sed -E 's/(monthly[_ ]budget|monthly_usd|daily[_ ]budget|daily_usd|campaign[_ ]budget)[:= ]+\$?[0-9][0-9,]*(\.[0-9]+)?( *USD)?/ /gI')"

daily_bare="$(echo "$residue" \
    | grep -oiE '(\$[ ]?[0-9][0-9,]*(\.[0-9]+)?|[0-9][0-9,]*(\.[0-9]+)? *USD)\b' \
    | grep -oE '[0-9][0-9,]*(\.[0-9]+)?' \
    | awk '{gsub(",",""); s+=$1} END {printf "%g", s+0}')"

daily_total="$(awk -v a="${daily_scoped:-0}" -v b="${daily_bare:-0}" 'BEGIN {printf "%g", a+b}')"
monthly_total="${monthly_total:-0}"

daily_exceeded="$(awk -v t="$daily_total" -v c="$DAILY_CAP_USD" 'BEGIN {print (t>c) ? 1 : 0}')"
monthly_exceeded="$(awk -v t="$monthly_total" -v c="$MONTHLY_CAP_USD" 'BEGIN {print (t>c) ? 1 : 0}')"

if [ "$daily_exceeded" = "0" ] && [ "$monthly_exceeded" = "0" ]; then
    emit_allow
    exit 0
fi

reason="sem-spend-cap-gate: proposed budget exceeds soft threshold.

  detected daily total   = \$${daily_total}   (cap \$${DAILY_CAP_USD})
  detected monthly total = \$${monthly_total} (cap \$${MONTHLY_CAP_USD})

This is a pre-flight nudge, not the authoritative cap. The real budget gate
runs server-side in services/budget_gate.py (Kanban #1194). Confirm the
amounts are intentional before proceeding. If the values are correct and
have been approved by the operator, re-issue the Edit/Write — this hook is
a one-shot tripwire; subsequent invocations on the same content still emit
requires-attention until the source content drops below the threshold.

Future work: per-project override via GET /api/projects/<id> reading
budget_daily_usd / budget_monthly_usd fields (TODO Kanban #?)."

emit_attention "$reason"
exit 0
