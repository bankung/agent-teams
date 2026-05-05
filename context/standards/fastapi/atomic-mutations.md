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
