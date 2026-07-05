# /zb-jobs reconcile

Cross-reference email findings (from sweep) vs tracker A-rows. Apply factual status updates; flag ambiguous.

**Steps:**

1. Read the latest tracker (mandatory dedup read).
2. Take the sweep output (or re-run sweep if not already done this session).
3. For each A-row in the tracker with status "รอผล" (awaiting result):
   - Check if sweep found a matching email response.
   - If found rejection → propose status update: mark A-row result column "❌ ปฏิเสธ <date> (<source>)".
   - If found interview invite → propose: mark "📅 นัด interview <date>".
   - If found offer → propose: mark "✅ Offer received <date>".
   - If no email found → leave "รอผล" unchanged.
4. Flag ambiguous cases (multiple matches, unclear company identity, Confidential/MP references).
5. Show proposed changes as a diff table: A-row # · before → after. STOP and wait for operator approval.
6. After approval: apply updates via the `log` verb (see `references/log.md`).
