---
purpose: bootstrap hot-read INDEX for decisions.md (1 line per decision)
updated: 2026-06-23
covers: active decisions.md 2026-05-20 onward; older in decisions-archive-2026-05.md
---

# decisions INDEX -- agent-teams

> **This is the always-read hot artifact** (it replaces full-reading `decisions.md`, which had
> grown to ~62K tokens and was truncated at bootstrap). Scan the list below; when a task touches
> an entry's area, **pull its full body on demand** -- `grep -n '#<id>' decisions.md` then read
> that section, or `GET /api/projects/1/shared/search?mode=discovery&q=<terms>` (#1678 BM25).
> Do NOT full-read decisions.md at bootstrap.
>
> **Graceful floor:** every entry line is regenerable any time via
> `grep -E '^## [0-9]{4}-' decisions.md | sed 's/^## /- /'` -- so this index can never go blind. Curated
> enrichment (scope tags, short summaries) lands lazily ON TOP of this floor.
>
> **[CRIT]** = anti-re-litigation decision (do-not-revisit without reopening the entry). Pull its
> body before touching that area.

- 2026-06-24 — #5 Mode-A automation: Telegram-only async HITL + 4-ring runner autonomy boundary; tasks A #2564 → B #2565 → C #2566 (+ runner #2531) under v0.8.0 #50
- 2026-06-21 — #2520 story-doc layer was REDUNDANT, not neglected — sharpened story-vs-decisions trigger
- [CRIT] 2026-06-21 — #2506 recurrence dormancy accepted as-is (dedup-bounded; executor deferred) + langgraph unhealthy left known-down
- [CRIT] 2026-06-21 — #2500/#2503 review-batch security/RFC posture accepted as-is (solo-dev localhost)
- [CRIT] 2026-06-19 — #2417 ps=8 (halted-pending-user) intentionally surfaces in next-action + digest
- 2026-06-18 — #2474/#2475 glassmorphism: all-route rollout + DEFAULT-on flip
- 2026-06-16 — #1840 full-auto policy DSL (project-scoped auto-decision override)
- 2026-06-16 — #1841 halt notification channel (task_halted opt-in push)
- 2026-06-16 — #2426 nudge covers BLOCKED HITL + #2427 auto_unblock Question:-only halt clear (v0.7.0 bug-close)
- 2026-06-16 — #2422 BE blocker-readiness CANCELLED-parity + #2423 ps=8 status-label maps (intense-review fixes)
- 2026-06-16 — #2419 dashboard blocked-chip suppression (server-computed field)
- 2026-06-16 — #2412 stale blocked-badge suppression + #2416 ps=8 board lane
- 2026-06-15 — #1678 zero-LLM lexical recall over shared/ corpus (BM25 endpoint; MCP deferred)
- 2026-06-15 — #2404 board-chrome polish + #1781 header-cap REMOVAL (operator decision)
- 2026-06-14 — #2367 backlog re-milestone: the 4-kind milestone taxonomy
- 2026-06-12 — #2330/#2332 story-based context system + activity-rail-mandatory (design lock)
- 2026-06-12 — auto-run batch 2: #2104 audit truthfulness + #2155 interrupt usage metering + #1265 consolidation
- 2026-06-12 — #2301 default Anthropic model → claude-opus-4-8 + pricing refresh (+ Fable-5 descope)
- 2026-06-12 — #2122-L1/N1 + #1909 hardening batch — contract decisions
- 2026-06-12 — #2327 per-role effort overrides via operator file (no UI) — design lock
- 2026-06-11 — #2320 Mode A Lead report-back into the #980 activity rail (design lock)
- 2026-06-11 — #2300 Anthropic effort/thinking as per-project cost lever (Slice 1 design lock)
- 2026-06-11 — #2100 Tier-3 email send routes (reply/forward/send-internal/external-send) + security hardening
- 2026-06-11 — #2127 operator-gate marker: "what's blocked on ME" is now one query
- 2026-06-11 — #2215 Mode-B fs-tool destination guard (working_path subtree + HITL ask-where-to-save)
- 2026-06-11 — #2298 multi-board starvation: parked HITL question starved all later boards
- 2026-06-11 — #1972 scheduled_at enforced at next-autorun + scheduler path live-verified
- 2026-06-11 — #2275 probe polish shipped; Gemini matrix HOLD on prepaid key (free tier now 20 RPD)
- 2026-06-11 — Grooming batch: M1-evidence closes + dedupe + #2127 taxonomy lock
- 2026-06-11 — #2274 classify_exception: Google 429/RESOURCE_EXHAUSTED → transient:rate_limit
- 2026-06-11 — #2194 auditor heuristic-skip guard: prior audit history forces LLM audit
- 2026-06-10 — #2185 Local-LLM capability verdict (gemma4) + the multi-board tool regression it exposed
- 2026-06-10 — #2184 H5a: worker multi-board mode live + mini-secretary pilot board
- 2026-06-10 — #2162/#2179 Code map + over-engineering review → simplify A+B executed
- 2026-06-10 — Harness batch H1–H4 (#1961/#1973/#2135/#2136) + intense review (#2137)
- 2026-06-10 — #2134 T5 regression pack: repeatable harness suite on board 661
- 2026-06-10 — #2120 Harness T4: local Gemma 4 QAT (ollama) = GO as the quota-free testing rig
- 2026-06-10 — #1225 WSL2/Docker RAM: Phase 1 applied, Phase 2–4 deferred
- 2026-06-09 — Non-ASCII task-field corruption (Thai/arrow/emoji → '?') is HISTORICAL tooling, NOT an app bug (#2124)
- 2026-06-09 — #2108 perf: board DONE-lane server pagination (#2112) + FE bundle/runtime cuts (#2111)
- 2026-06-08 — #1005 task_comments: append-only comment thread per task
- 2026-06-07 — #2047 operator-proof gate: 0.6.0 ships as documented known-gap
- 2026-06-07 — #2044 Dashboard layout locked as canonical UI baseline (visual-regression reference)
- 2026-06-07 — #1261 GOV2 followups: vs_cap null wording + audit_report on TaskCreate
- 2026-06-07 — #1244 description_annotation meta-key (adjust_continue, Path A)
- 2026-06-07 — #1243 Playwright E2E for /review (+ #2021 crash fix)
- 2026-06-07 — #1240 tasks.is_active auto-archive sweep
- 2026-06-02 — #1852 Phase 1: operator-proof primitive landed (gate INACTIVE until provisioned) — #1857
- 2026-06-02 — Operator-vs-AI write-auth distinction: env operator-token, phased — #1852
- 2026-06-02 — Per-task model-tier override + precedence — #1677
- 2026-06-02 — Test hygiene: ephemeral project teardown — #1796
- 2026-06-02 — P0 tool governance: config.tool_grants + in-code registry + hard-403 — #1799
- 2026-06-02 — Mode-B engine (#1191) rescope + browser-bridge decision
- 2026-06-02 — Recurrence scheduler dedup gate (stop-gap for no-executor pile-up) — #1728
- 2026-06-02 — Mode-B Phase-1 host-prereq guard: standalone `required_binaries`, not `runtime_config` — #1800 / #1652
- 2026-06-02 — Backup gap recovery: reschedule cron + startup catchup — #1474
- 2026-06-02 — Bootstrap-context reduction (api-contracts split) + Mode-B Option-1 decision — #1798, #1652
- 2026-06-02 — Per-project progress charts (#1292) + project-board header redesign (#1781)
- 2026-05-30 — Cost display G1: surface ESTIMATED cost (not metered) — Kanban #1688
- 2026-05-29 — Platform "Integrations" settings popup — Kanban #1655
- 2026-05-29 — Weekly release cadence: dev branch + weekly merge-to-main + vMAJOR.MINOR.PATCH (trial) — Kanban #1646
- 2026-05-29 — Public-repo hygiene: removed internal working notes — Kanban #1637
- 2026-05-28 — api suite determinism: triage closed, 0051 downgrade regression fixed, concurrent-invocation lock added — Kanban #1599
- 2026-05-28 — web 500 (.next hot-reload corruption): heal-script + runbook, not autoheal sidecar — Kanban #1625
- 2026-05-28 — projects.team CHECK dropped; team enum is app-validated single-source — Kanban #1620
- 2026-05-22 — Env-var wiring trap documented (root .env + compose mapping) — Kanban #1449
- 2026-05-22 — Mobile push provider pick: ntfy — Kanban #1192
- 2026-05-22 — Cron scheduling: Path A pick + 5 standard schedules + quiet hours parking — Kanban #1283
- 2026-05-20 — Compact + reward-hacking pass on dev-*.md agents — Kanban #1293 PILOT GATE
