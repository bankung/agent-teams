#!/usr/bin/env bash
# DRAFT (Kanban #1271 AC3) — POSIX twin of draft-data-dashboard-publish.ps1.
# PostToolUse on Write for dashboard-designer. Scans content for PII / finance
# / health keywords; emits stderr WARN + audit-log entry. Never blocks.
#
# DRAFT ONLY — do NOT install. Lead handles agent file + .claude/hooks/ placement
# per feedback_claude_dir_humans_only.md.
#
# Registration snippet (Lead writes into .claude/agents/<agent>.md frontmatter):
#   hooks:
#     PostToolUse:
#       - matcher: Write
#         hooks:
#           - type: command
#             command: bash .claude/hooks/data-dashboard-publish.sh
#
# Audit log: _scratch/data-audit-trail.log (future: POST /api/audit-events).
# Fail-soft on any parse error.

set +e  # never let a parse hiccup block analyst work

AUDIT_LOG="_scratch/data-audit-trail.log"

# Minimum-viable PII / financial / health vocabulary (12 keywords).
SENSITIVE_KEYWORDS=(
    "email"
    "phone"
    "ssn"
    "passport"
    "credit_card"
    "salary"
    "bank_account"
    "patient_id"
    "diagnosis"
    "prescription"
    "medical_record_no"
    "date_of_birth"
)

emit_allow() {
    printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","permissionDecision":"allow"}}\n'
}

payload="$(cat 2>/dev/null)"
if [ -z "$payload" ]; then emit_allow; exit 0; fi

if ! command -v jq >/dev/null 2>&1; then emit_allow; exit 0; fi

tool_name="$(echo "$payload" | jq -r '.tool_name // empty' 2>/dev/null)"
if [ "$tool_name" != "Write" ]; then emit_allow; exit 0; fi

success="$(echo "$payload" | jq -r '.tool_response.success // "true"' 2>/dev/null)"
if [ "$success" = "false" ]; then emit_allow; exit 0; fi

file_path="$(echo "$payload" | jq -r '.tool_input.file_path // empty' 2>/dev/null)"
if [ -z "$file_path" ]; then emit_allow; exit 0; fi

# Path pattern scope.
is_dashboard=0
is_analytics_spec=0
echo "$file_path" | grep -qiE 'dashboard.*\.md$' && is_dashboard=1
echo "$file_path" | grep -qiE 'data-analytics.*spec.*\.md$' && is_analytics_spec=1
if [ "$is_dashboard" -eq 0 ] && [ "$is_analytics_spec" -eq 0 ]; then
    emit_allow
    exit 0
fi

# Extract content. Prefer tool_input.content; fallback to reading file.
content="$(echo "$payload" | jq -r '.tool_input.content // empty' 2>/dev/null)"
if [ -z "$content" ] && [ -f "$file_path" ]; then
    content="$(cat "$file_path" 2>/dev/null)"
fi

if [ -z "$content" ]; then emit_allow; exit 0; fi

# Scan for sensitive keywords (word-boundary, case-insensitive).
found=()
for kw in "${SENSITIVE_KEYWORDS[@]}"; do
    if echo "$content" | grep -qiE "\\b${kw}\\b"; then
        found+=("$kw")
    fi
done

if [ "${#found[@]}" -gt 0 ]; then
    ts="$(date -u +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)"
    found_list="$(IFS=', '; echo "${found[*]}")"
    warn_line="[sensitive-data-touch] $ts file=$file_path keywords=$found_list"

    # stderr WARN.
    echo "$warn_line" >&2

    # Append to audit log; fail-soft.
    audit_dir="$(dirname "$AUDIT_LOG" 2>/dev/null)"
    if [ -n "$audit_dir" ] && [ ! -d "$audit_dir" ]; then
        mkdir -p "$audit_dir" 2>/dev/null
    fi
    echo "$warn_line" >> "$AUDIT_LOG" 2>/dev/null
    # TODO (Kanban #?): POST to /api/audit-events once available.
fi

emit_allow
exit 0
