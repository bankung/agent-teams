# /tn-jobs mine-alerts

Mine JobsDB and Michael Page job-alert emails for new roles, match vs criteria, dedup vs tracker.

**Steps:**

1. Invoke tn-email to pull job alert emails from both inboxes:
   - Gmail: `tn-email search "from:jobsdb OR from:michaelpage subject:(job alert OR new jobs)" --cap 30`
   - Outlook: equivalent KQL search via tn-email
2. For each alert email, invoke `tn-email read <id>` to get the body (alert emails contain listing summaries).
3. Extract roles: for each listing in the email body, capture:
   - Role title · Company · Location · Salary (if stated) · Source (JobsDB id or MP ref)
4. **Dedup pass** (Section 1 — mandatory): cross-check every extracted role against tracker A-rows, SKIP, stop-list.
   Discard already-applied/skipped. Flag YELLOW companies explicitly.
5. **Score new roles** using the rubric in `job-criteria.md` (4-category weighted score).
   Apply comp-first ranking: sort by salary band first, title second (per `feedback_comp_first_strategy.md`).
6. Present:
   - New roles table: rank · title · company · salary · score · flag (NEW/YELLOW/verify-needed)
   - Deduped/skipped: brief list (role + reason)
7. STOP. Do not prepare bundles until operator approves.
