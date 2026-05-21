"""Pydantic schemas for the credentials vault (Kanban #1326 M3).

Five shapes:

  - `CredentialCreate`     — POST body (plaintext value goes IN here)
  - `CredentialUpdate`     — PATCH body (plaintext re-encrypted if provided)
  - `CredentialRead`       — list / single-credential response — NEVER carries
                             plaintext or ciphertext. Bound at the response
                             boundary so accidental field additions cannot
                             leak.
  - `CredentialUseRequest` — POST /use body (task_id + reason annotations)
  - `CredentialUseResponse` — POST /use response — the ONLY shape that
                              returns plaintext.

`extra='forbid'` on every body-shaped class (parity with other routers). Typo'd
keys 422.

`kind` is a Literal mirror of the migration's CHECK constraint vocabulary —
kept in lockstep with models/credential.py::CREDENTIAL_KINDS.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# Mirror of the migration CHECK + models/credential.py::CREDENTIAL_KINDS.
CredentialKind = Literal["api_key", "oauth_token", "webhook_secret", "app_password"]


class CredentialCreate(BaseModel):
    """Body for POST /api/projects/{project_id}/credentials.

    The plaintext `value` goes IN here once — the router encrypts it via
    services/credentials_crypto.py and stores only the ciphertext.

    `meta` is exposed as `metadata` on the wire (matches Kanban spec wording)
    via `validation_alias`; the field's Python name is `meta` because the
    ORM column collides with SQLAlchemy's DeclarativeBase.metadata.
    `populate_by_name=True` lets either key in.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=200)
    kind: CredentialKind
    # Plaintext caps at 4 KB — comfortably covers API keys, OAuth tokens, app
    # passwords, webhook secrets. Larger payloads (cert files, etc.) are out
    # of scope for M3.
    value: str = Field(min_length=1, max_length=4096)
    meta: dict[str, Any] | None = Field(default=None, validation_alias="metadata")


class CredentialUpdate(BaseModel):
    """Body for PATCH /api/projects/{project_id}/credentials/{name}.

    Only the supplied fields are written; omitted fields are unchanged
    (exclude_unset PATCH semantics). When `value` is present, the router
    re-encrypts and replaces the ciphertext.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    value: str | None = Field(default=None, min_length=1, max_length=4096)
    meta: dict[str, Any] | None = Field(default=None, validation_alias="metadata")


class CredentialRead(BaseModel):
    """Response shape for list + single-credential reads.

    NEVER includes `ciphertext` or any decoded value. The router's
    `response_model=CredentialRead` strips any field that isn't listed here —
    this is the load-bearing wall for AC#3 ("List endpoint returns names +
    metadata only").

    DB column is `metadata` but SQLAlchemy's DeclarativeBase reserves
    `metadata` on every model class (Table registry collision), so the ORM
    attribute is `meta`. We expose `metadata` on the WIRE (matches Kanban
    spec wording + matches the body shape used in POST/PATCH) via the field
    `meta` + a `serialization_alias='metadata'`. Pydantic's from_attributes
    reads `obj.meta` (no collision); the serializer writes `"metadata":`
    on the JSON wire. `model_config.populate_by_name=True` lets clients
    feed either `metadata` or `meta` in body shapes (CredentialCreate /
    Update — those use `validation_alias='metadata'`).
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    project_id: int
    name: str
    kind: CredentialKind
    meta: dict[str, Any] | None = Field(default=None, serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime | None
    last_accessed_at: datetime | None
    access_count: int
    status: int


class CredentialUseRequest(BaseModel):
    """Body for POST /api/projects/{project_id}/credentials/{name}/use.

    Both fields optional — `task_id` lets the access log row reference the
    task the credential was used in; `reason` is free-form audit annotation.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=500)


class CredentialUseResponse(BaseModel):
    """Response for POST /use — the ONLY shape that returns plaintext.

    Routers MUST NOT use this as the response_model of any other endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    value: str
    credential_id: int
    access_log_id: int
