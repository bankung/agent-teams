# Live smoke runbook — Kanban #1274 AC4/AC5

The Pattern 5 hook + settings.json wiring exist. The OPEN QUESTION the smoke
answers: does the Claude Code auto-mode harness classifier defer to the
hook's `permissionDecision=allow` for a tool call that the classifier would
otherwise block?

**Cascade impact.** A positive answer (harness defers) unblocks Patterns 3
and 4 in the design doc — they all rely on the same hook-deferral
mechanism. A negative answer collapses the entire hook-based authorization
strategy and forces a redesign around Pattern 1 (operator-in-the-last-click)
+ Pattern 2 (settings allowlist) only. This is the single highest-value
experiment in the chain.

## Pre-conditions

1. Lead has promoted `_scratch/draft-approval-policies-gate.ps1` to
   `.claude/hooks/approval-policies-gate.ps1`.
2. Lead has applied the three matcher additions per
   `_scratch/draft-settings-additions.md` to `.claude/settings.json`.
3. Claude Code session has been restarted post-promotion so the new hook
   registrations load (per `[[feedback-agents-load-at-start]]`).
4. The bound project (`_runtime/lead_project_id.txt`) is one of:
   - `1` (agent-teams), or
   - the secretary project (look up via `curl /api/projects/by-name/secretary`).
   The secretary project is recommended for the LinkedIn target because its
   pipeline already has the LinkedIn workflow scaffolded.

## Step 1 — author a permissive rule on the target project

Choose a target that is **known classifier-gated** (the design doc lists the
LinkedIn post Submit-click from #1201 as the cleanest case). Patch the
project's `approval_policies` to include ONE rule that should auto-approve:

```json
{
  "rules": [
    {
      "name": "smoke-1274 linkedin allow",
      "match": {
        "tool_name": "WebFetch",
        "target_url_pattern": "^https://(www\\.)?linkedin\\.com/.*"
      },
      "action": "auto_approve",
      "reason": "smoke-1274 — testing whether harness honors hook allow"
    }
  ]
}
```

Apply via PATCH (replace `<project-id>` with the target project's id):

```bash
curl --silent -X PATCH \
  -H "Content-Type: application/json" \
  -H "X-Project-Id: <project-id>" \
  -d '{"approval_policies":{"rules":[{"name":"smoke-1274 linkedin allow","match":{"tool_name":"WebFetch","target_url_pattern":"^https://(www\\.)?linkedin\\.com/.*"},"action":"auto_approve","reason":"smoke-1274 — testing whether harness honors hook allow"}]}}' \
  "http://localhost:8456/api/projects/<project-id>"
```

Verify:

```bash
curl --silent -H "X-Project-Id: <project-id>" \
  "http://localhost:8456/api/projects/<project-id>" | findstr approval_policies
```

## Step 2 — pre-flight the hook against the target

Confirm the hook returns `allow` when invoked manually with the same shape
the harness will send for a LinkedIn `WebFetch`:

```powershell
$payload = '{"tool_name":"WebFetch","tool_input":{"url":"https://linkedin.com/posts/create","prompt":"smoke"}}'
$payload | powershell -NoProfile -ExecutionPolicy Bypass -File ".claude/hooks/approval-policies-gate.ps1"
```

Expected stdout:
```json
{"hookSpecificOutput":{"permissionDecision":"allow","hookEventName":"PreToolUse","permissionDecisionReason":"approval-policies-gate: smoke-1274 linkedin allow — smoke-1274 — testing whether harness honors hook allow"}}
```

If you see `ask` instead of `allow` — the bound-project file or API path
is wrong; debug before the live smoke.

## Step 3 — run the live smoke

In the same Claude Code session, trigger a `WebFetch` to a LinkedIn URL.
Easiest path: invoke through secretary's existing pipeline, OR manually
in chat:

> Lead, please WebFetch `https://linkedin.com/posts/create` and report the
> response status.

Observe the harness behavior. There are three possible outcomes:

### Outcome A — `permissionDecision=allow` honored, no classifier block

The harness honored the hook's `allow`. **Pattern 5 works.** The
design-doc Patterns 3 + 4 are now unblocked. File followups:
- Close #1274 with `acceptance_criteria` AC4 + AC5 → passed.
- Unblock #1275 (Stub B Pattern 4) for implementation.
- Unblock #1269 + #1271 for policy authoring.

### Outcome B — harness still gates the call despite hook `allow`

The harness ignores PreToolUse hook decisions for classifier-gated tool
calls. **Pattern 5 fails the harness-deferral test.** Capture the exact
transcript (the harness's gate prompt text + Lead's view). File a new
task:
- Title: `[authz-chain] Pattern 5 hook does NOT override harness classifier — investigate alternative gate layer`
- Body should reference #1274's smoke transcript + the design doc section
  9 (out-of-scope: modifying the classifier) — the followup explores
  whether a browser-extension-side or system-level authorization layer
  could substitute.
- Patterns 3 + 4 STAY blocked pending the alternative-layer decision.

### Outcome C — hook didn't fire / fired wrong / inconclusive

Hook stderr WARN line shows up but no allow JSON, or harness shows no
gate prompt at all (suggests the call went through normal allowlist path
already). Diagnose:
- Confirm `.claude/settings.json` PreToolUse registration is correct via
  `cat .claude/settings.json | findstr approval-policies-gate`.
- Confirm `_runtime/lead_project_id.txt` matches the project that has the
  rule set.
- Confirm a Claude Code session restart happened after the
  settings.json edit.
Re-run the smoke once causes are cleared.

## Step 4 — capture transcript + result

Whatever the outcome, paste the relevant transcript snippet (5–20 lines:
hook prompt, hook output, harness response, Lead's tool result) into
the Kanban task `#1274`:

```bash
curl --silent -X PATCH \
  -H "Content-Type: application/json" \
  -H "X-Project-Id: 1" \
  -d '{"status_change_reason":"AC4 smoke transcript: <paste>","acceptance_criteria":[... AC4 + AC5 updated ...]}' \
  "http://localhost:8456/api/tasks/1274"
```

The AC4 + AC5 updates depend on the outcome:
- **Outcome A** → AC4 `status=passed`, AC5 `status=passed`, both `verified_by=user`, notes reference the followup ids.
- **Outcome B** → AC4 `status=passed` (smoke ran), AC5 `status=failed` (followup decision: investigate alternative layer) + filed followup task id in notes. Hold task in REVIEW (process_status=4) until cascade decisions made.
- **Outcome C** → AC4 `status=pending` until re-run; do not flip the task done.

## Step 5 — revert the smoke rule (after capture)

The permissive Pattern 5 rule from Step 1 was added for this experiment
only. If the target project should not retain the auto-approve LinkedIn
rule (it almost certainly should not), revert via:

```bash
curl --silent -X PATCH \
  -H "Content-Type: application/json" \
  -H "X-Project-Id: <project-id>" \
  -d '{"approval_policies":null}' \
  "http://localhost:8456/api/projects/<project-id>"
```

If the project already had other `approval_policies` rules before Step 1,
restore the prior shape instead of nulling.
