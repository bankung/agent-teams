# SEM Credential / Secret Policy (v1 — recommend-only)

**Status:** locked 2026-05-20 (Kanban #1269 AC6 — followup of #996).
**Scope:** v1 = SEM agents output RECOMMENDATIONS only — no live ad-account mutations. This doc captures the credential/scope/audit posture that v2 (active campaigns + live spend) will inherit.
**Connects to:** [Mode B authorization-chain doctrine](design/mode-b-authorization-chain.md) (Kanban #1205) — v2 live spend is the canonical "real money flowing out" scenario the authz-chain was designed for.

---

## Scope of v1 (today)

The 4 SEM agents (`sem-campaign-lead` Opus / `google-ads-specialist` Sonnet / `meta-ads-specialist` Sonnet / `platform-ads-coordinator` Sonnet) currently:

- READ ad-library / SERP / competitor / campaign-history references
- RECOMMEND campaign structure, ad copy variants, audience targeting, budget allocations, bidding strategies
- WRITE markdown briefs to `_scratch/` or `context/projects/<p>/shared/`

They DO NOT today: launch ads, mutate live budgets, pause/resume campaigns, upload creatives, set bids, or call any ads API with a write-scoped credential.

**The v1 hard rule:** no live ad-account writes. Specialists produce a brief; the operator launches it themselves in the platform's native UI (Google Ads UI, Meta Ads Manager, LinkedIn Campaign Manager, etc.). This is the same posture as `[[feedback-job-apply-scope]]` for the secretary job-applications: read + analyze + draft, never auto-submit.

This is the same v1 posture as the Data Analytics team (read-only / recommend-only); the credential-policy below is pre-staged so v2 can land cleanly.

## Why v1 is hard-locked

Three reasons. All three must be addressed before any v2 unlock.

1. **Real money blast radius.** A misconfigured Google Ads campaign can burn $5k in 24 hours on a small account, $50k+ on a large account. The classifier-gated authorization chain (Pattern 5 hook + worker-side evaluator per Kanban #1274 + #1279) is the gate; until it's smoke-validated against an actual write attempt, no auto-mutate.
2. **Reversibility asymmetry.** A bad SEO recommendation costs a content rewrite; a bad SEM campaign launch costs displayed impressions that can't be unspent. Asymmetric blast radius demands a high-friction gate.
3. **Platform ToS.** Google Ads / Meta / LinkedIn TOS allow scripted API access but place specific requirements on attribution + audit. v1 read-only means we don't trip those requirements; v2 requires explicit compliance review (operator-led) before unlock.

## Credential storage — where secrets actually live

Same 3-tier architecture as the Data Analytics integration policy (`data-analytics-integration-policy.md`):

| Tier | Location | When to use | Rotation cadence |
|---|---|---|---|
| **T1 — host env via `.env`** | `.env` at repo root (gitignored) → docker compose loads into containers | v2 default for non-mutating reads (ad-library fetches, performance reports). Operator manages by hand. | 90 days or on operator job-change |
| **T2 — local file under `_runtime/secrets/`** | Untracked dir per-machine, never synced. Service-account JSON files (Google OAuth client secrets), Meta system-user tokens. | Long-form creds (multi-line JSON, PKCS12) | 90 days; immediately on suspected leak |
| **T3 — vault / Tailscale-tunneled secret service** | Operator's vault-of-choice (Bitwarden / 1Password Connect / Vault). Surfaced into containers via runtime fetch at startup. | v2 mandatory for ALL write-scoped credentials | 30 days; immediately on contractor offboard or job-change |

Today's repo lives at T1+T2; **v2 write-capable credentials MUST live at T3 — no T1/T2 for write tokens**. This is the architectural commitment, codified here so we don't softpedal later.

**Rule 0 — secrets NEVER in chat / Kanban / git.** Subagents requiring a credential value ask the operator inline; operator pastes into env; subagent reads `$env:VAR_NAME` and NEVER echoes back. Logs / Kanban descriptions / specs reference credential NAMES only.

**Rule 1 — env var naming convention:** `SEM_<PLATFORM>_<KIND>_<SCOPE>`. The trailing `_SCOPE` distinguishes read-only vs write-scoped credentials at the env-name level so misconfiguration is visible at-a-glance. Examples:

### Google Ads

- `SEM_GOOGLEADS_DEVELOPER_TOKEN=<token>` (T1) — the developer token; tied to a Google account, NOT a campaign
- `SEM_GOOGLEADS_OAUTH_CLIENT_ID=<id>` (T1)
- `SEM_GOOGLEADS_OAUTH_CLIENT_SECRET=<secret>` (T2, JSON file form `_runtime/secrets/google-ads-oauth.json`)
- `SEM_GOOGLEADS_REFRESH_TOKEN_READ=<token>` (T1 in v1; T3 mandatory in v2) — scopes: `https://www.googleapis.com/auth/adwords` is the universal Ads scope; **v1 enforces read-only via the Google Ads MCC's user-permission layer (link the agent's email as "Read only" on the MCC), NOT via OAuth scope** (Google Ads OAuth has only one scope; per-permission is set on the ad-account side)
- `SEM_GOOGLEADS_REFRESH_TOKEN_WRITE=<token>` (T3 only, v2) — same OAuth scope but linked to an MCC user with "Standard" or "Admin" permissions. **MUST be wrapped by Pattern 5 hook**.
- `SEM_GOOGLEADS_LOGIN_CUSTOMER_ID=<id>` (T1) — the MCC ID for impersonation; required for API auth even on reads.
- `SEM_GOOGLEADS_TARGET_CUSTOMER_ID=<id>` (T1) — the customer ID being managed; per-project setting.

### Meta (Facebook + Instagram + Audience Network)

- `SEM_META_APP_ID=<id>` (T1) — Meta app ID
- `SEM_META_APP_SECRET=<secret>` (T1+T2 in v1; T3 in v2)
- `SEM_META_SYSTEM_USER_TOKEN_READ=<token>` (T1) — system-user token with `ads_read` permission only
- `SEM_META_SYSTEM_USER_TOKEN_WRITE=<token>` (T3 only, v2) — `ads_management` + `business_management`. **Pattern 5 hook + worker-evaluator**.
- `SEM_META_BUSINESS_ID=<id>` (T1)
- `SEM_META_AD_ACCOUNT_ID=<id>` (T1) — per-project setting

### LinkedIn (Campaign Manager)

- `SEM_LINKEDIN_CLIENT_ID=<id>` (T1)
- `SEM_LINKEDIN_CLIENT_SECRET=<secret>` (T2)
- `SEM_LINKEDIN_ACCESS_TOKEN_READ=<token>` (T1) — scopes: `r_ads`, `r_ads_reporting` only
- `SEM_LINKEDIN_ACCESS_TOKEN_WRITE=<token>` (T3 only, v2) — adds `rw_ads` + `rw_organization_admin`. **Pattern 5 hook**.
- `SEM_LINKEDIN_AD_ACCOUNT_URN=<urn>` (T1) — per-project setting

### TikTok Ads

- `SEM_TIKTOK_APP_ID=<id>` (T1)
- `SEM_TIKTOK_APP_SECRET=<secret>` (T2)
- `SEM_TIKTOK_ACCESS_TOKEN_READ=<token>` (T1) — read-only role on the BC
- `SEM_TIKTOK_ACCESS_TOKEN_WRITE=<token>` (T3 only, v2) — operator role on BC. **Pattern 5 hook**.
- `SEM_TIKTOK_ADVERTISER_ID=<id>` (T1) — per-project setting

### Microsoft Ads (Bing)

- `SEM_MICROSOFTADS_DEVELOPER_TOKEN=<token>` (T1)
- `SEM_MICROSOFTADS_OAUTH_CLIENT_ID=<id>` (T1)
- `SEM_MICROSOFTADS_REFRESH_TOKEN_READ=<token>` (T1) — `https://ads.microsoft.com/msads.manage` scope (no separate read/write scopes — granularity at user-permission level)
- `SEM_MICROSOFTADS_REFRESH_TOKEN_WRITE=<token>` (T3, v2)
- `SEM_MICROSOFTADS_CUSTOMER_ID=<id>` + `SEM_MICROSOFTADS_ACCOUNT_ID=<id>` (T1)

### Other platforms (Reddit / X-Twitter / Pinterest / Snapchat / Amazon Ads)

Same shape: `<APP_ID> + <CLIENT_SECRET> + <READ_TOKEN> + <WRITE_TOKEN>` quad, with WRITE forced to T3 in v2. Per `platform-ads-coordinator` agent's playbook, each gets its own env-var quad as the platform graduates from "one-off-experiment" to "regular-cadence."

## Per-platform minimum-scope guidance

The pattern across all 5 major platforms (Google / Meta / LinkedIn / TikTok / Microsoft) is identical:

1. **Account-level role separation.** Have TWO distinct OAuth users / system-users per platform: one with `Read-only` / `Reports-only` (used by v1), one with `Operator` / `Standard` / `Admin` (used by v2 — MUST be hook-gated).
2. **Per-project scope.** Each project's brief carries the customer ID / business ID / ad account ID for ONLY that project's targets. Subagents read those scope-IDs from env vars and don't enumerate `list_accounts` to discover others — defense-in-depth against credential-overshare.
3. **Audit trail.** Every API call (read OR write) logs to `_scratch/sem-audit-trail.log` (today; `/api/audit-events` later) with: timestamp, agent, platform, endpoint, scope_id, response_summary.
4. **Token rotation.** Refresh tokens have indefinite life until revoked; the operator rotates the OAuth credential on the platform's UI on the 90-day cadence (or immediately on suspicion). Subagents read the new token from env on next spawn — no token persistence beyond the env file.

## v2 unlock prerequisites — what must land before any platform goes live

Each of these is a hard gate. Skipping any of them = no v2 unlock for that platform.

1. **Pattern 5 hook smoke-verified for the platform's write tool.** The harness PreToolUse hook (`.claude/hooks/approval-policies-gate.ps1` per #1274) must return `requires-attention` or `deny` on a synthetic "launch campaign" call AND the harness must honor the decision. #1274 REVIEW state pending operator smoke; until that passes, no v2 platform.
2. **Worker-side approval_evaluator coexistence verified.** Per #1279 (closed 2026-05-20), the worker fail-soft on unknown matcher keys — coexistence schema doc at `decisions-approval-policies-schema.md`. Already done; gates re-verify.
3. **`projects.approval_policies` JSONB rule** authored for the platform: a Pattern 5 rule with `tool_name` matching the write tool + `target_url_pattern` matching the platform's API host + `content_predicate` matching campaign-launch keywords. Operator authors per platform; reviewable in Kanban.
4. **Per-project budget cap composes.** The hard cost cap from #1194 (`services/budget_gate.py`) must extend to count SEM spend against `projects.budget_daily_usd`. v2 SEM-write spawns add a `cost_usd_estimate` field; cap-check fires before any write tool call.
5. **PostToolUse audit-emit** on the write tool. Performance-dashboard hook (#1269 AC4 — `_scratch/draft-sem-performance-dashboard.ps1`) extends to capture write-tool side-effects; audit log includes operator's authorization signal (which Kanban task triggered the write).
6. **Operator review of platform's ToS.** Each platform has slightly different rules around automated-management. Document operator's acknowledgment in `_scratch/sem-tos-acks-<platform>.md` per platform.

When all 6 are satisfied for a single platform (say, Google Ads first), that platform unlocks. Other platforms unlock independently — Google Ads being live doesn't grant Meta automatic v2 status.

## Read-only operations safe under v1 (today)

These are the operations v1 specialists CAN perform via API (operator pre-grants read tokens):

- Google Ads: `GoogleAdsService.search`, `CampaignService.getCampaign`, `AdGroupService.getAdGroup`, ad library reads
- Meta: `GET /act_<id>/ads`, `GET /<page_id>/insights`, ad library
- LinkedIn: `GET /v2/adAccountsV2`, `GET /v2/adCampaignsV2`, reporting endpoints
- TikTok: `GET /campaign/get/`, `GET /reports/integrated/get/`
- Microsoft Ads: `ReportingService` SOAP reports, `CampaignManagementService.GetCampaignsByIds`

The `analytics-platform-integrator` Sonnet agent (from the Data Analytics team) is the natural cross-team helper here — read-and-summarize across platforms, aggregate into the SEM specialist's briefs.

## Audit trail format

Every SEM agent's API touch logs to `_scratch/sem-audit-trail.log` (today; future `/api/audit-events`):

```
<iso-ts>\tproject_id=<id>\tagent=<agent>\tplatform=<platform>\tcall_kind=<read|metadata|estimate|write>\tendpoint=<endpoint>\tscope_id=<account>\tresult=<ok|denied|error>\tlatency_ms=<N>
```

Three fields are load-bearing for v2 review:
- `call_kind=write` — must be ZERO in v1; appearance = bug
- `result=denied` — a Pattern 5 hook denial; expected during v2 calibration
- `scope_id` — confirms the call hit the project's account, not a sibling

## Rotation cadence + offboarding

- **Quarterly (90 days):** rotate T1 + T2 SEM credentials via operator runbook. Read-tokens get rotated as part of the broader analytics rotation. Write-tokens (v2) rotate every 30 days at T3.
- **On operator job-change:** rotate ALL T1+T2+T3 credentials immediately. Re-register OAuth apps on the new tenant where applicable. Update `_runtime/secrets/`.
- **On suspected leak:** revoke at the platform's UI + rotate the env var + audit `_scratch/sem-audit-trail.log` for last 30 days for anomalous `endpoint` or `scope_id` rows.
- **Per-platform offboard:** if a platform is sunsetted for the operator (e.g., stop using LinkedIn ads), revoke the LinkedIn OAuth grant at the platform's app-management page AND remove the env vars AND archive the audit log row.

## v2 hooks — what changes when active campaigns land

When v2 (live writes) ships for a platform, this doc gets extended:

1. **Authorization chain (Mode B integration):** the platform's write tools route through Pattern 5 hook → approval_policies JSONB rule → either auto-approve (rare, for pre-vetted shapes), auto-deny (for known-bad patterns), or requires-attention (operator confirms). See `design/mode-b-authorization-chain.md` for the 5 patterns.
2. **Budget gate composition:** spend-cap PreToolUse hook (#1269 AC4 — `_scratch/draft-sem-spend-cap-gate.ps1`) becomes authoritative; reads `projects.budget_daily_usd` + composes with `services/budget_gate.py` for atomic check-and-spend.
3. **Per-platform approval_policies sample rules:**
   ```json
   {
     "rules": [
       {
         "name": "auto-deny Google Ads writes >$500/day",
         "match": {
           "tool_name": "Bash",
           "content_predicate": "googleads.*mutate.*campaign|googleads.*create_campaign",
           "amount_usd_gt": 500
         },
         "action": "auto_deny"
       },
       {
         "name": "require-attention all Meta auto-mutations",
         "match": {
           "tool_name": "Bash",
           "target_url_pattern": "graph\\.facebook\\.com/.+/(campaigns|adsets|ads)\\?.*METHOD=POST"
         },
         "action": "require_attention"
       }
     ]
   }
   ```
4. **PostToolUse audit emit on write:** `_scratch/sem-audit-trail.log` extends; `call_kind=write` rows appear; `result=ok` rows trigger a Kanban audit-task creation (`task_type=audit`) for operator review at next cadence.

These v2 hooks are intentionally OUT of v1 scope. Operator reviews this doc + the smoke result of #1274 (Pattern 5 harness deferral) before unlocking the first platform.

## Cross-references

- Kanban #996 — SEM team Phase 1 (parent)
- Kanban #1269 AC6 — this doc
- Kanban #1205 — Mode B authorization-chain doctrine
- Kanban #1194 — Hard cost cap (budget_gate.py)
- Kanban #1274 — Pattern 5 harness PreToolUse hook (REVIEW state — gates v2 unlock)
- Kanban #1279 — approval_policies coexistence schema (closed)
- `context/projects/agent-teams/shared/design/mode-b-authorization-chain.md`
- `context/projects/agent-teams/shared/decisions-approval-policies-schema.md`
- `context/projects/agent-teams/shared/data-analytics-integration-policy.md` (sibling — same 3-tier credential model)
- `_scratch/draft-sem-spend-cap-gate.ps1` + `.sh` (PreToolUse — #1269 AC4)
- `_scratch/draft-sem-performance-dashboard.ps1` + `.sh` (PostToolUse — #1269 AC4)
