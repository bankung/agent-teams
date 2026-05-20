# External-callback cleanup — auto-removing stale local state when an upstream API reports it

**Scope:** when an external API tells us our cached state is stale (Web Push 410 Gone, OAuth token revoke, webhook deactivate, etc.), the local cleanup MUST run in a fresh transaction independent of the audit-row write that recorded the upstream failure. Otherwise a cleanup failure rolls back the audit row, and the upstream-failure record vanishes too.

## The pattern

When the upstream signals "your cached identifier is stale":

1. **Run the audit/log write in the caller's transaction.** Whatever record captures "we tried the upstream call and got 410" stays there.
2. **Run the local cleanup (e.g. soft-delete the stale subscription / token / webhook row) in a FRESH session/transaction.** Do NOT couple it to the audit transaction.
3. **The adapter's return contract is `{ok: False, detail: "..."}`** — never raise. The dispatch chain that called the adapter expects to keep iterating through fallbacks; raising would break the chain.

```python
# api/src/services/notify_web_push.py (Kanban #955.A canonical example)

async def send_web_push(target: dict, payload: dict) -> dict[str, Any]:
    try:
        webpush(subscription_info=..., data=json.dumps(payload), vapid_private_key=..., vapid_claims=...)
        return {"ok": True, "detail": "delivered"}
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            # Fresh SessionLocal — NOT the caller's session. Cleanup failure here
            # does NOT roll back the audit row that recorded the 410.
            await _soft_delete_subscription(target["chat_id"])
            return {"ok": False, "detail": "Subscription invalid; auto-removed"}
        return {"ok": False, "detail": f"WebPushException: {exc!s}"}
```

## Why "fresh transaction" matters

Coupling the cleanup to the audit transaction means: if the cleanup raises (DB unavailable, row already gone, transient error), the whole transaction rolls back — and the audit row recording "we got a 410 on chat_id=N" rolls back with it. Next call retries the dead subscription, gets 410 again, audit attempts another write — same rollback. The operator never sees the upstream-failure record.

A fresh session decouples the two concerns: the audit row lands or fails on its own merits, the cleanup runs or fails on its own merits. Cleanup-failure recovery becomes a separate concern (retry next tick, alert the operator, etc.) without corrupting the audit trail.

## Generalizes to

- Web Push: 404 / 410 from the push service → soft-delete `push_subscriptions` row.
- OAuth refresh tokens: 400 `invalid_grant` from the IdP → delete the cached token row.
- Webhooks: 410 / persistent 5xx from the subscriber → deactivate the webhook row.
- Any "external told us our cached identifier is stale" pattern.

## Anti-pattern

```python
# DON'T — cleanup coupled to caller's session
async def send_web_push(target, payload, session):
    try:
        webpush(...)
    except WebPushException as exc:
        if exc.response.status_code in (404, 410):
            # Wrong: this UPDATE rides the caller's transaction.
            await session.execute(update(PushSubscription).where(...).values(status=0))
            # If anything raises after this, the audit row vanishes.
        ...
```

## Cross-reference

- Canonical implementation: `api/src/services/notify_web_push.py` `_soft_delete_subscription()` (Kanban #955.A, 2026-05-20).
- Adapter contract: `api/src/services/notification_router.py::_ADAPTERS` — adapters return `{ok, detail}`, never raise.
- Sibling pattern (any `dispatch chain that walks priority-ordered targets`): the same return contract keeps the chain resumable.
