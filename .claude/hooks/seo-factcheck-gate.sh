#!/usr/bin/env bash
# SEO fact-check gate (PreToolUse) — YMYL citation enforcement (POSIX pair).
#
# Trigger: PreToolUse on Edit + Write tools when invoked by seo-strategist
# or content-seo-optimizer agents.
#
# See draft-seo-factcheck-gate.ps1 for the design rationale (YMYL keyword
# list, citation marker patterns, fail-open policy). This is the Linux/Mac
# container pair — same logic, jq + bash regex instead of PowerShell.
#
# Registration in .claude/settings.json (operator step):
#   "PreToolUse": [{"matcher": "Edit|Write",
#                   "hooks": [{"type":"command",
#                              "command":".claude/hooks/seo-factcheck-gate.sh"}]}]
#
# Kanban #1266 AC1.

set -u  # fail on unset vars; do NOT set -e (fail-open policy)

emit_decision() {
    # $1=decision (allow|deny), $2=reason
    local decision="$1"
    local reason="$2"
    # jq-encode the reason for safe JSON embedding.
    local reason_json
    reason_json=$(printf '%s' "$reason" | jq -Rs .)
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"%s","permissionDecisionReason":%s}}\n' \
        "$decision" "$reason_json"
}

fail_open() {
    local msg="$1"
    printf 'WARN: seo-factcheck-gate: %s ; failing open (allow)\n' "$msg" >&2
    emit_decision "allow" "seo-factcheck-gate fail-open: $msg"
    exit 0
}

# Read stdin payload --------------------------------------------------------
payload=$(cat 2>/dev/null) || fail_open "could not read stdin"
[ -z "$payload" ] && fail_open "empty PreToolUse payload"

# Require jq (fail-open if not installed).
command -v jq >/dev/null 2>&1 || fail_open "jq not installed"

tool_name=$(printf '%s' "$payload" | jq -r '.tool_name // empty')
[ -z "$tool_name" ] && fail_open "tool_name missing"

# Scope to invoking agent ---------------------------------------------------
agent_name=$(printf '%s' "$payload" | jq -r '.agent_name // .tool_input.subagent_type // empty')
if [ -n "$agent_name" ]; then
    case "$agent_name" in
        seo-strategist|content-seo-optimizer)
            ;;  # in scope; continue
        *)
            emit_decision "allow" "seo-factcheck-gate: agent '$agent_name' out of scope"
            exit 0
            ;;
    esac
fi

# Extract content to scan ---------------------------------------------------
case "$tool_name" in
    Write)
        content=$(printf '%s' "$payload" | jq -r '.tool_input.content // empty')
        ;;
    Edit)
        content=$(printf '%s' "$payload" | jq -r '.tool_input.new_string // empty')
        ;;
    *)
        emit_decision "allow" "seo-factcheck-gate: tool '$tool_name' not in scope"
        exit 0
        ;;
esac

if [ -z "$content" ]; then
    emit_decision "allow" "seo-factcheck-gate: empty content"
    exit 0
fi

# YMYL keyword scan (case-insensitive, word-boundary) -----------------------
ymyl_keywords=(
    "medical" "medication" "dosage" "treatment" "diagnosis" "prescription"
    "legal advice" "lawyer" "attorney"
    "financial advice" "investment return" "tax advice" "insurance claim"
)

matched_ymyl=""
content_lower=$(printf '%s' "$content" | tr '[:upper:]' '[:lower:]')
for kw in "${ymyl_keywords[@]}"; do
    # \b-word-boundary via grep -w only works for single tokens; use a regex
    # with [^a-z0-9] sentinels (or string boundary) for multi-word phrases.
    if printf '%s' "$content_lower" | grep -E "(^|[^a-z0-9])${kw}([^a-z0-9]|$)" >/dev/null 2>&1; then
        matched_ymyl="$kw"
        break
    fi
done

if [ -z "$matched_ymyl" ]; then
    emit_decision "allow" "seo-factcheck-gate: no YMYL keyword present"
    exit 0
fi

# Citation marker scan ------------------------------------------------------
citation_patterns=(
    '\[source:'
    '\[citation:'
    'https?://'
    '\[[0-9]+\]'
    '^[Ss]ource:'
    '[Ss]ources?:[[:space:]]'
    '[Cc]itation:[[:space:]]'
    '[Rr]eference:[[:space:]]'
)

has_citation=0
for pat in "${citation_patterns[@]}"; do
    if printf '%s' "$content" | grep -E "$pat" >/dev/null 2>&1; then
        has_citation=1
        break
    fi
done

if [ "$has_citation" = "1" ]; then
    emit_decision "allow" "seo-factcheck-gate: YMYL keyword '$matched_ymyl' present with citation marker"
    exit 0
fi

# YMYL present, no citation -> deny.
deny_reason="seo-factcheck-gate: YMYL keyword '$matched_ymyl' detected in content WITHOUT a citation marker.

YMYL (Your-Money-Your-Life) content — medical, legal, financial, civic — is
held to E-E-A-T standards by Google rater guidelines and by operator policy
in this repo. Misinformation in these areas can cause real-world harm, so
the gate requires a verifiable source for every YMYL claim.

To unblock, add ANY of the following to the same content blob:
  - A bracketed citation: [source: <name>] or [citation: <doi/url>]
  - An inline URL: https://...
  - A numbered footnote: [1]
  - A 'Source:' / 'Reference:' / 'Citation:' line

Kanban #1266 AC1. See _scratch/draft-seo-factcheck-gate.sh source for details."

emit_decision "deny" "$deny_reason"
exit 2
