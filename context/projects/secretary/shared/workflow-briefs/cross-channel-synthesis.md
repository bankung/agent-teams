# Workflow brief — Cross-channel synthesis

> Spawn template for `secretary` agent. Lead reads this when operator says "weekly synthesis" / "what's happened across everything" / "Sunday rollup" / similar.
>
> Mode A (multi-source: email + calendar + LinkedIn + job apps + news). Heavy operator-context reads; conservative on actions.

## Pre-flight (Lead checks)

- [ ] Lead extracted `operator_context` — REQUIRED: `name`; recommended: `synthesis_horizon_days` (default 7), `synthesis_channels` (default [email, calendar, linkedin, jobs, news]), `synthesis_focus` ("operational" | "strategic" — default operational)
- [ ] Chrome MCP connected + all relevant services logged-in
- [ ] Existing per-channel `general/{YYYY-MM-DD}/*.md` files from the past `horizon_days` exist (else: synthesis reads thinner since each channel's history is shorter)

If pre-flight fails → halt + report.

## Secretary's expected workflow

1. **Read frameworks + operator_context**:
   - All shared/*.md as needed per channel scope
   - `operator_context.synthesis_focus`: operational (concrete inbox + actions) vs strategic (themes + trends across weeks)
2. **For each channel in `synthesis_channels`**, scan `general/` files for the past `synthesis_horizon_days`:
   - **email**: `triage-{YYYY-MM-DD}/triage-summary.md` per day → aggregate counts + outstanding replies
   - **calendar**: `calendar-prep-{YYYY-MM-DD}.md` per day → meetings held + outcomes (if operator updated notes post-meeting)
   - **linkedin**: `linkedin-log-{YYYY-MM}.md` → posts published + engagement (if operator added) + drafts saved for later
   - **jobs**: `applications-{YYYY-MM}.md` → applications submitted + statuses + follow-up dates
   - **news**: `news-digest-{YYYY-MM-DD}.md` per day → recurring themes + cross-references
3. **Cross-channel patterns** — identify connections:
   - Recruiter from email triage matches company from job apps log → "still waiting on X re Y"
   - News theme matches operator's posted-about topic → "your post on Z appeared after N news items in the same theme"
   - Calendar meeting overlaps with job interview prep → "Fri 3pm interview followed by team standup at 4 — buffer time?"
   - Email thread from boss matches recent LinkedIn post → "boss may have seen your Z post; consider mentioning"
4. **Surface metrics**:
   - Approval count this week (HITL pauses operator handled)
   - Auto-action count (auto_archive + auto_deny by policy)
   - Total secretary spend this week (cost via /api/projects/599/pl?period=weekly)
   - Drift detection: any aging HITLs (>48h) or stale drafts (>3d)
5. **Surface strategic observations** (if `synthesis_focus=strategic`):
   - Job market signal (N applications this week vs last week → trend up/down)
   - Content signal (N posts this week vs N posts × engagement → audience grew or shrank?)
   - Email composition (% recruiters vs % colleagues vs % automated — is operator's signal-to-noise getting worse?)
   - Calendar composition (% external meetings vs internal — is operator over-meeting?)
6. **Identify week's wins + drags**:
   - Wins: completed actions, posts that performed, interviews secured
   - Drags: aging items, repeated questions from operator, workflows that halted
7. **Stash synthesis** in `general/{YYYY-MM-DD}/weekly-synthesis.md`
8. **Return to Lead** with: cross-channel observations + 3-5 most important threads + suggested focus for next week

## Auto-execute (no HITL)

- All reading from `general/` files
- All cross-channel analysis
- Compute metrics from /api/projects/599/pl (cost via secretary's project ledger)
- Identify drift / aging items
- Surface observations

## Never HITL (this workflow has NO external effect — pure analysis)

Synthesis is read-only. If operator wants to act on a synthesis observation, that triggers a different workflow (reply / post / apply).

## Output structure

`general/{YYYY-MM-DD}/weekly-synthesis.md`:
```markdown
# Weekly synthesis — week ending {YYYY-MM-DD}

Horizon: {N} days
Channels scanned: {list}
Focus: {operational | strategic}

## TL;DR
{1-2 sentences — biggest signal of the week}

## Cross-channel threads
- {Recruiter X from email × Job app Y from JobsDB} — waiting on...
- {News theme Z × Your LinkedIn post Q} — pattern...
- {Calendar overload pattern}

## Metrics
- Emails triaged: N (M auto-archived, K replies)
- Jobs reviewed: N (M submitted, K still in proposal queue)
- LinkedIn posts: N (M with engagement above threshold)
- HITL pauses this week: N (% auto-policied / % manual)
- Spend: $X.XX / $35.00 weekly budget pro-rated

## Strategic observations (if focus=strategic)
- {observation 1}
- {observation 2}

## Wins
- {bullet}
- {bullet}

## Drags
- {aging item or repeated friction}

## Suggested focus next week
- {1-3 bullets}

## File pointers
- Per-day digests: general/2026-05-{11..17}/
- Application log: general/applications-2026-05.md
- LinkedIn log: general/linkedin-log-2026-05.md
```

## Operator-facing summary (Lead renders — compressed for mobile)

```
🗓️ Weekly synthesis — May 11-17

TL;DR: solid email + job activity (3 applications submitted, 2 with positive recruiter response). LinkedIn post on auditor pattern outperformed last 3 (8x impressions). 1 strategic drift: calendar is 70% internal meetings — eats deep work time.

🔗 Cross-channel threads
- Recruiter Anna @ Mango (email) ↔ Job app you submitted Mon (jobs) → her reply landed Thu, draft ready in general/2026-05-17/email-drafts/
- News theme "MCP adoption" appeared 7x this week ↔ your post on the topic landed 3 days into the spike

📊 Metrics
- Triaged: 287 emails, 41 replies, 12 escalated
- Jobs: 18 reviewed, 3 submitted, 2 follow-up due next week
- LinkedIn: 2 posts, 1 outperformer
- Spend: $3.20 (well under $35/week)
- HITL: 27 pauses, 6 auto-policied (78% required attention — typical)

🏆 Wins
- Auditor-pattern post performed
- 3 quality job apps submitted (down from 6 attempted — better targeting)
- All emails triaged within 24h of arrival

⚠️ Drags
- Mon 11am recruiter call AND team standup conflict (2nd week running)
- 2 LinkedIn drafts saved-for-later from 9 days ago, still not posted

🎯 Suggested next week
- Block deep work Mon mornings (no meetings)
- Review the 2 stale drafts — post or kill
- Anna's company response is high-priority; clear time for Thu reply

Full: general/2026-05-17/weekly-synthesis.md
```

## Cadence guidance

- Run weekly: Sunday evening or Monday early morning
- NOT daily (overkill; daily digest already exists per `daily-digest-template.md`)
- Operator-initiated only (Mode A doesn't auto-schedule; if operator wants cron-style, Mode B-read with `--read-only` after that lands)

## Failure modes

- Some channels have no `general/` files (operator hasn't done that workflow this week) → synthesis renders thinner for that channel; surface "no data for {channel} this period"
- All channels empty → halt + report "no per-channel digests this week — run individual workflows first"
- Cost > $1.00 → cap analysis depth + flag (synthesis should be cheap — heavy compute = misconfig)
- Time horizon expansion (operator asks for 30 days) → halt + ask "30 days is a lot — focus operational or strategic?" before proceeding

## Tuning hooks

- **Horizon**: operator inline `horizon: 14d` (default 7d)
- **Channels**: operator inline `channels: [email, jobs]` (subset; default all)
- **Focus**: operator inline `focus: strategic` (default operational)
- **Output format**: operator inline `output: markdown_full` or `output: chat_compressed` (default compressed for mobile)
- **Skip channels with no data**: default on; operator can `force_all_channels: true` to render empty sections explicitly
