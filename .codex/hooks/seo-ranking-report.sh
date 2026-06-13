#!/usr/bin/env bash
# SEO ranking-report audit (PostToolUse) — POSIX pair.
#
# Trigger: PostToolUse on Write when invoked by seo-reporting-analyst agent
# and the written file looks like a ranking report.
#
# See draft-seo-ranking-report.ps1 for design rationale. This is the
# Linux/Mac container pair — same logic, jq + bash regex instead of PowerShell.
#
# Registration in .codex/hooks.json (operator step):
#   "PostToolUse": [{"matcher": "Write",
#                    "hooks": [{"type":"command",
#                               "command":".codex/hooks/seo-ranking-report.sh"}]}]
#
# Kanban #1266 AC1.

set -u  # fail on unset vars; do NOT set -e (fail-soft policy)

emit_allow() {
    local reason="$1"
    local reason_json
    reason_json=$(printf '%s' "$reason" | jq -Rs .)
    printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","permissionDecision":"allow","permissionDecisionReason":%s}}\n' \
        "$reason_json"
}

fail_soft() {
    local msg="$1"
    printf 'WARN: seo-ranking-report: %s ; allowing (PostToolUse is informational)\n' "$msg" >&2
    emit_allow "seo-ranking-report fail-soft: $msg"
    exit 0
}

# Repo root (script lives in .codex/hooks/ ; parent..parent is repo root).
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
repo_root=$(cd -- "$script_dir/../.." &>/dev/null && pwd)

# Read stdin payload --------------------------------------------------------
payload=$(cat 2>/dev/null) || fail_soft "could not read stdin"
[ -z "$payload" ] && fail_soft "empty PostToolUse payload"

command -v jq >/dev/null 2>&1 || fail_soft "jq not installed"

# Scope to invoking agent + tool --------------------------------------------
agent_name=$(printf '%s' "$payload" | jq -r '.agent_name // .tool_input.subagent_type // empty')
if [ -n "$agent_name" ] && [ "$agent_name" != "seo-reporting-analyst" ]; then
    emit_allow "seo-ranking-report: agent '$agent_name' out of scope"
    exit 0
fi

tool_name=$(printf '%s' "$payload" | jq -r '.tool_name // empty')
if [ "$tool_name" != "Write" ]; then
    emit_allow "seo-ranking-report: tool '$tool_name' not in scope"
    exit 0
fi

file_path=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // empty')
if [ -z "$file_path" ]; then
    emit_allow "seo-ranking-report: no file_path in payload"
    exit 0
fi

success=$(printf '%s' "$payload" | jq -r '.tool_response.success // true')
if [ "$success" = "false" ]; then
    emit_allow "seo-ranking-report: tool reported failure ; nothing to log"
    exit 0
fi

# Filename pattern check (case-insensitive) ---------------------------------
lower=$(printf '%s' "$file_path" | tr '[:upper:]' '[:lower:]')
matches_report=0
matches_brief=0
case "$lower" in
    *seo-reporting-analyst*report*.md) matches_report=1 ;;
esac
case "$lower" in
    *ranking-brief*.md) matches_brief=1 ;;
esac

if [ "$matches_report" = "0" ] && [ "$matches_brief" = "0" ]; then
    emit_allow "seo-ranking-report: '$file_path' not a ranking report ; not logged"
    exit 0
fi

# Parse report file ---------------------------------------------------------
date_range='(unknown)'
delta_count=0

if [ -f "$file_path" ]; then
    # Date range — grep first matching line, strip the label, strip ** suffix.
    raw_date=$(grep -iE '^\s*(\*\*)?(date range|period)(\*\*)?\s*:' "$file_path" 2>/dev/null | head -n 1 || true)
    if [ -n "$raw_date" ]; then
        date_range=$(printf '%s' "$raw_date" | sed -E 's/^\s*(\*\*)?(date range|period)(\*\*)?\s*:\s*//I' | sed -E 's/\*+\s*$//' | sed -E 's/^\s+|\s+$//g')
        [ -z "$date_range" ] && date_range='(unknown)'
    fi

    # Delta count — count occurrences of +/-N position(s).
    delta_count=$(grep -oE '[+\-][0-9]+ position' "$file_path" 2>/dev/null | wc -l | tr -d ' ')
    [ -z "$delta_count" ] && delta_count=0
else
    fail_soft "report file not found post-write: $file_path"
fi

# Resolve project_id from _runtime/lead_project_id.txt ----------------------
project_id='?'
pid_file="$repo_root/_runtime/lead_project_id.txt"
if [ -f "$pid_file" ]; then
    project_id=$(tr -d '[:space:]' < "$pid_file")
    [ -z "$project_id" ] && project_id='?'
fi

# Append audit line ---------------------------------------------------------
log_path="$repo_root/_scratch/seo-audit-trail.log"
ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
line=$(printf '%s\tproject=%s\tfile=%s\tdate_range=%s\tdelta_count=%s\n' \
    "$ts" "$project_id" "$file_path" "$date_range" "$delta_count")

if ! printf '%s' "$line" >> "$log_path" 2>/dev/null; then
    fail_soft "could not append audit line to $log_path"
fi

emit_allow "seo-ranking-report: logged audit line for '$file_path' (deltas=$delta_count)"
exit 0
