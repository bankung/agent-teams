# Sample digest — synthetic data

> Example rendering of what operator sees end-of-day. All data SYNTHETIC (no real names / companies / emails). Use this to calibrate expectations before tomorrow's first real test.

---

# Secretary digest — 2026-05-18

## TL;DR

Triaged 47 emails (3 replies pending approval), reviewed 24 jobs on JobsDB+LinkedIn (4 ready to submit, 1 over-budget — your call), drafted 1 LinkedIn post on "auditor pattern" (review). 1 escalation: a recruiter named a specific salary that touches your floor — needs your read.

---

## ACTION-REQUIRED (4 pending)

- **email_reply-1** — Recruiter "Anna" @ FakeTech, asking about staff backend role — drafted polite reply with 3 clarifying questions. — `[approve] [reject] [edit]`
  - Draft: `general/2026-05-18/email-drafts/01-anna-faketech.md`
- **email_reply-2** — Hiring manager "Sarah" @ AcmeCo confirming interview Friday 3pm ICT — drafted accept with Zoom link. — `[approve] [reject] [edit]`
  - Draft: `general/2026-05-18/email-drafts/02-sarah-acme-interview.md`
- **job_apply-1** — Staff Backend @ FakeTech, score 78/100, cover letter customized with their recent open-source release. — `[approve_submit] [edit_draft] [skip]`
  - Cover letter: `general/2026-05-18/applications/03-faketech-staff-backend.md`
  - Resume: from operator_context.resume_path (manual upload via Chrome MCP during HITL)
- **linkedin_post-1** — Draft on "auditor pattern in langgraph" (340 words, voice-check passed). — `[approve_post] [edit_draft] [save_for_later] [skip]`
  - Draft: `general/2026-05-18/linkedin-drafts/auditor-pattern.md`

## COMPLETED — no operator action (for awareness)

- **Emails auto-archived**: 31
  - 18 newsletters (TLDR AI, Pragmatic Engineer, HN digest, etc.)
  - 7 GitHub notifications
  - 4 calendar confirmations
  - 2 receipts (Anthropic, AWS)
- **Emails reply-later**: 8
  - Stashed in `general/2026-05-18/triage-reply-later.md` for batch
- **Jobs reviewed**: 24 listings across 2 sources
  - JobsDB: 14 reviewed → 6 below threshold + 5 deal-breakers (3 onsite Singapore, 2 below salary floor)
  - LinkedIn: 10 reviewed → 4 below threshold + 2 deal-breakers (1 anti-title "junior", 1 blacklisted company)
  - **4 proposed** above + **2 saved-for-later** (interesting but lower priority)
- **LinkedIn**: 3 topic candidates surfaced earlier → operator picked "auditor pattern" → drafted (above)
- **Research items**: 0 (no research request today)

## AGING / DRIFTING — needs your decision

- **email_reply-old-1** — Friend "Kai" replied to your message from 5 days ago, asking about meetup — secretary stashed as reply_later but it's now stale; suggest reply today.
- **job_apply-old-1** — App #1135 you submitted Monday — no response yet from "BravoCo"; follow-up auto-scheduled for next Monday (day +7).

If list is empty: "**No aging items** — operator is up to date."

## ESCALATIONS — your read needed

- **escalation-1** — Recruiter "Patricia" @ DeltaCo offered explicit base salary 380K THB/month — touches your floor (350K) but with 12 months bonus mentioned. Your call on response framing. — Original at `general/2026-05-18/escalations/01-patricia-delta.md`

## BUDGET WATCH

- **Today's secretary spend**: $1.23 / $5.00 daily cap (25% used) ✓
- **Month-to-date**: $1.23 / $50.00 (3% used) ✓
- **Project-wide spend (incl. specialists)**: $1.45 ✓

No warnings.

## ERRORS / ANOMALIES

- 1 HITL pause unresolved >48h: **email_reply-old-1** (5 days)
- Chrome MCP failures today: 0 ✓
- Knowledge base TODO markers: 0 ✓
- Approval policy auto-denials: 0

## TOMORROW'S SUGGESTED FOCUS

Review the 4 pending approvals before noon (30 min). 1 interview Friday (block your prep time). Linkedin post on "auditor pattern" suggest posting Tue or Thu morning before 10am ICT for SEA reach (Wed evening drops engagement).

---

## Operator one-tap actions

```
approve email_reply-1
approve email_reply-2
approve job_apply-1
edit linkedin_post-1   # then operator pastes their edits
reject escalation-1    # decline DeltaCo politely
defer email_reply-old-1 to next week
```

---

## File pointers

- Draft folder today: `context/projects/secretary/general/2026-05-18/`
- Application log: `context/projects/secretary/general/applications-2026-05.md`
- LinkedIn log: `context/projects/secretary/general/linkedin-log-2026-05.md`
- Triage state cursor: `context/projects/secretary/general/triage-state.json`

---

## What to expect IRL (different from this sample)

- **Counts will be smaller** for first session — operator hasn't built up a backlog of HITL pauses yet.
- **First-day budget**: probably $0.10-0.50 (low LLM volume; mostly summarization).
- **First-day error count**: likely 1-3 (Chrome MCP rough edges, voice mismatches that drove redrafts).
- **First-day TODO markers**: likely 0 IF operator filled `general/operator-context.md` per template; secretary halts on any TODO so missing context surfaces as explicit failures.
- **TL;DR length** will vary by activity — could be 1 sentence on a quiet day, 4-5 on a busy one. Lead aims for ≤2 sentences but won't suppress to compress.
- **Aging section** likely empty on day 1 (nothing has had time to age yet).

## How Lead renders to chat (vs to file)

This file is the **full digest** (kept on disk for audit). What Lead surfaces in chat is a **compressed view**:

```
📊 Today's digest

📧 47 triaged → 3 replies need approval (#1 Anna, #2 Sarah interview, see drafts in general/2026-05-18/)
💼 24 jobs reviewed → 4 ready to submit (top score 78, FakeTech staff backend)
✍️ 1 LinkedIn draft on auditor pattern (voice ✓)
⚠️ 1 escalation: recruiter named 380K — your call on response (file: ...)
⏳ Aging: 1 email from "Kai" 5d old

💰 $1.23 used (25% of daily)

Tomorrow: review 4 approvals before noon; interview prep Fri 3pm
```

Operator can ask `full digest` for the markdown version above. Mobile default = compressed.
