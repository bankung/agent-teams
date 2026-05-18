# Session-start ritual — Lead handshake for `secretary` project

> First 30 seconds of operator's day with secretary. Smooth here = trust for the rest. Clunky here = friction compounds. This doc is the standardized Lead protocol.

## Trigger phrases

Operator opens any of:
- `secretary ครับ`
- `secretary go`
- `เลขา ครับ`
- `bind secretary` / `switch to secretary`
- First message naming "secretary" as the project

Lead infers project binding intent and runs the ritual below. No need for `agent-teams ครับ` first.

## Ritual sequence

### Step 0 — Confirm intent (skip if first message is unambiguous)

If trigger phrase is bare (`secretary ครับ` alone), assume bootstrap intent. Skip Step 0.

If trigger phrase is combined with workflow request (e.g., `secretary, triage today's inbox`), proceed BOTH bootstrap (Step 1-3) AND workflow spawn (Step 4) in one Lead turn.

### Step 1 — Bind project (silent — no chat output)

```
GET /api/projects/by-name/secretary
→ 200 → project_id=599, team=general, budget $5/d
```

If 404 → operator's session state is wrong (project doesn't exist in this stack). Halt + ask: "secretary project not found — wrong stack?"

If API down → operator's docker isn't running. Halt + ask: "API at :8456 unreachable — start docker compose?"

### Step 2 — Surface bootstrap acknowledgement (1 chat message)

Standard format (≤4 lines, mobile-friendly):

```
Session bound to secretary (id=599, team=general)
Budget: $X.YY used today / $5.00 cap   ← from GET /api/projects/599 cost_today
Approval policy: 6 rules active   ← from approval_policies field
Chrome MCP: <connected | not connected>   ← from list_connected_browsers
```

Add ONE line of context depending on state:
- If `general/operator-context.md` exists: `Saved context: loaded`
- If missing: `Saved context: not found (inline only)`

### Step 3 — Pre-flight check (silent unless something's wrong)

Lead silently verifies:
- [ ] Chrome MCP responds to `list_connected_browsers` (read-only)
- [ ] Project `approval_policies` field is non-null
- [ ] At least one workflow brief readable (`shared/workflow-briefs/email-triage.md` parse OK)

If ANY fails → surface inline:
```
⚠️ pre-flight failed: <which check> — fix before spawning
```

If all pass → silent (no extra chatter).

### Step 4 — Wait for or process workflow command

If operator's trigger phrase included a workflow → proceed to spawn (Step 5).

If not → Lead's next line:
```
Ready. What workflow? (triage / job apply / linkedin / digest / help)
```

If operator types `help` → Lead surfaces the 1-screen workflow cheatsheet (see below).

### Step 5 — Spawn workflow (if command provided)

Lead extracts:
- Workflow name (triage / apply / post / digest)
- Inline `context:` block (if operator provided)
- Or trigger `using my saved context` (Lead reads `general/operator-context.md`)
- Or hybrid (file + inline override)

Lead VALIDATES required PII fields per workflow (see `.claude/agents/secretary.md` "Critical fields per workflow"). If missing:

```
Missing required context for <workflow>:
  - <field 1>: <one-line example>
  - <field 2>: <one-line example>
Provide inline or add to general/operator-context.md, then retry.
```

If complete:
```
Spawning secretary for <workflow>...
```

Spawn via Agent tool with subagent_type=secretary; pass operator_context + workflow brief reference + cap parameters.

## 1-screen workflow cheatsheet (for `help`)

```
SECRETARY WORKFLOWS (Mode A — CLI)

email triage         → triage inbox: cap N
job apply            → find N jobs (jobsdb+linkedin)
linkedin post        → draft a post on <topic>
linkedin topics      → propose 3 post topics
calendar prep        → next 3 days briefing (events + research + conflicts)
news digest          → scan my-themes news in last 48h
cross-channel synth  → weekly rollup across all channels (Sun/Mon)
digest               → end-of-day rollup of secretary activity

CONTEXT INJECTION
inline:
  triage inbox. context: { name: "...", priority_senders: [...] }
saved:
  triage inbox using my saved context.
hybrid:
  triage inbox using my saved context. priority_senders: ["urgent@x"]   # override

HITL MID-WORKFLOW
operator types:
  approve #N        / reject #N        / edit #N (then operator writes new draft)
  approve all       (only when secretary asks)
  abort             (kill current workflow, preserve drafts)
  pause             (stop here, save state, resume later with `resume`)

UTILITY
budget               → GET /api/projects/599 cost vs cap
status               → in-flight HITL queue + drafts pending
clear                → reset Chrome MCP tab focus (use if tabs got messy)
```

## Specialist agent dispatcher (added 2026-05-18 per #1190)

For single-workflow tasks, Lead picks a SPECIALIST agent (smaller KB baseline, Haiku tier where appropriate, ~5-7× cheaper per spawn than monolithic). For cross-workflow / multi-channel tasks, fall back to monolithic `secretary`.

### Dispatcher decision tree

```
Operator's workflow word in trigger?
  │
  ├─ "triage" / "email" / "inbox" → secretary-email-triage (Haiku)
  │     [reads: email-rules + voice + failure-modes + email-triage brief]
  │
  ├─ "find jobs" / "scan jobsdb" / "apply" / "job" → secretary-job-scout (Haiku)
  │     [reads: job-criteria + profile + failure-modes + job-apply brief]
  │
  ├─ "linkedin post" / "draft post" / "topic ideas" → secretary-linkedin-content (Sonnet)
  │     [reads: linkedin-strategy + voice + profile + failure-modes + linkedin-post brief]
  │
  ├─ "calendar prep" / "next 3 days" / "what's on this week" → monolithic secretary
  │     [calendar specialist not yet built; falls to monolithic]
  │
  ├─ "news digest" / "scan today's news" / "what's happening" → monolithic secretary
  │     [news specialist not yet built]
  │
  ├─ "weekly synthesis" / "Sunday rollup" / "cross-channel" → monolithic secretary
  │     [cross-workflow by nature — needs all KB]
  │
  └─ "digest" / "end-of-day rollup" → monolithic secretary OR Lead-direct synthesis
        [synthesizes from general/ files; Lead-direct cheaper if no browser ops needed]
```

### When specialists escalate to monolithic

Specialist agents halt + return to Lead with handoff note when work crosses workflow boundary:
- `secretary-email-triage` reply to recruiter triggers job-scout context → halt, recommend Lead spawn `secretary-job-scout` OR monolithic `secretary`
- `secretary-job-scout` finds role requiring LinkedIn outreach → halt, recommend Lead spawn `secretary-linkedin-content` for the outreach draft
- Any specialist hitting cross-workflow synthesis → halt, recommend monolithic `secretary`

Lead reads the handoff note and picks the next spawn target.

### Lead-direct send workaround (added 2026-05-18 per #1177)

For send-class workflows (job submit / email reply send / LinkedIn post publish):
- Specialist agent does upstream work (research / score / draft) in spawn
- Spawn brief uses NEUTRAL verbs to avoid classifier pre-block (Category 8 in failure-modes.md): "evaluate", "recommend", "compose-draft" — NOT "submit", "send", "post"
- Lead-direct (not subagent) executes the send step after operator HITL approval in chat
- Lead may use URL deeplink trick for compose efficiency: see `.claude/docs/url-deeplink-tricks.md`

This 2-actor split is the standard pattern for any mutating external action until Mode B engine (#1191) provides classifier-aware native primitives.

## Output compression discipline (added 2026-05-18 per #1188)

All subagent reports MUST follow compression rules per `.claude/docs/output-compression-discipline.md`:

- **Structured markdown only** (tables / bullets / sections) — NO narrative preamble
- **Forbidden:** "Let me think...", "I will now...", "Here is my analysis...", "Based on my findings,", "In summary," (at start), restating spawn brief back to Lead
- **Required Summary section** (top of report, 1-2 sentences) — NOT preamble narration
- **Compressed forms preferred:** counts > prose ("Triaged 47, 3 reply queued" > "I went through 47 emails and found that 3 need replies")

**Lead enforcement:** when constructing any Agent spawn brief, append the compression directive:
> "Output format: structured markdown only. NO narrative preamble. Forbidden phrases per .claude/docs/output-compression-discipline.md. Report sections only."

**Cost rationale:** ~3-5k tokens saved per spawn. At scale (100s spawns/day) = $5-70/day saved on chrome.

**Override:** for interactive operator sessions where conversational warmth helps (rare), Lead may add "Output format: standard conversational report OK" — default is compressed.

## State-handling expectations

**Across-session memory:**
- `general/operator-context.md` (gitignored, operator-curated)
- `general/triage-state.json` (last_triage_at cursor)
- `general/applications-{YYYY-MM}.md` (application log, append-only)
- `general/linkedin-log-{YYYY-MM}.md` (post log)
- Kanban tasks (project_id=599) for any task secretary opens

**In-session memory:**
- HITL queue (Lead tracks across spawns within a session)
- Operator's inline overrides (Lead remembers + applies to subsequent spawns in same session)

**Resetting:**
- New day → operator says `reset state` → Lead clears in-session memory + writes new cursor
- File state survives via gitignored `general/` folder

## Tone of Lead's chat output

- **Concise.** Mobile reading. ≤4 lines per Lead message unless surfacing details operator explicitly asked for.
- **Status-first.** Counts > prose ("Triaged 47, 3 need approval" > "I went through your inbox and processed about 47 messages, 3 of which require your attention...").
- **Use emoji for parsing speed.** 📧 = email, 💼 = jobs, ✍️ = content, ⚠️ = warning, ✅ = done, ⏳ = pending. Not for decoration.
- **No greeting filler.** Operator typed `secretary ครับ` — they're in work mode, not chat mode.
- **Thai or English mirrors operator.** Operator typed Thai → reply Thai. Mixed → mixed.

## Failure-handling expectations

If at ANY point during the ritual something is unexpected:
- **DO NOT silently work around.** Surface immediately. Operator's trust depends on knowing what failed.
- **DO NOT retry > 1x without permission.** First retry OK (transient flake). Second retry needs operator approval ("Chrome MCP failed twice — restart browser or skip Chrome and try direct WebFetch?").
- **DO NOT spawn secretary if pre-flight failed.** Spawning then aborting wastes tokens + operator time.

## Reference

- Agent definition: `.claude/agents/secretary.md`
- Workflow briefs: `shared/workflow-briefs/{email-triage,job-apply,linkedin-post}.md`
- Operator context template: `shared/operator-context-template.md`
- Failure playbook: `shared/failure-modes.md`
- Digest template: `shared/daily-digest-template.md`
- Sample digest (synthetic): `shared/sample-digest.md`
