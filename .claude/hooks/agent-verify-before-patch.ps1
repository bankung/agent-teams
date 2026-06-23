# PostToolUse hook on Agent tool calls — injects a Karpathy Mode B
# "verify before PATCH" reminder into Lead's next conversation turn.
#
# Soft layer (feedback_karpathy_lane.md + CLAUDE.md text) proven insufficient:
# Mode B recurred 5 times culminating in strike #5 (2026-05-17 dev DB wipe
# of ~1100 audit rows). Per CLAUDE.md golden-rule escalation path, recurrence
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
#   '{"tool_response":{"is_error":false}}' | powershell -NoProfile -ExecutionPolicy Bypass -File .claude/hooks/agent-verify-before-patch.ps1
#   # -> expect stdout JSON with hookSpecificOutput.additionalContext containing "KARPATHY MODE B GUARD"
#
#   # ERROR case -> no output (Lead will already read the error)
#   '{"tool_response":{"is_error":true}}' | powershell -NoProfile -ExecutionPolicy Bypass -File .claude/hooks/agent-verify-before-patch.ps1
#   # -> expect no stdout, exit 0

$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json

# Skip on Agent errors — the reminder is for SUCCESSFUL agent runs that
# Lead might trust naively.
if ($payload.tool_response.is_error -eq $true) { exit 0 }

# Trimmed to the actionable cue (T1/#2541): the verify-before-PATCH rule + the 4 check
# methods. The strike-#5 narrative + doc refs live in CLAUDE.md / feedback_karpathy_lane.md
# / the incident doc — no need to re-inject ~145 tokens of history into every spawn turn.
$reminder = @"
[KARPATHY MODE B GUARD] A specialist just reported done. Before any PATCH
/api/tasks/{id}, independently verify the claim with the smallest check —
code edit: Read the file; tests pass: re-run the selector (never pytest -q);
file created: Glob the path; DB row: GET /api/<resource>/<id>.
Report the verify command + observable result BEFORE flipping Kanban state.
"@

$output = @{
    hookSpecificOutput = @{
        hookEventName     = "PostToolUse"
        additionalContext = $reminder
    }
} | ConvertTo-Json -Compress -Depth 4

Write-Output $output
exit 0
