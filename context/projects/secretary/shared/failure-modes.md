# Failure modes — secretary expected breaks + handling

> When secretary doesn't work, KNOWING WHY beats panic-debugging. This doc enumerates expected breaks + Lead's standard handling + operator's expected role.
>
> Triage rule: **fail loud, fail early, never fake success**. Better to surface "Chrome session expired" than silently retry with stale cookies and look successful.

---

## Category 1 — Bootstrap / connection failures

### F1.1 — `secretary ครับ` returns "API at :8456 unreachable"

**Symptom:** Lead's ritual Step 1 fails on the GET /api/projects/by-name call.

**Probable cause:** Docker compose stack not running on the host.

**Operator action:**
```powershell
docker compose -p agent-teams ps
docker compose -p agent-teams up -d   # if down
```

**Lead's handling:** halt ritual; surface 1-line "API down — start docker compose on host." Don't retry without operator confirmation.

### F1.2 — `secretary ครับ` returns "project secretary not found (404)"

**Symptom:** API responds but secretary project missing from DB.

**Probable cause:** Bootstrap was on a different machine / Postgres data lost / restored to pre-bootstrap snapshot.

**Operator action:** re-create the project — either re-run the bootstrap (Lead can POST /api/projects from the agent-teams session) OR restore from backup if one exists.

**Lead's handling:** halt; surface "project missing — re-create or restore backup?"

### F1.3 — Chrome MCP shows "no connected browsers"

**Symptom:** `list_connected_browsers` returns empty list.

**Probable cause:**
- Chrome extension not installed
- Extension installed but not connected to this Claude Code session
- Chrome closed entirely
- Wrong Chrome profile (operator logged into Gmail on profile A; extension talks to profile B)

**Operator action:**
1. Open Chrome (the profile with logged-in accounts)
2. Visit `chrome://extensions` → confirm "Claude in Chrome" enabled
3. Click extension icon → look for "Connect to Claude Code" or similar
4. Verify connection by asking Lead `take a screenshot`

**Lead's handling:** halt before spawning any browser-dependent workflow; surface checklist above.

### F1.4 — Chrome MCP connects but operator's session is logged out

**Symptom:** Chrome MCP navigates to Gmail / LinkedIn / JobsDB, sees login page.

**Probable cause:** Cookies expired (typical: 30-90 day re-login on Gmail), or operator was force-logged-out by service.

**Operator action:** open the service in browser, log in manually, then retry the secretary workflow.

**Lead's handling:** secretary's pattern: ask `read_page` for current URL → if URL contains `accounts.google.com/signin` or `linkedin.com/login` → halt + report which service needs re-login.

---

## Category 2 — Missing operator_context

### F2.1 — Workflow request without required PII

**Symptom:** Operator types `triage inbox` without inline context AND `general/operator-context.md` doesn't exist.

**Probable cause:** Operator forgot pre-flight OR is using a fresh machine.

**Lead's handling:** validate context BEFORE spawning. Surface missing-fields list:

```
Need to triage but missing:
  - name (e.g. "Thanit N.")
  - signature (e.g. "Best,\nThanit")
  - priority_senders (e.g. [boss@x, hr@x])

Type inline or fill general/operator-context.md.
```

### F2.2 — `general/operator-context.md` exists but malformed

**Symptom:** Lead reads file, fails to parse YAML-ish content.

**Lead's handling:** surface error with the offending line excerpt + ask operator to fix or type inline override for this session.

### F2.3 — File has fields but they conflict with inline

**Symptom:** File says `target_roles: [CTO]`; inline says `target_roles: [Staff Engineer]`.

**Lead's handling:** inline OVERRIDES file. No error. Lead may surface 1-line "(inline override applied)" so operator knows file wasn't consulted for that field.

---

## Category 3 — Workflow-time browser failures

### F3.1 — Page took >30s to load

**Probable cause:** slow network / overloaded service / Chrome MCP timeout.

**Lead's handling:** ONE retry with 2x timeout (60s). If second attempt fails → halt + report "service unresponsive — try again later or switch to direct WebFetch?".

### F3.2 — Element not found (DOM changed)

**Symptom:** secretary expected `<button>Archive</button>` to be findable, but DOM shape changed.

**Probable cause:** Gmail / LinkedIn / JobsDB UI updated overnight.

**Lead's handling:**
- ONE retry with broader selector (e.g., aria-label search)
- If second attempt fails → halt + report "UI changed — operator must complete manually for this workflow; secretary can resume on next session if UI stabilizes".
- File Kanban task: `bug: <service> UI selector broke on YYYY-MM-DD — investigate`

### F3.3 — Captcha appears

**Symptom:** read_page returns captcha challenge HTML.

**Probable cause:** rate-limit / bot detection / unusual traffic pattern.

**Lead's handling:** halt IMMEDIATELY. Surface "Captcha on <service> — solve manually in your browser then say `resume`". DO NOT try to solve.

### F3.4 — Rate limit hit

**Symptom:** service returns 429 / "Too many requests" page.

**Probable cause:** secretary scanned too many listings in a session.

**Lead's handling:** halt. Report `X listings processed before rate limit; M remaining; back off and retry in N minutes?`. Update per-run-cap recommendation in `job-criteria.md` if pattern recurs.

### F3.5 — Service returns "account suspended" or "account restricted"

**Symptom:** unexpected error page suggesting account is in trouble.

**Lead's handling:** halt IMMEDIATELY + escalate to operator with full screenshot. DO NOT continue any workflow on that service. Could be:
- Operator's account flagged for unusual activity (secretary's pattern may have triggered)
- Pre-existing issue operator wasn't aware of
- Service-side false-positive

---

## Category 4 — Drafting failures

### F4.1 — Draft fails voice.md anti-pattern self-check (1st attempt)

**Symptom:** secretary drafted a reply / post / cover letter; self-check found banned phrase or anti-pattern.

**Lead's handling:** secretary redrafts ONCE.

### F4.2 — Draft fails self-check 2nd time

**Probable cause:** topic or context inherently triggers the anti-pattern (e.g., asking about "passion" in a job application that explicitly uses the word).

**Lead's handling:** halt + escalate `can't draft <task> within voice constraints; operator review or override needed`. Surface the failing draft + which check it failed.

### F4.3 — Cover letter research returns no usable hook

**Symptom:** secretary spent 5 min on company about / news / blog; couldn't find anything specific.

**Lead's handling:** use generic-warm template + flag `[low customization possible]` in the HITL pause. Operator decides whether to submit anyway.

### F4.4 — LinkedIn topic candidates: 0 usable

**Symptom:** secretary scanned all RSS / sources; nothing fits operator's themes for 48h window.

**Lead's handling:** report "no candidates this window — try expanding sources, picking your own topic, or skip today".

---

## Category 5 — HITL flow failures

### F5.1 — Operator answers HITL with ambiguous input

Example: operator types `edit` without specifying what to edit.

**Lead's handling:** ask 1 clarifying question (`edit how? paste your version or describe`) — limit to ONE clarifier. If still ambiguous → halt + ask operator to retry the workflow.

### F5.2 — Operator approves but secretary's submit/post failed mid-execution

**Symptom:** operator said `approve`; secretary clicked Submit; form returned error.

**Lead's handling:** report exact error + draft state preserved + ask `retry with same draft / edit draft first / abort?`. Never silently retry submit (could double-submit applications).

### F5.3 — Operator types `abort` mid-workflow

**Lead's handling:** secretary stops at next safe checkpoint (between actions, not mid-action). Drafts preserved in `general/{date}/`. Report `aborted after N actions; M drafts saved; resume with <command>`.

### F5.4 — Session disconnects mid-HITL (operator's mobile loses signal)

**Symptom:** Lead waiting on operator answer; session timeout.

**Lead's handling:** state already serialized (drafts in `general/`, Kanban tasks at BLOCKED if any were filed). When operator reconnects: type `resume`; Lead reads in-progress state + offers to continue.

---

## Category 6 — Cross-system / data integrity failures

### F6.1 — Approval policy disagrees with workflow expectation

Example: workflow says "submit application requires approval" but project policy says auto-approve (operator misconfigured).

**Lead's handling:** WORKFLOW BRIEF WINS for safety. Even if policy says auto-approve, secretary halts on `submit application` per workflow brief's HITL rule. Surface `policy says auto-approve but workflow requires HITL — keep HITL for safety. Update policy if intentional.`

### F6.2 — Budget cap hit mid-workflow

**Symptom:** `estimated_cost_usd` exceeds `budget_daily_usd` during a session.

**Lead's handling:** Cost budget enforcement (#951) auto-pauses run_mode → secretary halts. Surface `daily budget $5 hit at $X.YY — raise budget or wait till tomorrow?`.

### F6.3 — Health monitor auto-paused the task

**Symptom:** Lead detects task transitioned to run_mode=manual mid-workflow.

**Probable cause:** Health monitor detector fired (stale state, repeated retries, token burn without progress).

**Lead's handling:** read `health_alert` JSONB on the task; surface to operator: `health monitor paused because <detector>; <evidence>. resume? investigate? skip?`

### F6.4 — Auditor classifies the secretary's work as ESCALATE

**Symptom:** secretary's specialist sub-spawn (e.g., dev-researcher for company-research) returned output the auditor flagged as escalate.

**Lead's handling:** auditor's HITL request_user_input fires → BLOCKED state with question_payload. Lead surfaces to operator in chat. Operator answers → resume.

---

## Category 7 — Operator-side process failures

### F7.1 — Operator forgets to commit operator-context.md edits

**Symptom:** operator changed file in editor but didn't save / OS hasn't flushed.

**Lead's handling:** Lead reads file at session start; if file mtime < operator's stated edit time + 5s, Lead asks `file seems older than your recent edit — did you save?`.

### F7.2 — Operator's Chrome profile != Chrome MCP profile

**Symptom:** Chrome MCP connects but to a profile where operator isn't logged in.

**Lead's handling:** see F1.3 + F1.4. Lead's read_page returns login screen → halt + ask operator to verify they're using the right Chrome profile.

### F7.3 — Operator on mobile when Chrome MCP needs desktop interaction

**Symptom:** workflow asks operator to "log in to JobsDB then say resume"; operator is on phone with no JobsDB session.

**Lead's handling:** halt + surface `Chrome MCP runs on host machine — need desktop access. Defer workflow until you're at the computer?`.

---

## Severity ladder

| Severity | Examples | Lead behavior |
|---|---|---|
| **info** | Draft passed self-check; auto-archived 5 emails | Silent (counts in digest only) |
| **warn** | 1 draft retried after self-check fail; Chrome MCP slow | Surface in chat 1 line |
| **error** | Submit failed mid-execution; rate limit hit | Halt workflow + escalate |
| **critical** | Account suspended message; captcha; payment-related | Halt EVERYTHING + escalate immediately + DO NOT continue any service |

## Recovery commands operator can type

```
resume                    # continue paused workflow from last checkpoint
retry                     # retry the last failed action with same context
retry with edit           # operator pastes corrected draft; secretary uses it
abort                     # stop current workflow; preserve drafts
skip                      # skip current action; continue with next
reset state               # clear in-session memory + cursors (for next day)
budget                    # GET cost vs cap; surface in chat
status                    # in-flight HITL + drafts + pending actions
clear chrome              # close all Chrome MCP tabs except active
help                      # surface workflow cheatsheet
```

## What operator MUST handle (not Lead's job)

- Chrome extension install / connect
- Login to Gmail / LinkedIn / JobsDB / etc.
- Resume PDF on disk + remembering path
- Approving / rejecting HITL pauses
- Captcha solving
- Account suspension follow-up
- Salary negotiation framing (Lead surfaces; operator decides)
- Final say on every external action

## What Lead handles (operator doesn't need to)

- Project bootstrap + binding
- Pre-flight checks
- Reading operator-context.md
- Spawning secretary with extracted PII
- Routing HITL responses back to secretary
- Composing digest from secretary's outputs
- Tracking budget / token usage
- Filing Kanban tasks for surfaced issues
- Logging errors + retries to audit history

## What secretary handles (Lead doesn't need to)

- Chrome MCP navigation / form interaction
- Email classification per email-rules.md
- Job scoring per job-criteria.md
- Cover letter drafting per voice.md
- LinkedIn topic research + drafting
- Self-checks (voice / quality gates)
- Per-run state file writes (drafts, logs)
- Reporting back to Lead with summary

## Anti-patterns to AVOID

- ❌ Silently retry browser actions > 1x
- ❌ Submit / post without HITL even if policy seems to allow
- ❌ "Estimate" results when actual action failed
- ❌ Use generic templates without flagging "low customization" to operator
- ❌ Continue after rate-limit / captcha / suspension warning
- ❌ Echo operator PII back in chat unnecessarily (mobile screenshot leak risk)
- ❌ Send multiple parallel browser actions (LinkedIn/JobsDB rate-detection)
- ❌ Cache PII across sessions in Lead-side memory (must come from operator_context channel)
