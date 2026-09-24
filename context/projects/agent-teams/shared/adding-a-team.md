# Adding a team to agent-teams

Platform work on the agent-teams repo itself — cut out of the universal `CLAUDE.md` in #3321, which must stay free of anything true only of this repo as a target project. Post-#1620 there is NO migration; the team CHECK was dropped. Precedent: #2811 social, #2871 mobile. **The full list — netops and social each shipped missing one of these and needed a follow-up (#2830, #2827a), so walk it:**

1. `api/src/constants.py` — `ProjectTeam.<NAME>` + add to `ALL` + a `TEAM_ROSTERS` entry. The roster entry is **mandatory**: a team in `ALL` with no roster raises at module import, so the API won't start.
2. `.claude/agents/<role>.md` for every role named in that roster — the scaffold manifest requires a matching file per role.
3. `api/src/services/agent_validation.py::_DOMAIN_RULES` — a `("<prefix>-", "<team>", "prefix")` entry, if the team introduces its own agent-name prefix. **This is the one CLAUDE.md used to omit** (#2830 fixed netops after the fact).
4. `web/lib/constants.ts` — mirror `ProjectTeam`, plus new `TaskRole` codes and their `TEAM_ROLE_RANGE` entry if the team claims a range. **#2827a existed because this was skipped.** Update `web/__tests__/constants.test.ts` — it hard-codes the role count.
5. `api/src/constants.py::TaskRole` — new codes + `ALL` + bump `RANGE_MAX` if the range grows. On a `RANGE_MAX` bump, grep for sibling CHECK-constrained columns mirroring the same field (the #2819 class).
6. `.claude/teams/<name>.md` — the playbook.
7. **A new `config.standards` lane** (only if the team needs one) — `api/src/schemas/project.py::_Standards` **and** `web/components/EditProjectModal.tsx` **and** the role→lane table in `context/standards/README.md`. An undeclared lane is dropped SILENTLY by Pydantic `extra="ignore"` — no 422, no warning.

NOT needed: migration, ORM CheckConstraint, `bin/agent-teams-init.ps1`, scaffold templates — those all derive from `constants.py` + the API.

The universal `CLAUDE.md` keeps the "Available teams" table (which teams exist is universal) and points here for the mechanics.
