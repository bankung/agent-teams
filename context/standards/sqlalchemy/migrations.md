# Alembic migrations — project conventions

**Scope:** how we write Alembic migrations against the async stack defined in `sqlalchemy/orm.md`. See `context/standards/general.md` for general file naming.

## File naming and layout

- **Filename format:** `YYYY_MM_DD_HHMM_<slug>.py`. Set in `api/alembic.ini` via `file_template = %%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d_%%(slug)s` and `timezone = UTC`. Do not bypass the template.
- **One logical change per migration.** Bundle co-dependent objects (a table + its trigger + its CHECK + its indexes) in the same file; do NOT split across files.
- **`sqlalchemy.url` stays blank** in `alembic.ini`. The URL is injected at runtime in `api/alembic/env.py` from `src.settings.get_settings().database_url` — never hardcode a connection string.

## Async engine pattern

`api/alembic/env.py` uses `async_engine_from_config(...)` + `connection.run_sync(do_run_migrations)`. This is the only supported pattern — do not add a sync `create_engine` fallback.

`context.configure(...)` runs with `compare_type=True` and `compare_server_default=True` so autogenerate notices type changes and default drift.

## Migrations don't import app code

- **No `from src...` imports inside migration files.** Migrations are immutable history; importing application code creates a circular dep (the running app must be able to apply them at any commit). If a helper is needed, **duplicate it locally** with a comment pointing at the canonical source.
- **Canonical example: `_in_clause`.** The initial migration carries a private `_in_clause(column, values)` copy of `src.constants.in_clause` plus its own `_TASK_STATUS_ALL` / `_TASK_PRIORITY_ALL` / `_TASK_ROLE_ALL` tuples. Comment them as "kept in sync with src/constants.py". See `api/alembic/versions/2026_05_04_2130_initial_schema.py:31-39`.
- **Once a migration is committed, its constants are frozen.** Future migrations that change a CHECK list must `op.drop_constraint(...)` + `op.create_check_constraint(...)` with the new tuple — they do not edit history.

## Co-locate everything a table needs

In one migration's `upgrade()`:

1. `op.create_table(...)` with all columns, `ForeignKey("...", ondelete="CASCADE")`, and inline `sa.CheckConstraint(...)`.
2. `op.create_index(...)` for every index on that table (including `postgresql_where=...` partial indexes — see `ux_projects_active_one`).
3. PG functions and triggers via `op.execute("""...""")` heredoc strings.

`downgrade()` drops in reverse dependency order: triggers → functions → indexes → tables. Use `DROP ... IF EXISTS` for triggers and functions (idempotent rollback).

## Business-table checklist (target convention — pending migration)

When adding a business table, the migration must include:

```python
sa.Column(
    "status",
    sa.SmallInteger(),
    nullable=False,
    server_default=sa.text("1"),
),
sa.CheckConstraint("status IN (0, 1)", name="ck_<table>_status_valid"),
```

This is the uniform soft-delete flag (1=active, 0=deleted). The audit trigger captures flips as `'U'`. Audit append-only tables (`tasks_history` and similar) are exempt.

The queued soft-delete migration also renames `tasks.status → tasks.process_status` so the column name stays uniform across the schema. Future migrations that touch `tasks` must use `process_status` for the 1-5 lifecycle.
