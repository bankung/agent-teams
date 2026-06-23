# tn-email — sweep-jobs verb

## 5m. `sweep-jobs`

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
