# PostToolUse hook on Agent tool calls — injects a Karpathy Mode B
# "verify before PATCH" reminder into Lead's next conversation turn.
#
# Soft layer (feedback_karpathy_lane.md + AGENTS.md text) proven insufficient:
# Mode B recurred 5 times culminating in strike #5 (2026-05-17 dev DB wipe
# of ~1100 audit rows). Per AGENTS.md golden-rule escalation path, recurrence
# of any drift mode after 2026-05-17 warrants a hard hook on the affected
# surface. This hook targets the Agent surface — every specialist spawn now
# emits an unmissable reminder before Lead can naively flip Kanban state.
#
# Skips on Agent errors: the failure path already forces Lead to read the
# subagent output; the trust-the-success trap is the actual Mode B surface.
#
# Manual smoke (operator runs these post-merge to verify):
#
#   # SUCCESS case -> emits reminder JSON
#   '{"tool_response":{"is_error":false}}' | powershell -NoProfile -ExecutionPolicy Bypass -File .codex/hooks/agent-verify-before-patch.ps1
#   # -> expect stdout JSON with hookSpecificOutput.additionalContext containing "KARPATHY MODE B GUARD"
#
#   # ERROR case -> no output (Lead will already read the error)
#   '{"tool_response":{"is_error":true}}' | powershell -NoProfile -ExecutionPolicy Bypass -File .codex/hooks/agent-verify-before-patch.ps1
#   # -> expect no stdout, exit 0

$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json

# Skip on Agent errors — the reminder is for SUCCESSFUL agent runs that
# Lead might trust naively.
if ($payload.tool_response.is_error -eq $true) { exit 0 }

$reminder = @"
[KARPATHY MODE B GUARD] An Agent (specialist) just completed and reported.
Before any PATCH /api/tasks/{id} based on this report, independently verify
the specialist's "done" claim via the smallest concrete check:

  - Code edit claimed: Read the modified file, confirm the diff matches
  - Tests pass claimed: re-run the smallest selector that exercises the
    claim (NOT pytest -q — that wiped the DB on 2026-05-17)
  - File created claimed: ls / Glob for the path
  - DB row written claimed: GET /api/<resource>/<id>, confirm the field

In your next message, report the verification command + observable result
BEFORE flipping any Kanban state. Mode B strike #5 (2026-05-17) wiped
~1100 tasks because Lead trusted "854 passed" without independent verify.

See: feedback_karpathy_lane.md (Mode B section), context/projects/
agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md.
"@

$output = @{
    hookSpecificOutput = @{
        hookEventName     = "PostToolUse"
        additionalContext = $reminder
    }
} | ConvertTo-Json -Compress -Depth 4

Write-Output $output
exit 0
