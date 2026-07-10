---
purpose: bootstrap hot-read INDEX for decisions.md (1 line per decision)
updated: 2026-07-04
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

- 2026-07-10 — #2812 wire social TaskRole 51-57 + RANGE_MAX 50→60 (from #1318/#2811): constants-only (no migration — tasks.assigned_role has no DB CHECK since mig 0002, app validator derives from RANGE_MIN/MAX); codes 51-57 named (SOCIAL_BI_ANALYST=56/SOCIAL_GENERAL_RESEARCHER=57 prefixed to avoid shadowing BI_ANALYST=41 in ALL); live-verified 51→201/61→422; FE mirror na (web dev-only role map, no non-dev range mirrored); **soft-delete = list-exclusion NOT is_active flip**; latent-bug follow-up #2819 (handoff_templates.default_assigned_role DB CHECK still ≤50, never dropped → diverges post-bump)
- 2026-07-10 — #2440 committed api lockfile + fastapi/starlette HOLD (from #2437): `api/requirements.lock` (pip freeze --exclude-editable, 124 pins) consumed as `-c` constraint on the editable install (Dockerfile:55) — zero new tooling (no uv/pip-compile), no hashes (dev image), regen procedure in header; **DECISION: HOLD** fastapi 0.139.0/starlette 1.3.1/pydantic 2.13.4 — starlette already on 1.x via #2737 CVE (not voluntary), fastapi 0.139 needs only starlette>=0.46 (pin coherent), 0.137 `_IncludedRouter` refactor PERMANENT so #2437 openapi-introspection tests stay necessary, no migrate; proven build+swap /docs+/health 200
- 2026-07-10 — #1906 resources #1309 follow-ups (from #1309): SSRF-guarded link probe (`link_probe.py` — scheme allowlist + `_addr_blocked_reason`=is_global+multicast+NAT64 64:ff9b::/96 on literal+resolved paths, follow_redirects=False, NEVER-raises urlparse/UnicodeError-guarded, TOCTOU accepted-residual); xlsx/pdf parsers (lazy openpyxl/pdfplumber, parse offloaded off event-loop, malformed→parse_error); deps openpyxl>=3.1 + defusedxml>=0.7 (XXE auto-wire) + pdfplumber>=0.11.10 (pdfminer-six 20260107 = CVE-2025-64512 fix); DATA_ROOT env relocates null-working_path storage + autouse test-isolation fixture; +490/-73+2new; security follow-up #2814 (docker-exec reads OPERATOR_ACTION_KEY)
- 2026-07-10 — [CRIT] #2767 review-layer design LOCK (v0.9.0 gate): 4 decisions (both-surfaces / rework placeholder→#2813 / 3-tiers+never_auto_clear-flag / per-team emphasis) + close_record snapshot-immutable contract; spec-review 2 BLOCKERs fixed in-doc, #2770/#2775 realigned pre-spawn; contract shared/design/review-layer-design.md; unblocks #2770-2776
- 2026-07-10 — #806 MCP adapter DUAL-MODE: .claude/ flow stays authoritative, MCP server (#2518, 6 tools) = additional client surface, no sunset; AC3 desktop-mount smoke pending operator round-trip
- 2026-07-10 — #2811/#1306/#1307/#1318 new-team-infra batch: social team registered (post-#1620 enum, borrow-only content roster), data-analytics playbook +recipe→cohort +container-usage slot, agent-teams-data sandbox (opt-in profile), social.md authored; TaskRole 51-57 wiring → #2812
- 2026-07-04 — project-auditor 4th metric #2744: `task_stall_rate` (liveness) = stale_inflight/total_inflight, in-flight=ps IN(2,3) [BLOCKED excluded — else false-positive on healthy blocked_by chains], stale=updated_at>48h; folds into breach→continue/review/pause; defaults 50% / 48h / min_sample=2; prompt-resident metric (no api change), `.claude/agents/project-auditor.md` edit via ii, commit 81899af; **GOTCHA agent prompt BODIES cache at session-start → an in-session edit needs a restart** (verified live by resuming the auditor with the def); audit surfaced 3 out-of-scope data-quality findings (cost="0.0000" degenerate, CANCELLED completed_at=null blinds failure-rate, subagent_models often empty) → follow-up candidates
- 2026-07-04 — Telegram Phases 2+3 done #2779/#2780: safe-mutation verbs /new(create=MANUAL,create!=execute)+/approve+/deny(resolve_gate provenance=telegram)+/hold(status_change_reason→TODO) on _VERB_CLASS auth framework (deny-by-default fail-closed; endpoint unauthenticated=inherits localhost posture, docstring de-over-claimed); Phase 3 gap endpoints POST /tasks/{id}/run-now (prime to next_task_stmt-selectable, auto_pickup not headless, doesn't touch blockers) + /halt (cooperative ps2→8 operator_halt, intentionally kill/pause-EXEMPT); no migration; both dev-security-reviewer CLEAN + Lead live-curl; MCP = future sibling
- 2026-07-03 — walker ms50 batch #1020/#1021/#2778: cost-estimate endpoint (spawn-element rollup on GIN, task-level attribution, per-key threshold fail-safe) + FE badge/red-confirm; tool-risk classifier (fail-closed, All-tools pseudo-chip, SendMessage=shell-tier) + gallery chips/badge/grouped-detail; Telegram Phase 1 LIVE (0076 chat-state sticky+watermark, poller-dumb, read verbs, /tasks predicate reviewer-fixed to canonical pending; gate path byte-equivalent); probe rows left for operator cleanup
- 2026-07-03 — #2768 agent_overrides audit trail: REUSE projects_audit — migration 0075 widens CHECK (+agent_config), delta rides drain_summary JSONB, X-Actor convention, atomic same-commit insert, no-op=no-row; NEW GET /{id}/audit-log (first projects_audit reader, no header dep by design); downgrade-fails-if-rows caveat; round-trip proven pre-first-row
- 2026-07-03 — #2518 MCP phase 2: server 3→6 tools (get_task/update_task/complete_task), all thin /api/* shims; complete_task = AC-verify-then-flip (refuse on pending/failed, zero partial writes; combined PATCH); update_task refuses ps=5 client-side → ONE path to DONE; destructive/email tiers stay unexposed; no pytest (mcp/tests/ home undecided) — evidence = live FastMCP round-trip; throwaways #2785-2788
- 2026-07-03 — #2783 walker long-run incident root-caused: drain healthy (one 5h53m turn, 12 commits, stop report delivered 15:49); the real wedge = claude-desktop v2.1.197 post-manual-compact continuation never ran (queued /compact auto-fired at turn end against preTokens=457k); per-agent timing audit clean (max 31.5 min; the 12:45 "13-at-once" = a parallel review battery, by design); zb-walker §7 drain-end hygiene added (new-session over /compact · never queue /compact mid-drain · ESC to interject · max:N for >3-4h boards)
- 2026-07-02 — ms54 perf cluster #2722–#2726 (#2699 audit fix set): milestones lifted to Board prop (N+1 gone); Board callbacks ref-stabilized + BoardDndCanvas memo'd (residual→#2782); SSE provider StrictMode footgun (clear() deleted, spy-test); ?task_type= server filter + LIVE audit path converged (named fn was dead code); buildTasksQs dedup + one-effect ref-sync (render-time ref writes = ERROR-tier, lint-proven). Host-side ×15 = the determinism gate now. from ms54
- 2026-07-02 — #2558 Artifacts view (v0.8.0 #7): GET /api/projects/{id}/outputs on the locked #1305 guards (int-only discovery→collection boundary, ASCII-digit dir probe, cap-2000-keep-largest) + /p/[name]/artifacts page + ViewSwitcher 5th entry; download reuses per-task route via apiOrigin(). BE self-review caught Unicode-digit collision + wrong-end truncation pre-landing. Host ×15 = authoritative determinism gate (in-container loops contention-prone). 42 BE tests → operator pytest. from ms50
- 2026-07-02 — #2391 approval-policy editor hardened (#2390 review): caps 200-char/20-cond/50-rule double-enforced; unsaved-draft inline guard; TS preview regex-aligned to the hook (_shared.ps1:252, fail-closed) with url/content-predicate preview structurally 0 + disclaimer; 7/8 SHOULD-FIX fixed, text_contains_all/any UI deferred (engine test-locked); editRule-switch discard flagged as follow-up candidate. vitest 517/49 ×15 + Lead re-run green. from ms24
- 2026-07-02 — #2352 GIN jsonb_path_ops index on tasks.subagent_models (migration 0074, applied live + downgrade round-trip proven): serves the spawn-history @> pre-filter; default planner uses it on selective probes, seq-scans common agents at today's volume (honest). [CRIT-adjacent gotcha] alembic revision ids must be ≤32 chars (alembic_version VARCHAR(32)) — 33-char id failed at stamp time post-DDL (clean rollback); 3 existing ids AT the ceiling; reviewer checklist: check id LENGTH. from ms27
- 2026-07-02 — #2410 forecast tier-alias pricing (mode-a-cost): bare {haiku,sonnet,opus} model_override now defers to resolve_provider_model() — worker read proved tier = effort-only signal (worker.py:1782), provider 100% env-driven; pre-fix openai stacks silently $0/low via swallowed ValueError; ollama $0 now by-design. 8 tests → drain-end operator pytest. from ms37
- 2026-07-02 — #2125 Resources panel a11y/perf (deferred #1315): shared `useFocusTrap` hook, zero-dep (ModalShell ~20 modals + preview drawer); PERF-1 WIRED "Load more" (API `?limit&offset` pre-existed, resources.py:406); mime-chip canonical-suppress + hidden-both tabpanels + stable CSV keys + upload `accept` hint; SSRF HEAD-probe known-gap recorded (resources.py:111, #1309 follow-up); jsdom gotcha: visibility via `[hidden]`, never `offsetParent`. vitest 493 ×15 exit-0 + Lead re-run green. from ms25
- 2026-07-01 — #1018 per-project agent enable/disable + tier + notes (agent-gallery, v0.8.0): ADDITIVE (operator-locked) — #777 `agent_overrides` tier map untouched, enabled/notes in new `config.agent_settings`; new GET/PATCH /api/projects/{id}/agent-overrides unified view; Agents tab in ProjectSettingsPanel; Lead spawn-filter rule in dev.md. Reviews: security clean; dev-reviewer M1 (stale rowState cross-project→wrong-write, fixed `key={project.id}`) + M2 (GET 500 on legacy tier, fixed normalize→None). Live-curl + tsc/lint + vitest ×15 (481) green. AC4→dev.md rule (runtime #2769), AC6 audit→#2768. from #50
- 2026-06-26 — #2716 /settings two-pane category nav (Phase-0 UI/easy-setup): left `SettingsNav` (ViewSwitcher pattern) + right active-section pane, `?section=` state (Server Component, default Appearance, back-compat); single-source `web/lib/settingsCategories.ts`; retired `AdvancedSettingsDisclosure` (own flat category); audit-fetch gated to Advanced. tsc/lint/vitest green (host). PARKED on operator: visual render + CI vitest. from 0.8.0 theme
- 2026-06-26 — #2664 re-queued TODO tasks now run FRESH: worker `_poll_once` clears the stale LangGraph checkpoint (`hitl.clear_checkpoint`→`adelete_thread`) before invoke, `has_checkpoint`-guarded; HITL + transient-retry resume UNTOUCHED (AC2 data-loss invariant confirmed by dev-reviewer + regression test) + FE destructive-drop confirm (ModalShell). PARKED on operator: merge + Mode B live-gating. from #2660
- 2026-06-26 — #2735 board "Mode A" now shows REAL usage_events cost (not the task-estimate): new `actual_interactive_cost` 5th aggregate on `/api/projects/stats` re-points `CostSummary` (estimated_cost/Query4/budget/PL untouched) + new per-session `SessionCostPanel`; verified live $2200 vs $22.61, CI-green (run 28232926892). from #2728
- 2026-06-26 — #2729 base-image refresh: pin api+langgraph to a fresh `python:3.12-slim` digest (scout High 18→5, Low 55→40) + repo-root `.snyk` for 3 no-fix perl base CVEs (expiry 2026-09-20); app-layer Highs (starlette/cryptography) → #2736
- 2026-06-26 — #2732 Option C: autonomous-execution onboarding flow — `ProjectConsentGrantModal` posture radio (Q&A only / Standard tools) sets consent + `tools_config` in one action; FE-only (`setProjectToolsConfig`); Custom tier-editor deferred; verified live on demo #690
- 2026-06-26 — #2707 Option B: `tools_enabled` decoupled from multi-board eligibility (consent alone → eligible; Q&A-only when tools off); `permission_gate` kill-switch intact; operator-facing FE onboarding flow → Option C #2732
- 2026-06-24 — #5 Tasks A/B/C BUILT: task_gates HITL channel + picker `gate_resume_tasks` field live (disjoint from next_task); resume_context contract in async-hitl-gates §12; runner #2531 + W-1 index + AC5 drain-run deferred
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
- 2026-05-22 — Mobile push provider pick: ntfy — Kanban #1192 (superseded: ntfy removed #2756/#2757; push = Telegram + web push)
- 2026-05-22 — Cron scheduling: Path A pick + 5 standard schedules + quiet hours parking — Kanban #1283
- 2026-05-20 — Compact + reward-hacking pass on dev-*.md agents — Kanban #1293 PILOT GATE
