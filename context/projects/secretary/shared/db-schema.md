# Database schema — secretary

> **N/A for this project.** Secretary stores no DB rows of its own. State lives in:
> - **Operator-curated knowledge base**: `secretary/shared/{profile,voice,email-rules,job-criteria,linkedin-strategy}.md`
> - **Per-run state**: `secretary/general/{YYYY-MM-DD}/` directories (drafts, logs, session notes)
> - **Cross-run state**: `secretary/general/{triage-state.json, applications-{YYYY-MM}.md, linkedin-log-{YYYY-MM}.md}`
> - **Platform-shared rows**: Kanban tasks under `project_id=599` (HITL queues, audit_report, health_alert, etc.) — owned by agent-teams platform schema
>
> If secretary later needs a dedicated table (e.g., longitudinal application-status tracking with structured queries), file the schema here at that time. Until then this file is intentionally empty.
