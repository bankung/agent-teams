# agent-teams — backlog roadmap & grooming

**Generated:** 2026-05-31 (post v0.4.0 release). Living doc — update as work lands.

> **Priority note:** `tasks.priority` (1=LOW..4=URGENT) is NOT a reliable signal in this project — large feature epics are tagged P1=LOW and real bugs P1=LOW. The tiers below are **judgment-based** (impact + dependency + bug-first), not the raw priority field.

---

## A. Stale / duplicate — recommend close or reconcile

| Task | Finding | Action |
|---|---|---|
| **#974**, **#1322** | `.claude/teams/content.md` already authored (12.8KB). Both ask to "write/draft content.md". | **Close** (superseded by the existing file) |
| **#1622** | "Author content.md + confirm roster" — likely the one that authored it. | Verify roster confirmed → **close** (or keep a tiny "confirm roster" remainder) |
| **#1272** | "Verify 17 new agents load post-restart" — content-*/seo-*/sem-*/data-*/thai-proofreader are all in the live spawnable roster. | **Close** (agents confirmed loadable) |
| **#1603** | Email-module parent. AC#0/#1/#2 show pending but are factually DONE (module + router exist; Phase 0 shipped via #1604/#1611; token DB via #1712; Outlook via #1608/#1711). | **Reconcile ACs → close**; only doc-AC (#4) may remain → trim to a tiny followup |
| **#1709** (in-progress) | Board USAGE/P&L collapse shipped (a34239a); operator has now seen the board has vertical space. | **Confirm visual → close** |
| **#975 / #1130** | Mode-B / langgraph items partly overlapped by the now-validated harness (#1710 + #1717 DONE — gemini-harness). | **Review vs current harness**; fold into the Mode-B epic, rescope or close stale parts |

---

## B. Prioritized roadmap (tiers)

### T0 — Grooming (do now; clears ~5-7 tasks)
Execute section A: close #974/#1322/#1272, reconcile+close #1603, confirm+close #1709, review #975/#1130. Verify+close #1622.

### T1 — Bugs first (clear real bugs before features)
- **#1614** — approval-policies-gate.ps1 Layer-A match-all bug + change default fall-through ask→allow. *(security-relevant gate — highest real priority here)*
- **#1673** — Integrations popup "Failed to fetch" in operator browser (works on localhost) → client API-base / origin mismatch.
- **#1729** — Outlook #1721 follow-up nits: nextLink host-check (SSRF) + pagination test + router `body.query or ""`.
- **#1624** — web 500 (corrupt .next) after FE hot-reload. *Root-caused 2026-05-31:* `next build` against a live `next dev` container overwrites `.next` → chunks 404. Mitigation = never run `next build` against the live container; recover via `docker compose -p agent-teams restart web`. → add a guard / close.
- **#1454** — investigate 78-file context/ working-tree deletion during docker compose build (data-loss class; relates to worktree bind-mount drift).

### T2 — Foundational unblockers (Mode-B runtime + full-auto)
This cluster kills the scheduler-noise root cause (#1728) and unblocks headless automation. Sequence:
- **#1652** — verify Mode-B runtime/dependency gap → operator design decision (gate before resuming headless).
- **#1728** — scheduler piles up unexecuted [schedule:] fires (no executor) → pause-vs-dedup-vs-retention. *(depends on the #1652/executor decision)*
- **#776 → #781** — full-auto mode (auto-approve + auto-pickup) → decision policy + halt-and-queue.
- **#1191** — Mode-B langgraph engine (persistent worker, context reuse, headless-ready).
- Fold in #975 / #1130 after review.

### T3 — Feature epics (large; sequence by dependency DAG)
**Platform "X.*" infrastructure — do FIRST (data + social depend on it):**
- #1302 (project_resources schema) → #1309 (resources API) → #1315 (resources panel UI) → #1316 (attach-to-task UI)
- #1303 (task_templates schema) → #1310 (template picker UI)
- #1304 (pre-task cost forecast) · #1305 (task output viewer)

**Data team "D.*"** (blocked on platform infra #1301/#1303):
- #1306 / #1307 (data container) / #1308 (auto-scaffold) → #1313 (run-code docs) / #1314 (seed templates)

**Social team "S.*"** (depends on #1303 templates):
- #1317 (team migration) → #1318 (playbook) / #1319 (auto-scaffold) → #1320 (seed templates) · #1321 (voice setup, deferred)

**Agent-gallery "AG" UI** (standalone 12-task epic — schedule when X.* settles):
- #1016 (metadata schema + validator) is the natural first; then #1017 (gallery list+UI), #1018 (enable/disable), #1019 (hot-reload UX), #1020 (cost preview), #1021 (tool-scope viz), #1022 (designer wizard), #1023 (hook presets), #1024 (Lead override), #1025 (onboarding wizard), #1026 (test-spawn), #1015 (cookbook).

### T4 — Methodology / housekeeping / nice-to-have
- **Standards promotion:** #969 (Thai-prose taxonomy → standards), #970 (truth_spec framework → standards).
- **Housekeeping:** #1583 (compact decisions.md 227KB), #1296 → #1297 (test-pattern catalog → B-audit), #1225 (Docker/WSL2 RAM), #1382 (pnl N+1 refactor), #1128 (optimistic locking on PATCH /api/tasks).
- **Perf:** #1187 (per-agent model tier routing) · #1189 (self-throttling pre-spawn) · #1200 (investigate cost-savings hypothesis) · #1677 (per-task model override).
- **Secretary-niche:** #1587 (mailbox cleanup), #1585 (expanded email perms + HITL), #1387 (agent voice female/upbeat), #1108 (Obsidian-vault KB), #1132 (Mode-A capture).
- **UI nice-to-have:** #1000 (approval inbox /inbox), #1005 (comment thread), #1008/#1013 (inline AC edit), #1014 (approval-policy authoring UI), #1292 (burndown charts), #1582 (onboarding tour), #1457 (HITL inbox phase 2), #1678 (zero-LLM memory search).
- **Misc/integration:** #806 (Kanban-as-MCP adapter), #1086 (DeepSeek provider), #1474/#1475 (backup recovery + drill), #1015-cookbook.
- **External-integration / notification plumbing:** #1439 (external-notify rate-limit+retry), #1275 / #1424 (PreToolUse authz hook patterns + audit-trail-to-gate), #1388 (local-cron LinkedIn/JobsDB response check + push), #1470 (web test framework + phone E2E smoke).
- **Audit/auditor followups:** #1222 (stale-doc curator), #1223 (auto-propose runbook stubs), #1233/#1239/#1240/#1261/#1243/#1244 (GOV-series followups), #1213 (threshold drift), #1265 (cache-hit verify), #1263 (public-repo hygiene Phase 2B).
- **netops:** #1643 (in-progress, blocked on operator: move agents + restart, tracked by #1649) · #1649 (smoke-gate).

---

## C. Dependency quick-map (who blocks whom)
- #1301/#1302/#1303 (platform schema) → block most of X.* / data / social.
- #1296 → #1297 (B-audit waits on test catalog).
- #1317 → #1318/#1319 (social playbook waits on team migration).
- #1652/executor decision → #1728 / #776 / headless resume.
- #972 (agent specs) → #974 (content playbook) — now moot (content.md exists).
