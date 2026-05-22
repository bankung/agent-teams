"""HTTP-level contract tests for /api/transactions (Kanban #953).

Coverage:
- POST / GET / PATCH happy paths.
- X-Project-Id header gate (missing, body mismatch, cross-project).
- Filters: ?kind / ?category / ?since / ?until / ?task_id.
- Pagination: limit / offset.
- Cross-project leakage guard (AC6): A's txn invisible to B header.
- Currency validation + uppercasing.
- Kind validation (DB CHECK).
- task_id FK behavior.

Uses the seeded `agent-teams` project (id=1) + a throwaway scaffold for
cross-project tests (same convention as test_session_project_header.py).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, team: str = "dev") -> dict:
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


def _txn_payload(
    project_id: int,
    *,
    amount_minor: int = 10000,
    currency: str = "USD",
    kind: str = "revenue",
    category: str | None = "stripe_sale",
    occurred_at: datetime | None = None,
    task_id: int | None = None,
    notes: str | None = None,
    source: str | None = None,
    source_ref: str | None = None,
) -> dict:
    body: dict = {
        "project_id": project_id,
        "amount_minor": amount_minor,
        "currency": currency,
        "kind": kind,
        "occurred_at": (occurred_at or datetime.now(timezone.utc)).isoformat(),
    }
    if category is not None:
        body["category"] = category
    if task_id is not None:
        body["task_id"] = task_id
    if notes is not None:
        body["notes"] = notes
    if source is not None:
        body["source"] = source
    if source_ref is not None:
        body["source_ref"] = source_ref
    return body


async def _make_fresh_project(client, scaffold_cleanup, slug: str) -> int:
    name = scaffold_cleanup(_unique_name(slug))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# =============================================================================
# 1. Header gate
# =============================================================================


@pytest.mark.asyncio
async def test_list_transactions_missing_header_returns_400(client):
    resp = await client.get("/api/transactions")
    assert resp.status_code == 400, resp.text
    assert resp.json() == {
        "detail": "X-Project-Id header is required for task endpoints"
    }


@pytest.mark.asyncio
async def test_post_transaction_missing_header_returns_400(client):
    resp = await client.post("/api/transactions", json=_txn_payload(1))
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_patch_transaction_missing_header_returns_400(client):
    resp = await client.patch("/api/transactions/1", json={"notes": "x"})
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_post_transaction_body_project_id_mismatch_returns_400(client):
    resp = await client.post(
        "/api/transactions",
        json=_txn_payload(2),
        headers={"X-Project-Id": "1"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json() == {
        "detail": "X-Project-Id header 1 does not match request body project_id 2"
    }


# =============================================================================
# 2. POST happy path + validation
# =============================================================================


@pytest.mark.asyncio
async def test_post_transaction_201_returns_created_row(client):
    resp = await client.post(
        "/api/transactions",
        json=_txn_payload(1, amount_minor=12345, currency="USD", kind="revenue"),
        headers={"X-Project-Id": "1"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["project_id"] == 1
    assert body["amount_minor"] == 12345
    assert body["currency"] == "USD"
    assert body["kind"] == "revenue"
    assert body["id"] > 0


@pytest.mark.asyncio
async def test_post_transaction_lowercase_currency_normalized_to_uppercase(client):
    resp = await client.post(
        "/api/transactions",
        json=_txn_payload(1, currency="thb"),
        headers={"X-Project-Id": "1"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["currency"] == "THB"


@pytest.mark.asyncio
async def test_post_transaction_invalid_currency_shape_422(client):
    resp = await client.post(
        "/api/transactions",
        json=_txn_payload(1, currency="DOLLAR"),
        headers={"X-Project-Id": "1"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_post_transaction_invalid_kind_422_via_literal(client):
    body = _txn_payload(1, kind="bogus")
    resp = await client.post(
        "/api/transactions",
        json=body,
        headers={"X-Project-Id": "1"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_post_transaction_extra_field_rejected(client):
    body = _txn_payload(1)
    body["bogus_key"] = "lol"
    resp = await client.post(
        "/api/transactions",
        json=body,
        headers={"X-Project-Id": "1"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_post_transaction_fk_to_unknown_project_returns_404(
    client, scaffold_cleanup
):
    # Pick a project_id that doesn't exist. Use a very large number to be safe
    # against test ordering / seed counts. Header MUST match body to clear the
    # cross-check gate; the project-active guard (#1403 M2) then short-circuits.
    impossible_id = 9_999_999
    resp = await client.post(
        "/api/transactions",
        json=_txn_payload(impossible_id),
        headers={"X-Project-Id": str(impossible_id)},
    )
    # Project-active guard (#1403 M2) short-circuits with 404 BEFORE the FK check.
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "project not found or inactive"


# =============================================================================
# 3. GET list + filters + pagination
# =============================================================================


@pytest.mark.asyncio
async def test_list_transactions_scoped_to_session_header(client, scaffold_cleanup):
    project_b = await _make_fresh_project(client, scaffold_cleanup, "txn-scope-b")
    headers_b = {"X-Project-Id": str(project_b)}

    # Create a txn in B.
    resp = await client.post(
        "/api/transactions",
        json=_txn_payload(project_b, amount_minor=1, currency="USD", kind="revenue"),
        headers=headers_b,
    )
    assert resp.status_code == 201
    b_txn_id = resp.json()["id"]

    # GET with header=1 must NOT include B's txn.
    resp_a = await client.get("/api/transactions?limit=500", headers={"X-Project-Id": "1"})
    assert resp_a.status_code == 200
    a_ids = {t["id"] for t in resp_a.json()}
    assert b_txn_id not in a_ids

    # GET with header=B must contain ONLY B's txn (fresh project).
    resp_b = await client.get("/api/transactions?limit=500", headers=headers_b)
    assert resp_b.status_code == 200
    assert {t["id"] for t in resp_b.json()} == {b_txn_id}


@pytest.mark.asyncio
async def test_list_transactions_filter_by_kind(client, scaffold_cleanup):
    project = await _make_fresh_project(client, scaffold_cleanup, "txn-filter-kind")
    headers = {"X-Project-Id": str(project)}
    for kind in ("revenue", "cost", "expense"):
        await client.post(
            "/api/transactions",
            json=_txn_payload(project, kind=kind, amount_minor=100),
            headers=headers,
        )
    resp = await client.get("/api/transactions?kind=cost", headers=headers)
    assert resp.status_code == 200
    kinds = {t["kind"] for t in resp.json()}
    assert kinds == {"cost"}


@pytest.mark.asyncio
async def test_list_transactions_filter_by_category(client, scaffold_cleanup):
    project = await _make_fresh_project(client, scaffold_cleanup, "txn-filter-cat")
    headers = {"X-Project-Id": str(project)}
    await client.post(
        "/api/transactions",
        json=_txn_payload(project, kind="cost", category="llm_anthropic"),
        headers=headers,
    )
    await client.post(
        "/api/transactions",
        json=_txn_payload(project, kind="cost", category="hosting"),
        headers=headers,
    )
    resp = await client.get("/api/transactions?category=llm_anthropic", headers=headers)
    assert resp.status_code == 200
    cats = {t["category"] for t in resp.json()}
    assert cats == {"llm_anthropic"}


@pytest.mark.asyncio
async def test_list_transactions_filter_by_since_until(client, scaffold_cleanup):
    project = await _make_fresh_project(client, scaffold_cleanup, "txn-filter-window")
    headers = {"X-Project-Id": str(project)}
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    for n in range(5):
        await client.post(
            "/api/transactions",
            json=_txn_payload(project, occurred_at=base + timedelta(days=n)),
            headers=headers,
        )
    since = (base + timedelta(days=2)).isoformat()
    until = (base + timedelta(days=4)).isoformat()
    # Use params= dict so httpx URL-encodes `+00:00` correctly; literal `+`
    # in an f-string query gets decoded as space by the server (HTTP form rule).
    resp = await client.get(
        "/api/transactions",
        params={"since": since, "until": until},
        headers=headers,
    )
    assert resp.status_code == 200
    # since inclusive, until exclusive → days 2, 3.
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_list_transactions_filter_by_task_id(client, scaffold_cleanup):
    project = await _make_fresh_project(client, scaffold_cleanup, "txn-filter-task")
    headers = {"X-Project-Id": str(project)}
    # Create a task in the project for the FK.
    task_resp = await client.post(
        "/api/tasks",
        json={"project_id": project, "title": "k953 filter task"},
        headers=headers,
    )
    assert task_resp.status_code == 201
    task_id = task_resp.json()["id"]

    await client.post(
        "/api/transactions",
        json=_txn_payload(project, task_id=task_id, kind="cost"),
        headers=headers,
    )
    await client.post(
        "/api/transactions",
        json=_txn_payload(project, kind="revenue"),  # no task_id
        headers=headers,
    )
    resp = await client.get(
        f"/api/transactions?task_id={task_id}", headers=headers
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["task_id"] == task_id


@pytest.mark.asyncio
async def test_list_transactions_pagination(client, scaffold_cleanup):
    project = await _make_fresh_project(client, scaffold_cleanup, "txn-page")
    headers = {"X-Project-Id": str(project)}
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    for n in range(7):
        await client.post(
            "/api/transactions",
            json=_txn_payload(project, occurred_at=base + timedelta(days=n)),
            headers=headers,
        )
    page1 = await client.get("/api/transactions?limit=3&offset=0", headers=headers)
    page2 = await client.get("/api/transactions?limit=3&offset=3", headers=headers)
    assert page1.status_code == 200 and page2.status_code == 200
    assert len(page1.json()) == 3
    assert len(page2.json()) == 3
    # No overlap between pages.
    ids1 = {t["id"] for t in page1.json()}
    ids2 = {t["id"] for t in page2.json()}
    assert ids1.isdisjoint(ids2)


@pytest.mark.asyncio
async def test_list_transactions_default_sort_is_occurred_at_desc(
    client, scaffold_cleanup
):
    project = await _make_fresh_project(client, scaffold_cleanup, "txn-sort")
    headers = {"X-Project-Id": str(project)}
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    for n in range(3):
        await client.post(
            "/api/transactions",
            json=_txn_payload(project, occurred_at=base + timedelta(days=n)),
            headers=headers,
        )
    resp = await client.get("/api/transactions", headers=headers)
    assert resp.status_code == 200
    occurred = [t["occurred_at"] for t in resp.json()]
    assert occurred == sorted(occurred, reverse=True)


# =============================================================================
# 4. PATCH
# =============================================================================


@pytest.mark.asyncio
async def test_patch_transaction_partial_update(client, scaffold_cleanup):
    project = await _make_fresh_project(client, scaffold_cleanup, "txn-patch")
    headers = {"X-Project-Id": str(project)}
    create = await client.post(
        "/api/transactions",
        json=_txn_payload(project, kind="revenue", notes="initial"),
        headers=headers,
    )
    txn_id = create.json()["id"]
    resp = await client.patch(
        f"/api/transactions/{txn_id}",
        json={"notes": "updated", "category": "stripe_2026q2"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["notes"] == "updated"
    assert body["category"] == "stripe_2026q2"
    assert body["kind"] == "revenue"  # unchanged


@pytest.mark.asyncio
async def test_patch_transaction_project_id_field_rejected(
    client, scaffold_cleanup
):
    """project_id is NOT in TransactionUpdate — extra='forbid' → 422."""
    project = await _make_fresh_project(client, scaffold_cleanup, "txn-patch-pid")
    headers = {"X-Project-Id": str(project)}
    create = await client.post(
        "/api/transactions",
        json=_txn_payload(project),
        headers=headers,
    )
    txn_id = create.json()["id"]
    resp = await client.patch(
        f"/api/transactions/{txn_id}",
        json={"project_id": 1},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_patch_transaction_404_when_unknown_id(client):
    resp = await client.patch(
        "/api/transactions/9999999",
        json={"notes": "x"},
        headers={"X-Project-Id": "1"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Transaction id=9999999 not found"


@pytest.mark.asyncio
async def test_patch_transaction_cross_project_returns_404(client, scaffold_cleanup):
    """AC6: a txn belonging to project B is INVISIBLE to A — 404, not 400."""
    project_b = await _make_fresh_project(client, scaffold_cleanup, "txn-cross-b")
    headers_b = {"X-Project-Id": str(project_b)}
    create = await client.post(
        "/api/transactions", json=_txn_payload(project_b), headers=headers_b
    )
    txn_id = create.json()["id"]
    resp = await client.patch(
        f"/api/transactions/{txn_id}",
        json={"notes": "leak attempt"},
        headers={"X-Project-Id": "1"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == f"Transaction id={txn_id} not found"


# =============================================================================
# 5. Cross-project leakage on GET list (AC6)
# =============================================================================


@pytest.mark.asyncio
async def test_list_transactions_cross_project_returns_empty_for_unrelated_header(
    client, scaffold_cleanup
):
    project_b = await _make_fresh_project(client, scaffold_cleanup, "txn-leak-b")
    headers_b = {"X-Project-Id": str(project_b)}
    await client.post(
        "/api/transactions",
        json=_txn_payload(project_b, kind="cost", amount_minor=100),
        headers=headers_b,
    )
    # GET with header=B-but-task_id-on-A → B sees its own; A sees A's only.
    resp_b = await client.get("/api/transactions?limit=500", headers=headers_b)
    assert resp_b.status_code == 200
    # B is fresh — exactly one row.
    assert len(resp_b.json()) == 1


# =============================================================================
# 6. Source-text-lock — detail strings
# =============================================================================


def test_transaction_not_found_detail_template_pinned_in_router_source():
    from pathlib import Path
    from src.routers import transactions as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    pinned = '"Transaction id={txn_id} not found"'
    assert pinned in source, (
        f"Kanban #953 not-found detail template drifted in routers/transactions.py — "
        f"expected {pinned!r}"
    )


# =============================================================================
# 7. Project-active guard (Kanban #1403 M2)
# =============================================================================


@pytest.mark.asyncio
async def test_get_transactions_on_soft_deleted_project_returns_404(
    client, scaffold_cleanup
):
    """GET /api/transactions with X-Project-Id pointing at a soft-deleted
    project must return 404, not 200. Guard added in Kanban #1403 M2.
    """
    project_id = await _make_fresh_project(client, scaffold_cleanup, "txn-guard-get")
    headers = {"X-Project-Id": str(project_id)}

    # Soft-delete the project.
    del_resp = await client.delete(f"/api/projects/{project_id}")
    assert del_resp.status_code == 204, del_resp.text

    resp = await client.get("/api/transactions", headers=headers)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "project not found or inactive"


@pytest.mark.asyncio
async def test_post_transaction_on_soft_deleted_project_returns_404(
    client, scaffold_cleanup
):
    """POST /api/transactions against a soft-deleted project must return 404.
    Guard added in Kanban #1403 M2.
    """
    project_id = await _make_fresh_project(client, scaffold_cleanup, "txn-guard-post")
    headers = {"X-Project-Id": str(project_id)}

    # Soft-delete the project.
    del_resp = await client.delete(f"/api/projects/{project_id}")
    assert del_resp.status_code == 204, del_resp.text

    resp = await client.post(
        "/api/transactions",
        json=_txn_payload(project_id, kind="revenue"),
        headers=headers,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "project not found or inactive"


@pytest.mark.asyncio
async def test_get_transactions_on_active_project_still_returns_200(
    client, scaffold_cleanup
):
    """Regression: the project-active guard must not break the happy path.
    GET /api/transactions on an ACTIVE project returns 200 + correct rows.
    Kanban #1403 M2.
    """
    project_id = await _make_fresh_project(client, scaffold_cleanup, "txn-guard-ok")
    headers = {"X-Project-Id": str(project_id)}

    # Create one transaction.
    create_resp = await client.post(
        "/api/transactions",
        json=_txn_payload(project_id, amount_minor=555, kind="cost"),
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    txn_id = create_resp.json()["id"]

    # List should still work and return the row.
    resp = await client.get("/api/transactions", headers=headers)
    assert resp.status_code == 200, resp.text
    ids = {t["id"] for t in resp.json()}
    assert txn_id in ids
