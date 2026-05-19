# Mode B authorization chain — design

**Decision date:** 2026-05-19
**Source task:** Kanban #1205
**Status:** design lock; implementation children seeded as separate tasks
**Promote target (Lead, after lock-code check):** `context/projects/agent-teams/shared/design/mode-b-authorization-chain.md`

---

## 1. Problem statement (formal)

Mode B is the autonomous-langgraph-loop execution mode of the agent platform: a worker drives browser/tool actions on **external surfaces** (LinkedIn post, JobsDB submit, Gmail send, ad-spend mutations, etc.) on the operator's behalf, with HITL only at codified checkpoints. For Mode B to do anything beyond read-only, AI-driven external writes must pass the **Claude Code auto-mode harness classifier** — a layer above any in-process hook or subagent policy, run by the harness on every tool call.

### 1.1 Block axes — the intersection that triggers the classifier

The harness classifier appears to gate a tool call when ALL of the following axes intersect:

| Axis | Plain meaning | Example positive | Example negative |
|---|---|---|---|
| **External-System-Write** | the action mutates a system the operator does not control end-to-end | LinkedIn post / Gmail send / JobsDB Apply / ad-platform spend | local file write / Kanban API on our box |
| **Public-facing audience** | the mutated artefact is visible to third parties | LinkedIn timeline / job-application recipients | private calendar event / private note |
| **AI-drafted content** | the payload was generated or transformed by the model in this session | drafted cover letter / drafted post body | operator-pasted final text Lead only forwards |
| **Unverifiable-from-transcript** | the classifier cannot see proof the operator approved this specific payload | spawn-brief says "operator approved" with no signature | operator types the final character themself |
| **Operator-identity** | the action is attributable to the operator-as-person | jobs posted under operator name / messages sent from operator account | service-account batch jobs (unattributed) |

**Single-axis failure (any one of the five missing) = no block.** The calendar non-block (#1184) is the clearest positive evidence — calendar create/save/delete events have External-System-Write × Operator-identity × AI-drafted, but lack **Public-facing audience** (calendar entries are private to the operator's account), and the classifier let those actions through.

### 1.2 Classifier visibility — what it CAN and CANNOT see

The harness classifier reads the **transcript that goes to it**: the chain of tool calls, tool results, system reminders, and assistant turns within the current Claude Code session. It DOES NOT read:

- The operator's separate chat-channel "approve en" message that lives in a Lead session above the subagent's transcript.
- Project-state DB rows (`approval_policies`, `acceptance_criteria.verified_by`, etc.) — unless explicitly fetched into the transcript via a tool call the classifier sees.
- Past sessions / Kanban tasks / external systems of record.
- Operator identity attestation (no signed token in the transcript).

This means that a spawn-brief sentence such as "the operator has approved this LinkedIn post" is **rhetoric, not evidence**, from the classifier's standpoint. The brief was drafted by Lead, which is itself an AI — the classifier has no way to distinguish operator-approved-then-Lead-relays from Lead-fabricated-claim-of-approval. Treating the brief as authoritative would defeat the gate.

Practical corollary: **any authorization scheme that relies only on natural-language assertion inside the transcript will fail the classifier.** Authorization signal must be either (a) cryptographically verifiable inline, (b) externalized to a hook that the harness defers to, or (c) reduced to a physical operator action the classifier can observe (the click happened from the operator's input device).

### 1.3 Calendar non-block evidence (#1184)

`#1184` exercised the same Mode-B-style worker on Google Calendar's create/save/delete event verbs. The classifier did NOT block. The action met four of the five block axes (External-System-Write × AI-drafted × Unverifiable-from-transcript × Operator-identity) but failed the **Public-facing audience** axis — calendar events are private to the operator's own account and not broadcast to third parties. The classifier intersected the axes and let the action through.

This is the cleanest evidence the gate is **axis-intersection**, not single-axis-OR. It also gives Mode B a known-safe envelope: internal-only verbs on operator-private surfaces are unblocked today, no new infra required. Calendar + private-note + Kanban-API workflows can run autonomously now.

### 1.4 Operator's per-action HITL is not the answer at scale

Operator-in-the-last-click defeats the classifier (the click came from the operator's input device, observable to the harness), but at scale it negates Mode B's purpose: a worker that pauses for a human click on every external write is just a slower assistant. The design problem is **how to keep the gate honest while removing the per-action human bottleneck for codified, low-blast-radius patterns** (post a job application within today's locked filter, send a calendar invite to a known-contact, etc.) — and reserve the human click for genuinely high-stakes one-offs.

---

## 2. The 5 mitigation patterns

For each pattern: mechanism → trade-offs (blast radius if compromised / operator effort / coverage breadth / classifier-compatibility risk).

### 2.1 Pattern 1 — Operator-in-the-last-click

**Mechanism.** Worker drafts, validates, fills the form, navigates to the action page, then HALTS with a clear "click Submit/Publish in your browser to complete" prompt. Operator performs the physical click; harness classifier sees the click came from the operator's input device → no block.

**Proven via #1201.** LinkedIn post smoke ran end-to-end this way: secretary drafted + self-checked + opened the composer + waited; operator clicked Publish manually. Took ~5s of operator time.

**Trade-offs:**
- **Blast radius if compromised:** zero — every external write requires a human click.
- **Operator effort:** high per-action; near-zero setup.
- **Coverage breadth:** universal — works for every URL, verb, and content type. Strongest gate available.
- **Classifier-compatibility risk:** zero — the click *is* the auth.

**Use case.** Genuinely high-stakes one-offs (salary negotiation reply, public statement, irreversible financial action). Also the **always-available manual fallback** when any codified pattern can't decide.

### 2.2 Pattern 2 — Narrow settings.json allowlist scoped to URL + verb

**Mechanism.** Operator pre-authorizes specific URL + verb pairs in `.claude/settings.json` (or per-project `.claude/settings.local.json`) — e.g., `WebFetch(linkedin.com/posts/create)`, `mcp__claude-in-chrome__form_input(jobsdb.com/apply/<job-id>)`. Harness consults the allowlist before invoking the classifier; matched rules skip the gate.

**Trade-offs:**
- **Blast radius if compromised:** scoped to the URL + verb. Rule creep (operator adds `linkedin.com/*`) widens the radius silently.
- **Operator effort:** one-time per URL-verb pair; low ongoing.
- **Coverage breadth:** narrow — only the URLs explicitly listed. Doesn't generalize across job boards / posting surfaces.
- **Classifier-compatibility risk:** medium — the harness MAY still gate based on **content** (e.g. detecting "AI-drafted" via writeback inspection) even when URL is allowlisted. Not yet smoke-tested.

**Use case.** Interim hardening for 2-3 high-traffic surfaces while the deeper patterns (Pattern 5 + Pattern 3) land. NOT a long-term answer.

### 2.3 Pattern 3 — Pre-signed authorization token

**Mechanism.** Operator runs a small CLI / web UI that issues a signed token for `{action_type, payload_hash, expiry}`. Lead embeds the token in the spawn brief or stores it on the linked Kanban row. A PreToolUse hook verifies the signature against the operator's public key + checks the payload hash against the proposed tool call's actual arguments. Hook emits `allow` to the harness; the classifier defers.

**Trade-offs:**
- **Blast radius if compromised:** bounded by the token's `{action_type, payload_hash, expiry}` — narrow if used right. Wide if operator issues a "blanket day pass" token.
- **Operator effort:** non-trivial setup (issuance UI, key management, hook). Per-action effort: one button-press to issue.
- **Coverage breadth:** universal — works for any action shape, including edge cases Pattern 5 can't codify (one-off ad-spend approval, custom payload).
- **Classifier-compatibility risk:** depends on whether the harness honors PreToolUse hook `allow` decisions for classifier-gated calls. Pattern 5's existing hook layer is the testbed.

**Use case.** Open-ended fallback for actions outside codified policies. Also the right shape for **delegating Mode B to a phone-side approve flow** later — operator taps approve on phone → phone signs token → token rides the brief.

### 2.4 Pattern 4 — Classifier-readable Kanban audit trail

**Mechanism.** A PreToolUse hook reads the linked Kanban task (parent of the spawn) via `GET /api/tasks/<id>`, inspects `acceptance_criteria[].verified_by` + `status_change_reason` + `requires_human_review` flag, and emits `allow` if the criteria are passed-by-operator + recent. Effectively makes the existing AC-discipline-as-audit-trail readable to the gating layer.

**Trade-offs:**
- **Blast radius if compromised:** bounded by Kanban-API auth. If the API has no operator-vs-AI write distinction, an AI could PATCH `verified_by='user'` and unlock — so this pattern REQUIRES an operator-vs-AI auth distinction on the Kanban write path (which we don't have today).
- **Operator effort:** zero per-action (operator already marks ACs verified during normal workflow); medium setup (hook + auth distinction).
- **Coverage breadth:** broad — any work that flows through a Kanban task can hook into this. Almost everything in Mode B does.
- **Classifier-compatibility risk:** medium — same harness-deferral question as Pattern 3.

**Use case.** Composable with Pattern 5 — codified policies cover the common case, Kanban audit trail covers edge cases where the codified rule was overridden by an operator-passed AC.

### 2.5 Pattern 5 — `approval_policies` harness enforcement

**Mechanism.** The `projects.approval_policies` JSONB column (added Kanban #953, currently labeling-only via langgraph worker fetch) becomes **enforcement input** to a PreToolUse hook. Rules are operator-defined per-project: matchers on tool name + target URL/endpoint + content predicates → `allow` / `deny` / `requires-attention`. Hook fires before classifier; matched-allow rules bypass the gate.

**Today's state.** The JSONB column exists, langgraph worker fetches it with a 10s TTL cache, the worker uses it for HITL-pause routing — but nothing wires it into the harness PreToolUse layer. The labeling-only foundation is solid; the missing piece is the harness-side hook.

**Trade-offs:**
- **Blast radius if compromised:** bounded by the policies the operator wrote — operator owns the radius via codification.
- **Operator effort:** medium setup (write the rules per project); zero ongoing once codified.
- **Coverage breadth:** broad for codified action types within a project; narrow for novel actions (which fall to Pattern 3 or Pattern 1).
- **Classifier-compatibility risk:** medium — same question as Pattern 3 / 4 about whether the harness honors hook decisions for classifier-gated calls. **This is the testbed.** If Pattern 5's hook can override the gate, Pattern 3 + 4 inherit the answer.

**Use case.** The default authorization layer for routine, codified Mode B actions (recurring job application within filter, weekly LinkedIn post within voice rules, calendar-invite send to known contacts).

---

## 3. Ranked build order + rationale

Ranking by **autonomy-unlocked-per-unit-of-infra-work**:

1. **Pattern 5 — `approval_policies` harness enforcement.** Top rank because: (a) JSONB column + worker-side fetch already exist (Kanban #953), so half the infra is built; (b) it codifies operator's per-project rules in the same place that already routes HITL pauses (one source of truth); (c) it is the testbed answering the "does the harness honor PreToolUse `allow` for classifier-gated calls" question — the answer cascades to Patterns 3 + 4. Blocked on: harness PreToolUse hook implementation + smoke test against a real classifier-gated action (LinkedIn post or JobsDB Apply).

2. **Pattern 4 — Kanban audit-trail integration.** Second rank because: (a) it composes with Pattern 5 — codified policies cover the common case, audit trail covers per-task overrides; (b) it reuses the AC-discipline operators already do; (c) it requires an **operator-vs-AI auth distinction on Kanban writes** as a prerequisite, which is a generally-valuable platform addition (not just an authz-chain need). Blocked on: that auth distinction + Pattern 5's harness-deferral question being answered.

3. **Pattern 3 — Pre-signed token.** Third rank — open-ended fallback for actions Pattern 5 can't codify. Highest infra cost (issuance UI, key management, signature verification) but **the only pattern that scales to phone-side approval delegation** (operator taps approve on phone → phone signs → token rides into Lead's brief). Deprioritized until Pattern 5 lands and proves the harness-deferral path, then this becomes the long-tail solution.

4. **Pattern 2 — Narrow allowlist.** Fourth rank — interim hardening for the 2-3 high-traffic URLs while Pattern 5 builds. Avoid investing past 2-3 rules; rule creep is the failure mode.

5. **Pattern 1 — Operator-in-the-last-click.** Always available. Not a "build" item — it's the manual fallback that requires nothing. Rank-5 only in the build-order sense; for genuinely high-stakes actions it is and should remain rank-1 in usage.

**Critical dependency chain:** Pattern 5's smoke test answers the harness-deferral question for ALL hook-based patterns (3, 4, 5). If the harness honors PreToolUse `allow` for classifier-gated calls → 3 + 4 are unblocked. If it does NOT → the entire hook-based approach fails and the design collapses back to Pattern 1 + 2 only. **Pattern 5's smoke test is therefore the highest-information experiment in this chain** and should run before deep investment in Pattern 3 or 4.

---

## 4. Inter-pattern composability

- **Pattern 5 + Pattern 4.** Codified policies cover the routine case; audit trail covers per-task overrides where the operator explicitly passed an AC outside the codified rule. Hook checks Pattern 5 first → if no rule matches, fall through to Pattern 4 audit-trail check → if neither approves, fall through to Pattern 1 (HITL pause for operator click).
- **Pattern 5 + Pattern 3.** Pre-signed token complements the codified policy when the action's payload shape doesn't fit a clean matcher (e.g. one-off ad-spend approval at unusual amount). Operator signs the specific payload; Pattern 3 hook accepts it as an override.
- **Pattern 1 + any.** Always-available manual fallback. Every hook chain terminates in Pattern 1 if no other layer approves.
- **Pattern 2 + Pattern 5.** During the transition where Pattern 5's harness hook is being built, Pattern 2 allowlists the highest-traffic URLs as a temporary bridge. Pattern 2 entries retire as Pattern 5 rules land covering the same surface.

The natural shape is a **decision chain** at the PreToolUse layer:

```
PreToolUse fires
  ↓
Check Pattern 2 (settings allowlist) → match? allow, done.
  ↓
Check Pattern 5 (approval_policies match) → match? allow / deny per rule.
  ↓
Check Pattern 3 (signed token in brief) → valid? allow.
  ↓
Check Pattern 4 (Kanban AC verified_by=operator + recent) → allow.
  ↓
No layer matched → defer to harness classifier → likely block → Pattern 1 HITL pause.
```

---

## 5. Cross-references to incidents

- **#1174 — secretary 2026-05-18 morning, subagent-brief classifier block on send-intent.** Different classifier layer (subagent-prompt-time, not harness-runtime). Lead-direct sidestepped that one because Lead's prompt is not subagent-classified the same way. Lesson: there are MULTIPLE classifier layers and a workaround at one layer does NOT necessarily work at others. The design here targets only the **harness-runtime** layer.
- **#1201 — secretary 2026-05-18 afternoon, harness auto-mode classifier block on click-Publish.** Primary origin of this design. Confirmed Lead-direct does NOT sidestep the harness layer. Pattern 1 (operator-in-the-last-click) was the fallback. The block-axis intersection identified above (Section 1.1) was derived from #1201's failure.
- **#1184 — calendar smoke, classifier did NOT block internal-app verbs.** Block-axis POSITIVE EVIDENCE — confirms Public-facing-audience is required for the intersection to fire. Used as the known-safe envelope for what Mode B can already do today without new infra.
- **#1180 — job submit pipeline (pending).** Predicted to hit the same harness gate at the Submit-click step. Designated **validation target** for Pattern 5's smoke test — when Pattern 5 lands, run #1180 with codified policies covering `jobsdb.com/apply/<job-id>` + filter-locked criteria; the classifier should defer to Pattern 5's `allow`.

---

## 6. Failure-modes Category 10 disposition

**Investigation.** `failure-modes.md` exists in the secretary project at `context/projects/secretary/shared/failure-modes.md` (319 lines, 7 categories covering bootstrap / context / browser / drafting / HITL / cross-system / operator-process failures). No file at this name exists in agent-teams team methodology or in `context/standards/`.

**Disposition recommendation:** **Category 10 (Mode B harness-classifier-block class) belongs in agent-teams team methodology, NOT in secretary's KB.**

Rationale:
- The harness classifier is a **platform-layer concern** — it gates EVERY agent (Lead, every subagent, every team) running in Claude Code on agent-teams' substrate. The same classifier would block a `news-analyzer` Mode B post to a Discord channel, a `novel-drift` Mode B publish to a blog, a hypothetical `support-bot` Mode B email reply to a customer — none of which are secretary's concern.
- Putting the category in `context/projects/secretary/shared/` is the **dogfood-pollution anti-pattern** (CLAUDE.md Q0-Q2 trap): secretary is the first project to hit it, but the failure-mode is universal. Future Mode B projects scaffolded later would not inherit the category.
- The natural home is `context/teams/dev/failure-modes.md` (team methodology — applies to every project under the `dev` team). If the agent platform later grows non-dev teams that also do Mode B (e.g. novel-team auto-publish), the category may need to move UP again to a standards file — but that decision should wait for evidence (Q2 says push UP when in doubt, but don't pre-generalize past evidence).

**Action item for Lead post-promotion:** before secretary's failure-modes.md grows a Category 10 section, file a small task to create `context/teams/dev/failure-modes.md` (or extend an existing team-methodology doc) with Category 10 — referencing this design doc as authority. Secretary's failure-modes.md gets a one-line pointer: `Category 10 (Mode B classifier-block) — see context/teams/dev/failure-modes.md`.

---

## 7. Connection to today's filed followups

- **#1269 (SEM credential policy, today's AC3 followup).** References this design as the **authorization layer** for SEM live ad-spend mutations. SEM mutations are textbook block-axis intersection (External-System-Write × Public-facing × AI-drafted × Operator-identity × Unverifiable). The path forward: SEM mutations land in Pattern 5's `approval_policies` per-project rules with strict matchers (max bid delta per call, max daily spend delta, whitelist of campaign IDs). Anything outside the matcher falls to Pattern 3 (signed token per mutation) or Pattern 1 (operator click). This design's promotion unblocks #1269's policy authoring.
- **#1271 (Data Analytics integration policy, today's AC2 followup).** References this design for v2 live integrations. Read-only analytics queries (BigQuery read, GA4 reporting) do not intersect Public-facing-audience and are unblocked already (similar to calendar non-block). v2 integrations that WRITE (push a custom audience to an ad platform, schedule a Tableau extract refresh that triggers a downstream email) DO intersect — and inherit Pattern 5 + Pattern 3 chain from this design.

Both followups depend on **Pattern 5 + Pattern 3 landing**. Operator should not start authoring SEM/DA policy details until Pattern 5's harness-deferral smoke test returns a positive — otherwise the policy work is wasted if hook-based enforcement turns out infeasible.

---

## 8. Implementation task stubs (seeded for Lead to open)

These are content for Lead to convert into actual Kanban tasks via `POST /api/tasks` after promotion of this doc. Both stubs are **children of #1205** (parent_task_id=1205).

### 8.1 Stub A — Pattern 5: `approval_policies` harness PreToolUse hook

**Title:** `[authz-chain] Pattern 5 — approval_policies harness PreToolUse hook (impl from #1205)`

**Description body (excerpt):**
> Implement a PowerShell PreToolUse hook (`.claude/hooks/approval-policies-gate.ps1`) that reads `projects.approval_policies` for the bound project (`_runtime/lead_project_id.txt`), evaluates the JSONB rules against the proposed tool call, and emits `allow` / `deny` / `requires-attention` to the harness. Smoke test against a real harness-classifier-gated action (LinkedIn post or JobsDB Apply) — the smoke verifies whether the harness honors hook `allow` decisions for classifier-gated calls. The smoke result is HIGH-INFORMATION: it determines whether Patterns 3 + 4 are feasible at all.

**Acceptance criteria:**
1. Hook script at `.claude/hooks/approval-policies-gate.ps1` reads `_runtime/lead_project_id.txt` + GETs `/api/projects/<id>` and parses `approval_policies` JSONB; fails open with `requires-attention` if API down (paired with stderr WARN). Verified by: hook unit test against fixture project rows.
2. Rule evaluation supports matchers on `tool_name`, `target_url_pattern` (regex), and `content_predicate` (substring / regex). Each matched rule emits one of `allow` / `deny` / `requires-attention` with reason string in the hook response. Verified by: table-driven test covering at least 6 rule shapes.
3. Hook registered in `.claude/settings.json` PreToolUse for tool surfaces `WebFetch`, `mcp__claude-in-chrome__*`, `Bash` (curl detection). Verified by: tracing the hook fire across one synthetic call per surface.
4. Smoke test executed against ONE real classifier-gated action (target: LinkedIn post via secretary's existing pipeline) with a permissive Pattern 5 rule. Outcome documented: harness deferred OR harness still blocked. Verified by: live transcript pasted into the task.
5. Smoke result drives followup decision: if harness deferred → Stub B unblocked + #1269/#1271 unblocked; if harness still blocked → file new task to investigate alternative gate layer (browser-extension-side authz, etc.). Verified by: followup task id documented in the task's `status_change_reason`.

### 8.2 Stub B — Pattern 4: Kanban audit-trail readable to classifier

**Title:** `[authz-chain] Pattern 4 — Kanban audit-trail readable to PreToolUse gate (impl from #1205)`

**Description body (excerpt):**
> Implement a PowerShell PreToolUse hook (`.claude/hooks/kanban-audit-gate.ps1`) that reads the linked Kanban task (id discovered via spawn-brief convention or transcript scan) and emits `allow` if `acceptance_criteria` contains at least one criterion with `status='passed'` + `verified_by='user'` + `verified_at` within a configurable recency window (default 24h). Composes with Stub A: this hook fires AFTER Pattern 5's hook returns no-match.

**Prerequisite:** operator-vs-AI auth distinction on Kanban write path. Without it, an AI could PATCH `verified_by='user'` to unlock its own action. Surface this prerequisite as a BLOCKING followup task if not present at impl start.

**Acceptance criteria:**
1. Hook script at `.claude/hooks/kanban-audit-gate.ps1`, runs after Stub A's hook in the PreToolUse chain. Verified by: settings.json ordering visible + tested.
2. Hook discovers linked task id via: (a) spawn-brief convention (`X-Kanban-Task-Id` in the brief), (b) fallback `_runtime/current_task_id.txt`. Returns `requires-attention` if neither resolves. Verified by: unit test on both resolution paths.
3. Hook applies a recency window (default 24h, configurable per-project via `approval_policies.audit_trail_recency_sec`) to the `verified_at` field. Stale verifications do NOT pass. Verified by: time-warp test using fixture timestamps.
4. Prerequisite check: operator-vs-AI auth distinction on Kanban writes is in place (or a followup task tracking that prerequisite is filed and linked from this task). Verified by: prerequisite task id documented or implementation cited.
5. Smoke test against the same scenario as Stub A's smoke (LinkedIn post via secretary), but this time with Pattern 5 rules empty + a Kanban task whose AC `verified_by='user'` recently. Outcome documented. Verified by: live transcript.

---

## 9. Out of scope (reaffirmed)

- **Modifying the Claude Code auto-mode classifier itself.** It is a harness-managed component; this design works AROUND it via PreToolUse hooks + allowlist rules. If the harness does not honor hook decisions for classifier-gated calls (Stub A smoke result), the design collapses and an alternate route is needed — but inventing or modifying the classifier is not on the table.
- **Mobile / push / Tailscale infrastructure.** Separate Tier-1 thread (#1192 et al.). Pattern 3's token issuance flow will eventually integrate with phone-side approve, but the integration is not this design's scope.
- **Cost / budget enforcement.** Separate Tier-1 thread (#1194). Pattern 5's `approval_policies` shape is the natural home for budget rules, but budget rule authoring is a sibling task.
- **Implementation of any pattern.** This doc is design lock only. Implementation lives in Stub A + Stub B + their downstream tasks.
- **Cross-project rollout.** Once Pattern 5 lands, news-analyzer + novel-drift + future projects can author their own `approval_policies` — but rollout planning is a separate task per project, not this design's scope.

---

## 10. Open questions / known unknowns

1. **Does the harness honor PreToolUse hook `allow` for classifier-gated calls?** Answered by Stub A's smoke. Until then, every hook-based pattern carries this risk.
2. **Is there an operator-vs-AI auth distinction on the Kanban write path today?** Probably no (the API has no auth surface beyond `X-Project-Id`). Pattern 4 needs this; sequencing decision (Pattern 4 first vs auth-distinction first) will be made during Stub B planning.
3. **Does the classifier inspect payload content even when URL is allowlisted (Pattern 2)?** Not smoke-tested. If yes, Pattern 2 has narrower utility than its description suggests. Low priority to answer (Pattern 2 is interim only).
4. **What is the failure-modes.md home long-term?** Section 6 recommends `context/teams/dev/`. If a non-dev team later does Mode B (novel-drift auto-publish?), the file may need to move to a standards-layer file. Re-evaluate when the second team hits the gate.
