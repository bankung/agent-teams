# Design proposal — `context_footprint` audit metric (fills the auditor's drift slot)

**Status:** proposal (2026-06-02) — input to Kanban **#1213** (GOV5 drift metric). Author: Lead. Not yet approved/implemented.
**Related:** #1583 (decisions.md compaction — the incident that motivated this), #1786 (lightweight detect hook — the cheap sibling), #1222 (stale-doc curator — the action layer), #1223 (auto-propose, HITL).

## Problem
The per-session Lead bootstrap reads project context (shared/ docs). That context grows append-only and unchecked — `decisions.md` reached **244 KB (~61k tokens)** before manual compaction (#1583). There is no automatic signal when the bootstrap-read footprint drifts upward, so bloat is only noticed when startup becomes painful.

## Why this is the auditor's "drift" metric
`project-auditor` already computes 3 metrics; the 3rd is `drift_placeholder` = **"NOT IMPLEMENTED — needs design"** (slot reserved for #1213). **Context bloat is literally drift** — the project's context footprint drifting up over time — so it maps onto that slot cleanly, reusing the whole existing pipeline: `health_thresholds` (thresholds) → auditor run (GOV2 #1210) → recurring schedule + flag (GOV3 #1211) → `/review` UI (GOV4 #1212).

## Cross-project scope (this covers EVERY project, not just agent-teams)
The auditor is a **generic per-project** agent (spawned with a `project_id`; GOV3 schedules it across all active projects). So this metric runs for **every project** — which is correct: any project's shared/ context can bloat. Three consequences:

1. **The metric MUST be generic — do NOT hardcode dev-team filenames.** `decisions.md` / `api-contracts.md` / `db-schema.md` are the *dev* team's bootstrap docs; a content / seo / secretary project has different docs. So the primary measures are team-agnostic: **total shared footprint (KB), file count, top-N largest files**. The named-file measures are an OPTIONAL per-team overlay driven by a "bootstrap-read set" config (per `team` or per `project.config`), not baked into the metric.
2. **The hook (#1786) does NOT cover other projects — it's agent-teams-repo-only.** It's a git/`.claude` hook living in the agent-teams repo, so it only sees agent-teams' context (+ any legacy `working_path=null` projects whose context sits in-repo under `context/projects/<name>/`). Projects with an external `working_path` live outside this repo → the hook can't see them. **So: hook = cheap agent-teams-local guard; the auditor metric = the actual cross-project coverage.** Complementary, not redundant.
3. **Reachability caveat (open).** The backend endpoint can stat in-repo projects (agent-teams + `working_path=null`) fine. But `working_path`-set projects live outside the repo and **the API container may not reach those host paths** — the same split-brain that forced host-side scaffolding (#1618 / #795). So cross-project footprint for external-`working_path` projects needs one of: that path mounted into the API container, a host-side size reporter, or computing it where the bound session runs. Must resolve before the metric is trustworthy for non-in-repo projects.

## Constraint that shapes the design
The auditor is **curl-only** (its read-only PreToolUse hook narrows Bash to curl). It therefore **cannot `du`/`wc` the filesystem**. So the size data must come from the **backend** (the API container has FS access to the repo; `working_path` reachability is the open caveat above).

## Proposed shape

### 1. Backend endpoint (new)
`GET /api/projects/{id}/context-footprint` → team-agnostic size measures for the project's `shared/` (resolved via `projects.working_path`, fallback `context/projects/<name>/shared`):
```json
{
  "project_id": 1,
  "team": "dev",
  "shared_dir": "context/projects/agent-teams/shared",
  "toplevel_md_files": 21,
  "toplevel_md_kb": 310,                 // top-level *.md, EXCLUDING archives (grep-on-demand, not bootstrap reads)
  "largest": [ {"file":"api-contracts.md","kb":82}, {"file":"db-schema.md","kb":44}, "..." ],   // excl. archives
  "named_set": { "files": ["decisions.md","api-contracts.md","db-schema.md"], "kb": 153 },       // OPTIONAL per-team overlay; null unless a bootstrap-read set is configured
  "generated_at": "<ISO8601 Z>"
}
```
All primary measures are **filename-agnostic** (work for any team). Archives (`*archive*`) + subfolders are excluded (not bootstrap reads). `named_set` is the only team-specific field and is **null** unless a bootstrap-read set is configured (per `team` default or `project.config`). Read-only stat walk; one dir, no N+1.

### 2. Auditor metric (replace `drift_placeholder`)
`context_footprint` metric: auditor curls the endpoint, compares to thresholds, reports value + breach. Keeps the existing report schema shape (swap the placeholder object for a real one).

### 3. Thresholds (extend `health_thresholds` JSONB)
```json
{
  "context_largest_doc_kb_threshold": 100,   // ANY single top-level doc over this = breach (catches the decisions.md-244KB class, any project/filename)
  "context_toplevel_kb_threshold": 250,      // total top-level *.md (excl. archives) over this = breach
  "context_toplevel_files_threshold": 30,    // file-count sprawl
  "context_bootstrap_set_kb_threshold": 200  // OPTIONAL — only checked when named_set is configured
}
```
All **filename-agnostic**. Per-project override + baked defaults (mirrors the budget/failure pattern). Breach = any sub-measure over its threshold; the optional `bootstrap_set` check is skipped when `named_set` is null.

### 4. Recommendation impact
A context breach contributes **`review`** (NOT `pause` — bloat is not an emergency). Reason string is filename-agnostic, e.g. `"<largest doc> 142KB > 100KB — compact (split active+archive, see #1583 pattern)"` or `"top-level context 310KB > 250KB"`.

### 5. Breach → action
Flag surfaces in GOV4 `/review`. Action (the actual compaction — split active+archive like #1583) is:
- **today:** human-triggered (operator, or a Lead session) — because zero-human execution is gated on **Mode-B (#1652)**;
- **later:** auto-proposed + HITL-approved via **#1222 curator** + **#1223 auto-propose**.

## Layering (how the 3 pieces fit)
- **#1786 hook** = cheap, truly-auto detect (fires on push/write, no session/Mode-B) across ALL **in-repo** projects (`context/projects/*/shared`); external-`working_path` projects are out of its reach. First line of defense.
- **this metric (#1213)** = structured, scheduled signal inside the auditor + `/review` surfacing. Second line.
- **#1222 curator + #1223** = the action layer (propose specific compactions).
All three are detect/propose; the **reduce** half is the lazy-read doctrine (#2 / narrowed to api-contracts + db-schema on-demand).

## Phased plan
1. Backend `context-footprint` endpoint + 1 test (smallest surface; usable on its own + by the hook/dashboard).
2. Swap auditor `drift_placeholder` → `context_footprint`; add the 3 `health_thresholds` keys + defaults; wire into the recommendation count.
3. (later) curator action (#1222) + auto-propose (#1223).

## Open questions
- Threshold values: 80 / 200 / 30 are first-guesses from today's numbers (decisions 27KB post-compaction, bootstrap-read ~153KB) — tune from real data (#1213 is the "threshold iteration from real data" workstream).
- The optional `named_set` (per-team bootstrap-read set) needs a config home (a `team` default + `project.config` override) — defer to v2; v1 ships the filename-agnostic measures only (largest-doc + total + file-count), which already catch the bloat class with no per-team config.
- `context_footprint` is the FIRST concrete signal filling the auditor's `drift` slot. If #1213 later wants broader "drift" (scope / behavior), context_footprint becomes ONE component of a composite drift score, not the whole slot — name/shape so #1213 isn't painted into a corner.
- Endpoint auth: mirror `/pl` / `progress-stats` (X-Project-Id == path) for parity.
