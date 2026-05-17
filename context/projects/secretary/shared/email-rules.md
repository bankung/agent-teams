# Email triage rules — generic patterns + classification framework

> **PII (specific senders, employer domains, mentor names) is session-time injected**, NOT persisted here. This file holds the GENERIC rule shapes that apply to any operator.

## Action buckets (canonical)

- **auto_archive** — secretary archives without HITL (no pause)
- **reply_now** — secretary drafts reply per voice.md → returns to Lead for operator approve/edit
- **reply_later** — secretary stashes in `general/triage-{date}.md` under "reply when you have time"
- **escalate** — secretary surfaces to operator with 1-line summary; operator decides
- **forward_to** — secretary drafts forward → returns to Lead for operator approve

## Generic patterns (operator-agnostic, persisted)

### Always auto_archive (zero PII — pure shape)

```yaml
- sender_match: noreply@*               # Most "noreply" addresses
- sender_match: notifications@*         # Generic notification senders
- sender_match: hello@*newsletter*      # Newsletter naming convention
- subject_match: "^Re: Confirmation"    # Auto-confirmation chains
- subject_match: "*receipt*"            # Receipts (operator can override per sender)
- subject_match: "*shipping notification*"
- header_match: List-Unsubscribe        # Bulk mail header → almost certainly a list
```

If operator wants to OVERRIDE one (e.g., LinkedIn notification contains a job match they care about) → session-time inject "do NOT auto-archive notifications-noreply@linkedin.com today".

### Always escalate (sensitive — pure shape, no PII)

```yaml
- subject_contains_any: ["offer", "interview", "contract", "termination"]
- subject_contains_any: ["invoice", "payment", "wire", "refund"]
- sender_contains_any: ["@lawfirm", "@law.com", "legal@"]
- subject_contains_any: ["lawsuit", "subpoena", "compliance", "audit"]
- subject_contains_any_unknown_sender: ["business opportunity", "investment opportunity", "partnership"]
```

### Always reply_now (urgency signals — pure shape)

```yaml
- subject_contains_any: ["urgent", "asap", "blocker", "blocked", "critical", "down"]
- subject_starts_with: ["Re:", "Fwd:"] AND last_outbound_in_thread_within_hours: 48
```

### Reply_later default (low urgency signal)

```yaml
- sender_was_replied_to_in_last_7_days: true
  AND subject_does_not_contain: urgency_words
```

## Session-time overlays (operator injects per-session)

These are PII-heavy + change frequently. Operator passes inline at session start or stores in `general/operator-context.md` (gitignored):

```yaml
priority_senders:
  # Senders ALWAYS reply_now regardless of subject
  - <boss-email>
  - <key-client-domain>

trusted_senders:
  # Senders whose emails should NOT auto-archive (overrides newsletter pattern)
  - <specific newsletter operator actually reads>

blacklist_senders:
  # Senders to ALWAYS auto-archive (operator-specific spam)
  - <ex-vendor still emailing>

skip_folders:
  # Folders/labels secretary IGNORES (operator handles elsewhere)
  - "Promotions"
  - "Personal Finance"
  - <operator-specific>

read_dont_process:
  # Senders secretary should NOT classify at all (sensitive personal)
  - <family members>
  - <banking>

mentor_friends_casual:
  # Known relationships → reply tone = casual
  - <list>
```

## Classification algorithm

```
1. Check skip_folders / read_dont_process → skip entirely
2. Check escalate patterns → return escalate
3. Check priority_senders → return reply_now
4. Check blacklist_senders → return auto_archive
5. Check auto_archive patterns → return auto_archive
6. Check trusted_senders override → reclassify reply_later or reply_now per subject
7. Check reply_now patterns → return reply_now
8. Default → reply_later
```

First match wins. Operator overrides via inline session-time hints.

## Triage cadence

- **Per-run scope**: only unread emails since `last_triage_at` (stored in `general/triage-state.json` per session)
- **Volume cap**: 50 unread per run (secretary halts + reports + asks operator to continue beyond)
- **Frequency**: on-demand only (Lead spawns when operator says "triage inbox"; no scheduled triggers in Mode A)

## Drafting hints by reply type

When secretary drafts a `reply_now` response, use these conventions:

### Recruiter outreach (sender = recruiter, subject mentions opportunity)
- Default disposition: ask 3 specific clarifying questions per `job-criteria.md` must-haves (operator session-time provides those)
- Door-open close even if declining
- Length budget: 80-120 words

### Known colleague (casual tone)
- Direct, short, voice per `voice.md` casual context
- Length budget: 30-80 words

### Cold outreach unknown sender (low-context, low-relevance)
- Default action: **escalate** (don't auto-draft — operator may not want to engage)
- Exception: if escalation signal AND sender mentions a job operator's targets match → draft a "tell me more" 3-line response

### Meeting requests
- If from priority_senders + calendar shows free → draft accept with operator's standard meeting link
- If conflicts → propose 2-3 alternative times
- Always HITL (operator may want to decline)

## Per-run output

`general/triage-{YYYY-MM-DD}/triage-summary.md`:
```
- Unread processed: N (cap was 50)
- Auto-archived: N
- Replies drafted: N (HITL pending — operator approves)
- Reply-later stashed: N
- Escalations: N
- Forwards drafted: N (HITL pending)
- Skipped (rule conflict / missing context): N
```

## Tuning hooks

- **Generic patterns**: edit this file (rare — affects all sessions)
- **Session-time overlays**: operator types inline or edits `general/operator-context.md`
- **Reply tone defaults**: edit `voice.md` Tone framework
- **Volume cap per run**: operator says "cap at N" inline or edit "Triage cadence" above
