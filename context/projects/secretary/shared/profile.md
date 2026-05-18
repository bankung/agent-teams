# Profile: Identity & Session Injection Convention

**Purpose:** Define how operator identity and preferences reach the secretary agent at runtime. This file documents the CONVENTION for session-time identity injection — NOT the identity itself (which lives in operator_context, sourced from Lead's spawn brief or persistent gitignored file).

**Repository context:** These files are git-tracked; operator PII NEVER lives here. Instead, identity reaches secretary via two channels in priority order:

1. **Lead's spawn brief** (`operator_context` field) — the preferred channel. Lead extracts operator's chat input and passes identity inline to this agent.
2. **Persistent fallback** (`context/projects/secretary/general/operator-context.md`, gitignored) — optional secondary channel. Operator may store frequently-used values here for reuse across sessions.

**Precedence rule:** On any field present in both channels, **the spawn brief value wins**. Persistent file is for convenience, not override.

## Critical fields by workflow

**Source:** Secretary agent definition, lines 100–105. Each row lists required PII fields; missing any field = STOP and escalate to Lead.

| Workflow | Required Fields | Notes |
|---|---|---|
| **email-triage** | `name`, `signature` | Reply drafts need name + sig. Optional overlays: `priority_senders` (VIP inbox), `auto_archive_overrides` (site-specific exceptions), `mentor_friends_casual` (tone override for specific senders), `read_dont_process` (folders to skip), `skip_folders` (inbox sections to ignore) |
| **job-apply** | `name`, `email`, `phone`, `linkedin_url`, `resume_path`, `target_roles`, `must_have_skills`, `salary_floor`, `location_preferences`, `work_authorization`, `sources` | Sources = URLs for JobsDB + LinkedIn job boards to search. Resume path must be absolute (`/path/to/resume.pdf`). Location preferences = list of remote-OK cities or "remote-only". Work auth = visa sponsorship need (boolean or country list). |
| **linkedin-post** | `linkedin_handle` | Required for attribution sanity. Optional overlays: `operator_themes` (list of personal theme pillars), `audience` (target persona), `audience_NOT_for` (exclusion list), `operator_rss_feeds` (personal subscription list), `stance_for_this_post` (POV override for one post) |
| **daily-digest** | none | Synthesizes from `general/` outputs; no PII required. |

## Channel-specific overrides (added 2026-05-18 per #1176)

Operator may use multiple email channels (Gmail + Outlook/hotmail confirmed 2026-05-18). Signature handling differs per channel:

| Channel | Signature handling | operator_context field |
|---|---|---|
| Gmail (`bankung99@gmail.com`) | Secretary draft uses `operator_context.signature` (manual append) | `signature: "Best,\nThanit"` |
| Outlook/hotmail (`bankung99@hotmail.com`) | Outlook AUTO-INSERTS its own configured signature at compose body cursor — Lead-direct flow does NOT need to append (would cause duplication) | `outlook_signature_override: <string>` ONLY if operator wants to clear Outlook's auto-sig and use a different one (rare; usually accept Outlook's default) |

### When to use which channel

- **Default (`channel` not specified)**: Gmail for triage + send (Gmail is operator's primary)
- **`channel: outlook`**: route to Outlook for triage or send — secretary uses `outlook.live.com` URLs + handles auto-sig per channel-UI-differences in `email-rules.md`
- **Cross-channel**: cross-account sends validated (Gmail→Hotmail + Hotmail→Gmail) — Lead-direct compose with appropriate URL deeplink per `.claude/docs/url-deeplink-tricks.md`

### PII reminder

Outlook's auto-signature contains operator phone (`+66 ...`). When operating on Outlook compose:
- Do NOT echo auto-sig content back to chat unnecessarily
- For Lead-direct sends to NON-operator addresses, accept the auto-sig (it's intentional contact info)
- For ANY workflow that would screenshot/read_page the Outlook compose UI, minimize follow-up references to phone digits

## How to read identity fields at session start

1. **Check Lead's spawn brief** for `operator_context` object. Extract all fields present.
2. **Check `context/projects/secretary/general/operator-context.md`** (if file exists). Extract fields NOT already in spawn brief.
3. **Validate against the table above:**
   - If any **required field** for the chosen workflow is missing → STOP. Return list of missing fields to Lead. Lead prompts operator to provide; re-spawn with the answer.
   - If **optional fields** are missing → proceed with sensible defaults (e.g., empty `priority_senders` list = no VIP senders, use standard reply tone).

## Spawn brief structure example (for Lead reference)

```
operator_context:
  name: <placeholder>
  signature: <placeholder>
  email: <placeholder>
  phone: <placeholder>
  linkedin_url: https://linkedin.com/in/<placeholder>
  target_roles: ["role1", "role2"]
  must_have_skills: ["skill1", "skill2"]
  salary_floor: <number or null>
  location_preferences: ["remote", "city1", "city2"]
  priority_senders: ["sender@domain.com"]
  auto_archive_overrides: {
    "newsletter@provider.com": "keep-important"
  }
```

## What NOT to store here

- Passwords, API keys, tokens — operator logs in once via Chrome MCP; secretary uses that session.
- Full address, SSN, government ID — never needed for workflows.
- Credit card numbers — job applications don't ask for payment.
- Detailed salary history — only floor/ceiling needed for filtering.

## Persistent file location (gitignored fallback)

If operator chooses to store frequently-used identity in the persistent fallback, the file path is:
```
context/projects/secretary/general/operator-context.md
```

This file is `.gitignore`-d (kept private). Secretary reads it automatically at session start if the spawn brief is incomplete. Operator is responsible for updating it; Lead does NOT auto-populate it.

## Handoff to Lead: identity gaps

If secretary detects a missing required field at task start, the halt message is:

```
HALT: required field(s) missing for workflow '<workflow>': <field1>, <field2>
— Lead extract from operator's chat + re-spawn with operator_context populated
```

See `failure-modes.md` for the full escalation protocol.
