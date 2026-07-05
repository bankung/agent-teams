# /zb-jobs deep-dive <company/role>

Company profile + role scope + comp + requirements. Dedup vs tracker/stop-list first.

**Steps:**

1. **Dedup check** (Section 1): is this company/role already in the tracker (applied, skipped, or stop-listed)?
   If stop-listed → report and STOP (do not research further unless operator overrides).
   If YELLOW → proceed with explicit flag.
2. Research sources (parallel):
   - Company website careers page (WebFetch)
   - LinkedIn company page (WebFetch — ToS compliant read)
   - JobsDB posting if a job id is known (WebFetch)
   - Michael Page / recruiter page if applicable (WebFetch)
3. Compile:
   - **Company profile:** industry · size · ownership · BKK presence · culture signals
   - **Role scope:** title · seniority · team size · direct reports · reporting line · key responsibilities
   - **Requirements:** must-have skills · experience bar · language requirements · any hard-stops (SAP required? sponsorship?)
   - **Comp:** stated range or market estimate · base vs package · equity/bonus mentions
   - **Red flag scan:** cross-check against red flags in tracker Section 3 + `project_job_search_red_flags.md`
4. Score using `job-criteria.md` rubric. Apply comp-first ranking.
5. **Recommendation:** PROPOSE (apply / skip / investigate further / operator decides). Do NOT begin preparing bundle until operator approves.
