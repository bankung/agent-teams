# Full-auto methodology — dev team

**Status:** MVP (Kanban #786 + #787, 2026-05-12). Activated only on projects with `LEAD_AUTOPICKUP=1` env var at Lead bootstrap. agent-teams (the dogfood) does NOT enable this — agent-teams stays interactive so the methodology stays under human review.

This file covers MVP-3 (auto-pickup loop) and MVP-4 (top-5 decision matrix) together because the two work as a pair: pickup is what keeps the loop running, the matrix is what keeps the loop unattended. Polish for both lives under umbrella tasks #776 + #781; this file is the MVP slice only.

---

## When this methodology fires

A Lead session is "full-auto" when ALL three conditions hold:

1. The session bootstrapped with `LEAD_AUTOPICKUP=1` in env at start time.
2. The session is bound to a project whose `.claude/settings.json` wires in `.claude/hooks/auto-approve-safe-writes.ps1` (Kanban #784) for safe-zone Write/Edit auto-approval.
3. Lead has announced the mode at bootstrap with the announce string below.

If any of the 3 is missing, default to interactive (per-task user-in-the-loop). Do NOT partially enable.

## Bootstrap announce

When `LEAD_AUTOPICKUP=1` is detected, Lead's bootstrap message MUST include:

> "Session bound to `<name>` (team=`<team>`, id=`<id>`, path=`<working_path>`, **auto-pickup=ON, idle-policy=`<wakeup-30 | stop>`**)."

Idle policy defaults to `wakeup-30` unless `LEAD_AUTOIDLE=stop` is set.

The announce is not optional — it gives the user reading the transcript a clear signal that Lead is in unattended mode, so any subsequent silence is expected (not a hang).

---

## MVP-3 — The pickup loop

After each task closes (i.e., Lead sets `process_status=5`), Lead automatically queries the backend for the next eligible task:

```
curl --silent "http://localhost:8456/api/tasks?project_id=<p>&process_status=1&order_by=priority,created_at" -H "X-Project-Id: <p>"
```

From the response, Lead picks the first row that satisfies ALL:

- `task_kind != "human"` (only agent-eligible kinds — current schema's `task_kind` is `'ai' | 'human'` so picks must be `'ai'`)
- `halt_reason IS NULL` (Kanban #785 — halted tasks are explicitly excluded)
- `status = 1` (active, not soft-deleted; default of the `?status=1` filter)

**Picked-row flow:**
- Lead PATCHes the row to `process_status=3` (in_progress) + `started_at=<UTC now>`.
- Lead announces: `Auto-picking up #N — <title>`.
- Lead begins the normal lifecycle for the role chain stated in that task's description.

**Empty-response flow:**
- Lead announces: `Queue empty — entering idle (policy=<wakeup-30 | stop>)`.
- If policy is `wakeup-30`: Lead schedules a 30-minute wakeup that re-runs the pickup query. On wakeup, if still empty, schedule again. (Indefinite — user halts the session manually.)
- If policy is `stop`: Lead halts the session. User must restart.

**User interruption:**
At any moment the user can send a message; Lead aborts the auto-pickup loop until the user explicitly re-enables (typically by starting a new session, since `LEAD_AUTOPICKUP` is read at bootstrap).

**Filtering note:** the auto-pickup query does NOT include a `blocked_by` predicate today. `tasks.blocked_by` is P3-deferred (Kanban #771). When/if that lands, this query needs a filter update — flag in this file at that time.

---

## MVP-4 — In-flight decision matrix (top-5)

When mid-flight (specialist returned, reviewer returned, tester returned), Lead routinely faces judgment calls that interactive Lead asks the user to resolve. In full-auto, the user is not available. Lead applies these defaults:

### 1. Reviewer WARN

**Detection signal:** dev-reviewer report contains `## WARN-N` sections.

**Default action:** For EACH WARN, evaluate the two conditions:

| condition | true → FOLD | false → FILE FOLLOW-UP |
|---|---|---|
| Proposed fix ≤ 10 LOC | continue → | break |
| Does NOT change a public API signature, wire contract, or shared/ document | continue → | break |

- **FOLD**: spawn the relevant specialist (typically dev-backend or dev-frontend) with the WARN as a fold brief, applied in the same task's slice. Re-run reviewer briefly to confirm the fold doesn't regress.
- **FILE FOLLOW-UP**: open a new Kanban task (`task_kind='human'`, priority=2, `parent_task_id = <current task>`) summarizing the WARN + the proposed fix. Close the current task with the WARN noted in the commit body. The follow-up enters the queue for later interactive review.

**Why this default:** tiny fixes are cheaper inline (one specialist round-trip); significant fixes deserve their own spawn brief + a fresh reviewer pass.

### 2. Reviewer NIT

**Detection signal:** dev-reviewer report contains `## NIT-N` sections.

**Default action:** Always defer. Append the NIT to a single consolidated follow-up task (filed on first NIT of the slice; subsequent NITs in the same slice append to that same task). NEVER fold in auto.

**Why this default:** NIT polish is judgment-heavy (style preferences, naming choices, comment clarity). Safer to batch and let the user decide later than to auto-apply a polish that turns out to be wrong.

### 3. Tester proposes new standard (strike #1)

**Detection signal:** dev-tester report contains a `## Standards insights` section proposing a new rule for `context/standards/**`.

**Default action:** Log the proposal to `_scratch/standards-proposal-<topic>.md`. Append a bullet under "Standards-candidates" in the project's `shared/decisions.md`. **NEVER auto-write to `context/standards/**`** — humans-only invariant holds in auto mode too. Mark for user review at the next interactive session.

**Why this default:** standards persistence across projects is a human judgment call. Codification happens on strike #2 minimum (dogfood-pollution discipline). One strike alone could be a misread.

### 4. Validator semantics ambiguity (Option A vs Option B)

**Detection signal:** Lead or specialist surfaces 2+ valid implementations with different wire contracts (typical pattern: a `## Open questions` entry asking "which one?" — or a reviewer WARN that proposes 2 distinct fix paths).

**Default action:** **HALT.** PATCH the current task with:

```json
{"halt_reason": "Option A/B decision needed: <one-line summary of choices>"}
```

Stop the lifecycle. Commit any partial work on the current branch with a message body explaining the halt and the unresolved question.

The task stays at its current `process_status` (typically 3 = in_progress); the auto-pickup query skips it because `halt_reason IS NOT NULL`. User unhalts after deciding (see Unhalt flow below).

**Why this default:** wire contracts are user-facing decisions. Wrong choice cascades through every consumer. Better to halt one task than to ship the wrong contract.

### 5. Cross-task scope creep

**Detection signal:** specialist proposes touching files outside the spec's `## Scope`, OR proposes a fundamentally different approach than the spec (e.g., "I want to refactor X while I'm here", or "this would be easier if we also did Y").

**Default action:** **HALT.** PATCH the current task with:

```json
{"halt_reason": "Scope creep proposed: <specialist's proposal in 1 line>"}
```

Stop the lifecycle. Commit any in-scope partial work.

**Why this default:** scope drift hides budget. User must accept the new scope explicitly (either widen the spec or file a separate task).

---

## Halt format (shared across decisions 4 + 5)

When applying HALT defaults:

1. PATCH `tasks/{id}` with `{"halt_reason": "<reason>"}` (Kanban #785 schema). Reason MUST start with one of: `"Option A/B decision needed:"`, `"Scope creep proposed:"`, or a future matrix-extending prefix. The prefix is the categorical signal; the rest is the human-readable specifics.
2. Process_status stays at whatever it was (typically 3 = in_progress). Halt is orthogonal to lifecycle code, same pattern as `is_pending` (Kanban #750).
3. Auto-pickup query skips this row from now on.
4. Commit any in-scope partial work on the current branch with the message body explaining the halt and quoting the unresolved question verbatim.
5. The next task in the queue (if any) is picked up automatically by the loop (the halt does not stop the session, just this one task).

## Unhalt flow (user-driven, not Lead-driven)

User unhalts a halted task by:

1. Reviewing the halt_reason + commit body to understand the unresolved question.
2. Editing the task description (or shared/decisions.md) to resolve the ambiguity / accept the scope change.
3. PATCH `tasks/{id}` with `{"halt_reason": null}`.

The auto-pickup query will then pick the row up on the next loop iteration. No Lead action required between PATCH and next pickup; the loop's idle-policy timer (`wakeup-30`) will trigger naturally.

---

## Out of scope (umbrella #776 + #781 polish)

This methodology is MVP. The following are deferred until MVP-5 smoke (Kanban #788) closes successfully and reveals which polish actually matters:

- **All decision points beyond the top-5 above** — e.g., "split commits", "write the standard now or wait", "bundle PR or separate". (Umbrella #781.)
- **`process_status=8` dedicated halted enum value** — MVP uses `halt_reason IS NOT NULL` as the flag. (Umbrella #781.)
- **`halted_at` timestamp + FE halted lane** — MVP relies on `updated_at` + filter-by-halt_reason for visibility. (Umbrella #781.)
- **Granular Bash auto-approve patterns** — MVP hook allows only Write/Edit; every Bash command still prompts. (Umbrella #776.)
- **`blocked_by` integration in pickup query** — depends on #771 which is P3-deferred.
- **Notification webhooks** — when a task halts, the user has to notice via Kanban polling. No push. (Umbrella #781.)
- **Cross-project Meta-Lead coordination** — explicitly tabled by user 2026-05-11.
- **Per-judgment-point scripted tests** — MVP-5 smoke covers the common path; bespoke tests per matrix entry land later.

---

## Strike log

- **Strike #1 — 2026-05-12, Kanban #786 + #787:** MVP definition.
- **Strike #2 — 2026-05-12, Kanban #788 (MVP-5 smoke on NewsAnalyzer):** PASS. Lead picked up smoke task #790 (`api/health.py` bootstrap on NewsAnalyzer, project_id=567) via manual kickoff. dev-backend spawned, file written, committed (NewsAnalyzer `4f6f425`), task closed. All 5 ACs hit:
  - AC-1 ✅ file exists at `api/health.py`.
  - AC-2 ✅ contents include FastAPI router returning `{"status": "ok"}`.
  - AC-3 ✅ **auto-approve hook fired without prompting on a non-agent-teams repo** — the critical cross-project validation.
  - AC-4 ✅ task closed (process_status=5); queue empty → Lead announced idle.
  - AC-5 ✅ commit body references #790.
  - **Bet outcome: VALIDATED.** Multi-project full-auto orchestration works end-to-end. agent-teams as a meta-orchestration product clears proof-of-concept.
  - **Caveat surfaced (filed as #791):** Lead does NOT spontaneously start the pickup loop on session bootstrap. Claude Code is reactive — user must send one kickoff message to trigger Lead's first action. True unattended overnight requires a follow-up integration (`/loop` skill, ScheduleWakeup self-rearm, or accept the manual-kickoff limit).
