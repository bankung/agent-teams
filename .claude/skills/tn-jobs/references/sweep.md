# /tn-jobs sweep

Sweep both inboxes for job-application mail (responses, interviews, rejections). Classify.

**Steps:**

1. Invoke tn-email `sweep-jobs` — this searches both Gmail and Outlook for application-response mail.
   It returns threads grouped by type (response/interview/reject/no-update). Use its output directly.
2. For any thread that needs more body context, invoke `tn-email read <id>` or `tn-email thread <id>`.
3. Classify each response into:
   - **Interview invited** — explicit scheduling request or phone screen
   - **Rejection** — "regret to inform", "not moving forward", "unsuccessful"
   - **Holding pattern** — "under review", "will be in touch"
   - **Offer/negotiation** — offer details, terms, next steps
   - **Recruiter outreach (new)** — inbound recruiter, not from an existing application
   - **Noise** — automated acknowledgement, no-reply, newsletter
4. **Dedup / tracker match:** for each classified response, identify the matching A-row in the tracker
   (by company + role title). Flag if no A-row found (untracked application).
5. Present summary table: A-row # · company · role · current tracker status → found email status.
   Highlight mismatches (tracker says "รอผล" but email says "ปฏิเสธ").
6. Propose `reconcile` as a natural follow-up. STOP.
