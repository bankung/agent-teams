# Operator pre-flight — Mode A test setup

> Knowledge base scaffolds in `shared/` are GENERIC frameworks (no PII). Identity / targets / senders are session-time inputs you type when running each workflow. ~15 min setup + ~5 min per workflow command.

## Part 1 — Chrome MCP setup (~10 min, one-time)

### Install / verify Chrome extension

1. Open Chrome
2. Visit `chrome://extensions`
3. Look for "Claude in Chrome" extension
   - If missing: install from Chrome Web Store
   - If present: ensure it's enabled

### Pre-login to required services

In the SAME Chrome profile the extension uses:

- [ ] **Gmail** (https://mail.google.com)
- [ ] **LinkedIn** (https://linkedin.com)
- [ ] **JobsDB** (https://th.jobsdb.com — adjust country if needed)
- [ ] Any other job board you want secretary to scan

### Verify Chrome MCP from CLI

Tomorrow morning before secretary work:
```
You: take a screenshot of my current Chrome tab
```
Should return a screenshot. If not → extension not connected. Fix before spawning secretary.

## Part 2 — (Optional) Personal context file for persistence

If typing identity every session feels annoying, save a personal note at:
```
context/projects/secretary/general/operator-context.md
```

This folder is **gitignored** — file stays on your machine, never committed.

Template:
```yaml
# Personal context (LOCAL ONLY — gitignored)
identity:
  name: <full name as on CV>
  signature: <how you sign off informally>
  email: <primary email>
  phone: <if used for job forms>
  linkedin_url: <https://linkedin.com/in/...>
  resume_path: <absolute path on disk>

defaults_for_email_triage:
  signature_style: <Best,/Cheers,/etc>
  tone_for_unknowns: formal-warm
  priority_senders: []     # add as you discover
  auto_archive_overrides: []
  skip_folders: ['Promotions', 'Personal Finance']
  read_dont_process: []

defaults_for_job_apply:
  target_roles: []         # e.g. ['CTO', 'Head of Engineering']
  acceptable_roles: []
  anti_titles: ['Junior', 'Intern']
  must_have_skills:        # name + weight 1-25
    - { name: '...', weight: 20 }
  nice_to_have_skills: []
  salary_floor_thb: 0
  salary_target_thb: 0
  preferred_locations: ['Remote', 'Bangkok']
  acceptable_locations: []
  time_zone_overlap_with: 'UTC+7'
  min_hours_overlap: 4
  preferred_stages: []
  blacklist_companies: []
  work_authorization:
    citizenship: ''
    visa_status: ''
  per_run_caps:
    listings_reviewed: 20
    applications_proposed: 5
    applications_submitted: 3
  sources:
    jobsdb_url: ''
    linkedin_url: ''

defaults_for_linkedin:
  audience: []
  audience_NOT_for: []
  operator_themes: []
  anti_themes: []
  operator_rss_feeds: []
```

Fill what you want persisted; leave others empty. Secretary uses session-time spawn brief values FIRST, falls back to this file SECOND. Operator can override per-session even if file has values.

## Part 3 — Tune approval policy (~5 min)

Current `approval_policies` on project 599:
- **auto_deny**: destructive account actions (delete account, unsubscribe-all, cancel subscription), financial actions (pay/purchase/subscribe/upgrade)
- **require_attention** (HITL): submit application, post/publish, send email/reply, send DM/connection

Tighten / relax via:
```bash
curl --silent -X PATCH -H "X-Project-Id: 599" -H "Content-Type: application/json" \
  http://localhost:8456/api/projects/599 \
  --data @<your-edited-policy-file>.json
```

Most operators leave defaults until first test reveals friction.

## Part 4 — Budget sanity (~2 min)

Current: $5/day, $50/month. Adjust if needed:
```bash
curl --silent -X PATCH -H "X-Project-Id: 599" -H "Content-Type: application/json" \
  http://localhost:8456/api/projects/599 \
  --data '{"budget_daily_usd":"10.00","budget_monthly_usd":"100.00"}'
```

## Part 5 — First test session (Mode A flow)

When you sit down to test:

1. **Open Claude Code CLI**
2. **Bind to secretary project**:
   ```
   secretary ครับ
   ```
3. **Confirm bootstrap** — Lead announces session bound to secretary (id=599)
4. **Run a workflow with operator_context inline**:

   ### Email triage example (start here — lowest risk)
   ```
   triage today's inbox.
   context:
     name: <your name>
     signature: <Best,/Cheers,/etc>
     tone_for_unknowns: formal-warm
     priority_senders: <list emails/domains>
     auto_archive_overrides: []
   cap: 20
   ```

   ### Job apply example (after email triage works)
   ```
   find 5 matching jobs on jobsdb + linkedin.
   context:
     name: <your name>
     email: <email>
     resume_path: <absolute path>
     target_roles: [CTO, Head of Engineering]
     must_have_skills: [{name: 'agent orchestration', weight: 20}]
     salary_floor_thb: <number>
     location: [Bangkok, Remote-SEA]
     stages: [Series A-C startup]
     sources:
       jobsdb_url: <pasted filtered URL>
       linkedin_url: <pasted filtered URL>
     propose_max: 5
     submit_today_max: 3
   ```

   ### LinkedIn post example
   ```
   draft a linkedin post on <topic>.
   context:
     audience: <list>
     audience_NOT_for: [hustle-bros, AI hypers]
     operator_themes: [<3-5 themes>]
     stance_for_this_post: contrarian-but-respectful
     length_target: 250
   ```

5. **Watch HITL pauses** — Lead surfaces them in chat (Mode A flow, NOT via Kanban). Respond with `approve / reject / edit_draft / skip`
6. **End of day**: `end-of-day digest` — Lead reads secretary's day output + renders to chat per `daily-digest-template.md`

### Alternative: paste from `operator-context.md`

If you filled `general/operator-context.md` (Part 2), instead of typing the full `context:` block, just say:
```
triage today's inbox using my saved context. cap: 20.
```
Lead reads `general/operator-context.md` → passes relevant fields to secretary.

## Part 6 — Capture feedback during test

Keep `_scratch/secretary-test-day-1-notes.md` as you go:
- Which fields you typed manually vs which were in operator-context.md (signals what to persist)
- Workflows that worked vs broke
- Voice mismatches (drove redrafts)
- Token cost actually consumed (`GET /api/projects/599` → `estimated_cost_usd`)
- Time saved vs doing manually

Feeds Phase 2 priorities (Web Push, digest delivery, browser-tools in langgraph).

## Optional smoke test tonight (~5 min)

After Chrome MCP setup, do a tiny dry-run:
```
secretary ครับ
```
→ Wait for bootstrap.
```
verify chrome mcp connection. take a screenshot of my current gmail tab.
```
→ Should return a Gmail screenshot. If not → Chrome MCP setup issue.

```
list what operator_context fields you'd need for an email triage workflow.
```
→ Lead surfaces secretary's required-fields list. You'll see exactly what to type tomorrow.

This catches Chrome MCP + spawn-brief shape issues WITHOUT consuming real tokens on browser work.
