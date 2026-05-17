# Email triage rules

> **Lead is the only writer of this file.** Operator dictates; Lead writes.
>
> Used by secretary for: classifying every inbox message into action buckets. Rules apply in order — first match wins. Default action if no rule matches: **escalate** (operator decides).

## Action buckets

- **auto_archive** — secretary archives without asking (no HITL pause)
- **reply_now** — secretary drafts reply + HITL pause for operator approve/edit
- **reply_later** — secretary stashes in `general/triage-<date>.md` under "reply when you have time"
- **escalate** — secretary surfaces to operator with summary; operator decides
- **forward_to** — secretary drafts forward + HITL pause (target email in rule)

## Priority senders [TODO — operator fills]

Senders whose email is ALWAYS classified `reply_now` regardless of content:

- [TODO e.g. "boss@currentcompany.com — reply_now"]
- [TODO e.g. "domain @oldcompany.com — escalate (might be lay-off / reference)"]
- [TODO e.g. "specific recruiters operator wants to keep warm"]

## Auto-archive rules [TODO — operator fills]

Patterns that are safe to archive without asking (newsletter subscriptions, expected receipts, notifications operator has already seen):

- Sender matches `*@newsletter.example.com` → auto_archive
- Sender matches `noreply@*` AND subject contains "receipt" → auto_archive
- [TODO list operator's specific safe-archive senders]

## Reply-later rules [TODO]

Patterns that don't need immediate attention but operator wants to see:

- Sender in `[mentor1, friend1, ...]` AND subject not urgent → reply_later
- [TODO]

## Escalation rules [TODO]

Patterns that ALWAYS escalate (don't auto-action — operator needs to decide):

- Subject contains "offer" OR "interview" OR "contract" → escalate
- Subject contains "invoice" OR "payment" → escalate
- Sender contains "legal" OR "lawyer" OR "@lawfirm" → escalate
- Sender unknown AND subject mentions money / business / opportunity → escalate
- [TODO operator-specific signals]

## Reply-now patterns [TODO]

Patterns that should be drafted + HITL-paused for operator approval same day:

- Sender in `[priority_senders]` → reply_now
- Subject contains "blocked" OR "urgent" OR "asap" → reply_now
- Subject is reply to operator-sent email less than 48h ago → reply_now
- [TODO]

## Drafting hints (per sender / topic)

When secretary drafts a reply, use these hints to shape the response:

### Recruiter outreach (when classified reply_now)

- **Default response template**:
  ```
  Hi [name],

  Thanks for reaching out about [role at company]. [Brief signal of interest level — see job-criteria.md].

  [If interested]: I'd like to learn more. Could you share:
  - [3 specific questions per job-criteria.md must-haves]

  [If not interested]: Not a fit for me right now because [1 concrete reason — keep door open].

  Best,
  [operator name]
  ```
- HITL question shape: "approve reply to recruiter [name] about [role]? Draft says: [draft]"

### Known colleague / friend (informal)

- **Default style**: short, direct, voice per `voice.md` casual-context
- HITL question shape: "approve reply to [name]?"

### Cold outreach from unknown sender (low signal)

- Default action: **escalate** (don't auto-draft — operator may not want to engage)

### Meeting requests

- If from priority sender + calendar shows free → draft accept with operator's standard meeting link
- If conflicts → propose 2-3 alternatives from calendar
- HITL pause always (operator may want to decline)

## Excluded folders / labels

Secretary IGNORES emails in these folders/labels (operator-managed elsewhere):

- [TODO e.g. "Promotions, Spam, Forums — Gmail auto-categorized"]
- [TODO e.g. "personal-finance — operator handles manually"]

## Read-don't-process rules

Some messages secretary should NOT classify (operator's call only):

- Family / personal life messages — sender list: [TODO]
- Financial statements / banking — sender pattern: [TODO]

## Triage cadence

- **Per-run scope**: only unread emails since `last_triage_at` (stored in `general/triage-state.json`)
- **Frequency**: when Lead asks ("triage inbox" command), not autonomous
- **Volume cap**: if >50 unread, secretary triages first 50 + reports remaining count + asks Lead whether to continue

## Operator fill checklist

- [ ] Priority senders (3-10 senders)
- [ ] Auto-archive patterns (2-5 patterns covering biggest noise sources)
- [ ] Escalation patterns (sensitive topics)
- [ ] Reply-now patterns (urgency signals)
- [ ] Drafting hints for recruiter / cold-outreach / meeting templates
- [ ] Excluded folders/labels (what to skip entirely)
- [ ] Read-don't-process senders (personal life / finance)

**Time estimate**: 15-25 min — most can be inferred by scanning your inbox for the past week and noting patterns.
