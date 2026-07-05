# /zb-jobs log <update>

Write a status update or new candidate entry to the master tracker.

**Pre-conditions:**
- `Write` and `Edit` are ONLY used in this verb. All other verbs are READ-ONLY.
- Operator must have explicitly approved the update (either in the current session or via prior go-signal).

**Steps:**

1. Read the latest tracker (mandatory — never overwrite stale content).
2. Identify the target section and row (A-row, NEW section, SPOTTED, SKIP, etc.).
3. Apply the minimum surgical edit:
   - Status update on an A-row: update the `ผล` column with date + outcome.
   - New application logged: add A-row with next sequence number, all columns populated.
   - New spotted role: add row to 👀 SPOTTED table.
   - Skip confirmed: move to ❌ SKIP table with reason.
4. Use `Edit` for surgical row-level changes. Do NOT rewrite whole sections.
5. After edit: read back the changed section to verify. Report the before → after diff in chat.
6. If this is a rejection status update, prompt: "Run `zb-jobs postmortem <A-row>` to analyse the rejection?"

**Tracker location:**
`C:\Users\banku\Documents\Personal\Projects\WebApp\secretary\jobs-search\job-search-tracker.md`
