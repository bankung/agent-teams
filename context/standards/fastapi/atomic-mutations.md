# FastAPI — atomic mutations against partial unique indexes

**Scope:** the "clear-conflicts-then-set" pattern for switching a singleton flag (e.g., "the active project") backed by a partial unique index. Generalizes to any "current/featured/primary X" semantic.

## The constraint

Postgres lets you express "at most one row may have flag=true" via a **partial unique index**:

```sql
CREATE UNIQUE INDEX ux_projects_active_one
  ON projects (is_active) WHERE is_active IS TRUE;
```

The index is checked at **COMMIT**, not per-statement. That property is what makes the pattern below safe inside a single transaction.

## The naive bug

Setting a new row's flag to `true` while another row still has `true` causes the unique index to flag two `true` values at COMMIT — even if you "intended" to clear the old one in the next statement of the same handler. The order of writes within the transaction is what matters.

## The pattern

Do the clearing UPDATE first, then set the target, all on **one session**, then commit once:

```python
async def _clear_other_active(session: AsyncSession, keep_id: int | None) -> None:
    stmt = update(Project).values(is_active=False).where(Project.is_active.is_(True))
    if keep_id is not None:
        stmt = stmt.where(Project.id != keep_id)
    await session.execute(stmt)
```

Two callers in `api/src/routers/projects.py`:

- **`create_project`** (`keep_id=None`): clear all rows (the new row is not yet inserted), then `session.add(project)` + commit. The new row inherits `is_active=true` and is the only one when COMMIT runs the unique check.
- **`update_project`** (`keep_id=project.id`): clear all *except this row*, then set `is_active=true` on the target via the normal PATCH attribute assignment.

Both callers commit once at the end of the handler — same transaction as the clear, same transaction as the set.

## Why it works

- Both writes share the same `AsyncSession`, so they're in the same DB transaction.
- The partial unique index is enforced at **COMMIT**, after the transaction's net effect (all rows but one cleared, one row set). The intermediate state — where two rows briefly hold `true` between statements — never reaches the constraint check because we commit only after the clear has run.
- The pattern would NOT work in autocommit mode, with separate sessions, or if the clear were issued in a previous request. It's the single-transaction guarantee that makes it atomic.

## Generalizing

Any time the schema has "exactly one row with flag X" backed by a partial unique index, switching the flag uses the same recipe:

1. UPDATE `<table>` SET `<flag>` = false WHERE `<flag>` IS TRUE [AND id != <keep_id>]
2. Set `<flag>` = true on the target row (via `session.add(...)` for create, attribute assignment for update)
3. `await session.commit()` once

Examples this would apply to: "current sprint", "featured post", "default address", "primary email" — anything backed by a partial unique index on a boolean.

## Cross-reference

- The partial unique index itself (`ux_projects_active_one`) is declared in the model — see `context/standards/sqlalchemy/orm.md` for model conventions and `context/standards/sqlalchemy/migrations.md` for the migration form.
- Routing-level error wrapping (`IntegrityError → 409`) is documented in `fastapi/routing.md`. The `_clear_other_active` pattern aims to make the IntegrityError path *unreachable* on the legitimate switch-the-singleton flow.

## Post-PATCH cross-resource side effects

When a PATCH state transition triggers a side effect on a different resource (parent → child spawn, audit row insert, push delivery, etc.), the side effect MUST land in the SAME transaction as the trigger PATCH. The hook fires AFTER the `setattr` loop (so resolved-final values are visible) but BEFORE `session.commit()`.

```python
# api/src/routers/tasks.py (Kanban #1004 canonical example — 3rd hook of this shape)

@router.patch("/{task_id}")
async def update_task(task_id: int, payload: TaskUpdate, session: AsyncSession = Depends(get_session)):
    task = await get_or_404(session, Task, task_id, ...)
    updates = payload.model_dump(exclude_unset=True)

    # Pre-PATCH state captured BEFORE setattr loop so transition detection
    # can compare old-vs-new values.
    pre_process_status = task.process_status

    for field, value in updates.items():
        setattr(task, field, value)

    # Post-setattr hook — runs in the same transaction as the parent flip.
    if (
        "process_status" in updates
        and pre_process_status != 5
        and task.process_status == 5
        and task.handoff_template_id is not None
    ):
        try:
            await spawn_child_from_handoff(session, task)
        except HTTPException:
            raise   # Atomic rollback — operator sees the malformed-template 422.

    await session.commit()
    return task
```

## Transition detection — "field in updates" pattern

The "field IS in `updates`" check (NOT "field changed value") is the canonical transition detector:

- `payload.model_dump(exclude_unset=True)` returns ONLY fields the caller actually sent in the body. Fields omitted from the PATCH don't appear in `updates`.
- A PATCH that only sets `status_change_reason` (without touching `process_status`) does NOT have `"process_status"` in `updates` — the hook correctly skips.
- A PATCH that re-sets `process_status=5` on an already-DONE task DOES have `"process_status"` in `updates` (caller explicitly sent it), but the additional `pre_process_status != 5` guard makes the hook idempotent.

Combine both conditions: `"field" in updates AND old_value != new_value`. This gives:
- Idempotent re-PATCH semantics for free (re-sending the same value is a no-op).
- Correct skip when the caller omitted the field (PATCH'd something else).
- Correct fire when the caller actually transitioned.

## Errors raise, don't swallow

The hook MUST raise `HTTPException` (or let the exception propagate) rather than be caught and swallowed. The transaction's `await session.commit()` runs AFTER the hook; an exception aborts the commit and rolls back BOTH the parent setattr changes AND any partial side-effect writes. This gives atomicity for free.

```python
# WRONG — swallows the error; transaction commits with half-applied state
try:
    await spawn_child_from_handoff(session, task)
except Exception:
    logger.exception("spawn failed")
    # ← BAD: parent flip lands but the child never spawned. Audit trail looks
    # like the spawn succeeded.
```

Exception: idempotent / informational side effects (e.g., emit a push notification — the push pipeline returns ok=False on failure but doesn't raise). Those use the adapter contract pattern from `general/external-callback-cleanup.md` and DON'T need to roll back the parent transaction.

## Generalizes to

This is now the 3rd post-PATCH hook of this shape on `routers/tasks.py`:

1. **#832 — auto-unblock dependents.** When a task flips to DONE, find any `blocked_by=this.id` rows and clear their `blocked_by`.
2. **#1211 — audit-flag pipeline.** When an audit task flips to DONE, the #1211 governance flag fires (or doesn't, per audit_report).
3. **#1004 — handoff template spawn.** When a task with `handoff_template_id` flips to DONE, the child task is built in the same transaction.

The next author adding a 4th hook on this surface should pattern-match the shape exactly.

## Cross-reference

- Canonical implementations: `api/src/routers/tasks.py` PATCH handler around the `#1211` and `#1004` hook comments (Kanban #1004, commit 41971da).
- Adapter-contract sibling: `context/standards/general/external-callback-cleanup.md` for hooks whose external call CAN fail without rolling back the parent.
