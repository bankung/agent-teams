# Workflow brief — Calendar prep

> Spawn template for `secretary` agent. Lead reads this when operator says "calendar prep" / "next 3 days briefing" / "what's on this week" / similar.
>
> Mode A (Chrome MCP — needs operator's authenticated Google Calendar / Outlook session).

## Pre-flight (Lead checks)

- [ ] Lead extracted `operator_context` — REQUIRED: `name`, `signature`; recommended: `prep_horizon_days` (default 3), `prep_focus` (default "external meetings only"), `priority_attendees` (auto-flag if calendar event includes these)
- [ ] Chrome MCP connected + Calendar app logged-in (Google Calendar at https://calendar.google.com OR Outlook at https://outlook.live.com)
- [ ] Today's date directory exists: `context/projects/secretary/general/{YYYY-MM-DD}/`

If pre-flight fails → halt + report.

## Secretary's expected workflow

1. **Read frameworks + operator_context**:
   - `shared/voice.md` — reply tone for any prep notes that include drafted messages
   - `operator_context` from spawn brief — name + signature + horizon + focus + priority_attendees
2. **Open Calendar** via `mcp__Claude_in_Chrome__navigate("https://calendar.google.com/")` (or outlook URL)
3. **Read calendar** via `read_page` — extract events for `prep_horizon_days` from today
4. **Classify each event**:
   - `external_meeting` — has attendees outside operator's domain (recruiter, client, vendor) → PREP
   - `internal_meeting` — only colleagues → light prep only
   - `solo_block` — no other attendees (focus time, gym, lunch) → skip
   - `recurring_no_change` — same as last week, no agenda update → skip
   - `cancelled` — log but don't prep
5. **For each event needing prep** (>= 30 min OR `external_meeting`):
   - Identify attendees (LinkedIn lookup via Chrome MCP if name unfamiliar → research role + recent post)
   - Read attached docs / meeting notes if linked (Google Doc / Confluence / etc.)
   - Read prior thread context if recurring (last meeting's notes if findable)
   - Draft briefing note in `general/{YYYY-MM-DD}/calendar-prep/{event-slug}.md`:
     - Event time / duration
     - Attendees + 1-line role each
     - Stated agenda (from event description)
     - Suggested talking points (2-3 bullets based on attendees + context)
     - Pre-read materials (links to docs operator should skim)
     - Questions operator might want to ask
6. **Identify scheduling conflicts** (overlapping events, back-to-back without buffer, late-night meetings)
7. **Return to Lead** with: count of events / prep notes written / conflicts found / suggested focus

## Auto-execute (no HITL)

- Read calendar
- Read attached docs (Google Docs operator already has access to)
- Lookup attendees on LinkedIn (public profile data)
- Draft prep notes locally
- Identify conflicts

## Always HITL pause

- Send meeting accept / decline (Mode A — operator approves via chat)
- Reschedule existing event (operator approves)
- Forward calendar invite (operator approves)
- Add new event with external attendee (operator approves)
- Any action that modifies the calendar (read-only by default; write needs explicit approval)

## HITL question template

For meeting decisions:
```
question: "{event_title} on {date_time} — accept / decline / propose alternative?"
options: ["accept", "decline_polite", "propose_alternative", "skip"]
```

For prep summary review:
- No HITL — prep notes stay local in `general/` until operator opens them. Operator reads on their own.

## Pattern matching

### External meeting prep is HIGH-VALUE — invest research time
- Recruiter call → research recruiter's company + their LinkedIn
- Sales call → research vendor's product + pricing model
- Investor call → research investor's portfolio + recent investments
- Job interview → research interviewer's background + company's recent news

### Internal recurring meeting prep is LOW-VALUE — skip unless agenda explicit
- 1:1 with manager → only prep if explicit agenda items in invite
- Team standup → no prep (real-time format)
- Recurring strategy meeting with same group → no prep unless operator says "prep this one"

## Failure modes

- Calendar UI not navigable via Chrome MCP (Google Calendar UI changes) → halt + screenshot + report
- Attendee LinkedIn lookup fails (private profile / not found) → log + continue prep without that attendee context
- Event has no description / agenda → flag as "no context — operator may want to add agenda before meeting"
- Conflict found but operator's calendar has tentative status → escalate ("two events overlap — both tentative; want me to decide?")
- Calendar permission scope mismatch (Chrome MCP can read but not write) → halt + report "operator must enable calendar write in their browser"

## Per-run output

`general/{YYYY-MM-DD}/calendar-prep-summary.md`:
```markdown
# Calendar prep — {YYYY-MM-DD HH:MM}

Horizon: next {N} days
Events scanned: N
Events needing prep: M
Conflicts found: K

## Prepped events
- {event-slug} — {time} — {attendees} — see general/{date}/calendar-prep/{slug}.md

## Conflicts
- {event1} overlaps {event2} on {date}

## No-prep events
- {count} — recurring / solo / internal

## Skipped
- {count} — cancelled / past
```

## Operator-facing summary (Lead renders)

```
📅 Calendar prep — next 3 days

12 events scanned → 4 needed prep, 2 conflicts found

## Prep ready
- Fri 3pm — Interview with Sarah @ AcmeCo (45min) → notes at general/2026-05-19/...
- Mon 11am — Recruiter call Anna @ Mango → notes ready
- Tue 2pm — Vendor demo Atlas Inc → quick prep
- Wed 10am — 1:1 manager (agenda: roadmap Q3) → bullet points drafted

## Conflicts (your call)
⚠️ Mon 11am Anna conflicts with team standup 11-11:30
⚠️ Wed 10am 1:1 ends at 11am, back-to-back with 11am demo (no buffer)

## Suggested focus
Block 30 min Thu morning for interview prep deeper dive (Sarah's company just announced funding).
```

## Tuning hooks

- **Horizon**: operator inline `horizon: 7` (look 7 days ahead instead of default 3)
- **Focus filter**: operator inline `focus: external_only` or `focus: all`
- **Priority attendees**: in `operator-context.md` `defaults_for_calendar.priority_attendees` (always-prep regardless of event type)
- **Prep depth**: operator inline `prep_depth: deep` (more research per event — costs more tokens) or `light`
