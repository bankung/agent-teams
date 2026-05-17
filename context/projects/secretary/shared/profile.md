# Operator identity — session-time injection convention

> **DO NOT fill PII into this file.** Operator identity (name, email signature, phone, resume path, exact employer, etc.) MUST be passed per-session, NEVER persisted to repo. Reasoning: repo is git-tracked; identity should be ephemeral + revocable at the conversation level.
>
> This file documents **how** operator provides identity each time secretary runs.

## Two ways to provide identity

### Option 1 — Inline at session start (RECOMMENDED)

When operator opens Claude Code + binds to secretary project, type identity context inline with the first workflow command. Lead pulls fields out and passes to secretary's spawn brief.

**Example session start:**

```
operator: secretary ครับ
Lead:     [bootstraps, binds project_id=599]

operator: triage today's inbox.
          context for this session:
            name: <Full Name>
            signature: <how you sign off — first name only, "Best, X", etc.>
            tone for unknowns: <formal-warm | casual | crisp>
            priority senders: <2-5 emails/domains that always reply_now>
            auto-archive: <patterns like "newsletter@*", "noreply+receipts@stripe.com">

Lead:     [spawns secretary with operator_context above]
```

### Option 2 — Personal note file on disk (LOCAL ONLY, gitignored)

If operator wants persistence across sessions WITHOUT git tracking, save a personal note at:

```
context/projects/secretary/general/operator-context.md
```

This folder is **gitignored** (`general/` is per-role ephemeral state — see `.gitignore` line `context/projects/secretary/general`). Files inside survive across sessions but never get committed.

Use this convention if you find typing identity at every session annoying. Lead will check the file at session start and surface a "loaded from general/operator-context.md" confirmation.

**Risk:** the file lives on operator's disk. If operator backs up their machine to cloud, identity backs up too. Operator's call.

## What identity fields secretary actually consumes

For email triage:
- **name** + **signature** (for draft signing)
- **tone preferences** (overlays `voice.md` defaults)
- **priority senders** (overlays `email-rules.md` rule shapes)
- **auto-archive list** (overlays)

For job application:
- **name** + **email** + **phone** + **LinkedIn URL** (for form prefill)
- **resume path on disk** (for HITL-pause upload via Chrome MCP file_upload)
- **target roles** (filters scoring)
- **salary floor** (filters scoring)
- **location preferences** (filters scoring)
- **work authorization** (excludes jobs requiring sponsorship operator can't provide)

For LinkedIn post:
- **name** (post attribution — already in operator's LinkedIn session)
- **themes operator wants to be known for** (filters topic candidates)
- **anti-themes** (rejects unwanted topics)

For ALL workflows:
- **language preference** (Thai / English / mixed) — overlays `voice.md` "Language mix"

## Operator session-start template (copy + adapt)

Save this in a personal note (outside repo) so it's easy to paste:

```
context for this session:
  name: <Full English name as used on CV / LinkedIn>
  signature: <how you sign off informally>
  email: <primary email; bankung99@gmail.com is the public sample but use your real if different per context>
  phone: <if needed for job forms>
  linkedin_url: <https://linkedin.com/in/...>
  resume_path: <absolute path on your machine>
  target_roles: <list — e.g. CTO, Head of Engineering, Staff/Principal>
  target_companies: <stage / industry / specific companies>
  salary_floor: <currency + amount>
  location: <Bangkok / remote / hybrid preference>
  work_authorization: <citizenship / visa status if relevant>
  tone_unknowns: <formal-warm | casual | crisp>
  language: <Thai / English / mixed>
  priority_senders: <list>
  auto_archive: <patterns>
  themes_to_be_known_for: <2-3 themes>
  anti_themes: <topics to avoid publicly>
```

Trim to what's relevant for the workflow you're starting. Email triage doesn't need salary_floor; job apply doesn't need themes_to_be_known_for; etc.

## What this file does NOT contain (intentionally)

- ❌ Operator's actual name
- ❌ Operator's resume path
- ❌ Target job titles / companies
- ❌ Salary numbers
- ❌ Email priority sender list
- ❌ Any other PII

If you see PII in this file, it's a regression — strip and replace with the convention text above. Lead is the only writer; subagents flag the regression in their final report.
