---
name: tn-email
description: >-
  Secretary email operations across Gmail and Outlook. Use when the operator asks
  to check, search, read, triage, or manage email; mentions inbox, unread, drafts,
  attachments, threads, or job-application mail; asks to archive, mark, trash, or
  draft a reply; mentions the secretary inbox; or asks for an open-mail digest.
  All mutation actions are HITL-gated — always show count + ids before/after
  any write. Requires X-Project-Id 599 (secretary project). READ-ONLY until
  explicit operator go-signal on each mutate/triage/clean verb.
argument-hint: >-
  search <query> [--cap N] | read <id> | thread <id> | attachment <id> <att-id> |
  labels | auth-status [gmail|outlook] | usage |
  mark read|unread <ids...> | archive <ids...> | trash <ids|query> [--dry-run] |
  draft <to> <subject> <body> |
  triage [N] | sweep-jobs | clean <category> | phishing-scan | status |
  open [--jobs] [--since Nd]
allowed-tools:
  - Bash(curl:*)
  - Read
---

# /tn-email — secretary email operations playbook

`$ARGUMENTS` contains the verb and its options. Follow this playbook exactly.

---

## 0. Project-id binding (MANDATORY on every call)

Email endpoints are scoped to the **secretary project (id=599)**. This is FIXED —
do NOT read `_runtime/lead_project_id.txt` for email calls.

Every `curl` to `http://localhost:8456/api/tools/email/*` MUST carry:

```
-H "X-Project-Id: 599"
```

If the operator is running a different active project, that project's id governs
Kanban calls; email calls always use 599 regardless.

---

## 1. Provider capability matrix

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

## 2. API reference (base: `http://localhost:8456/api/tools/email`)

All endpoints require `-H "X-Project-Id: 599"`.
All bodies are `Content-Type: application/json`.

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

---

## 3. HITL gate (every mutate — non-negotiable)

Before any POST to `/gmail/mark`, `/gmail/archive`, `/gmail/draft`,
`/gmail/trash`, `/outlook/mark`, `/outlook/archive`, `/outlook/draft`,
`/outlook/trash`:

1. Run READ calls (search / get) to identify the affected messages.
2. Show the operator: count, message ids, subjects/senders, the action you intend.
3. STOP. Wait for explicit go-signal ("yes", "proceed", "do it", "ok").
4. Fire the mutate. Report result: `modified_count`/`trashed_count`, ids, any errors.

For trash specifically: always run `dry_run=true` first and show results before
the real call. The dry-run response IS the HITL preview.

**After every mutate, report:**
- Action taken
- Count affected
- Ids affected (full list or first 20 + "…N more")
- Any errors from the `errors` array

---

## 4. Safety gates (hard rules — no exceptions)

- **NO send.** There is no `/gmail/send` or `/outlook/send` endpoint. Do not attempt
  to fire a draft via any other path. Drafts sit in the Drafts folder until the
  operator sends manually.
- **NO hard delete.** Trash is the only delete operation (moves to Trash / Deleted
  Items). Permanent deletion is explicitly denied in the policy manifest.
- **NO link clicks.** Never open URLs found in email content via any tool. If a URL
  is relevant, show it to the operator and let them decide.
- **Phishing guard.** When reading email, flag: urgent/threatening language,
  mismatched sender domain, unexpected attachment requests, unusual payment/credential
  requests. Report flags to the operator; do NOT act on linked content.
- **PII guard.** body_text, subject, sender, and attachment data from fetched messages
  MUST NOT be written to any log file, _scratch file, or Kanban task body. Summarize
  in chat output only.
- **Label add/remove.** There is no dedicated `/gmail/label` endpoint for arbitrary
  label mutations. The only label operations available are: mark (UNREAD add/remove)
  and archive (INBOX remove). If the operator needs arbitrary label mutations, note
  this gap and escalate to the secretary-backend team.

---

## 5. Verb playbooks

### 5a. `search <query> [--cap N]`

1. Check auth: `GET /auth/gmail/status` + `GET /auth/outlook/status`.
2. Search both inboxes (or Gmail only if Outlook not authenticated):
   ```
   POST /gmail/search  {"query": "<query>", "max_results": <N or 10>}
   POST /outlook/search {"query": "<kql-equivalent>", "max_results": <N or 10>}
   ```
   Note: you must manually translate the query concept to KQL for Outlook — the
   syntaxes differ; do NOT pass the Gmail query string to Outlook verbatim.
3. Display results grouped by provider: from / subject / date / snippet.

### 5b. `read <id>`

1. Determine provider from id format (Gmail ids are short hex ~16 chars; Outlook ids
   are long base64url ~150+ chars). Or ask the operator if ambiguous.
2. `POST /gmail/get {"message_id": "<id>"}` or `POST /outlook/get {"message_id": "<id>"}`.
3. Display: from, to, subject, date, body_text. Summarize if long.
4. Note: `body_text` may be empty for HTML-only messages.

### 5c. `thread <id>` (Gmail only)

```
POST /gmail/thread {"thread_id": "<id>"}
```
Display each message in the thread: from / date / body_text summary.

### 5d. `attachment <msg-id> <att-id>` (Gmail only)

```
POST /gmail/attachment {"message_id": "<id>", "attachment_id": "<att-id>"}
```
Display: filename, mime_type, size. Offer to summarize content if text-based.
Never write attachment data to any file.

### 5e. `labels` (Gmail only)

```
POST /gmail/labels {}
```
Display the label list (id + name + type).

### 5f. `auth-status [gmail|outlook]`

Check status for the named provider (or both if unspecified):
```
GET /auth/gmail/status
GET /auth/outlook/status
```
Report: authenticated, email, expires_at.
If not authenticated, show the start URL.

### 5g. `usage` (Gmail only)

```
GET /gmail/usage
```
Report: units_consumed / cap / remaining for today.

### 5h. `mark read|unread <ids...>`

HITL gate: confirm ids + action with operator first.
- Parse provider from id length (short=Gmail, long=Outlook).
- Fire the appropriate `POST /{provider}/mark` with `read: true|false`.

### 5i. `archive <ids...>`

HITL gate: confirm ids with operator first.
- `POST /gmail/archive {"message_ids": [...]}` or `POST /outlook/archive {"message_ids": [...]}`.

### 5j. `trash <ids|query> [--dry-run]`

1. ALWAYS run dry-run first regardless of `--dry-run` flag:
   ```
   POST /gmail/trash   (or /outlook/trash)   # dry_run goes in the BODY
   {"message_ids": [...], "dry_run": true}   or   {"query": "...", "dry_run": true}
   ```
2. Show would_affect_count + would_affect_ids + subjects (from prior search results).
3. STOP. Wait for operator go-signal.
4. Fire without dry_run. Report trashed_count + trashed_ids + errors.

### 5k. `draft <to> <subject> <body>`

HITL gate: show the draft details to operator before creating.
```
POST /gmail/draft {"to": "<to>", "subject": "<subject>", "body": "<body>"}
```
Report: draft_id, message_id.
Remind operator: draft will NOT send until they open Gmail and send manually.

### 5l. `triage [cap N]` (default N=20)

READ-only diagnosis phase, then HITL-gated action phase.

Phase 1 — read:
1. `POST /gmail/search {"query": "is:unread", "max_results": <N>}`
2. `POST /outlook/search {"query": "isRead:false", "max_results": <N>}` (if authenticated)
3. For each result, optionally call `POST /gmail/get` or `POST /outlook/get` on
   messages with non-obvious subjects (to read body for categorization).

Phase 2 — categorize into buckets:
- **Needs reply** — direct questions/requests to operator
- **Informational / FYI** — newsletters, receipts, automated notifications
- **Job-related** — application responses, recruiter outreach
- **Action item** — deadlines, confirmations needed
- **Trash candidates** — promotional, no-reply, expired

Phase 3 — propose:
Report the categorized list. For each bucket, propose actions (archive, mark read,
draft reply, trash). STOP and wait for operator approval per bucket.

Phase 4 — execute:
After per-bucket operator approval, fire the agreed mutate calls. Report results.

### 5m. `sweep-jobs`

Synthesizes job-application email tracking. READ-ONLY unless operator approves action.

1. Search Gmail and Outlook for job-related mail:
   ```
   POST /gmail/search  {"query": "subject:(application OR interview OR offer OR rejection OR recruiter)", "max_results": 50}
   POST /outlook/search {"query": "subject:application OR subject:interview OR subject:offer", "max_results": 50}
   ```
2. Get body for any thread that looks like an application response.
3. Reconcile against the canonical job tracker (owned by the `tn-jobs` skill) at
   `C:\Users\banku\Documents\Personal\Projects\WebApp\secretary\jobs-search\job-search-tracker.md`
   to cross-reference the already-applied A-rows.
   IMPORTANT: the tracker LAGS reality — always read the latest. For the actual
   reconcile/dedup logic, hand off to `tn-jobs reconcile` (tn-jobs owns job logic).
4. Report: untracked responses, interviews to confirm, offers/rejections to note.
5. HITL: propose any archive/mark-read for processed threads. Wait for approval.

### 5n. `clean <category>`

Targeted bulk cleanup within a named category (e.g. "newsletters", "receipts").

1. Search for the category pattern (operator may supply or confirm the query).
2. Dry-run trash or archive — show count + sample subjects.
3. HITL: operator approves, adjusts, or rejects.
4. Execute after approval. Report results.

### 5o. `phishing-scan`

READ-ONLY review of recent/unread mail for suspicious signals.

1. `POST /gmail/search {"query": "is:unread", "max_results": 20}`
2. For each, check:
   - Sender domain mismatch (display name vs actual From address)
   - Urgency language ("verify immediately", "account suspended", "click now")
   - Unexpected attachment types (.exe, .zip from unknown senders)
   - Requests for credentials, payment, or personal info
3. Report flagged messages with reason. Do NOT open links. Recommend operator
   actions (mark spam, delete). Wait for go before any mutate.

### 5p. `status`

Quick cross-inbox count overview. READ-ONLY.

1. Auth status for both providers.
2. Gmail unread count: `POST /gmail/search {"query": "is:unread", "max_results": 1}`
   (use `count` in response as a rough indicator; for exact count see Gmail usage).
3. Outlook unread: `POST /outlook/search {"query": "isRead:false", "max_results": 1}`.
4. Gmail usage: `GET /gmail/usage`.
5. Report: authenticated providers, approximate unread counts, daily units used.

### 5q. `open [--jobs] [--since Nd]` (READ-ONLY digest)

Generates a digest of open / actionable email. Default `--since 30d` unless specified.
This is READ-ONLY — propose actions but do NOT execute unless operator gives explicit go.

**Definition of "open":**
- Threads where the last inbound message is unanswered (operator has not replied)
  AND the thread is not older than `--since` threshold
- Important unread (starred, important-flagged, question/request subjects)
- Job-application responses awaiting a decision
- Explicitly EXCLUDES: newsletters, promotions, noreply senders, receipts/order-confirmations,
  threads where operator replied last, automated notifications

**Steps:**

1. Check auth for both providers.
2. Gmail queries (adjust `older_than`/`newer_than` per `--since`):
   ```
   POST /gmail/search {"query": "is:unread newer_than:30d", "max_results": 50}
   POST /gmail/search {"query": "is:starred OR is:important newer_than:30d", "max_results": 20}
   ```
3. Outlook queries:
   ```
   POST /outlook/search {"query": "isRead:false", "max_results": 50}
   ```
4. For each candidate, call `POST /gmail/get` or `POST /outlook/get` to read
   body/thread context as needed (prioritize threads with question words or
   explicit requests in subject).
5. Filter OUT: senders matching `noreply@`, `no-reply@`, `donotreply@`;
   subjects matching `(receipt|order|shipment|invoice|confirmation|newsletter|unsubscribe)`.
6. If `--jobs` flag: additionally run `sweep-jobs` logic (step 5n) and include
   application responses in the digest.
7. Group output into three priority buckets:
   - **Need reply** — unanswered threads with direct questions/requests
   - **Decide** — offers, invitations, approvals pending operator decision
   - **Follow-up** — things operator sent that haven't received a response
8. For each item: `from | subject | age | why-open | suggested-action`.
9. STOP after digest. Do NOT execute any suggested action without explicit go.

---

## 6. Error handling

| HTTP status | Meaning | Action |
|-------------|---------|--------|
| 401 | Not authenticated | Show OAuth start URL; stop. |
| 403 | Tool grant denied or operator-proof required | Report and stop; do not retry with different role. |
| 413 | Attachment too large (>10 MB) | Report to operator; skip that attachment. |
| 429 | Daily cap reached | Report usage snapshot; stop until next UTC day. |
| 400 + bulk_threshold | >100 items without force | Show count; ask operator to confirm; retry with `?force=true` after approval. |
| 502 | Upstream Gmail/Graph error | Retry once; if still failing, report and stop. |

---

## 7. Auth refresh reminder

Token store is in-memory (lost on container restart). If `auth-status` shows
`authenticated: false` after a recent restart, the OAuth flow must be restarted.
Report the start URL and stop.

---

## 8. References and KB notes

- Policy manifest (tier model, approval modes): `_runtime/secretary-email-policy.json`
- Secretary project context: `WebApp/secretary/shared/` (read-only reference)
- NOTE: the pre-#1604 Chrome-based email-triage.md brief in the secretary project KB
  (`WebApp/secretary/shared/`) documents the old browser-driven approach.
  It is now STALE. The canonical approach is this API-based skill. A separate task
  (filed on the secretary project) should update that KB file — this skill cannot
  write to project 599's shared folder.
- Daily-units cap: shared across Gmail + Outlook operations. Monitor with `tn-email usage`.
- The `label add/remove` verb in the original skill spec has NO dedicated endpoint.
  The only label operations available via the API are UNREAD (via mark) and INBOX
  (via archive). Arbitrary label mutations require a future backend endpoint.

---

## Usage examples

```
/tn-email auth-status
/tn-email status
/tn-email search "is:unread from:recruiter@" --cap 20
/tn-email read 18b3f1a2c9d4e5f6
/tn-email thread 18b3f1a2c9d4e5f0
/tn-email open --jobs --since 14d
/tn-email triage 10
/tn-email sweep-jobs
/tn-email trash "from:noreply@newsletter.com older_than:60d" --dry-run
/tn-email archive 18b3f1a2 18b3f1a3
/tn-email mark read 18b3f1a2 18b3f1a3
/tn-email draft "hiring@acme.com" "Re: Application" "Thank you for the update..."
/tn-email phishing-scan
/tn-email clean newsletters
```
