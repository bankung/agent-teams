# Tool Governance — registry, grants, discovery (Mode-A-first)

**Date:** 2026-06-02 · **Proposing role:** Lead · **Status:** design FINALIZED (2 adversarial review rounds + P0 spec review, all open decisions resolved) · **Related:** Kanban #1799 (P0 impl), #1797 (instance: wire `/api/tools/email` into secretary)

## Problem

The platform exposes tools as FastAPI HTTP endpoints (today: `/api/tools/email/{gmail,outlook}/trash` + auth/usage). Agents "call" a tool by `curl`-ing it via `Bash`. Adding one tool today touches Python + `settings.json` allow-list + every agent prompt + a session restart — so growing the catalog *feels* like rebuilding the system.

**Question:** how do we add tools over time and let agents call them *within the boundaries we define*, **without build/deploy of the whole system**?

## Headline answer

A genuinely new **capability** must ship code somewhere — you cannot avoid a deploy 100%. **But** *who-can-call-what* and *agent-awareness* can be made **data + Lead-mediated**, so:
- recurring cost to grow the catalog = **write one handler + add one registry entry**
- day-to-day governance cost = **edit one JSON field** (`config.tool_grants`) — with **no settings/prompt/restart edits and no deploy**.

## Separate THREE concerns (not two)

| Concern | Mechanism | Cost to change |
|---|---|---|
| **Capability** (the logic that runs) | handler in the API, **action-generic per provider** | deploy **one API service** (prod) — never "the whole system" |
| **Authorization** (boundary, enforced) | `config.tool_grants` (JSONB) read at request time | **0** — PATCH the project, live |
| **Discovery** (agent awareness, advisory) | **Lead reads grants → injects allowed-tool specs into the spawn brief** | **0** — no new endpoint, no prompt edit |

> Discovery is *advisory* (an LLM can ignore/hallucinate it). Authorization is *authoritative*. Do not conflate them — see Trust boundary.

## Relationship to the EXISTING permission gate (do not reinvent)

There is already a tool-permission gate: [`langgraph/tools/permission_gate.check_permission()`](../../../../../langgraph/tools/permission_gate.py) → `auto_allow|halt|reject` from a tool's **tier** (`read|write|network|destructive`) + the `tools_enabled` kill switch in `tools_config` (Kanban #979/#949). It is **per-tier** and gates **LangGraph specialist tools (Mode B engine)** — NOT the HTTP `/api/tools/*` endpoints (Mode A), which today have no per-caller gate (only `X-Project-Id` + the `gate.py` daily cap).

P0 is therefore a **different, complementary layer**: per-**agent-name** membership for **HTTP/Mode-A** tools. Decision: **keep them separate in P0** (different tool worlds); converge only in Mode B. To make convergence cheap later, P0 **reuses the existing `ToolTier` vocabulary** (`read|write|network|destructive`) in its registry and does **not** invent a parallel tier enum.

## P0 — minimal, do-now (RESOLVED spec)

1. **Grant store** — `config.tool_grants` (the free-form `config` JSONB where `enabled_roles` already lives — **NOT `tools_config`**, which is the strict `ToolsConfig` model with `extra="forbid"` and would 422 on an extra subkey):
   ```json
   { "<agent-type-name>": ["<tool_name>", "..."] }
   ```
   - **role = agent-type-name string** (e.g. `secretary`, `secretary-email-triage`, `dev-backend`) — cross-team; NOT the int role codes `enabled_roles` uses.
   - **Membership only** — presence in the list = allowed. No `{allowed, max_units}` object (per-role units are out — see Authorization gate below).
   - Add a Pydantic validator on `config` mirroring `_validate_enabled_roles_in_config` (values are lists of registry-known tool names).
2. **Registry** — a static in-code module (mirror [`services/integrations_registry.py`](../../../../../api/src/services/integrations_registry.py)): `{ tool_name: { tier: ToolTier, version } }`, seeded `gmail.trash` + `outlook.trash` (tier `destructive`). **No `cost_units`** (would duplicate `_TRASH_UNITS_PER_MESSAGE` etc. in the router). `tier`/`version` are **forward-compat metadata — NOT consumed by the P0 check.**
3. **Role signal** — an **optional `X-Agent-Role` header** read by a new dependency mirroring `require_project_id_header`. The Lead/agent sets `-H "X-Agent-Role: secretary"`. Advisory + spoofable → acceptable for Mode A (see Trust boundary).
4. **Authorization check (no hack — gate.py & permission_gate.py both UNTOUCHED)** — a new pure module `services/tool_grants.py::check_grant(config, role, tool_name)`, styled like `permission_gate.check_permission` and following the grant+refusal-audit precedent of [`credentials.py::_policy_grants_use`](../../../../../api/src/routers/credentials.py). It writes its OWN audit (role + tool + decision) via its own function — it does **not** modify `gate.py`'s FROZEN `log_audit` (interface frozen by #1604/#1608; only `tools_email.py` consumes it). The existing `gate.py` daily-cap (`check_and_increment`) stays as the **single combined units gate** — P0 adds NO per-role units.
5. **Discovery** — Lead-mediated: Lead reads `config.tool_grants[role]` + the registry and injects the allowed tools + usage spec into each spawn brief. No agent-facing manifest endpoint in Mode A.

### Enforcement semantics (RESOLVED)

- `tool_grants` **key absent** → **unrestricted** (every role, every tool).
- role **NOT a key** in `tool_grants` → **unrestricted** for that role *(opt-in restriction: you only lock down roles you explicitly list)*.
- role **IS a key** → allowlist regime: tool in its list → allow; **tool not in its list → hard `403`**. (Empty list = that role is denied every tool.)

## Deferred → tie to the Mode A→B roadmap (do NOT build ahead of justification)

- Agent-facing **manifest endpoint** + one-time generic prompt instruction — only when agents run autonomously with no Lead composing briefs (Mode B).
- **Registry/grants as DB tables + CRUD + UI** — only when the catalog grows or a non-dev must manage grants.
- **Unspoofable identity** (signed capability token / MCP-authenticated connection) **AND collapsing the allow-list to a single `/api/tools/*` pattern** — ship **together only**; collapsing the outer guard before identity is unspoofable removes a real wall.
- **MCP proxy** (registry-driven) — when shifting transport for native discovery + no-Bash across the system.
- **Convergence** of P0's per-agent membership with the tier-based `permission_gate` — Mode B.
- **Registry as cost source of truth** (move `_TRASH_UNITS_PER_MESSAGE` etc. into the registry) — only if/when a second consumer needs it.

## 🔒 Trust boundary (do not oversell)

`X-Agent-Role` is **spoofable**. Hard `403` on a missing grant therefore stops **agent drift/confusion** (the actual Mode-A threat in a single-operator system) — it does **not** stop a malicious agent, which is out of the Mode-A threat model. The *fully* enforced wall in Mode A remains the **Claude Code layer** (per-agent `tools:` list + hooks + `settings.json` allow-list). `config.tool_grants` becomes a hard wall against malice only once unspoofable identity exists (Mode B). Do not invest in crypto capability tokens prematurely.

## 📦 Deploy boundary

| Change | Build? | Restart? |
|---|---|---|
| grant / revoke a tool ↔ role | ❌ | ❌ (PATCH project) |
| agent becomes aware of a new tool | ❌ | ❌ (Lead injects into brief) |
| new **action** on an existing provider | deploy one API service | — |
| new **provider / capability** | deploy one API service | — |

## 🧭 "Add a tool in the future" — the real checklist

1. Write the handler (or add an action to an existing handler) → deploy the API service *(the only deploy step)*.
2. Add one registry entry (`tier` / `version`).
3. PATCH `config.tool_grants` for the roles that should be allowed it *(live — no deploy)*. Remember: a role is restricted ONLY if it appears as a key; an unlisted role stays unrestricted.
4. Done — the Lead injects the tool into that role's brief. **No prompt / settings / restart edits.**

## Interaction with #1797 (secretary email wiring)

#1797 (prompt-edit MVP) is **independent** of P0 and not blocked by it. Under P0 hard-enforce: if `secretary` is **listed** in `tool_grants`, its allowlist MUST include `gmail.trash` (+ any other #1797 tool) or the secretary delete flow will `403`; if `secretary` is **unlisted**, it stays unrestricted and #1797 is unaffected. P0 seeding should account for this.

## Review rationale (why P0 is this small)

- **Round 1** caught: discovery≠boundary conflation; spoofable `role` key; MCP oversold; "hot-reload avoids deploy" is dev-only; full registry tables = YAGNI; collapsing the allow-list before strong identity inverts the safe sequence.
- **Round 2** caught (against Round 1's fixes): wrong threat model (single-operator Mode A → drift, not malice → no signed tokens); MCP proxy is premature Mode-B infra; the manifest endpoint is unnecessary in Mode A because the **Lead already composes every brief**. → P0 collapsed to grants-JSON + lightweight registry + Lead-inject.
- **P0 spec review** caught (against the implementation): `tools_config` is `extra="forbid"` → grants must live in `config`; an existing `permission_gate` (tier-based, Mode B) must not be reinvented and its `ToolTier` vocab must be reused; `gate.py` is frozen → add a NEW module, never mutate it; per-role `max_units` dropped (combined gate stays); registry `cost_units` dropped (would clash with router constants). Open decisions resolved: hard-403 enforcement; opt-in per-role restriction (unlisted role = unrestricted).
