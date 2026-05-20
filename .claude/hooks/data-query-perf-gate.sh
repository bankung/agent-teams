#!/usr/bin/env bash
# DRAFT (Kanban #1271 AC3) — POSIX twin of draft-data-query-perf-gate.ps1.
# PreToolUse on Bash for sql-optimizer / bi-analyst / analytics-platform-integrator.
# Emits requires-attention when a SELECT looks unbounded (no LIMIT, no EXPLAIN).
#
# DRAFT ONLY — do NOT install. Lead handles agent file + .claude/hooks/ placement
# per feedback_claude_dir_humans_only.md.
#
# Registration snippet (Lead writes into .claude/agents/<agent>.md frontmatter):
#   hooks:
#     PreToolUse:
#       - matcher: Bash
#         hooks:
#           - type: command
#             command: bash .claude/hooks/data-query-perf-gate.sh
#
# Fail-open on any internal error.

set +e  # never let a parse hiccup halt analyst work

QUERY_TIMEOUT_SECONDS=10

# Read full stdin payload.
payload="$(cat 2>/dev/null)"
if [ -z "$payload" ]; then exit 0; fi

# Need jq for JSON parsing; fail-open if missing.
if ! command -v jq >/dev/null 2>&1; then exit 0; fi

tool_name="$(echo "$payload" | jq -r '.tool_name // empty' 2>/dev/null)"
if [ "$tool_name" != "Bash" ]; then exit 0; fi

cmd="$(echo "$payload" | jq -r '.tool_input.command // empty' 2>/dev/null)"
if [ -z "$cmd" ]; then exit 0; fi

# Agent-name scope (multi-key fallback).
agent_name="$(echo "$payload" | jq -r '.agent_name // .subagent_type // .agent // .agentName // empty' 2>/dev/null)"
if [ -n "$agent_name" ]; then
    case "$agent_name" in
        sql-optimizer|bi-analyst|analytics-platform-integrator) ;;
        *) exit 0 ;;
    esac
fi

# ---------------------------------------------------------------------------
# Extract embedded SQL from common surfaces. We use grep -oP for each
# pattern; first match wins.
# ---------------------------------------------------------------------------
query_body=""

# psql -c "..." or psql -c '...'
query_body="$(echo "$cmd" | grep -oP '(?i)\bpsql\b[^"'\'']*-c\s+["'\''](?<q>[^"'\'']+)' | head -n1 | sed -E 's/.*-c[[:space:]]+["'\'']//')"

if [ -z "$query_body" ]; then
    query_body="$(echo "$cmd" | grep -oP '(?i)\b(bigquery|bq)\s+query\s+["'\''][^"'\'']+' | head -n1 | sed -E 's/.*query[[:space:]]+["'\'']//')"
fi

if [ -z "$query_body" ]; then
    query_body="$(echo "$cmd" | grep -oP '(?i)\bsnowsql\b[^"'\'']*-q\s+["'\''][^"'\'']+' | head -n1 | sed -E 's/.*-q[[:space:]]+["'\'']//')"
fi

if [ -z "$query_body" ]; then
    py_body="$(echo "$cmd" | grep -oP '(?i)\bpython\b[^"'\'']*-c\s+["'\''][^"'\'']+' | head -n1 | sed -E 's/.*-c[[:space:]]+["'\'']//')"
    if [ -n "$py_body" ] && echo "$py_body" | grep -qiE '\bexecute\s*\(' && echo "$py_body" | grep -qiE '\bSELECT\b'; then
        query_body="$py_body"
    fi
fi

if [ -z "$query_body" ]; then exit 0; fi

# Skip if operator prefixed EXPLAIN.
if echo "$query_body" | grep -qiE '^[[:space:]]*EXPLAIN\b'; then exit 0; fi

# Must be a SELECT ... FROM ...
if ! echo "$query_body" | grep -qiE '\bSELECT\b.*\bFROM\b'; then exit 0; fi

# DDL/DML carve-out — handled by block-raw-sql-dml hook.
if echo "$query_body" | grep -qiE '\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE)\b'; then exit 0; fi

# LIMIT analysis.
limit_n="$(echo "$query_body" | grep -oiE '\bLIMIT[[:space:]]+[0-9]+' | head -n1 | grep -oE '[0-9]+')"
if [ -n "$limit_n" ] && [ "$limit_n" -le 1000 ]; then exit 0; fi

# Smell -> emit requires-attention JSON.
query_preview="$(echo "$query_body" | cut -c1-80)"

reason="data-query-perf-gate: SELECT looks unbounded (no LIMIT or LIMIT > 1000, no EXPLAIN ANALYZE prefix). Default soft threshold N=${QUERY_TIMEOUT_SECONDS} seconds.

Before running this query, run EXPLAIN ANALYZE first to confirm the plan fits the threshold:

    psql -c \"EXPLAIN ANALYZE ${query_preview}...\"

If the planner shows total cost / time within budget, re-run the original query; this gate will allow it on the second invocation because the prior turn established the EXPLAIN evidence (operator judgement).

See: data-analytics team standards for query budget conventions."

jq -n \
    --arg reason "$reason" \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "requires-attention", permissionDecisionReason: $reason}}' \
    2>/dev/null

exit 0
