# /zb-jobs comp-rank

Rank the ACTIVE pipeline by comp band first, title second.

**Steps:**

1. Read the latest tracker. Collect all ACTIVE rows: NEW (🔵⚠️⚪), SPOTTED, plus pending A-rows where result = "รอผล".
2. For each active role, extract comp:
   - Stated salary range from tracker or JD
   - If "undisclosed": use market estimate from `feedback_comp_first_strategy.md` heuristic
     (big-co Director/Head undisclosed = OK, flag as "est. above floor" for ranking purposes)
3. Sort by:
   - Primary: salary band LOW-END descending (high → low)
   - Tie-breaker: title tier (CTO/VP > Head/Director > IC)
4. Annotate each: floor match and sweet-spot flag per the salary floor / sweet-spot bands defined in
   `feedback_comp_first_strategy.md`; comp-first strategy fit (per that file).
5. Present ranked table: rank · role · company · salary (stated/est) · comp tier · status · note.
6. STOP. Do not auto-recommend applications from this ranking without operator approval.
