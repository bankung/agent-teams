#!/usr/bin/env bash
# DRAFT (Kanban #1269 AC4) — POSIX twin of draft-sem-performance-dashboard.ps1.
# PostToolUse on Write for SEM agents OR file_path matching *sem-campaign-*.md
# / *sem-performance-*.md / *sem-report-*.md. Parses platform / budget /
# date_range and appends a TSV audit line. Never blocks.
#
# DRAFT ONLY — do NOT install. Lead handles agent file + .codex/hooks/ placement
# per feedback_codex_dir_humans_only.md.
#
# Registration snippet (Lead writes into .codex/agents/<agent>.md frontmatter):
#   hooks:
#     PostToolUse:
#       - matcher: Write
#         hooks:
#           - type: command
#             command: bash .codex/hooks/sem-performance-dashboard.sh
#
# Audit log: _scratch/sem-audit-trail.log (future: POST /api/audit-events).
# Fail-soft on any parse error.

set +e

AUDIT_LOG="_scratch/sem-audit-trail.log"

emit_allow() {
    printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","permissionDecision":"allow"}}\n'
}

fail_soft() {
    echo "WARN: sem-performance-dashboard: $1 ; allowing (PostToolUse is informational)" >&2
    emit_allow
    exit 0
}

payload="$(cat 2>/dev/null)"
if [ -z "$payload" ]; then emit_allow; exit 0; fi

if ! command -v jq >/dev/null 2>&1; then fail_soft "jq not installed"; fi

tool_name="$(echo "$payload" | jq -r '.tool_name // empty' 2>/dev/null)"
if [ "$tool_name" != "Write" ]; then emit_allow; exit 0; fi

success="$(echo "$payload" | jq -r '.tool_response.success // "true"' 2>/dev/null)"
if [ "$success" = "false" ]; then emit_allow; exit 0; fi

file_path="$(echo "$payload" | jq -r '.tool_input.file_path // empty' 2>/dev/null)"
if [ -z "$file_path" ]; then emit_allow; exit 0; fi

# Agent-name (multi-key fallback). Either an SEM agent OR a path-pattern match
# triggers the audit; both checks are OR'd.
agent_name="$(echo "$payload" | jq -r '.agent_name // .subagent_type // .agent // .agentName // empty' 2>/dev/null)"
agent_in_scope=0
case "$agent_name" in
    sem-campaign-lead|google-ads-specialist|meta-ads-specialist|platform-ads-coordinator) agent_in_scope=1 ;;
esac

path_in_scope=0
echo "$file_path" | grep -qiE 'sem-(campaign|performance|report)-[^/\\]*\.md$' && path_in_scope=1

if [ "$agent_in_scope" -eq 0 ] && [ "$path_in_scope" -eq 0 ]; then
    emit_allow
    exit 0
fi

# Extract content (prefer tool_input.content; fallback to disk).
content="$(echo "$payload" | jq -r '.tool_input.content // empty' 2>/dev/null)"
if [ -z "$content" ] && [ -f "$file_path" ]; then
    content="$(cat "$file_path" 2>/dev/null)"
fi

if [ -z "$content" ]; then fail_soft "no content to scan for '$file_path'"; fi

# ---------------------------------------------------------------------------
# Extract platform / budget / date_range.
# ---------------------------------------------------------------------------
platform="$(echo "$content" | grep -oiE '^[[:space:]]*(\*\*)?platform(\*\*)?[[:space:]]*[:=][[:space:]]*.+' | head -n1 | sed -E 's/.*[:=][[:space:]]*//; s/\*+$//; s/[[:space:]]+$//')"
if [ -z "$platform" ]; then platform="(unknown)"; fi

# Budget — sum scoped matches + bare $-amounts.
budget_scoped="$(echo "$content" \
    | grep -oiE '(daily[_ ]budget|monthly[_ ]budget|campaign[_ ]budget|daily_usd|monthly_usd|budget)[:= ]+\$?[0-9][0-9,]*(\.[0-9]+)?( *USD)?' \
    | grep -oE '[0-9][0-9,]*(\.[0-9]+)?' \
    | awk '{gsub(",",""); s+=$1} END {printf "%g", s+0}')"

if [ "${budget_scoped:-0}" = "0" ]; then
    budget_bare="$(echo "$content" \
        | grep -oiE '\$[ ]?[0-9][0-9,]*(\.[0-9]+)?' \
        | grep -oE '[0-9][0-9,]*(\.[0-9]+)?' \
        | awk '{gsub(",",""); s+=$1} END {printf "%g", s+0}')"
    budget_total="${budget_bare:-0}"
else
    budget_total="$budget_scoped"
fi

if [ "$budget_total" = "0" ]; then
    budget_str="(unknown)"
else
    budget_str="$budget_total"
fi

# Date range.
date_range="$(echo "$content" | grep -oiE '^[[:space:]]*(\*\*)?(date[_ ]range|period)(\*\*)?[[:space:]]*[:=][[:space:]]*.+' | head -n1 | sed -E 's/.*[:=][[:space:]]*//; s/\*+$//; s/[[:space:]]+$//')"
if [ -z "$date_range" ]; then
    from_to="$(echo "$content" | grep -oiE 'from[[:space:]]+[^[:space:]]+[[:space:]]+to[[:space:]]+[^[:space:]]+' | head -n1)"
    if [ -n "$from_to" ]; then
        date_range="$from_to"
    else
        date_range="(unknown)"
    fi
fi

# Sanity warn if both budget + platform unparseable.
if [ "$platform" = "(unknown)" ] && [ "$budget_str" = "(unknown)" ]; then
    echo "WARN: sem-performance-dashboard: no parseable platform/budget fields in '$file_path'" >&2
fi

# Append audit TSV line.
ts="$(date -u +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)"
line="${ts}	agent=${agent_name}	file=${file_path}	platform=${platform}	budget_usd=${budget_str}	date_range=${date_range}"

audit_dir="$(dirname "$AUDIT_LOG" 2>/dev/null)"
if [ -n "$audit_dir" ] && [ ! -d "$audit_dir" ]; then
    mkdir -p "$audit_dir" 2>/dev/null
fi
echo "$line" >> "$AUDIT_LOG" 2>/dev/null
# TODO (Kanban #?): POST /api/audit-events when available.

emit_allow
exit 0
