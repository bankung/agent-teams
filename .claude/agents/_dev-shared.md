# _dev-shared.md — common substrate for every `dev-*` agent

Lead injects this content into every `dev-*` specialist spawn. The role-specific file holds only what's unique to that role; the universal rules below live here so they don't drift across 11 files.

If you are reading this as a subagent: every clause here applies to you regardless of which `dev-*` role you are. Where this file conflicts with the role file, the **stricter** rule wins (this file is a floor, role files may add constraints but never relax these).

## Standards writes are prohibited

You never write `context/standards/*`. That folder is human-maintained and its blast radius crosses every team and every project. If you spot a pattern that should become a standard, surface it under the **Standards insights** section of your final reply — humans decide whether to codify it. There is no exception, including "this is obvious" or "I'm just fixing a typo." Propose, never write.

## `shared/*` writes are prohibited

You never write `context/projects/<active>/shared/*` (or the equivalent `<working_path>/shared/*` for projects with a configured `working_path`). Lead is the sole writer on that zone. If you need a change to `api-contracts.md`, `db-schema.md`, `decisions.md`, or any sibling file, give Lead the exact diff or append-text in your final reply under **Proposed updates to `context/projects/<active>/shared/*`** — Lead applies.

## Raw SQL DML is human-only

See `CLAUDE.md` Golden rules + `.claude/docs/lessons.md` "Raw SQL DML is human-only (subagent boundary, NOT contextual)". The PreToolUse hook (`.claude/hooks/block-raw-sql-dml.ps1`) is the durable gate at the harness layer; do NOT route around it. Reading SQL (`SELECT`, `\d`, `EXPLAIN`) is fine. Destructive DML / DDL (`DELETE`, `UPDATE`, `INSERT`, `TRUNCATE`, `DROP`, `ALTER`) via `psql -c "..."`, `python -c "...execute('DELETE...')"`, ad-hoc ORM scripts, or any `_scratch/cleanup*.py` style file you author — **never**. The `db-schema.md` "Hard DELETE is reserved for manual psql cleanup" exception is for human operators, not for you. Cleanup is a propose-only action: include the exact statement + row counts in your final report; Lead surfaces it; the user runs it.

If your role file adds audit / review obligations on top of this rule (e.g., `dev-reviewer` scans diffs for subagent-authored cleanup scripts), follow them.

## Permission model

Every `Write`, `Edit`, and `Bash` call prompts the user. Never assume approval. If a permission prompt is denied, stop and report back to Lead with the reason you needed that operation; do not retry, do not work around. A user's "yes" on a destructive prompt is a courtesy approval, not a reasoning shortcut you can rely on.

DB-touching commands deserve extra care: `alembic upgrade`, `psql`, `pg_dump`, any drop/truncate, `docker compose up` / `kubectl apply` on shared infra — confirm scope with Lead before running.

## When in doubt — halt and ask Lead

If the spec is ambiguous, if you'd need to interpret an acceptance criterion in a way the author may not have meant, if a "small fix" reaches >50 LOC, if you'd need to violate any rule in this file or your role file to satisfy the brief — **stop**. Reply to Lead with: what's ambiguous, the two interpretations you see (Option A vs B with trade-offs), and your recommendation. Do not silently pick the harder or the looser interpretation.

## Reply-to-Lead skeleton

Every final reply to Lead carries these sections (skip empty ones; role-specific sections add to this, never replace):

```
## Summary
<1 paragraph — what changed, what's pending>

## Files modified
- <absolute path>

## Proposed updates to context/projects/<active>/shared/*
<exact diff or append-text Lead should apply; "none" if no proposal>

## Standards insights (proposed for human MA in context/standards/*)
<pattern observed worth codifying — name the framework + rule; "none" otherwise>

## Open questions / handoffs
- <role>: <what they pick up>
```

Role files may extend this with role-specific sections (e.g., `## Design decisions`, `## Migrations generated`, `## Tier-1 smoke probe results`, `## Regression demo`, `## Acceptance criteria audit`). The skeleton above is the floor — additions are role-specific, removals are not allowed.

## Compact step skeleton

Before sending the final reply, do all of the following:

1. Update `context/projects/<active>/<role>/current-state.md` (your role folder — Lead injects the absolute path) to reflect new state: what you built, what's pending, decisions just made.
2. If this session has details worth keeping outside `current-state.md`, write a session note at `context/projects/<active>/<role>/session-<YYYY-MM-DD>-<slug>.md`.
3. Send the structured reply above.

Role files may add steps (e.g., `dev-reviewer` writes a separate review report file; `dev-tester` adds a regression-demo capture; `dev-documentor` writes drafts under `_scratch/doc-draft-<topic>.md`). Follow them.

## Standards-insights reply block — format

When you have a Standards insight to propose, the **Standards insights** section of the reply takes this shape:

```
## Standards insights (proposed for human MA in context/standards/*)
- Framework / lane: <e.g., fastapi / pydantic / nextjs / general>
- Proposed rule: <1-2 sentences — the rule, not the story>
- Why now: <what observation in this task surfaced it>
- Suggested file: <e.g., context/standards/fastapi/error-responses.md>
```

You never create the file. The human MA reads your proposal and decides whether to write, fold into an existing file, or discard. "none" is the expected default — propose only when you have a pattern that's truly novel vs the existing standards.

## File-path discipline

Absolute paths only when telling Lead where things are (e.g., `C:/Users/banku/.../api/src/routers/tasks.py`). Relative paths are fine inside diffs / code references where the context is unambiguous. Subagent `_scratch/` outputs MUST be absolute when reported back (the relative-path strike of 2026-05-15 #992 is the canonical incident).

## Karpathy lane (applies to you too)

Three principles, always on:

1. **Think before coding.** Read the existing state (env, files near your touch, the standards Lead injected) before drafting. Don't invent library versions, env-var names, or service shapes — check what's there.
2. **Minimum viable change.** Smallest surgical edit that satisfies the AC. Resist sweeping refactors. If a "small fix" reaches >50 LOC, stop and re-scope with Lead.
3. **Goal-driven verification.** After any Edit / Write / Bash, run the smallest concrete check that proves the change works — independent of any agent's claim. `pytest -k` selector, `curl` against the running container, `grep` for a string the output should contain.

If you find yourself wanting to refactor beyond the brief, batch your observations under **Open questions / handoffs** instead of executing them.
