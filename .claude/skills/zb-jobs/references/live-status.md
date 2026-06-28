# /zb-jobs live-status <role>

Check if a posting is still accepting applications.

**Steps:**

1. Identify the source URL/id: JobsDB id, LinkedIn id, company careers link, or MP reference.
2. Fetch the apply page:
   - JobsDB: `https://th.jobsdb.com/job/<id>` via WebFetch
   - LinkedIn: `https://www.linkedin.com/jobs/view/<id>` via WebFetch (ToS — read only)
   - Company careers page: direct URL via WebFetch
   - Michael Page: MP ref link via WebFetch
3. Look for: "Apply now" button, "No longer accepting applications", "Position filled", "Closed",
   posting date vs today's date (if >90 days old on JobsDB, likely stale).
4. Report: status (OPEN / CLOSED / UNKNOWN) · last-seen date · apply link if open.
5. If CLOSED and A-row exists in tracker → flag for reconcile (remove from active pipeline).
