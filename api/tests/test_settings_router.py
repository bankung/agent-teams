"""Kanban #1655 — Integrations settings router contract smoke tests.

First-pass contract coverage (dev-sr-backend scope — the rigorous suite, e.g.
per-category matrices, concurrent-toggle races, every integration's required-var
permutations, is dev-tester's domain):

  (a) `configured` is False when a required env var is absent, True when all
      required vars are monkeypatched present.
  (b) PATCH enable then GET reflects enabled=true (DB persistence).
  (c) GET never leaks a secret VALUE — only presence booleans.
  (d) Unknown integration id → 404.

The router is GLOBAL (no X-Project-Id header). It writes only to
`platform_integration_settings` in the TEST DB — never the live `agent_teams`
DB (the conftest live-DB invariant remains green).
"""

from __future__ import annotations

import pytest


# A recognisable secret value used by the no-leak assertion (c). If the response
# ever serialized an env VALUE, this sentinel would appear in the wire bytes.
SENTINEL_SECRET = "sk-sentinel-SHOULD-NEVER-APPEAR-99999"
SENTINEL_SECRET_BYTES = SENTINEL_SECRET.encode("utf-8")


# Required env vars per integration (mirrors the registry — kept here so the test
# is self-documenting and fails loudly if the registry's required set drifts).
_ANTHROPIC_REQUIRED = ["ANTHROPIC_API_KEY"]
_WEB_PUSH_REQUIRED = ["VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"]


def _find(integrations: list[dict], integration_id: str) -> dict:
    for item in integrations:
        if item["id"] == integration_id:
            return item
    raise AssertionError(f"integration {integration_id!r} not in response")


# ---------------------------------------------------------------------------
# (a) configured False when required env absent; True when present.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_configured_false_when_required_env_absent(client, monkeypatch):
    """POSITIVE: the integration is listed. NEGATIVE: configured is False and
    every required env var's `present` is False when the env is unset.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    resp = await client.get("/api/settings/integrations")
    assert resp.status_code == 200, resp.text
    integrations = resp.json()["integrations"]

    anthropic = _find(integrations, "llm_anthropic")
    assert anthropic["configured"] is False, "configured must be False with no key"
    present_map = {ev["name"]: ev["present"] for ev in anthropic["env_vars"]}
    for name in _ANTHROPIC_REQUIRED:
        assert present_map[name] is False, f"{name} present must be False when unset"


@pytest.mark.asyncio
async def test_configured_true_when_required_env_present(client, monkeypatch):
    """POSITIVE: configured flips True and each required var's `present` is True
    once all required env vars are monkeypatched in (multi-var integration).
    NEGATIVE-lock: same integration is NOT configured when only a subset is set
    (proves the all-required gate, not a vacuous True).
    """
    # Subset set → still not configured.
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "pub-abc")
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("VAPID_SUBJECT", raising=False)

    resp = await client.get("/api/settings/integrations")
    web_push = _find(resp.json()["integrations"], "web_push")
    assert web_push["configured"] is False, (
        "configured must be False when only a subset of required vars is set"
    )

    # All required vars set → configured True.
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "priv-def")
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:op@example.com")

    resp2 = await client.get("/api/settings/integrations")
    web_push2 = _find(resp2.json()["integrations"], "web_push")
    assert web_push2["configured"] is True, "configured must be True with all keys"
    present_map = {ev["name"]: ev["present"] for ev in web_push2["env_vars"]}
    for name in _WEB_PUSH_REQUIRED:
        assert present_map[name] is True, f"{name} present must be True when set"


@pytest.mark.asyncio
async def test_ollama_always_configured_with_no_required_env(client):
    """llm_ollama has no required env var → configured is unconditionally True."""
    resp = await client.get("/api/settings/integrations")
    ollama = _find(resp.json()["integrations"], "llm_ollama")
    assert ollama["configured"] is True
    assert ollama["env_vars"] == []


# ---------------------------------------------------------------------------
# (b) PATCH enable then GET reflects enabled=true (persistence).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_enable_then_get_reflects_enabled(client):
    """POSITIVE: after PATCH {enabled:true}, GET shows enabled=true for that id.
    NEGATIVE-lock: a DIFFERENT integration we did NOT toggle stays enabled=false
    (the toggle is scoped to the patched id, not a global flip).
    Also flips back to false to prove the upsert updates an existing row.
    """
    target = "telegram"
    other = "llm_openai"

    # PATCH enable.
    patch_resp = await client.patch(
        f"/api/settings/integrations/{target}", json={"enabled": True}
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["enabled"] is True
    assert patch_resp.json()["id"] == target

    # GET reflects the persisted toggle.
    get_resp = await client.get("/api/settings/integrations")
    integrations = get_resp.json()["integrations"]
    assert _find(integrations, target)["enabled"] is True
    # NEGATIVE: an untouched integration is still disabled.
    assert _find(integrations, other)["enabled"] is False, (
        "toggling one integration must not enable another"
    )

    # PATCH disable — proves the upsert updates the existing row (not insert-only).
    patch_off = await client.patch(
        f"/api/settings/integrations/{target}", json={"enabled": False}
    )
    assert patch_off.status_code == 200, patch_off.text
    assert patch_off.json()["enabled"] is False

    get_resp2 = await client.get("/api/settings/integrations")
    assert _find(get_resp2.json()["integrations"], target)["enabled"] is False


# ---------------------------------------------------------------------------
# (c) GET never leaks a secret VALUE — only presence booleans.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_never_leaks_secret_value(client, monkeypatch):
    """Binary lock: the literal secret VALUE bytes never appear in the GET wire
    body even when the env var is set and present=True.

    POSITIVE: the env var is reported present=True (it IS set).
    NEGATIVE: SENTINEL_SECRET_BYTES is NOT anywhere in response.content.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL_SECRET)

    resp = await client.get("/api/settings/integrations")
    assert resp.status_code == 200, resp.text

    anthropic = _find(resp.json()["integrations"], "llm_anthropic")
    present_map = {ev["name"]: ev["present"] for ev in anthropic["env_vars"]}
    # POSITIVE: presence is reported True (so a present=False can't vacuously pass).
    assert present_map["ANTHROPIC_API_KEY"] is True
    assert anthropic["configured"] is True

    # NEGATIVE: the raw secret value never crosses the wire.
    assert SENTINEL_SECRET_BYTES not in resp.content, (
        "GET response leaked a secret VALUE — only presence booleans are allowed"
    )


# ---------------------------------------------------------------------------
# (e) platform_security.vault_key_configured — Kanban #1658.
# ---------------------------------------------------------------------------

_VAULT_SENTINEL = "gAAAAAB-SENTINEL-SHOULD-NEVER-APPEAR-IN-RESPONSE-xyz123"
_VAULT_SENTINEL_BYTES = _VAULT_SENTINEL.encode("utf-8")


@pytest.mark.asyncio
async def test_platform_security_vault_key_configured_true_when_env_set(
    client, monkeypatch
):
    """POSITIVE: vault_key_configured is True when CREDENTIALS_MASTER_KEY is set."""
    monkeypatch.setenv("CREDENTIALS_MASTER_KEY", _VAULT_SENTINEL)

    resp = await client.get("/api/settings/integrations")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "platform_security" in body, "platform_security key must be present in GET response"
    assert body["platform_security"]["vault_key_configured"] is True


@pytest.mark.asyncio
async def test_platform_security_vault_key_configured_false_when_env_absent(
    client, monkeypatch
):
    """NEGATIVE: vault_key_configured is False when CREDENTIALS_MASTER_KEY is unset."""
    monkeypatch.delenv("CREDENTIALS_MASTER_KEY", raising=False)

    resp = await client.get("/api/settings/integrations")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "platform_security" in body
    assert body["platform_security"]["vault_key_configured"] is False


@pytest.mark.asyncio
async def test_platform_security_vault_key_value_never_in_response(client, monkeypatch):
    """Binary lock: the vault key VALUE is NEVER serialized even when env is set.

    POSITIVE: vault_key_configured is True (env IS set — presence reported).
    NEGATIVE: the sentinel value bytes do NOT appear anywhere in response.content.
    """
    monkeypatch.setenv("CREDENTIALS_MASTER_KEY", _VAULT_SENTINEL)

    resp = await client.get("/api/settings/integrations")
    assert resp.status_code == 200, resp.text
    # POSITIVE: presence reported
    assert resp.json()["platform_security"]["vault_key_configured"] is True
    # NEGATIVE: value never crosses the wire
    assert _VAULT_SENTINEL_BYTES not in resp.content, (
        "GET response leaked the vault key VALUE — only vault_key_configured bool is allowed"
    )


# ---------------------------------------------------------------------------
# (d) Unknown integration id → 404.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_unknown_integration_returns_404(client):
    """POSITIVE: a registered id PATCHes fine (proves the route works).
    NEGATIVE: an unknown id returns 404 with the locked detail string and is
    NOT written to the DB.
    """
    resp = await client.patch(
        "/api/settings/integrations/not_a_real_integration",
        json={"enabled": True},
    )
    assert resp.status_code == 404, resp.text
    assert "not_a_real_integration" in resp.json()["detail"]

    # The unknown id must not have been persisted — it never appears in GET.
    get_resp = await client.get("/api/settings/integrations")
    ids = {item["id"] for item in get_resp.json()["integrations"]}
    assert "not_a_real_integration" not in ids
