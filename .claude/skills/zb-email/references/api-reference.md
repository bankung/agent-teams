# zb-email — API Reference

Full HTTP reference for all email endpoints. All endpoints require `-H "X-Project-Id: 599"`.
All bodies are `Content-Type: application/json`.

Base URL: `http://localhost:8456/api/tools/email`

---

## §1. Provider capability matrix

| Capability        | Gmail | Outlook |
|-------------------|-------|---------|
| search            | yes   | yes     |
| get (full body)   | yes   | yes     |
| thread            | yes   | no      |
| attachment        | yes   | no      |
| labels (list)     | yes   | no      |
| archive           | yes   | yes     |
| mark read/unread  | yes   | yes     |
| draft             | yes   | yes     |
| trash             | yes   | yes (moves to Deleted Items) |
| auth-status       | yes   | yes     |
| usage             | yes   | no      |

**Outlook is a subset of Gmail.** Thread, attachment, label listing, and usage
counters are Gmail-only. Outlook query syntax is Microsoft Graph KQL (NOT Gmail
syntax — do not translate between them).

---

## §2. API reference

### 2a. Auth / status

| Endpoint | Method | Body | Notes |
|----------|--------|------|-------|
| `/auth/gmail/start` | POST | none | returns `{auth_url}` — open in browser |
| `/auth/gmail/callback` | GET | query params: `code`, `state` | no X-Project-Id needed (Google redirect) |
| `/auth/gmail/status` | GET | none | returns `{authenticated, email, expires_at, calendar_readonly}` |
| `/auth/outlook/start` | POST | none | returns `{auth_url}` |
| `/auth/outlook/callback` | GET | query params: `code`, `state` | no X-Project-Id needed |
| `/auth/outlook/status` | GET | none | returns `{authenticated, email, expires_at}` |

If either status returns `authenticated: false`, report the OAuth start URL and
STOP. Do not attempt read or write operations on an unauthenticated provider.

### 2b. Gmail READ (auto-allow tier — no HITL)

**Search**
```
POST /gmail/search
{"query": "<gmail-query>", "max_results": 10}
```
- `max_results`: 1–50 (hard ceiling). Default 10.
- Returns `{results: [{id, thread_id, from, subject, date, snippet}], count}`.
- PRIVACY: query + subject + snippet are NOT logged by the server.
- Gmail query gotcha: `subject:` operator treats hyphen as EXCLUDE.
  `subject:job-application` = "job" AND NOT "application". Use
  `subject:"job-application"` (quoted) or hyphen-free terms instead.

**Get (full message)**
```
POST /gmail/get
{"message_id": "<hex-id>"}
```
- Returns `{id, thread_id, from, to, subject, date, body_text}`.
- `body_text` may be empty (some HTML-only messages) — handle gracefully.

**Thread**
```
POST /gmail/thread
{"thread_id": "<hex-id>"}
```
- Returns `{thread_id, messages: [{id, from, to, subject, date, body_text}], count}`.

**Labels (list)**
```
POST /gmail/labels
{}
```
- Returns `{labels: [{id, name, type}], count}`.
- Use this to discover label ids before referencing them.

**Attachment**
```
POST /gmail/attachment
{"message_id": "<hex-id>", "attachment_id": "<att-id>"}
```
- `attachment_id` comes from `payload.parts[].body.attachmentId` in the Gmail API
  (not surfaced directly by /gmail/get — requires knowing the att-id from the raw
  message structure). In practice, run search + get to identify attachments, then
  call this with both ids.
- Returns `{filename, mime_type, size, data_base64}`.
- 413 if attachment > 10 MB. 404 if not found.

**Usage**
```
GET /gmail/usage
```
- Returns `{date, units_consumed, cap, remaining}` for the current UTC day.
- Check before bulk operations. If `remaining` is low, warn the operator.

### 2c. Outlook READ (auto-allow tier — no HITL)

**Search**
```
POST /outlook/search
{"query": "<graph-kql-query>", "max_results": 10}
```
- `max_results`: 1–50.
- Outlook query syntax is Microsoft Graph KQL: `from:foo@bar.com AND subject:invoice`.
  NOT identical to Gmail. Do not translate.

**Get (full message)**
```
POST /outlook/get
{"message_id": "<graph-id>"}
```
- Returns same shape as GmailGetResponse: `{id, thread_id, from, to, subject, date, body_text}`.
- Graph message ids are ~150 chars base64url (much longer than Gmail hex ids).

### 2d. Gmail MUTATE (modify tier — OPEN; report count+ids after each fire)

All modify-tier calls are OPEN (no operator-proof token required), but every
mutate MUST be preceded by HITL: show the operator what will be affected
(count + ids + subjects from prior search), then STOP and wait for explicit go.

**Mark read/unread**
```
POST /gmail/mark
{"message_ids": ["<id>", ...], "read": true|false}
```
- `read: true` = mark read (remove UNREAD label).
- `read: false` = mark unread (add UNREAD label).
- Returns `{modified_count, modified_ids, errors}`.
- Add `?force=true` query param to bypass the bulk-threshold gate (>100 messages).

**Archive**
```
POST /gmail/archive
{"message_ids": ["<id>", ...]}
```
- Removes the INBOX label (messages stay in All Mail, not deleted).
- Returns `{modified_count, modified_ids, errors}`.
- Add `?force=true` for >100 messages.

**Draft**
```
POST /gmail/draft
{"to": "<addr>", "subject": "<subject>", "body": "<plain-text>"}
```
- Creates a draft in Gmail Drafts. Does NOT send.
- Returns `{draft_id, message_id}`.
- `subject` and `body` default to empty string if omitted.
- Drafts must be sent by the operator manually (send is explicitly NOT supported
  by this API — see Safety section).

### 2e. Outlook MUTATE (modify tier — OPEN; same HITL rule)

**Mark read/unread**
```
POST /outlook/mark
{"message_ids": ["<id>", ...], "read": true|false}
```
- Returns `{modified_count, modified_ids, errors}`.

**Archive**
```
POST /outlook/archive
{"message_ids": ["<id>", ...]}
```
- Moves to the well-known "archive" folder (not Deleted Items).

**Draft**
```
POST /outlook/draft
{"to": "<addr>", "subject": "<subject>", "body": "<plain-text>"}
```
- Returns `{draft_id, message_id}`.

### 2f. TRASH — DELETE TIER (operator-proof required; always use --dry-run first)

Trash is the `delete` tier. The operator-proof gate (#1859) is currently DORMANT
(fail-open when `OPERATOR_ACTION_KEY` is unset), but treat every trash call as
requiring explicit operator confirmation regardless — this is enforced by the
HITL rule, not just the gate.

**Gmail trash**
```
POST /gmail/trash        # dry_run is a BODY field, NOT a URL param — ALWAYS preview first
{"query": "<gmail-query>", "dry_run": true}      # query mode (XOR with message_ids)

POST /gmail/trash
{"message_ids": ["<id>", ...], "dry_run": true}  # id mode (XOR with query)
```
- `dry_run: true` (BODY field; `?dry_run=true` in the URL is IGNORED and WILL DELETE) returns `{trashed_count:0, would_affect_count:N, would_affect_ids:[...], dry_run:true}`.
  No messages are moved. No units charged for the trash step (list units still charged
  in query mode because the upstream list call resolves ids).
- After dry-run, show operator: count + ids (and subjects from prior search). Wait for go.
- Then fire WITHOUT `dry_run`:
  ```
  POST /gmail/trash
  {"query": "..."}    or    {"message_ids": [...]}
  ```
- Returns `{trashed_count, trashed_ids, errors}`.
- `?force=true` bypasses the bulk-threshold gate (>100 messages). Do NOT pass `force`
  without showing the operator the count.
- Gmail trash = moves to Trash folder (30-day auto-purge). NOT permanent delete.

**Outlook trash**
```
POST /outlook/trash
{"query": "<kql-query>", "dry_run": true}    or    {"message_ids": [...], "dry_run": true}
```
- Same dry_run semantics (dry_run is a BODY field). Outlook trash = moves to Deleted Items.
- Outlook query syntax is KQL, not Gmail syntax.
