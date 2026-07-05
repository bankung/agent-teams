---
name: zb-skill-new
description: >-
  Scaffold a new standard-compliant zb-* skill from a validated template so future skills
  start compliant-by-construction instead of hand-rolled. Use when the operator says "new
  skill", "scaffold a skill", "create a zb-* skill", "add a skill", "I want to author a
  skill", "make a new slash command", or "add a zb-<verb>".
argument-hint: "<skill-name> [category]"
allowed-tools:
  - Read
  - Write
  - Bash(node:*)
metadata:
  version: 1.0.0
  category: platform
  tags: [platform, skill, scaffold, authoring, mutate]
---

# /zb-skill-new — scaffold a compliant zb-* skill

`$ARGUMENTS` = `<skill-name> [category]` (e.g. `/zb-skill-new zb-deploy kanban`).

Scaffolds `_scratch/<skill-name>/SKILL.md` from a validated template, runs the #2463 validator
on it, and prints the move-into-place command. The operator applies; Claude Code restart activates it.

## Step 0 — gather inputs

Collect the following (from `$ARGUMENTS` or by asking the operator):

| Input | Validation |
|---|---|
| `name` | Must be `zb-<verb>` (kebab-case, no spaces). Reject anything else. |
| `purpose` | One-line imperative (≤120 chars). Used as the description opening line. |
| `category` | MUST be exactly one of: `kanban`, `platform`, `review`, `secretary`. Reject all others — the taxonomy is closed (skill-authoring standard §1.4). |
| `tags` | 3–6 lowercase hyphen-separated strings. Must include at least one noun (the domain) and the action-style tag (`read-only` or `mutate`). |
| `allowed-tools` | List the tools the new skill will actually call. Remind the operator of least-privilege: if the skill is read-only, `[Read]` alone is the expected range. |

Do NOT proceed with defaults for `name` or `category` — both must be explicit.

## Step 1 — emit `_scratch/<name>/SKILL.md`

Read `context/standards/skills/skill-authoring.md` to confirm the current taxonomy before writing.

Write the file at `_scratch/<name>/SKILL.md` using this exact template (fill placeholders from Step 0):

```
---
name: <name>
description: >-
  <purpose>. Use when the operator mentions <key phrase 1>, <key phrase 2>, <key phrase 3>,
  or says "<natural phrase 1>", "<natural phrase 2>".
argument-hint: "<argument syntax>"
allowed-tools:
  - <tool-1>
  - <tool-2>
metadata:
  version: 1.0.0
  category: <category>
  tags: [<tag-1>, <tag-2>, <tag-3>]
---

# /<name> — <purpose>

`$ARGUMENTS` = `<describe what the argument represents>`.

## Procedure

1. **<Step 1 title>** — TODO: describe the first action.
2. **<Step 2 title>** — TODO: describe the second action.
3. **<Step 3 title>** — TODO: describe any mutation or output.

## Footgun guards

| Step | Incident class / why it exists |
|---|---|
| TODO | Add a guard entry for every mutation or irreversible action in the Procedure. |

## Usage

```
/<name> <example-arg-1>
/<name> <example-arg-2>
/<name> <example-arg-3>
```

## Related skills

- **zb-spec** — spec the work before creating a task; run before this skill if the new skill's purpose is fuzzy.
- **skill-authoring standard** — `context/standards/skills/skill-authoring.md` — the eval rubric (E1–E10) this template satisfies.
- The emitted file is validated by `scripts/validate-skills.mjs` (#2463).
```

Rules for filling the template:
- The `description` MUST include a `Use when …` clause with ≥ 3 operator-natural phrases (eval criterion E2).
- `allowed-tools` list must match what the Procedure actually calls — no extras (E3).
- Footgun table MUST have at least one entry if the skill mutates state; "TODO" placeholder is acceptable at scaffold time and must be filled before the skill is used in production (E4).
- The `## Usage` code block must eventually contain ≥ 3 examples (E10) — the template stubs three.

## Step 2 — optional stubs (only if needed)

Apply the split rule from skill-authoring §2 ONLY when the new skill is expected to exceed BOTH thresholds:
- >~150 body lines, AND
- ≥ 4 distinct verb procedures.

If so, create a `_scratch/<name>/references/` directory and add one stub `.md` per verb. Default: do NOT create `references/` — the scaffolded flat file is already correct for the vast majority of skills.

If the new skill requires a helper script (e.g. a PowerShell gate or Python transform), scaffold a `_scratch/<name>/scripts/<helper>.ps1` stub with a single-line comment. Default: do NOT create `scripts/` (Karpathy YAGNI).

## Step 3 — validate (valid-by-construction)

Run the #2463 validator on the emitted skill ONLY — not the full `.claude/skills/` corpus:

```
node scripts/validate-skills.mjs --skills-dir _scratch/<smoke-parent>
```

where `<smoke-parent>` is a temporary directory containing ONLY `<name>/SKILL.md`
(e.g. create `_scratch/_validate_<name>/` → copy the SKILL.md there → validate → delete the temp dir).

Concrete sequence:
1. Write the SKILL.md to `_scratch/<name>/SKILL.md` (Step 1).
2. Create `_scratch/_validate_<name>/<name>/SKILL.md` as the validation staging dir (mirror, same content).
3. Run: `node scripts/validate-skills.mjs --skills-dir _scratch/_validate_<name>`
4. On exit 0 (OK): delete `_scratch/_validate_<name>/` and proceed to Step 4.
5. On non-zero exit: read the error lines, fix the template in `_scratch/<name>/SKILL.md`, update the staging copy, and re-run. Do NOT report success until exit 0.

## Step 4 — print operator instructions

Report:

```
Scaffold complete: _scratch/<name>/SKILL.md
Validator: OK (exit 0)

To activate:
  Copy-Item -Recurse "_scratch\<name>" ".claude\skills\<name>"

Then restart Claude Code — skills load at session start; /<name> is NOT invokable until restart.
```

---

## Footgun guards

| Step | Incident class / why it exists |
|---|---|
| 0 | Category must be one of the 4 taxonomy values — the validator hard-fails on any other value (skill-authoring §1.4). Reject "general" / "tooling" / etc. at input time, not after writing. |
| 1 | Write ONLY to `_scratch/` — `.claude/` is operator-applied; subagents writing there directly bypasses the review gate (CLAUDE.md, humans-commit rule; `ii` self-mod gate). |
| 1 | `allowed-tools` in the new skill must match what its Procedure actually calls (least-privilege; E3). Over-declaration defeats the audit trail. |
| 3 | Validate ONLY the just-scaffolded skill, not the full corpus — use `--skills-dir <staging-dir>` pointing at a directory containing only the new skill. Validating the corpus picks up pre-existing WARNs and can mask the new skill's result. |
| 4 | New skill is NOT invokable until Claude Code is restarted — skills load at session start. Do not tell the operator to try `/<name>` immediately. |

## Usage

```
/zb-skill-new zb-deploy platform
/zb-skill-new zb-notify kanban
/zb-skill-new zb-archive secretary
```

## Related skills

- **zb-spec** — harden a fuzzy task idea before creating a task; run before this skill if the new skill's scope is unclear.
- **zb-task-create** — open the Kanban task for the new skill's implementation after scaffolding.
- **skill-authoring standard** — `context/standards/skills/skill-authoring.md` — the full contract (frontmatter schema, split rule, eval rubric E1–E10) this scaffolder implements.
- The generated skill is validated by `scripts/validate-skills.mjs` (#2463); the same script validates the full corpus on every CI-like sweep.
