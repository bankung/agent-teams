# Naming & the Bestiary — death/underworld codename theme + story-slug drift detector

> **Status:** LOCKED 2026-06-23 (decision owner: operator). Captured from a consultation session (no implementation performed). Build is DEFERRED — tracked in Kanban. This file is the source of truth for the naming universe; the immediately-actionable part is §4 (the Bestiary Naming mechanism).
> **File naming:** named after the concept, not a codename, so a rename stays a trivial find/replace.

## 1. Theme lock — death / underworld

The codename theme is **death / underworld**, locked precisely so that BOTH a human and a machine can decide whether a name is in-theme:

- **In-theme (accepted):** entities, places, and figures from death/underworld myth & folklore, any pantheon — underworld realms & their gates (Hel, Hades, Diyu, Irkalla, Yomi), their guardians (Cerberus, Garmr), death-spirits / psychopomps (Dullahan, reapers), the undead (zombies/walkers, wraiths, liches, revenants), tombs / crypts / graves, necromancy.
- **Out-of-theme (rejected -> anomaly):** anything not tied to death/underworld — generic tech words (Swarm, Service, Engine), nature/animals, sci-fi, etc.
- **Selection rule within theme:** prefer **recognizable** names (Cerberus over Neti).
- **Mixed pantheon is fine** — recognizability beats pantheon purity.

The theme governs **entities**. Plain **structural / relational nouns** (e.g. team -> "swarm") stay neutral and pair with a themed entity: `agent (zombie) + team (swarm) = zombie swarm`.

## 2. Two-layer principle

| Layer | Rule | Example |
|---|---|---|
| **Product / brand-facing** (the surface a buyer/reader sees) | themed death/underworld codenames | ZommmBeeean Swarm, Necromancer, The Crypt |
| **Operational** (agent types, code identifiers, daily work) | **keep real functional names** — theming here only confuses | `dev-backend`, `task_gates`, `agent-teams` (repo/docker) |

`agent = zombie` is a **metaphor** (the origin of the brand), NOT a rename of the specialist agent types.

## 3. Core codename set (headline only — locked)

| Real component | Codename |
|---|---|
| brand · platform | **ZommmBeeean** · **ZommmBeeean Swarm** (= zombie swarm = agent team) |
| Mode A (attended, premium) | **Necromancer** |
| Mode B (headless, autonomous) | **Dullahan** |
| continuous runner (#2531) | **Walker** |
| kanban board · task · milestone | **The Crypt** · **Tombstone** · **Mausoleum** |
| HITL system · gate · gatekeeper/poller | **Seven Gates** · **Helgrind** · **Cerberus** |

The lifecycle reads as one story: dig & raise a **Tombstone** -> a zombie works it -> its Last Rites (AC) pass -> seal the Epitaph (activity rail) -> R.I.P. (DONE); if stuck it falls to Limbo awaiting Judgment at **Helgrind**. (Italicised terms are deferred — see §5.)

## 4. The Bestiary Naming mechanism (the functional part)

A themed naming convention for the platform's own (dogfood) **story-memory** that doubles as a **drift/anomaly detector readable by both humans and the system**.

- **Slug convention:** `<monster>_<descriptor>` — exactly one `_` (the zone delimiter); `-` is the kebab separator inside each zone. e.g. `dullahan_headless-engine`, `seven-gates_hitl-async`.
- **Bestiary registry:** one file mapping `monster -> meaning` = the single source of truth (humans read it for recall; the system uses it as the validation set).
- **Detection (dual-channel):**
  - *human* — an off-theme slug visually stands out in `shared/stories/`.
  - *machine* — a scan flags any agent-teams story slug where `slug.split('_')[0]` is not in the registry OR the underscore count != 1. Rides an existing seam (pre-push scan, like the keyword scan, or a project-auditor metric).
- **Scope:** agent-teams (dogfood) stories only. Every other project keeps plain descriptive slugs.
- **Boundary (honest):** catches **structural drift** only — NOT deliberate mis-mapping (junk named `dullahan` still passes). A housekeeping signal, not an integrity gate.

✅ **Locked:** theme · `<monster>_<descriptor>` convention · single registry · dual-channel detection · agent-teams-only scope · drift-detector-not-gate boundary · two-layer principle.
⬜ **Open:** scan seam (hook vs project-auditor) · registry file placement (likely this `shared/` tree) · full bestiary contents.

Builds on the story-context system (LOCKED 2026-06-12) — see `.claude/docs/context-lifecycle.md`.

## 5. Deferred / out of scope (parked — add later if wanted)

- **Agent-type roster** (e.g. tester->Coroner, researcher->Oracle, reviewer->Inquisitor) — **dropped: agent types keep their real names** (clarity).
- **Views:** list->Necrology, calendar->The Reaping, gantt->The Procession.
- **Infra tier:** frontend->The Veil, API->Charon, DB->The Ossuary, notify->The Knell, cost->The Toll.
- **Task internals:** AC->Last Rites, activity rail->Epitaph, decisions/story knowledge->Grimoire, blocked/HITL-wait->Limbo, approve->Judgment.
- **Misc:** all-boards->Necropolis, harness->Brainnn (infra, optional), governance->The Rites, hooks->Wards, operator->Hades, seed->Patient Zero.

---
*Source: consultation session 2026-06-23 (no implementation). Complements `async-hitl-gates.md`.*
