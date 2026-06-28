# /zb-jobs postmortem <application>

Reconstruct a rejected application → compare vs JD → rank rejection causes → improvement actions.

**Pattern (per Kanban #1952 rejection-analysis):**

1. **Reconstruct submission:** read tracker A-row for the application (role, company, date, source, CV version used).
   Use `zb-email sweep` output or `zb-email search` to find the original application email/confirmation
   and the rejection email. Read both via `zb-email read`.
2. **Retrieve JD:** if a JobsDB id or URL is in the tracker, fetch the JD via WebFetch.
   If no longer live, check `_jd-scrape/` folder:
   `C:\Users\banku\Documents\Personal\Projects\WebApp\secretary\jobs-search\_jd-scrape\`
3. **Gap analysis:** compare what was submitted (CV version, cover letter framing) vs what JD required.
   Identify: missing skills, seniority framing mismatch, over-tier signals (CTO-framing to below-tier seat),
   salary-field entry (if Easy Apply — per #1952 easy-apply-hygiene rule: log submitted salary + fields).
4. **Rejection cause ranking:** score each potential cause (1 = most likely):
   1. Over-tier framing (senior-level CV to below-tier seat = flight-risk screen)
   2. Hard-skill gap (stated required skill not evidenced in CV)
   3. Comp mismatch (submitted salary > budget)
   4. Volume / automated screen (>100 applicants, keyword mismatch)
   5. Timing (role already filled internally before posting closed)
5. **Improvement actions:** concrete, actionable per cause (e.g., "re-tier CV for IC-level roles",
   "add PostgreSQL project to CV Section 2", "anchor desired salary to posted band floor").
6. Present: timeline table + gap analysis + ranked causes + actions. Note: do NOT re-recommend the
   same role unless the posting reopens and operator explicitly asks.
