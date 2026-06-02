# Design — Operator-vs-AI write-authorization distinction

**Kanban:** #1852 · **Role:** dev-sr-backend (read-only design) · **Date:** 2026-06-02
**Status:** design draft (no code written) · **Promote target (Lead):** `context/projects/agent-teams/shared/design/operator-vs-ai-auth-1852.md`
**Unblocks:** #1275 (Kanban audit-trail gate), #1801 Phase-2 (`runtime_config` build), #1585 (secretary email tiers)
**Builds on:** #1205 (authorization-chain doctrine), #1652 §B.3 #5 (this prerequisite), #1799 (X-Agent-Role trust boundary)

---

## 1. Problem + threat model

### 1.1 The gap

The platform has **exactly one request-identity signal that the API trusts: `X-Project-Id`** (project scope; `session_project.require_project_id_header`). #1799 added `X-Agent-Role`, but its own docstring is explicit: it is **advisory + spoofable**, the agent sets it on itself, and an unknown role degrades to *unrestricted*. There is therefore **no signal the API can use to tell "the operator did this" apart from "an AI agent did this"**.

Three gates only become meaningful once that distinction is provable:

- **#1275** — the planned Kanban audit-trail PreToolUse gate emits *allow* when an AC has `verified_by='user'` + recent (`mode-b-authorization-chain.md` §2.4 / §8.2 Stub B). But `verified_by` is just a free-form `str | None` field on `AcceptanceCriterion` (`schemas/task.py:174`), set by an ordinary `PATCH /api/tasks/{id}`. **Any AI agent can PATCH `acceptance_criteria[].verified_by='user'` and unlock its own gated action** → the gate audits its own forgery.
- **#1801 Phase-2** — `projects.runtime_config` (JSONB) will drive an **engine-built per-project Docker image** that the worker then *runs in* (`mode-b-runtime-options.md` §B.2). `mode-b-runtime-options.md` §B.3 #5 already flags this as a **blocking prerequisite**: an autonomous agent that can `PATCH runtime_config` **defines the image it then executes** = supply-chain / code-exec escalation.
- **#1585** — secretary email-action tiers (trash today via `/api/tools/email/*`; reply/send later) must be gated to **operator-confirmed**, not AI-self-authorized. Today the email trash path is gated only by `X-Project-Id` + the spoofable `X-Agent-Role` grant + a daily-units cap — none of which is an operator-presence signal.

### 1.2 Threat model (anchor — do NOT oversell)

**Single-operator system.** The adversary in scope is **agent drift / context-confusion / a compromised-prompt agent acting autonomously** — NOT a human adversary with host access. If the attacker owns the host, they own the `.env`, the DB, and the Docker daemon; no app-layer token defends against that, and defending against it is out of scope (same posture as #1799's trust boundary).

Two execution modes, and the distinction matters very differently in each:

| | **Mode A** (interactive Claude Code) | **Mode B** (headless langgraph engine) |
|---|---|---|
| Operator present? | **Yes, per-action.** Every Write/Edit/Bash gets human approval at the Claude Code layer (`.claude/settings.json` prompts on Write/Edit/Bash). | **No.** Worker drives `compiled.ainvoke` autonomously (`worker.py:337`); no human in the per-action loop. |
| "Operator did this" is… | **structurally true** — a human physically approved the call. The problem is only **propagating** that fact to the API gate unspoofably. | **the open question** — an autonomous agent could self-authorize. This is where the distinction is load-bearing. |
| Real enforcement wall | The Claude Code approval layer (already there). | Must be a distinct, agent-inaccessible mechanism. |

**Honest framing of what is provable vs advisory:**

- In **Mode A**, operator-presence is *real* but lives **outside the API's view** — the API sees an HTTP request indistinguishable from an AI-issued one. The job is to carry an unspoofable token of "a human approved this" onto the wire.
- In **Mode B**, there is *no* operator-presence to carry; the only honest answer is **"this class of write cannot be AI-autonomous — it must HALT and wait for an out-of-band operator action."** Any scheme that lets the headless agent satisfy the gate by itself (including a typed-ack the agent can type) is theatre.
- **Typed-ack alone (ProjectGrantConsent style) does NOT distinguish operator from AI in Mode B.** `ProjectGrantConsent` requires the caller to type the project name verbatim (`routers/projects.py:886`). A headless agent **knows the project name** (it is in `_runtime/lead_project_id.txt` / the bound config) and can POST it. Typed-ack is *friction that proves deliberateness for a human at a UI*; it is **not** an identity proof against an autonomous agent that holds the same knowledge. This is the single most important honesty point in this design.

### 1.3 What #1205 / #1652 / #1799 already settle (so Lead does not re-decide)

- **#1205 §1.2 (classifier visibility)** already established: *natural-language assertion inside the transcript is rhetoric, not evidence*; authorization must be (a) cryptographically verifiable, (b) externalized to a hook the harness defers to, or (c) reduced to a physical operator action. **This design's Mode-B answer is (c)** — reduce to a physical/out-of-band operator action — with (a)/(b) deferred.
- **#1205 §10 Q2** already names *"is there an operator-vs-AI auth distinction on the Kanban write path today? Probably no"* as the open question this task closes. Stub B (§8.2) lists it as a **blocking prerequisite**. So #1852 is the prerequisite #1205 already scoped — not a new direction.
- **#1652 §B.3 #5** already prescribes the shape: *"`runtime_config` writes require explicit OPERATOR action (typed-ack endpoint, mirror `ProjectGrantConsent`), NOT autonomous PATCH."* This design **refines** that: typed-ack is the **Mode-A** mechanism; for Mode B the write must be **refused to the agent entirely** (operator does it out-of-band).
- **#1799 trust boundary** already settled: `X-Agent-Role` is spoofable, the Claude Code layer is the real Mode-A wall, and **crypto capability tokens are premature for the single-operator threat**. This design honors that — no crypto in the recommended phase.

**Genuinely-open for the operator (not pre-decided):** (1) Is a single env-injected operator key (Option B) acceptable operationally, or is per-action friction (Option A/C) preferred? (2) For Mode B, is "refuse + HALT for out-of-band operator action" acceptable UX, or does the operator want a phone-side approve flow (deferred Option C+)? (3) Scope of the first gated field set — just the three below, or a general mechanism?

---

## 2. The gated write paths — what each needs from the mechanism

| Path | Code surface | What it writes | What it needs |
|---|---|---|---|
| **#1275 — AC `verified_by='user'`** | `PATCH /api/tasks/{id}`; `acceptance_criteria[].verified_by` (`schemas/task.py:174`, serialized in `routers/tasks.py:205-213`) | Sets a criterion's `verified_by` to the literal string `'user'` (+ `status='passed'`, `verified_at`) | The API must **reject `verified_by='user'` unless the request carries an operator-presence proof.** AI-issued PATCHes may set `verified_by='agent'`/role-name but **never** `'user'`. |
| **#1801 — `runtime_config`** | `PATCH /api/projects/{id}` (future column per #1652 §B.1) | A declarative image spec the worker builds + runs | The API must **reject any `runtime_config` mutation unless operator-proof is present** — full stop. Highest privilege; no AI-autonomous path at all. |
| **#1585 — email tiers** | `POST /api/tools/email/{provider}/{trash,reply,send}` | An external mutation under the operator's account | Higher tiers (reply/send/delete) must **require operator-proof per call**; the existing `X-Agent-Role` grant gates *which role*, but tier escalation needs *operator presence*. |

**Common denominator:** each needs a way for the API to answer *"was an operator physically present/approving for THIS write?"* — unspoofably by an agent sharing the same `X-Project-Id` and network.

---

## 3. Options (≥3, with trade-offs)

### Option A — Typed-operator-ack endpoint per gated action (mirror ProjectGrantConsent)

**Mechanism.** A dedicated endpoint (or per-field ack) where the operator types an explicit confirmation; the ack is recorded server-side (a timestamp column like `auto_run_consent_at`, or an `operator_acks` ledger). The gated write then checks "is there a fresh operator-ack for this {field, scope}?"

**The honest hole (must address):** *how is "operator-only" enforced if the AI shares the same network + `X-Project-Id`?* **It is NOT, by typed-ack alone.** A headless agent can POST the same body (it knows the project name / the confirmation string). Typed-ack proves **deliberateness for a human driving a UI**; against an autonomous agent it proves nothing. Typed-ack only works **in Mode A**, and only because in Mode A the *Claude Code approval layer* already gates the POST — i.e. typed-ack's security in Mode A is **inherited from Option D, not from the typing**. In Mode B it is pure theatre.

- **Blast radius if compromised:** Mode A — bounded (operator approved the POST at the CC layer). Mode B — **none of the protection holds**; agent self-acks.
- **Operator effort:** per-action typing; near-zero setup (reuse `ProjectGrantConsent` shape).
- **Coverage:** good for Mode A; **fails Mode B** as a standalone.
- **Verdict:** **Mode-A-only mechanism. Necessary but not sufficient. Pair with D (Mode A) and refuse-to-agent (Mode B).**

### Option B — Operator-only secret/token (env-injected; the agent layer never holds it)

**Mechanism.** An operator key (e.g. `OPERATOR_ACTION_KEY`) lives in the **API container's `.env` only**. Gated writes require an `X-Operator-Token` header matching it (constant-time compare). The **agent process never receives this env var** — it is injected into the `api` service environment block, NOT into the Claude Code session env nor the langgraph worker env. An agent that wants to forge the header has nothing to forge it *with*.

**Where it lives / who injects it:** exactly the precedent the codebase already runs — `CREDENTIALS_MASTER_KEY`, `SECRET_KEY`, `PYTEST_DB_PASSWORD` are all operator-secrets read via `os.environ` by the API that the agent layer never sees (`settings.py:75`, `.env.example:160/222`). The operator supplies the token **out-of-band** when they want to authorize a gated write (paste into a UI field, a one-shot CLI, or a phone-side relay). The langgraph worker's env block is scoped to exclude it (same discipline #1652 §B.3 #6 applies to per-project images).

- **Blast radius if compromised:** if the token leaks into agent-readable scope, the whole distinction collapses — so the **single discipline that matters is keeping it out of the agent's env**. Bounded otherwise.
- **Operator effort:** medium setup (one env var + a way for the operator to present it per gated write); low per-action if a UI field holds it.
- **Coverage:** **universal — works in Mode A AND Mode B**, because it does not depend on a human being in the per-action loop, only on the secret being agent-inaccessible.
- **Classifier/spoof risk:** **unspoofable by the agent** (it lacks the secret) — this is the property #1799 said we'd need "once unspoofable identity exists." A shared static token is the *minimum-viable* unspoofable identity for a single-operator system (no PKI, no per-action signing). It is weaker than per-payload signing (a leaked token is a blanket day-pass) but **far cheaper** and matches the threat model (drift, not a key-exfiltrating adversary).
- **Verdict:** **strongest single mechanism for the threat model.** The one real risk (token in agent scope) is a deployment-discipline problem, identical in kind to the existing master-key discipline the platform already lives with.

### Option C — Out-of-band confirmation (push/ntfy + confirm endpoint, or web UI the agent cannot drive)

**Mechanism.** The gated write **HALTs** and emits a confirmation request over an existing out-of-band channel (web push / ntfy — `routers/push_ntfy.py`, `digest.py`). The operator confirms via the **web UI** (which the agent cannot drive in Mode B) or a phone tap; a confirm endpoint records the approval; the worker resumes.

- **Blast radius if compromised:** lowest — every gated write needs a human tap on a separate device/surface the agent does not control.
- **Operator effort:** highest per-action (a tap per write); medium setup (wire push → confirm → resume; the interrupt/resume loop already exists, #1107 / HITL).
- **Coverage:** universal; the **only option that gives genuine per-action operator presence in Mode B.**
- **Verdict:** the **right long-tail answer for high-stakes Mode-B actions** (#1585 send, #1801 build trigger), but heavyweight for routine writes. Composes on top of B as the escalation tier.

### Option D — Lean on the Claude Code approval layer (Mode A) + distinct mechanism (Mode B); a phased hybrid

**Mechanism.** In **Mode A**, treat the existing per-action Claude Code Write/Edit/Bash approval as the operator-presence signal — the human already approved the `curl`/PATCH. The API trusts a **Mode-A marker** that is only obtainable through that approved path (in practice: the operator-token of Option B, presented because the human approved the action). In **Mode B**, there is no CC layer, so the gated write **refuses the agent and HALTs** for an out-of-band operator action (Option B token presented by the operator, or Option C confirmation).

- **Blast radius:** Mode A — inherits the CC layer's per-action guarantee (strong). Mode B — refuse-by-default (strongest).
- **Operator effort:** near-zero in Mode A (already approving); per-action out-of-band in Mode B (acceptable — these are rare high-privilege writes).
- **Coverage:** universal **by construction** (each mode uses its native operator-presence source).
- **Verdict:** **the correct architecture.** It is not a separate mechanism so much as the *framing* that says: Mode A already has operator-presence (don't rebuild it), Mode B must refuse-and-wait. Option B is the concrete token that implements D's "distinct mechanism."

### Option E — Signed capability tokens (per-payload, PKI)

**Mechanism.** Operator runs a key-holding issuer that signs `{action, payload_hash, expiry}`; a verifier checks signature + payload-hash against the actual write (#1205 §2.3 Pattern 3).

- **Trade-offs:** narrowest blast radius (per-payload, expiring), scales to phone-side approval — but highest infra cost (issuance UI, key mgmt, verification).
- **Verdict:** **explicitly premature** per #1799's trust boundary (*"do not invest in crypto capability tokens prematurely"* for the single-operator threat) and #1205's rank-3 deferral. Defer until Option B's static token proves insufficient (e.g. multi-operator, or a real key-exfiltration threat emerges). **Not recommended now.**

---

## 4. Recommendation — phased, Mode-A-first, minimum-viable

**Recommended: Option D as the architecture, implemented with Option B's env-injected operator token as the concrete unspoofable signal, Option A's typed-ack as the Mode-A UX wrapper, and Option C reserved as the high-stakes Mode-B escalation tier. Option E (crypto) explicitly deferred.**

Justification against the threat model:

1. The threat is **agent drift in a single-operator system**, not a host-level adversary. The decisive property is **"the agent cannot produce the operator-proof."** Option B delivers exactly that at minimum cost — a static env secret the agent's process never holds, reusing the *identical* deployment discipline the platform already trusts for `CREDENTIALS_MASTER_KEY`. No PKI, no per-action signing.
2. **Mode A needs almost nothing new** — operator-presence already exists at the Claude Code layer (Option D framing). The token is simply how that approved action proves itself to the API.
3. **Mode B is refuse-by-default** — the only honest stance when no operator is in the loop. The gated write HALTs (`halt_reason=operator_proof_required`) and waits for the operator to present the token out-of-band (B) or confirm via push/UI (C). This matches #1652 §B.3 #5's "NOT autonomous PATCH" and #1205's "reduce to a physical operator action."
4. **Crypto is deferred** per #1799 — building signed capability tokens for a single-operator drift threat is over-engineering. Revisit only when the threat model changes (multi-operator, key-exfil, or phone-side delegation maturity).

### Phasing

- **Phase 1 (do-now, days):** Option B token + an `operator_action_audit` JSONL/ledger. Apply the check to the **#1275 `verified_by='user'`** path first (it is the keystone that unblocks the #1275 audit-trail gate, which in turn unblocks #1205 Stub B). Reuses `tool_grants.py`'s pure-function + own-audit pattern. **No new infra beyond one env var + one dependency.**
- **Phase 2 (with #1801 Phase-2 build):** apply the same token gate to **`runtime_config`** writes (`PATCH /api/projects/{id}`). This is the §B.3 #5 prerequisite landing exactly when #1801's build pipeline needs it — not before.
- **Phase 3 (with #1585 tiers):** apply to email **reply/send** tiers; wire Option C (push/ntfy confirm) as the escalation for the highest-blast tier (external send), composing on top of the token gate.
- **Deferred:** Option E crypto tokens; phone-side signed approval.

---

## 5. Per-path consumption — exactly how each gate checks the mechanism

A single pure module (mirror `services/tool_grants.py`): `services/operator_auth.py::check_operator_proof(token_header) -> OperatorDecision{OPERATOR | NOT_OPERATOR}` + an own-audit writer. A FastAPI dependency `require_operator_proof` (mirror `optional_agent_role_header`) extracts `X-Operator-Token` and constant-time-compares against `os.environ["OPERATOR_ACTION_KEY"]`.

- **#1275 (`verified_by='user'`).** In `routers/tasks.py` PATCH, after `acceptance_criteria` is serialized (`:205-213`), scan the incoming criteria: **if any criterion sets `verified_by='user'` (or `status='passed'` with a user attribution) and the request lacks operator-proof → 403** with a stable detail (`operator_proof_required: verified_by='user' may only be set by the operator`). AI-issued PATCHes may still set `verified_by` to a role/agent string — only the literal operator attribution is gated. The #1275 PreToolUse gate (#1205 Stub B) can then **trust `verified_by='user'` as genuinely-operator**, closing the self-unlock hole. *(Open sub-decision for operator: gate on the exact string `'user'`, or introduce a small reserved-attribution allowlist? Recommend: reserve `'user'`/`'operator'` as operator-only; everything else free-form — minimal change.)*
- **#1801 (`runtime_config`).** In `routers/projects.py` PATCH, **if the update dict contains `runtime_config` and operator-proof is absent → 403** (`operator_proof_required: runtime_config is operator-only`). No AI-autonomous path. In Mode B the worker never holds the token, so an autonomous `runtime_config` change is structurally impossible — exactly what §B.3 #5 demands before any adopter-set config triggers a real image build. Record an `operator_action_audit` row (actor='operator', action='runtime_config_write', project_id) mirroring `projects_audit`.
- **#1585 (email tiers).** In `routers/tools_email.py`, add a **tier check after the existing `_enforce_tool_grant` Layer-0 gate**: tiers above `read` (reply/send; and delete/trash if the operator wants it operator-gated) require operator-proof → 403 without it. The `X-Agent-Role` grant continues to answer *which role*; operator-proof answers *operator-present*. For the highest tier (external send), Phase 3 escalates to Option C (push confirm) instead of a bare token.

---

## 6. Decomposition — proposed atomic child-tasks (children of #1852)

1. **`[operator-auth] Phase 1 — operator-proof primitive + verified_by='user' gate`**
   *Scope:* `OPERATOR_ACTION_KEY` env var (+ `.env.example` doc, mirror `CREDENTIALS_MASTER_KEY`); pure `services/operator_auth.py::check_operator_proof` + own-audit (mirror `tool_grants.py`); `require_operator_proof` dependency (mirror `optional_agent_role_header`); 403-gate `verified_by='user'` in `routers/tasks.py` PATCH; 1-3 contract-smoke tests (proof present → 200, absent → 403, AI role-attribution → 200). **Unblocks #1275** (and #1205 Stub B downstream). *No migration — JSONL audit first; promote to a ledger table only if a second consumer needs it.*
2. **`[operator-auth] Phase 2 — runtime_config operator-only write gate`**
   *Scope:* gate `runtime_config` mutations in `routers/projects.py` PATCH behind `require_operator_proof`; `operator_action_audit` row on write (consider a small `operator_actions` ledger table here if §1 chose JSONL — decide at this task). Lands **with** #1801 Phase-2 (migration-vs-ORM timing: do NOT ship the gate ahead of the `runtime_config` column). **Unblocks #1801 Phase-2** (clears §B.3 #5 blocking prerequisite).
3. **`[operator-auth] Phase 3 — email tier escalation gate + out-of-band confirm`**
   *Scope:* tier model for `/api/tools/email/*` (read vs reply/send/delete); operator-proof required above `read`; wire Option C push/ntfy confirm + resume for the external-send tier (reuse #1107 interrupt/resume + `push_ntfy.py`). 1-3 smoke tests per tier. **Unblocks #1585.**
4. *(Optional, defer)* **`[operator-auth] Mode-B out-of-band operator-proof relay`**
   *Scope:* the headless HALT → operator presents token / taps push → resume loop, generalized across all three gated paths. Only if Phase 1-3 reveal the per-path HALT handling needs a shared component. Ties to #1205 Stub B + the mobile/push thread.

---

## 7. Risks / honest caveats

- **The whole scheme rests on one discipline: `OPERATOR_ACTION_KEY` must never enter agent-readable env** (not the Claude Code session env, not the langgraph worker env block). This is identical in kind to the existing `CREDENTIALS_MASTER_KEY` discipline — but it MUST be called out in `.env.example` and the langgraph worker's env scoping. A leak turns the gate into a blanket day-pass the agent can self-issue.
- **Static token = blanket pass while valid.** Acceptable for the single-operator drift threat (a drifting agent has no token at all; it is not trying to crack one). NOT acceptable if the threat model ever includes an agent that can read arbitrary host env — at which point escalate to Option E (per-payload signed tokens) or Option C (per-action human tap).
- **Typed-ack (Option A) must not be mistaken for the security boundary** — it is UX friction, not identity. The boundary is the agent-inaccessible token (B) / the CC approval layer (D) / the human tap (C). Document this prominently so a future task does not "simplify" by dropping the token and keeping only the typing.
- **Mode-B UX cost:** refuse-and-HALT means high-privilege Mode-B writes always pause for the operator. That is the intended cost — these are exactly the writes (#1801 image build, #1585 send) that should not be autonomous. If routine `verified_by='user'` marking in Mode B becomes a bottleneck, that is a signal the AC was mis-modeled (an AI should mark `verified_by='agent'`; only genuine operator sign-off is `'user'`).
