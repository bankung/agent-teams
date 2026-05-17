# Daily digest — template

> Used by Lead at end-of-day (or when operator types "digest"). Lead reads secretary's `general/digest-<date>.md` (filled per this template) + relevant general/ files, then renders the digest to operator.
>
> Goal: operator reads digest in ≤2 min and knows: what landed, what needs them, what's drifting.

---

# Secretary digest — {{YYYY-MM-DD}}

## TL;DR (read this first — 30 sec)

[1-2 sentences. What happened today, what most needs operator attention. No counts here — just the headline.]

Example: "Triaged 47 emails (1 reply queued for your approval), shortlisted 3 jobs (2 ready to submit pending approval), drafted 1 LinkedIn post on auditor pattern (awaiting review). 1 escalation: recruiter from CompanyX named a specific salary outside criteria — your call."

---

## ACTION-REQUIRED (HITL queue — operator approval needed)

For each pending HITL: 1 row, max 2 lines per row.

- **#TASK_ID** — [type: email_reply / job_apply / linkedin_post / escalation] — [1-line context] — `[approve] [reject] [edit]`
  - Drafted: `general/path-to-draft.md`

Example:
- **#1109** — email_reply — Recruiter "Phi" at Mango Tech, asking about full-stack roles — drafted polite decline with door-open close. — `[approve] [reject] [edit]`
  - Draft: `general/email-drafts/2026-05-17-recruiter-mango.md`
- **#1110** — job_apply — Senior Backend at Acme, score 78/100, cover letter customized — `[approve] [reject] [edit]`
  - Drafts: `general/applications/2026-05-17-acme-senior-backend.md`
- **#1111** — linkedin_post — "Auditor pattern in LangGraph (450 words)" — `[approve] [reject] [edit]`
  - Draft: `general/linkedin-drafts/2026-05-17-auditor-pattern.md`

## COMPLETED (no operator action — for awareness)

Counts + 1 example each:

- **Emails auto-archived**: N (per email-rules.md auto-archive list)
- **Emails escalated for later review**: N → `general/triage-<date>.md` under "reply_later"
- **Jobs reviewed**: N (M proposed, M-K skipped on deal-breakers — see `general/applications-<YYYY-MM>.md`)
- **LinkedIn posts drafted**: N (M ready for review above; N-M saved for later)
- **Research items processed**: N (logged in `general/research-log-<date>.md`)

## AGING / DRIFTING (>24h, needs decision)

Tasks the operator hasn't acted on. Surface aggressively — these rot the system.

- **#TASK_ID** — [type] — drafted 2 days ago, no operator response — [1-line context] — should this stay queued, or skip?
- [bullet per stale item]

If list is empty: "**No aging items** — operator is up to date on approvals."

## BUDGET WATCH

- **Today's secretary spend**: $X.XX / $5.00 daily cap (XX% used)
- **Month-to-date**: $Y.YY / $50.00 monthly cap (YY% used)
- **Project-wide spend including specialists**: $Z.ZZ

If >80% used: surface a warning + list top 3 highest-cost tasks.

## ERRORS / ANOMALIES (if any)

- HITL pauses unresolved >48h: count + ids
- Chrome MCP failures: count + last error
- Knowledge base TODO markers detected: list
- Approval policy hits that auto-denied: count + 1 example (so operator can review policy)

If clean: omit the section entirely.

## TOMORROW'S SUGGESTED FOCUS

[Lead's 1-2 sentence recommendation. Based on aging items + calendar + operator's stated weekly goals.]

Example: "5 jobs in 'review later' bucket from this week; suggest 30 min triage tomorrow morning before they go stale. LinkedIn post on auditor pattern ready to post — pick a window between 9-10am ICT for best SEA reach."

---

## Operator one-tap actions (Mode A: paste back into Lead session)

```
approve #1109                    # email reply
approve #1110 with edits         # job app with edits operator types in next
approve #1111                    # linkedin post
reject #1112                     # something operator wants to kill
defer #1113 to next week         # park for later
```

---

## File pointers

- Draft folder today: `context/projects/secretary/general/2026-05-17/`
- Application log this month: `context/projects/secretary/general/applications-2026-05.md`
- LinkedIn log this month: `context/projects/secretary/general/linkedin-log-2026-05.md`
- Triage state: `context/projects/secretary/general/triage-state.json`

---

## Render rules (for Lead — not in operator-facing output)

- Total length target: 300-500 words rendered (operator-facing)
- TL;DR section MUST be ≤2 sentences
- ACTION-REQUIRED MUST list every pending HITL (if 0 → say "Inbox zero today" explicitly)
- AGING section MUST be honest (don't suppress to look better)
- Use Lead's voice (concise Thai/English mix per operator preference) when rendering to chat
- If digest file `general/digest-<date>.md` is missing → Lead spawns secretary to fill it before rendering
