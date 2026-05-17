# Workflow brief — Email triage

> Spawn template for `secretary` agent. Lead reads this when operator says "triage inbox" / "check email" / similar.
>
> Lead spawn invocation (copy as Agent prompt):
> `Triage operator's inbox per context/projects/secretary/shared/email-rules.md. Read knowledge base first. Volume cap: 50 unread (configurable in rules). Stash classifications + drafts in general/triage-{date}.md. HITL pause every reply / forward / non-auto-archive action. Return summary per agent definition output format.`

## Pre-flight (Lead checks before spawn)

- [ ] `secretary/shared/email-rules.md` exists and has no `[TODO]` markers (else: tell operator to fill before triage can run)
- [ ] `secretary/shared/profile.md` has signature info (for draft signing)
- [ ] `secretary/shared/voice.md` exists (for reply tone)
- [ ] Chrome MCP connected + Gmail logged-in (check `list_connected_browsers` if uncertain)
- [ ] Today's date directory exists: `context/projects/secretary/general/{YYYY-MM-DD}/` (create if missing)

If any pre-flight fails → Lead reports to operator + halts (don't spawn).

## Secretary's expected workflow

1. **Read knowledge base** (mandatory):
   - `shared/email-rules.md` — classification rules
   - `shared/profile.md` — operator identity for signature
   - `shared/voice.md` — reply tone preferences
2. **Open Gmail** via `mcp__Claude_in_Chrome__navigate("https://mail.google.com/")`
3. **Read inbox** via `mcp__Claude_in_Chrome__read_page` (filter to unread)
4. **For each unread email** (cap = 50, configurable in email-rules.md):
   - Extract: sender, subject, snippet (first 200 chars body)
   - Apply rules in order → classify into one of: auto_archive / reply_now / reply_later / escalate / forward_to
   - If `auto_archive` → click Archive button (no HITL)
   - If `reply_now` → draft reply in `general/{YYYY-MM-DD}/email-draft-{n}.md` per voice.md → HITL pause "Approve reply to [sender] about [subject]?" with options `[approve, reject, edit_draft]`
   - If `reply_later` → append to `general/{YYYY-MM-DD}/triage-reply-later.md`
   - If `escalate` → append to `general/{YYYY-MM-DD}/triage-escalations.md` with 2-line context
   - If `forward_to` → draft forward in `general/{YYYY-MM-DD}/email-forward-{n}.md` → HITL pause
5. **Update triage state**: write current timestamp to `general/triage-state.json` (so next run knows where to start)
6. **Generate summary** per agent definition output format
7. **Report to Lead** — counts + HITL queue + draft file paths

## HITL question template

```
question: "Approve reply to {sender_name} re: {subject_truncated_80}?"
options: ["approve", "reject", "edit_draft"]
```

Draft body lives in the draft file; question_payload shouldn't inline it (too long). Lead's digest renders the file pointer.

## Failure modes (secretary must report, not work around)

- Gmail interface changed / unable to find Archive button → report to Lead, don't retry
- Chrome session expired → report "operator must re-login to Gmail" + halt
- Unread count > 100 → halt at 50 + report "{N} more unread; should I continue?"
- Sender not in any rule + no clear default → classify as `escalate` with explanation in escalation log
- Drafting fails voice.md anti-pattern check 2x → halt + escalate "can't draft per voice for this kind of email"

## Per-run output

`general/{YYYY-MM-DD}/triage-summary.md`:
```markdown
# Triage summary — {YYYY-MM-DD HH:MM}

- Unread processed: N (cap was 50)
- Auto-archived: N
- Replies drafted (HITL pending): N
- Reply-later stashed: N
- Escalations: N
- Forwards drafted (HITL pending): N
- Skipped (rule conflict / missing context): N

## HITL queue
- Task #IDs: ...

## Drafts ready
- Files: ...

## Skipped — needs operator decision
- ...
```

## Operator-facing summary format (Lead renders to chat)

```
📧 Triage done — 47 emails

✅ Auto-archived: 31 (newsletters, receipts, GitHub notifications)
📝 Replies drafted (need your approval): 3
  - #1109 — Recruiter Phi @ Mango Tech — declined politely with door-open
  - #1110 — Hiring manager Sarah @ Acme — accepted interview slot
  - #1111 — Colleague Wit @ current company — quick "got it" reply
📥 Reply-later (saved for batch): 8
⚠️ Escalations (your call): 5
  - 2 about job offers
  - 1 about contract renewal at current company
  - 1 invoice from accountant
  - 1 unknown sender mentioning a "business opportunity"
🚫 Skipped: 0

Drafts: context/projects/secretary/general/2026-05-17/
```

## Tuning hooks (operator can adjust)

- **Volume cap per run**: edit `email-rules.md` → "Triage cadence" section
- **What auto-archives**: edit `email-rules.md` → "Auto-archive rules" section
- **Reply tone**: edit `voice.md` → "Tone profile / Email to known colleagues"
- **Signature**: edit `profile.md` → "Identity" + "One-line summary"
