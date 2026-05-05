# FastAPI — routing conventions

**Scope:** how request handlers are organized, how they talk to the DB, and the contract surface they expose. SQLAlchemy mechanics live in `context/standards/sqlalchemy/orm.md`; integer codes live in `context/standards/general.md`. This file covers the routing perspective only — cross-reference, don't restate.

## Module layout

- **One router per resource** in `api/src/routers/<resource>.py`. Each module exports a single `router = APIRouter(prefix="/<resource>", tags=["<resource>"])`.
- **Mount in `api/src/main.py`** with the `/api` prefix: `app.include_router(<resource>.router, prefix="/api")`. The prefix lives at the mount, not on the router — keeps the router file portable.
- See `api/src/routers/projects.py:20` and `api/src/routers/tasks.py:20`.

## Handlers

- **All handlers `async def`.** Sync handlers on an async stack defeat the point. Decision 2026-05-04.
- **Session via `Depends(get_session)`** — type-annotated as `session: AsyncSession = Depends(get_session)`. Never instantiate `SessionLocal()` inside a handler. The dependency yields one session per request and closes it on return.
- **Single-row-or-404 goes through `db.get_or_404(...)`.** Never inline `select` + `execute` + `scalar_one_or_none` + `raise HTTPException(404, ...)` — that's exactly the duplication `get_or_404` exists to remove. Authoritative SQLAlchemy mechanics: `context/standards/sqlalchemy/orm.md`. From the routing perspective: pass `detail="..."` and equality kwargs (`is_active=True`, `name=name`, `id=task_id`).

## Error contract

- **Detail strings are part of the public contract.** Tests pin them verbatim (`api/tests/test_routes_smoke.py`) and `shared/api-contracts.md` documents them. Refactors that touch wording must update tests + contract in the same change. Locked examples:
  - `"No active project"` (GET `/api/projects/active`)
  - `f"Project {name!r} not found"` — note the `!r` repr produces single quotes
  - `f"Project id={project_id} not found"`
  - `f"Task id={task_id} not found"`
- **Status codes:** 404 for missing rows, 409 for unique-name collisions, 400 for FK / CHECK violations, 422 for Pydantic validation (FastAPI default — don't override).

## POST handlers (create)

- Wrap `await session.commit()` in `try/except IntegrityError`.
- On `IntegrityError`: `await session.rollback()` first, then `raise HTTPException(...) from exc` — no exceptions to that order. Examples: `routers/projects.py:90-97` (409 for unique-name), `routers/tasks.py:67-71` (400 for FK/CHECK).
- Use `status_code=status.HTTP_201_CREATED` on the decorator. `await session.refresh(obj)` after commit before returning, so server-defaults (timestamps, IDs) are populated.

## PATCH handlers (partial update)

- `payload.model_dump(exclude_unset=True)` is mandatory. It distinguishes "field omitted" from "field=null" — required for nullable columns under PATCH semantics.
- **Bump `updated_at = func.now()` manually.** `server_default` fires only on INSERT. Do this on every PATCH path, before commit. See `routers/tasks.py:100`.
- Status-transition side effects (e.g., stamp `started_at` when entering `in_progress`) use a small lookup dict at module scope: `_STATUS_TIMESTAMP_FIELDS: dict[int, str] = {...}`. Stamp via `updates.setdefault(field, func.now())` so a client-supplied explicit value wins. Reference: `routers/tasks.py:24-27, 88-94`.

## Response models

- **`response_model=...` on every endpoint.** Pydantic Read schemas (`TaskRead`, `ProjectRead`) declare `model_config = ConfigDict(from_attributes=True)` so SQLAlchemy ORM rows serialize directly without a `.from_orm()` call. See `pydantic/v2-conventions.md`.

## List endpoints

- **Pagination params:** `limit: int = Query(default=50, ge=1, le=500)` and `offset: int = Query(default=0, ge=0)`. Defaults documented in `api-contracts.md` Conventions.
- **Stable order.** Always `.order_by(<id>.asc())` (or another deterministic key) so `offset` is meaningful across requests.
- **Filters as additional query params** (e.g., tasks accept `project_id` required, `status` optional, `assigned_role` optional). Required scope keys (`project_id` for tasks) use `Query(..., description=...)` — no default.
- **Soft-delete default-filter (target convention — pending the soft-delete migration).** Once the uniform 0/1 `status` column lands, list endpoints filter `WHERE status = 1` by default and accept `?include_deleted=true` to opt in. Until the migration ships, list handlers do not filter — see `decisions.md` 2026-05-05 and `postgresql/soft-delete.md`.

## Out of scope

- App factory pattern, `/health`, settings, port — see `fastapi/runtime.md`.
- Partial-unique-index atomicity (e.g., `is_active` flip) — see `fastapi/atomic-mutations.md`.
- Validator wiring and integer-code schemas — see `pydantic/v2-conventions.md`.
