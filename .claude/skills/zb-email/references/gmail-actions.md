# zb-email — gmail-actions verbs (mark / archive / trash / draft / clean)

All verbs in this file are HITL-gated. See §3 in the thin SKILL.md index before executing.
Trash additionally requires dry-run first and is DELETE TIER (§2f, §4).

---

## 5h. `mark read|unread <ids...>`

HITL gate: confirm ids + action with operator first.
- Parse provider from id length (short=Gmail, long=Outlook).
- Fire the appropriate `POST /{provider}/mark` with `read: true|false`.

---

## 5i. `archive <ids...>`

HITL gate: confirm ids with operator first.
- `POST /gmail/archive {"message_ids": [...]}` or `POST /outlook/archive {"message_ids": [...]}`.

---

## 5j. `trash <ids|query> [--dry-run]`

1. ALWAYS run dry-run first regardless of `--dry-run` flag:
   ```
   POST /gmail/trash   (or /outlook/trash)   # dry_run goes in the BODY
   {"message_ids": [...], "dry_run": true}   or   {"query": "...", "dry_run": true}
   ```
2. Show would_affect_count + would_affect_ids + subjects (from prior search results).
3. STOP. Wait for operator go-signal.
4. Fire without dry_run. Report trashed_count + trashed_ids + errors.

---

## 5k. `draft <to> <subject> <body>`

HITL gate: show the draft details to operator before creating.
```
POST /gmail/draft {"to": "<to>", "subject": "<subject>", "body": "<body>"}
```
Report: draft_id, message_id.
Remind operator: draft will NOT send until they open Gmail and send manually.

---

## 5n. `clean <category>`

Targeted bulk cleanup within a named category (e.g. "newsletters", "receipts").

1. Search for the category pattern (operator may supply or confirm the query).
2. Dry-run trash or archive — show count + sample subjects.
3. HITL: operator approves, adjusts, or rejects.
4. Execute after approval. Report results.
