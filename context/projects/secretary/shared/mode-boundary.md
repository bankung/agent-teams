# Mode boundary — A / B-read / B-write

> "Mode B" was a single concept in early session-review docs; reality is **two distinct sub-modes** with different infrastructure + risk profiles. Pin them here before building.

## Mode A — interactive Lead session + Chrome MCP

**What:** operator opens Claude Code CLI, types workflow command; Lead extracts `operator_context`, spawns secretary; secretary uses Chrome MCP (mcp__Claude_in_Chrome__*) to drive operator's already-logged-in Chrome session.

**Identity:** secretary acts AS the operator (operator's authenticated Gmail / LinkedIn / JobsDB session). No service-side distinction between "operator did it" vs "secretary did it via operator's browser".

**Trigger:** operator-initiated, on-demand. No scheduling.

**Cost:** Claude API tokens only. No infrastructure overhead (Chrome MCP already in skill registry; operator's Chrome already running).

**Risk surface:**
- Browser-state fragility (cookies expire → workflow halts cleanly)
- Operator's mistake = secretary's mistake (acting as operator)
- Mid-session disconnect (mobile signal loss) → state preserved via `general/` files + Kanban BLOCKED tasks

**Use for:**
- Email triage (always — needs Gmail auth)
- Job application SUBMIT (always — needs JobsDB/LinkedIn auth)
- LinkedIn POST (always — needs LinkedIn auth)
- Calendar reads (always — needs Calendar auth)
- Any DM / reply / approve-and-act flow

**Don't use for:**
- Scheduled / unattended runs (Mode A requires operator presence)
- High-volume scraping (Chrome MCP is interactive-paced, ~1-2 actions/sec)
- Long-running background tasks (CLI session terminates if operator closes laptop)

**Status:** ✅ Live today. Substrate complete.

---

## Mode B-read — autonomous langgraph + headless browser (read-only)

**What:** langgraph worker runs scheduled / triggered (autorun queue per #786); specialist node uses headless browser (Playwright / Selenium in langgraph container) to fetch public web content. No operator session needed.

**Identity:** "secretary's own" headless session. Public-web only. No operator auth.

**Trigger:** scheduled (cron via APScheduler — existing in api/main.py), or queued by Lead.

**Cost:** Claude API + ~50-100MB RAM per concurrent headless browser. Negligible compute on hetzner-class host.

**Risk surface:**
- Anti-bot detection (LinkedIn / Indeed have improving fingerprinting; high risk of IP block on aggressive scraping)
- DOM changes break selectors silently (same as Mode A but no operator to notice immediately)
- Rate limit hit → workflow halts; doesn't damage anything but loses run

**Use for:**
- JobsDB scraping (public listings — no login required if just reading)
- LinkedIn public posts / company pages (no login for browsing, but rate-limited)
- News / RSS / blog content
- Job-board firehose (HN "Who's hiring", AngelList public listings)
- Stock prices / public APIs without auth
- Any scheduled "scan + summarize" flow

**Don't use for:**
- Anything behind login (Gmail, LinkedIn DMs, JobsDB application submit, Calendar) — use Mode A
- Anti-bot-sensitive scraping that needs human-like timing (use Mode A so operator's session has reputation)
- One-off operator-initiated work (overkill — Mode A is fine for that)

**Status:** ⏳ Deferred. Estimated 3-5 days dev for Phase 1 (wire Playwright + navigate + read_page tools into langgraph). Gated on Mode A measurement showing scaling pain.

**Implementation sketch (when ready):**
- `langgraph/tools/browser/` — new package mirroring shape of existing `langgraph/tools/`
- `browser.navigate(url) -> page_html`
- `browser.read_page(selector?) -> text | json`
- `browser.find(text, near?) -> element_ref`
- `browser.screenshot() -> bytes` (for debugging halt cases)
- No `browser.click()` / `browser.type()` in Mode B-read — those are write actions
- Rate-limit budget (max 30 requests / 5min per domain, configurable per-project)
- Anti-fingerprint mitigation: rotating user-agent, jitter timing, residential proxy hook (operator brings own)

---

## Mode B-write — autonomous langgraph + operator-authenticated session

**What:** would be "secretary writes / submits / posts without operator presence". E.g., scheduled "apply to 5 jobs daily at 9am" or "post LinkedIn content on Tuesdays".

**Identity:** would act AS operator — but without operator's real-time approval.

**Trigger:** scheduled.

**Cost:** would need persistent operator-authenticated browser session (cookie jar) on the langgraph host. Operator pre-loads cookies; secretary reuses.

**Risk surface:**
- Account suspension (LinkedIn's TOS-violation gate is sensitive to non-human posting patterns)
- Irreversible mistakes (submit wrong cover letter → operator's brand)
- Audit trail divergence (operator wasn't actually in the loop; HITL discipline broken)
- Cookie expiration / re-auth = workflow breaks at unpredictable times

**Use for:**
- Nothing yet. Mode B-write is **a planning category, not a target**.

**Status:** 🚫 **Intentionally not built.** Per session-review-2026-05-17.md W4: "HITL discipline assumes operator availability". Mode B-write violates that assumption.

**Why deferred indefinitely:**
1. The whole secretary architecture is premised on HITL approval before external effect. Mode B-write removes the human-in-loop.
2. Operator's reputation lives in their accounts; secretary-as-operator-without-supervision = unbounded blast radius if anything drifts.
3. Cost of HITL friction (operator approves N times per day) is much lower than cost of one autonomous mistake.
4. Approval policies (#957) already let operator auto-approve safe patterns IN Mode A (instant resume without typing) — captures most of the "I don't want to approve every tiny thing" friction.

**What would change the decision:**
- Operator reaches sustained 50+ HITL approvals / day on a single workflow (approval friction > approval value)
- A specific workflow has provably-zero-irreversible-mistake risk AND operator has run it manually 20+ times without correction
- New mechanism (e.g., approve-N-in-batch) reduces friction without removing HITL
- Operator changes mind explicitly with a written-down decision in `decisions.md`

---

## Decision tree — which mode for a workflow?

```
Does the workflow need to AUTHENTICATE to a service?
├── YES → Mode A (always — operator's session is the only safe auth)
│         (except: if workflow is "browse public catalog", use Mode B-read)
└── NO  → Is it scheduled / unattended?
          ├── YES → Mode B-read (when built)
          └── NO  → Mode A (simpler; reuse Chrome MCP)
```

```
Does the workflow have EXTERNAL EFFECT (send / post / submit / pay)?
├── YES → Mode A with HITL pause (always)
│         (Mode B-write is intentionally not built — see above)
└── NO  → Mode A or Mode B-read per auth requirement above
```

## Cost vs scaling matrix

| Workflow class | Volume / day | Operator presence | Best mode |
|---|---|---|---|
| Email triage | 1-5 runs | Mornings | Mode A |
| Job apply | 0-1 runs | Variable | Mode A |
| LinkedIn post | 0-1 runs | Twice/week | Mode A |
| Job-board firehose scan | 1 run | Async | Mode B-read |
| News digest | 1-2 runs | Async | Mode B-read |
| Calendar prep | 0-1 runs | As needed | Mode A |
| Cross-channel weekly synthesis | 1 run | Sunday evening | Mode A (multi-source, needs auth) |
| Stock-price watch | hourly | Async | Mode B-read (when built) |

If a workflow's volume × operator-overhead exceeds operator's tolerance → triggers Mode B-read investment for the SCAN portion (leaving SUBMIT in Mode A).

## Implications for substrate

- **Mode A (today):** Chrome MCP + secretary agent def + workflow briefs + operator_context channel — ALL in place
- **Mode B-read (future):** new `langgraph/tools/browser/` + headless browser dependency in langgraph container + per-project rate-limit policy + new workflow brief class ("scheduled scan" briefs distinct from "interactive" briefs)
- **Mode B-write:** no substrate needed (intentionally not built)

## Anti-pattern guard

If you ever find yourself writing a workflow brief that says "this should run scheduled AND post to LinkedIn without operator approval" → STOP. That's Mode B-write. Reframe as either:
1. Mode A on a schedule **with** HITL (cron triggers Lead session; operator approves the queued posts when they wake up)
2. Mode B-read for the scan + Mode A for the post (split: scheduled scrape produces drafts; operator approves + posts manually)

## Cross-references

- `.claude/agents/secretary.md` — "HITL discipline (Mode A — CLI flow)" section
- `context/projects/secretary/shared/failure-modes.md` — Category 3 (browser failures) + F7.3 (mobile-when-needs-desktop)
- `context/projects/agent-teams/shared/session-review-2026-05-17.md` — Insight 4 (two operating modes — original framing) + W4 (HITL assumes availability)
- `context/projects/agent-teams/shared/approval-policy-design.md` — approval policies as friction-reducer for Mode A
