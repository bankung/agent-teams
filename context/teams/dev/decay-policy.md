# Decay policy — when docs expire, how Lead handles it

**Scope:** every doc Lead writes or promotes under `context/projects/<p>/shared/docs/` or `context/teams/dev/`. Standards (`context/standards/*`) and decisions logs (`decisions.md`) are EXEMPT — see "Out of scope" below.

**Why this exists:** `_scratch/` is for tempfiles, `shared/docs/` is for keepers — but some keepers are inherently perishable (lib API snapshots, security advisories) and others are evergreen (decisions, lessons learned). Without a class system, all docs age into the same "is this still current?" doubt — and the doubt silently rots the trust in the whole tree. This policy declares the class **at write time** so future readers know what they're holding.

## The three decay classes

Every doc Lead promotes to `shared/docs/` or writes to `context/teams/dev/` (other than this policy itself, lessons, and decisions logs) **MUST** declare a `decay_class` in its frontmatter.

| Class | TTL? | Examples | Why |
|---|---|---|---|
| **perishable** | ✅ MUST set `decay_after` | research-*.md (lib docs), security advisories, perf baselines, "architecture as-of-<date>" snapshots, version-pinned compatibility notes | content ties to external state that drifts naturally |
| **review-on-touch** | ❌ no TTL, reviewed when adjacent code/topic changes | feature summaries, module READMEs, architecture maps, onboarding docs | rot is **code-driven** not time-driven; aging without code change is fine |
| **evergreen** | ❌ TTL forbidden | (this file, lessons.md, anti-pattern logs, design rationale that's been validated for ≥3 months) | age is a **feature** not a bug — older = more proven |

## Frontmatter pattern (perishable)

```yaml
---
decay_class: perishable
decay_after: 2026-08-10   # ISO date; ~90 days from write unless content-specific
decay_trigger: "#772 closed AND dnd-kit version pinned in package.json"   # non-time event that demotes earlier
---
```

- **`decay_after`** is the fallback. Set 60-90 days for fast-moving libs, 6 months for stable specs. Below 30 days = signal it should have stayed in `_scratch/`.
- **`decay_trigger`** is the realistic decay event. In practice, trigger fires before TTL — TTL just catches the case where the trigger never happens.
- Without `decay_trigger`, the TTL alone is fragile (forces re-research when nothing actually changed).

## Frontmatter pattern (review-on-touch)

```yaml
---
decay_class: review-on-touch
review_when: "api/src/routers/tasks.py is non-trivially edited"
---
```

- **`review_when`** is the human-readable description of the topic this doc tracks. When Lead (or specialist) is about to edit code matching that description, they re-read this doc; if mismatch, propose update.
- No date field — review is driven by what changes, not when.

## Frontmatter pattern (evergreen)

```yaml
---
decay_class: evergreen
---
```

That's it. No TTL, no trigger, no review condition. The class itself is a promise: this content does not expire on a calendar.

## When + who runs the check

| Trigger | What happens |
|---|---|
| Lead is about to **embed a perishable doc into a spawn brief** | Lead checks `decay_after` and `decay_trigger`. If past `decay_after` OR `decay_trigger` event has occurred: Lead surfaces to user — "this doc is N days past decay date; regen via dev-researcher, accept-as-is, or demote/prune?" |
| User invokes `/audit-decay` slash command (future — not built yet) | sweep all `shared/docs/` + `context/teams/dev/*.md`, list expired/triggered items, return as a punch list |
| The `decay_trigger` event happens (e.g., a referenced Kanban ticket closes) | the Lead closing that ticket scans docs whose triggers reference the event, demotes/prunes per the 4 demote conditions below |
| Session bootstrap | **NO automatic scan.** Adds latency + noise to every session for marginal value. Check is **on-demand at the read point**, not preventive. |
| review-on-touch doc, adjacent code edited | the role editing the code re-reads + proposes update if drift |
| evergreen doc | check only happens on explicit supersession (a new decision contradicts an old one) — see "demote evergreen" below |

## The 4 demote triggers (when to act)

These mirror the promote criteria — when the conditions that justified promotion fail, demote/prune.

1. **Generality decay** — content's effective scope narrowed (was cross-role / cross-project, now used by only one role / one feature). → demote one zone (team → project shared; shared → role state; role state → `_scratch/` or prune).

2. **Code-authoritative now** — answer derivable from current code, tests, or `git log`. Well-named identifiers + tests carry the meaning. → **prune** the doc. If the doc carried a WHY that isn't in code, distill it into a 1-2 line entry in the appropriate `decisions.md` before pruning.

3. **Stale snapshot** — perishable doc past `decay_after` or its `decay_trigger` fired, AND no one regen-ed in time. → **rename** to `<name>-superseded-<YYYY-MM-DD>.md` if the snapshot still has historical value (e.g., "here's what the API looked like at v6"); otherwise **prune**. Do NOT edit in place — that destroys the audit trail of what was claimed at promote time.

4. **Scope retired** — feature deprecated, ticket abandoned, project closed. → archive entire subtree to `context/_archive/<project-or-feature>-<YYYY-MM-DD>/` or prune.

### Demote evergreen — only via explicit supersession

evergreen docs (decisions, lessons, this policy) do NOT decay on schedule. They are demoted ONLY when:

- A newer decision **explicitly contradicts** the old one (newest entry at top of `decisions.md` is the live one; older entries stay for audit).
- The codebase has moved past the lesson — but the lesson stays as a "we used to do X, now we do Y, here's why" anti-pattern record. **Don't delete lessons.** They're cheap to keep and expensive to relearn.

## Who can demote

- **Lead** demotes within Lead-writable zones (project shared, team methodology) when a demote condition is **unambiguously** met — no user prompt needed. (Symmetric with promote: Lead can promote without prompting.)
- **User-only** demotes apply to `context/standards/*` — same rule as promote (humans-only).
- **DB-zone never demotes** — soft-delete via `status=0`, not file movement.
- **Always leave a breadcrumb.** Commit message states the trigger (e.g., "demote per decay-policy trigger #2: code-authoritative after #772 closed"). If the doc carried unique WHY, the breadcrumb says where the WHY landed.

## Anti-patterns

- **TTL on evergreen content.** A decision from 2 years ago is still valid until contradicted — not because the calendar says so. Slapping `decay_after` on decisions = noise → people start ignoring all decay notices → signal collapses.
- **TTL without `decay_trigger`.** Forces re-research on a calendar that doesn't match reality. Researcher runs again, finds nothing changed, output is identical, user wastes a turn.
- **TTL too short (< 30 days).** If the content was promote-worthy, it should outlive 30 days. < 30 days = it should have stayed in `_scratch/` and not been promoted at all.
- **Demote ก่อน promote rule ทำงาน.** Brand-new doc, used once, demoting "to clean up" → premature optimization. Wait 1-2 read cycles to see if it earns its place.
- **Scanning all docs at session bootstrap.** Latency tax for marginal value. The cheap moment to check is **the moment someone uses the doc** — that's when staleness actually matters.
- **Tracking "not read in Y days".** Filesystem mtime ≠ read time; instrumenting read access = new infra for marginal value. Unread docs are usually irrelevant docs — let neglect filter them naturally, then prune via Generality decay (trigger #1) when they next surface.
- **Editing perishable docs in place when superseded.** Destroys the claim audit (what did we believe at promote time?). Always rename to `*-superseded-<date>.md` or prune; write a fresh doc for current state.

## Out of scope (no frontmatter required)

- `context/standards/*` — humans-only zone; decay is governed by human review, not by Lead.
- Any `decisions.md` (project-shared or team) — append-only audit log; entries don't decay individually (the log AS A WHOLE is evergreen).
- `context/teams/<team>/lessons.md` or analogous lesson archives — evergreen by definition.
- `_scratch/*` — tempfiles; decay = the user runs `rm`.
- Source code, tests, migrations — owned by their stack standards, not by this policy.

## Worked example

`_scratch/research-dnd-kit-api.md` (Kanban #812, 2026-05-12) promoted to `context/projects/agent-teams/shared/docs/research-dnd-kit-api.md` with frontmatter:

```yaml
---
decay_class: perishable
decay_after: 2026-08-10
decay_trigger: "Kanban #772 closes AND dnd-kit version pinned in web/package.json"
---
```

Reasoning: research is read-value while #772 spec is pending; after #772 lands + version is locked, the code embodies the API choice → trigger #2 (code-authoritative) fires → Lead distills any unique WHY into `shared/decisions.md` + prunes the doc, OR renames `-superseded-<date>.md` if the snapshot value is still useful.
