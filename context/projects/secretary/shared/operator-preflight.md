# Operator pre-flight — before tomorrow's first secretary test

> Run through this checklist tonight so tomorrow's Mode A test goes smoothly. Estimate: ~1 hour total.

## Part 1 — Knowledge base (~45 min)

Open each file and replace `[TODO]` markers with operator-specific content. Secretary will halt if it finds TODOs.

- [ ] `context/projects/secretary/shared/profile.md` (~20-30 min)
  - Identity, contact, current role, summary (1-line + long)
  - Resume PDF absolute path on your machine + last-updated date
  - Skills (grouped), education, certs, languages, work auth
  - Working preferences

- [ ] `context/projects/secretary/shared/voice.md` (~15-25 min)
  - Tone profile across 5 contexts
  - Words/phrases to USE (3-5 signature phrases)
  - Words/phrases to AVOID (operator-specific bans on top of the defaults already in the file)
  - 2-3 real samples each: email (formal + casual), LinkedIn post, cover letter opener
  - Topic preferences / stances

- [ ] `context/projects/secretary/shared/email-rules.md` (~10-15 min)
  - 3-10 priority senders (emails always reply_now)
  - 2-5 auto-archive patterns (biggest noise sources)
  - Escalation patterns (sensitive topics)
  - Reply-now urgency signals
  - Excluded folders/labels (Promotions, Spam, personal-finance)
  - Read-don't-process senders (family, banking)

- [ ] `context/projects/secretary/shared/job-criteria.md` (~15-20 min)
  - 5-10 target role title variants + anti-titles
  - Must-have skills with weights (be honest about your strengths)
  - Deal-breakers (location, salary floor, anti-companies)
  - Salary numbers (THB floor + target; USD/SGD conversions if applying abroad)
  - Location + work model + time zone constraints
  - Sources URLs: 2 — your JobsDB filtered search URL + LinkedIn Jobs filtered URL
  - Per-run caps: how many listings to review + how many to apply in one run

- [ ] `context/projects/secretary/shared/linkedin-strategy.md` (~10-20 min)
  - Audience (primary + secondary + NOT-for)
  - 3-5 themes with sub-topics + frequency
  - Anti-themes (don't-touch list)
  - RSS feeds (5-10 trusted sources)
  - "Recent topics" section (refresh weekly)
  - Cadence target

**Tips for speed:**
- Copy-paste from existing resume / past LinkedIn posts / past cover letters
- Don't try to write perfect — secretary uses what's there + asks via HITL when ambiguous
- TODO markers are intentionally explicit so you can see what's missing at a glance

## Part 2 — Chrome MCP setup (~10 min)

### Install / verify Chrome extension

1. Open Chrome
2. Visit `chrome://extensions`
3. Look for "Claude in Chrome" extension
   - If missing: install from Chrome Web Store
   - If present: ensure it's enabled

### Connect to your Claude Code session

The extension talks to your local Claude Code via a localhost socket. To verify:
1. With Claude Code CLI open, ask: `"List connected browsers"`
2. Should return Chrome with your tab list — if not, follow extension setup wizard

### Pre-login to required services

Open Chrome (the SAME profile the MCP uses) and log in to:

- [ ] **Gmail** (https://mail.google.com) — leave logged in
- [ ] **LinkedIn** (https://linkedin.com) — leave logged in
- [ ] **JobsDB** (https://th.jobsdb.com — adjust country if different) — log in OR create account if you don't have one
- [ ] Any other job board you want secretary to scan

### Test Chrome MCP from CLI

Tomorrow morning before starting secretary work, do a sanity check:
```
User: take a screenshot of my current Chrome tab
```
Should return a screenshot. If not — extension's not connected. Fix before spawning secretary.

## Part 3 — Tune approval policy (~5 min)

The secretary project already has these auto-approve / auto-deny rules set. Review them and tighten/relax via:

```bash
curl --silent -X PATCH -H "X-Project-Id: 599" -H "Content-Type: application/json" \
  http://localhost:8456/api/projects/599 \
  --data @<your-edited-policy-file>.json
```

Default rules to expect:
- **auto-approve**: secretary read-only browser actions, file write to `general/`
- **auto-deny**: any pattern containing "delete account", "unsubscribe from all", "cancel subscription"
- **require-attention**: every reply / submit / post / pay action

Most operators leave defaults until first test reveals friction.

## Part 4 — Sanity check budget (~2 min)

Secretary project budget is currently:
- Daily: $5 USD
- Monthly: $50 USD

Adjust if too tight / too loose for your test scope:
```bash
curl --silent -X PATCH -H "X-Project-Id: 599" -H "Content-Type: application/json" \
  http://localhost:8456/api/projects/599 \
  --data '{"budget_daily_usd":"10.00","budget_monthly_usd":"100.00"}'
```

## Part 5 — Tomorrow morning flow

When you sit down to test:

1. **Open Claude Code CLI**
2. **Bind to secretary project**: First prompt should be:
   ```
   secretary ครับ
   ```
   (or `secretary project go` — whatever phrasing per your bootstrap convention)
3. **Confirm bootstrap** — Lead announces session bound to secretary (id=599, team=general)
4. **Pick a workflow** (pick ONE for first test — don't compound failures):
   - **Email triage first** (lowest risk — read-only + HITL on each reply)
     ```
     triage today's inbox
     ```
   - **Then job search** (more moving parts — application forms vary)
     ```
     find matching jobs on jobsdb + linkedin
     ```
   - **Then LinkedIn post** (most subjective — voice fit is the hard part)
     ```
     draft a linkedin post on [topic of your choice]
     ```
5. **Watch HITL pauses** — Lead surfaces them in chat; respond with approve / reject / edit
6. **End of day**: `digest` (or `end-of-day digest`) — Lead reads secretary's day output + renders to chat per template

## Part 6 — What to capture for next-session feedback

Keep a `_scratch/secretary-test-day-1-notes.md` as you go:
- Things that worked
- Things that broke (Chrome MCP fails, voice mismatches, criteria gaps)
- Things you'd change in `email-rules.md` / `job-criteria.md` / `voice.md` based on real run
- Token cost actually consumed (check via /api/projects/599 estimated_cost_usd)
- Time saved vs doing manually

Feeds back into Phase 2 priorities (which gaps to fix first).

---

## Smoke test before tomorrow (optional but recommended — 5 min tonight)

After filling profile.md basics, run a tiny dry-run:
```
secretary ครับ
```
→ Wait for Lead bootstrap.
```
read the knowledge base and tell me what's still TODO
```
→ Lead spawns secretary → secretary lists every TODO marker → you can knock out the remaining ones tonight before bed.

This catches the "didn't fill X" gap WITHOUT consuming real tokens on browser work.
