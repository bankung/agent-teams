---
decay_class: evergreen
scope: team-methodology (dev) — applies to every team='dev' project's user-facing docs
proposed_by: lead (Kanban #1642, follow-up to #1641 honest-docs sweep)
---

# Doc status-marker convention (production / in active development / roadmap)

User-facing docs (README, QUICKSTART, onboarding guides, feature/capability pages)
describe what the system **can do**. Without an explicit status marker on each
capability, a roadmap or half-built feature reads as if it ships today — and the
overclaim silently drifts across docs. This convention makes every capability's
maturity explicit and greppable.

> **Origin (Kanban #1641).** The headless `langgraph` auto-run engine was described
> as a live capability across **four** docs (README, QUICKSTART, CLAUDE-CODE-START,
> USAGE-POWER), each in different wording. There was no status marker, so the
> overclaim was easy to introduce and hard to find. This convention is the
> prevention layer.

## The three states

| State | Canonical phrase (case-insensitive) | Meaning |
|---|---|---|
| Production | `production` | Shipped and is the supported path today. Safe to rely on. |
| In active development | `in active development` | Partially implemented / works in a limited form. Treat output as draft — the full capability is **not live yet**. |
| Roadmap | `roadmap` | Planned, not yet started. No working implementation. |

**Use the canonical phrase verbatim** (case-insensitive). A maintainer must be able to
`grep -ri "in active development"` and find *every* place a given maturity is claimed.
Do **not** invent synonyms — "under development", "coming soon", "WIP", "beta",
"experimental", "not yet wired" all defeat the grep and re-introduce the #1641 drift.

## The marker form

A capability section carries a **Status line** as the first line under its heading:

```
**Status: <State>.** <optional one-clause qualifier — what works / what doesn't yet>
```

- Marker form capitalises the first letter (`**Status: In active development.**`);
  inline prose uses the lowercase phrase ("…the engine is in active development").
  Both forms contain the canonical phrase, so a case-insensitive grep catches both.
- Keep the qualifier to one clause. The full truth lives in the component registry
  (below), not duplicated into every doc.

**Examples**

```
## 2. Auto-mode (headless agent pickup)

**Status: In active development.** Posts a plan + status updates and checkpoints
state in Postgres; autonomous code edits / tests / commits are not live yet.
```

```
## 4. Mobile remote access

**Status: Production.**
```

Anti-pattern (the #1641 shape — same maturity, three wordings, no canonical string):

```
✗  "…the engine is under development"
✗  "…this feature is coming soon (WIP)"
✗  "…experimental — not yet wired"
```

## Where to apply (lightweight, not exhaustive)

1. **Any non-production capability MUST be marked wherever it appears.** This is the
   load-bearing rule — every `in active development` / `roadmap` claim is explicit.
2. **Capability-catalog docs** (e.g. `USAGE-POWER.md`, where each section is one
   feature) carry a Status line on **every** entry, so the reader gets a consistent
   scannable maturity column — including the `Production` ones.
3. **Prose/narrative docs** (e.g. `README.md` marketing-style bullets) do **not** get
   `Status:` lines bolted onto every paragraph — that harms the prose. Instead, ensure
   the inline canonical phrase is used for any non-production claim, and let production
   prose stand. The goal is honesty + greppability, not badge clutter.

## Single source of truth — the component registry

The authoritative current maturity of each component lives in one place per project:

- **agent-teams:** [`context/projects/agent-teams/shared/component-status.md`](../../projects/agent-teams/shared/component-status.md)
- Other dev projects: `context/projects/<project>/shared/component-status.md` (create on first need).

**Honest limitation:** static Markdown has no transclusion. The registry does **not**
auto-sync the prose in the docs — each doc still carries its own inline marker. The
registry's job is (a) the *canonical answer* to "what's the real status of X?", and
(b) the *reconciliation checklist* for the maintenance sweep below. It is primarily a
maintainer artifact; public readers rely on the inline markers in the docs.

## Maintenance sweep (when a component's status changes)

1. Update the component's row in `component-status.md` **first** (it is the truth).
2. `grep -ri "<canonical phrase>"` across the user-facing docs to find every claim
   about that component.
3. Update each occurrence to the new state (marker + inline prose) in the same change.
4. Verify with a final grep that no stale phrase for the old state remains.

This is the inverse of the #1641 failure: change the truth in one place, then fan out
to the docs deliberately, instead of editing one doc and letting the others drift.
