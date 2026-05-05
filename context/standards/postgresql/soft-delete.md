# PostgreSQL — Soft-delete policy

**Scope:** the project-wide rule for "deleting" rows in business tables. Authoritative source for the convention referenced from `sqlalchemy/orm.md` (column form) and `fastapi/routing.md` (list-endpoint default-filter). See `decisions.md` 2026-05-05 for the why-and-when.

## Policy

- **Every business table** carries `status SMALLINT NOT NULL DEFAULT 1 CHECK (status IN (0, 1))` where `1 = active` and `0 = deleted`.
- **Application code never issues SQL `DELETE`.** "Delete" endpoints flip the flag (`status = 0`).
- **Hard `DELETE` is reserved for manual operator cleanup via psql** — typically removing a typo'd row before any consumer saw it. App code MUST NOT issue hard DELETE; if you see one in source, that's a bug.

## Rationale

- **Never lose business data.** Single-tenant dogfood; recovery from a wrong delete must be trivial.
- **Audit trigger keeps soft deletes traceable.** A flag flip is an `UPDATE` — the trigger snapshots `to_jsonb(OLD)` with `status: 1` and `operation = 'U'`. Recovery = flip back. Cross-ref `postgresql/audit-trail.md`.
- **Uniform column name across tables.** Forces one rule (`WHERE status = 1`) instead of a per-table soft-delete column to remember.
- **Reverses the earlier "Soft delete: no" line** in `db-schema.md` Conventions (decision 2026-05-05).

## Column form — SQLAlchemy

Cross-ref `sqlalchemy/orm.md` for the model rules; this file owns the soft-delete-specific shape.

```python
status: Mapped[int] = mapped_column(
    SmallInteger,
    nullable=False,
    server_default="1",
    default=1,
)

__table_args__ = (
    CheckConstraint("status IN (0, 1)", name="ck_<table>_status_valid"),
    ...
)
```

## Column form — Alembic migration

Cross-ref `sqlalchemy/migrations.md` business-table checklist.

```python
sa.Column(
    "status",
    sa.SmallInteger(),
    nullable=False,
    server_default=sa.text("1"),
),
sa.CheckConstraint("status IN (0, 1)", name="ck_<table>_status_valid"),
```

## Index `status` on every soft-deletable table

Always add a plain (non-unique) B-tree index on `status`:

```sql
CREATE INDEX ix_<table>_status ON <table>(status);
```

List endpoints default-filter `WHERE status = 1`, so an index on `status` keeps that selective as the table grows. This is **separate from** the partial unique index pattern below — they coexist on the same table when both are needed: one for query speed (this), one for uniqueness-among-active rows (below).

## `tasks` naming exception

`tasks.status` historically meant the 1-5 lifecycle (`TaskStatus` codes — todo / in_progress / review / blocked / done; see `general.md`). To free the `status` name for the uniform 0/1 soft-delete semantic, the queued migration **renames `tasks.status → tasks.process_status`** while leaving the 1-5 codes themselves unchanged. Future code touching the lifecycle column must use `process_status`.

## Exempt tables

Audit append-only tables (`tasks_history`, any future `*_history`) do NOT carry a `status` column — they are append-only by design and have no concept of "active vs deleted". See `postgresql/audit-trail.md`.

## List endpoint behavior

- **Default-filter `WHERE status = 1`.** Soft-deleted rows are invisible by default.
- **Opt-in `?include_deleted=true`** returns active and soft-deleted rows together.
- **Detail endpoints (`GET /api/<resource>/{id}`) return the row regardless of `status`.** The caller already supplied the id; withholding by status would surprise consumers (404 on a row they just soft-deleted breaks recovery flows). Cross-ref `fastapi/routing.md`.

## Delete endpoint shape

- **Public verb is `DELETE /api/<resource>/{id}`.** Implementation flips `status = 0` and returns the updated row (or 204 No Content).
- **Don't expose `PATCH {"status": 0}` as the public delete path.** `DELETE` is the contract; the flag is implementation detail. (PATCH is acceptable as the underlying mechanism if there's a reason — document the choice in `api-contracts.md` when it lands.)

## Restore

- **PATCH `{"status": 1}` on the resource.** Admin-only path, deferred until UI demands it. Do NOT auto-expose on every resource; add per resource as the UI grows.

## Hard DELETE

- Reserved for manual psql cleanup (typo'd row before any consumer saw it).
- The audit trigger logs it as `'D'`. Seeing a `'D'` row attributable to app code = bug (app bypassing the soft-delete contract). Cross-ref `postgresql/audit-trail.md`.

## Operational consequences

- **Disk grows monotonically.** Soft-deleted rows accumulate forever.
- **Eventual sweep policy.** Not on the roadmap until growth becomes a real problem. Probable shape when needed:
  ```sql
  DELETE FROM <table> WHERE status = 0 AND updated_at < now() - interval '90 days';
  ```
  This DOES produce `'D'` audit rows — they're the legitimate exception to the "no `'D'` from app code" rule and should be traceable to a sweep job, not the request path.

## Uniqueness on soft-deletable tables

A plain `UNIQUE` constraint on a soft-deletable column blocks re-creating a row with the same key after a soft delete (the deleted row still occupies the unique slot). Use a **partial unique index gated on `status = 1`** instead:

```sql
CREATE UNIQUE INDEX ux_<table>_<col> ON <table>(<col>) WHERE status = 1;
```

Re-using `projects.name` after soft-deleting a project, or `tasks.title` per project, would both follow this pattern. Bare `UNIQUE` constraints stay only on tables exempt from soft delete (audit) or columns where collision-after-delete is genuinely unwanted.
