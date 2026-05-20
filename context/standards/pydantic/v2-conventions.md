# Pydantic v2 — project conventions

**Scope:** patterns this project commits to for request/response models. Schema files live in `api/src/schemas/`. Cross-reference: routing-level use of `from_attributes=True` and `model_dump(exclude_unset=True)` is in `fastapi/routing.md`.

## Read models serialize ORM rows directly

- **`model_config = ConfigDict(from_attributes=True)`** on every Read schema. Pydantic v2 replacement for v1's `orm_mode=True`. With this set, `response_model=TaskRead` on a handler that returns a SQLAlchemy `Task` instance just works — no `.from_orm(...)` call. See `api/src/schemas/task.py`.
- Read models list every column the API exposes — including `Optional` types for nullable columns and `datetime` for timestamp columns. Don't lean on Pydantic to "figure it out" from the ORM.

## Integer-code fields

- **Validate against the single-source `ALL` tuples in `src.constants`** — never duplicate the integer list inside the schema. Example: `_make_code_validator("status", TaskStatus.ALL, ...)`.
- **Document the field on the type alias, not in the model body.** `Annotated[int, Field(description="...")]` keeps swagger docs informative without polluting field declarations:

  ```python
  StatusCode = Annotated[int, Field(description="tasks.status — see TaskStatus.ALL")]
  PriorityCode = Annotated[int, Field(description="tasks.priority — see TaskPriority.ALL")]
  RoleCode = Annotated[int, Field(description="tasks.assigned_role — see TaskRole.ALL")]
  ```

  See `api/src/schemas/task.py:18-20`.

## Validator factory pattern

- **When the same validator logic applies to multiple fields/schemas, extract a closure factory.** Don't copy/paste validator bodies.
- The canonical example is `_make_code_validator(field_label, allowed, *, required, null_phrase="")` in `api/src/schemas/task.py:23-52`. It is registered six times — three in `TaskCreate`, three in `TaskUpdate` — with `required=True` (POST status/priority) or `required=False` (PATCH everywhere, plus nullable `assigned_role`), and with `null_phrase="NULL or "` for the nullable role field whose error message reads "must be NULL or one of (...)".
- Register via `field_validator("field")(factory(...))` — the factory returns the validator function; `field_validator` wraps it. See `schemas/task.py:65-75`.

## `field_validator` vs `model_validator`

- **`field_validator(...)`** for per-field rules (the common case).
- **`model_validator(mode="after")`** only when the rule needs cross-field state (e.g., "if `kind=foo` then `bar` is required"). No cross-field rules in the codebase today; flag if one is added.

## PATCH semantics

- **Optional fields are `T | None = None`.** Validators short-circuit on `None` (returning `None` means "no update"). The router uses `payload.model_dump(exclude_unset=True)` to distinguish "field omitted" from "field=null".
- See `TaskUpdate` in `api/src/schemas/task.py:78-107` for the canonical shape — every field is `<type> | None = None` (or `Field(default=None, ...)` when other constraints apply).

## Type coercion

- Avoid redundant `int(v)` casts in validators when Pydantic already coerces. The cast in `_make_code_validator` (`return int(v)`) is intentional defensiveness for the rare case `v` is a `bool` (Python bools are ints) or a numpy/decimal type — keep that pattern only inside the factory.
- Reach for explicit casts only when running a `mode="before"` validator that needs a specific type before normal coercion happens.

## Validator wording is part of the contract

- **Tests pin every error message verbatim.** See `api/tests/test_validators.py` and the locked phrases at the top of that file:
  - `"status must be one of (1, 2, 3, 4, 5), got <repr>"`
  - `"status is required"`
  - `"priority must be one of (1, 2, 3, 4), got <repr>"`
  - `"assigned_role must be NULL or one of (1, 2, 3, 4, 5), got <repr>"`
- A wording change is a contract change: update tests, update `shared/api-contracts.md`, update the consumer (eventually the FE that parses `errors[].msg` for inline form errors). Don't change wording during refactors.

## Discriminated JSONB payload shapes

When a JSONB column carries multiple payload shapes discriminated by another column (e.g., `tasks.question_payload` shape depends on `tasks.interaction_kind`), put the discriminator validation in a `model_validator(mode="after")` on the **input schema** (TaskCreate / TaskUpdate), NOT in the JSONB model itself. The JSONB model accepts the broadest union; the model_validator tightens it per-discriminator-value at the API boundary.

- **Why not in the JSONB model:** the JSONB model is also used for response serialization (`from_attributes=True`). If it rejects legacy shapes that exist in stored rows, a GET endpoint 500s before it can return the row. Read endpoints must stay tolerant.
- **Why on the input schema:** writes are the ONLY place new payload shapes can land. Tightening here keeps backward-compat on reads + invariant enforcement on writes.

Canonical example: `QuestionPayload.options` is `list[str | OptionItem] | None` (broad). `TaskCreate.model_validator(mode="after")` enforces — when `interaction_kind="decision"` — that the `options` list is non-empty AND every element is an `OptionItem` (not a bare string). Question tasks with the legacy `list[str]` shape still validate cleanly. See `api/src/schemas/task.py` (Kanban #1007, 2026-05-20).

```python
class QuestionPayload(BaseModel):
    # Tolerant on read; broad union on options.
    options: list[str | OptionItem] | None = None
    ...

class TaskCreate(BaseModel):
    interaction_kind: InteractionKindLiteral | None = None
    question_payload: QuestionPayload | None = None

    @model_validator(mode="after")
    def _validate_decision_payload(self):
        if self.interaction_kind == "decision":
            opts = self.question_payload and self.question_payload.options
            if not opts or any(isinstance(o, str) for o in opts):
                raise ValueError(
                    "decision task requires question_payload.options as list[OptionItem] "
                    "(typed dicts with id/label); got list[str] or empty"
                )
        return self
```

The same pattern applies to any "JSONB column carries shape A or B depending on discriminator column" surface — tasks.resume_context (discriminated by interaction_kind / spawn source / etc.), tasks.audit_report (discriminated by audit type), etc.

## Cross-reference

- Wire contract for the canonical example: `context/projects/agent-teams/shared/api-contracts.md` POST `/api/tasks` + POST `/api/tasks/{id}/decide` sections (Kanban #1007).
- Sibling FE concern: TypeScript consumers of the same JSONB column type their local model as `Array<string | OptionItem> | null` (heterogeneous) and narrow defensively per consumption site. See `web/lib/api.ts::QuestionPayload`.
