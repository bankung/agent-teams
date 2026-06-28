# zb-email — open / triage / status verbs

---

## 5l. `triage [cap N]` (default N=20)

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

---

## 5p. `status`

Quick cross-inbox count overview. READ-ONLY.

1. Auth status for both providers.
2. Gmail unread count: `POST /gmail/search {"query": "is:unread", "max_results": 1}`
   (use `count` in response as a rough indicator; for exact count see Gmail usage).
3. Outlook unread: `POST /outlook/search {"query": "isRead:false", "max_results": 1}`.
4. Gmail usage: `GET /gmail/usage`.
5. Report: authenticated providers, approximate unread counts, daily units used.

---

## 5q. `open [--jobs] [--since Nd]` (READ-ONLY digest)

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
