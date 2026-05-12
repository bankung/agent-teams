# SQLAlchemy 2.0 ORM — project conventions

**Scope:** rules this project commits to when defining ORM models and querying via `AsyncSession`. Not a tutorial — only points where we diverge from defaults or have made a deliberate call. See `context/standards/general.md` for Kanban codes and naming.

## Stack

- **Async-first.** SQLAlchemy 2.0 + `asyncpg` + `AsyncSession`. FastAPI handlers are `async def`. No sync engine, no sync session anywhere in `api/src/` (decision 2026-05-04). Sync I/O on the request path blocks the event loop.
- **Session factory** is built once at import in `api/src/db.py` with `expire_on_commit=False` and `autoflush=False`. The first avoids `MissingGreenlet`/`InvalidRequestError` when serializers touch attributes after `commit()`; the second keeps writes explicit — call `await session.flush()` when you actually need it.
- **Dependency injection.** Routers receive a session via `Depends(get_session)`. Never instantiate `SessionLocal()` inside business logic.

## Model definitions

- **Inherit from `src.models.base.Base`** (the project's single `DeclarativeBase`). One base, one metadata, one Alembic target.
- **Typed `Mapped[T]` + `mapped_column(...)` only.** Bare `Column()` is forbidden — it loses the type and breaks `mypy --strict`. See `api/src/models/task.py:53-98` for the canonical shape.
- **BigInteger autoincrement PKs.** `id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)`. UUIDs were intentionally rejected (decision 2026-05-04) — single-tenant app, smaller indexes, readable IDs in logs and URLs.
- **CASCADE at the DB level.** Use `ForeignKey("...", ondelete="CASCADE")` on the column and `passive_deletes=True` on the relationship. Do NOT rely on relationship-level `cascade="all, delete-orphan"` to actually issue DELETEs — the DB constraint is the source of truth, and `passive_deletes=True` tells SQLAlchemy to trust it. Under the soft-delete policy hard DELETE is rare (manual psql cleanup only), but when it happens CASCADE is what cleans up children atomically. See `api/src/models/project.py:63-68` and `api/src/models/task.py:55-59`.
- **Timestamps.** `created_at` and `updated_at` are `DateTime(timezone=True)` with `server_default=func.now()`. Note that `server_default` fires only on INSERT — PATCH handlers must bump `updated_at` manually (or move it to a PG trigger if write paths multiply). The same rule applies to any timestamp meant to track row changes.
- **JSONB columns with Pydantic-typed nested data.** When a Pydantic model with a non-JSON-native scalar (`datetime`, `UUID`, `Decimal`) gets written to a `JSONB` column via `model_dump()`, use `mode='json'` to coerce nested values to JSON-native types first. SQLAlchemy's default `json_serializer` only handles `dict/list/str/int/float/bool/None` — `datetime` etc. raise `TypeError` and surface as 500 at write time. Scope the `mode='json'` re-dump to the JSONB field only — sibling top-level `DateTime` columns (`started_at`, `completed_at`, etc.) must stay as native `datetime` so SQLAlchemy lands them as `TIMESTAMPTZ`, not ISO strings. Worked example: `api/src/routers/tasks.py` `create_task` + `update_task` — re-dump `acceptance_criteria` only. Strike: Kanban #801 (2026-05-12) — `AcceptanceCriterion.verified_at: datetime | None` crashed PATCH `/api/tasks/<id>` 500 when written via plain `model_dump()`.
- **CHECK constraints reference `src.constants` ALL tuples via `in_clause(column, values)`** — never duplicate the integer list. Example: `CheckConstraint(in_clause("status", TaskStatus.ALL), name="ck_tasks_status_valid")`. Constraint names are `ck_<table>_<column>_valid` (or a more descriptive suffix). See `api/src/models/task.py:102-114`.
- **Pydantic `Literal` lockstep guard for enum columns.** When the same enum value set lives in `src.constants.<X>.ALL` (used by ORM CHECK) AND in a Pydantic `Literal[...]` (used by request schemas), they will drift unless held together at import time. Pattern: at the bottom of the schema module, write a one-line assertion that the `Literal`'s `__args__` equals `set(<X>.ALL)`; raise on mismatch. Example: `api/src/schemas/task.py` `TaskTypeLiteral` + the closing `assert set(get_args(TaskTypeLiteral)) == set(TaskType.ALL), "..."`. Import-time fail-fast — module-load crashes if someone adds a new value to one side and forgets the other. Apply to any new enum column going forward (Kanban #803 introduced this pattern; documenting as the standard 2026-05-12).
- **`TYPE_CHECKING` import block** for relationship type hints — keeps relationship targets typed without creating import cycles. See `api/src/models/project.py:6-15`.

## Soft-delete column (target convention — pending migration)

Every business table declares:

```python
status: Mapped[int] = mapped_column(
    SmallInteger,
    nullable=False,
    server_default="1",
    default=1,
)
__table_args__ = (CheckConstraint("status IN (0, 1)", name="ck_<table>_status_valid"), ...)
```

`1` = active, `0` = deleted. App code never issues SQL DELETE — "delete" endpoints flip the flag (the audit trigger captures it as `'U'`). List endpoints filter `WHERE status = 1` by default; opt-in `?include_deleted=true`. Exempt: audit append-only tables (`tasks_history`).

Note: current `tasks.status` is the 1-5 lifecycle code (`TaskStatus`); the queued migration renames it to `tasks.process_status` and frees `status` for the uniform 0/1 soft-delete flag.

## Querying

- **Single-row lookup or 404.** Use `db.get_or_404(session, Model, *, detail, **filters)` from `api/src/db.py:53-69`. Do NOT inline the `select(...).where(...)` + `await session.execute(...)` + `.scalar_one_or_none()` + manual `HTTPException(404)` pattern. The helper is the single canonical way; if it can't express your filter, extend the helper rather than duplicating the four lines. (Cross-ref: a future `fastapi/routing.md` may reiterate this from the routing perspective — keep this file authoritative for the SQLAlchemy mechanics.)
- **List queries** stay explicit (`select(Model).where(...).order_by(...)`) — the helper is only for "fetch one or 404".
- **Bool kwarg over `.is_(True)` for non-nullable booleans.** Pass `is_active=True` to `get_or_404`, not `Project.is_active.is_(True)` — both compile to the same SQL on a NOT NULL column, and the kwarg form is shorter and matches the helper's signature. (`kwarg` = Python keyword argument, the `name=value` calling syntax.)
