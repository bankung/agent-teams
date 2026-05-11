# React — deliberate-action mutations (no optimistic updates)

**Scope:** when a UI mutation MUST wait for server confirmation before changing visual state, and when optimistic updates are acceptable. The split is mechanical, not a judgment call — apply the discriminator checklist below.

## Rule

Mutations that are **auditable**, **legally-binding**, or **hard-to-reverse** belong to the **deliberate-action class**. They MUST NOT use optimistic updates:

- Disable the submit button while in-flight (`disabled={submitting}`).
- Render server errors inline on non-2xx (do NOT close the modal / clear the form on error).
- Confirm visual state change only AFTER the server returns 2xx.
- Reconcile state via `router.refresh()` or a targeted refetch — don't manually flip local state ahead of the server.

Low-stakes mutations (drag-drop reorder, inline edit, toggle archived) use optimistic updates — the contrast pattern is locked in `react/optimistic-mutations.md` when first written.

## Discriminator checklist

Ask three questions about the mutation. **Any "yes" → deliberate-action class.**

1. **Auditable?** Does this action get logged for compliance / billing / audit-trail purposes?
2. **Legally-binding?** Does the user grant permission / consent / accept terms with this click?
3. **Hard-to-reverse?** Does undoing require admin intervention, a separate API call, or is it impossible?

| Examples in scope (deliberate-action) | Examples NOT in scope (optimistic IS fine) |
|---|---|
| Consent grant (legally-binding) | Drag-drop column change |
| Account delete (hard-to-reverse) | Inline-edit title / description |
| Payment confirm (auditable + hard-to-reverse) | Toggle archived state |
| Publish post (hard-to-reverse without revoke endpoint) | Reorder list items |
| Drop scratch database (hard-to-reverse) | Star / favorite toggle |
| Submit signed contract | Update color label |

## Canonical worked example (in-repo)

[web/components/ProjectConsentGrantModal.tsx](../../../web/components/ProjectConsentGrantModal.tsx) (Kanban #407 / #483) — consent grant is legally-binding (user grants per-project headless auto-run permission):

- Submit button disabled while `submitting` AND while `typed.length === 0` (typed-acknowledgment guard).
- 400 with locked detail `"confirm_name must match project name exactly"` renders inline in red. Modal stays open; form keeps its state.
- 200 path: `await grantConsent(...)` → `router.refresh()` → close modal. The Server-rendered `<ProjectConsentBanner>` re-runs with the fresh `auto_run_consent_at` and flips zinc → emerald. Only THEN does the user see the visual confirmation.
- No `setConsentedAt(new Date())` ahead of the server — banner state is owned by the SSR fetch.

Contrast: [web/components/BoardColumn.tsx](../../../web/components/BoardColumn.tsx) drag-drop (Kanban #709) IS optimistic — card moves to the new column on `onDragEnd` before the PATCH returns. Reversible (server rejection → toast + rollback). Acceptable per the discriminator (column change is not auditable / not legally-binding / trivially reversible).

## Anti-pattern

```tsx
// ❌ Wrong — deliberate-action with optimistic flip
const onGrant = async () => {
  setConsentedLocally(true);          // user sees emerald banner immediately
  try {
    await grantConsent(id, typed);
  } catch {
    setConsentedLocally(false);       // flip back on error
    setError("...");
  }
};
```

Failure modes:
- **User reads stale state** — sees "consented" before the server has agreed. Trust signal corrupted.
- **Flicker on rejection** — emerald → zinc within a frame is jarring for a consent surface.
- **Race on concurrent grants** — two tabs both flip locally; one wins server-side; the loser's banner stays falsely emerald until reload.
- **No trace if the catch swallows the error** — easy to lose track of whether grant actually landed.

```tsx
// ✅ Right — wait for server, reconcile via SSR
const onGrant = async (e) => {
  e.preventDefault();
  setSubmitting(true);
  try {
    await grantConsent(id, typed);
    router.refresh();                 // SSR re-runs, banner flips on next paint
    setOpen(false);
  } catch (err) {
    setError(err.message);            // inline red, modal stays open
  } finally {
    setSubmitting(false);
  }
};
```

## Error-rendering contract

For deliberate-action mutations, surface the server's detail string verbatim:
- 400 with structured detail (e.g., locked source-text strings) → render `err.message` inline.
- 422 array form (Pydantic validation) → join field-level messages with `; ` and render inline. (See `react/error-surfacing.md` when written, or extend `extractDetail` in `web/lib/api.ts`.)
- 500 / network → render a generic "Try again" inline; the user retries from the same form.

Do NOT close the form / clear the input on error — the user's typed-acknowledgment state is part of the UX trust loop.

## Out of scope

- Optimistic update pattern itself — separate standard (`react/optimistic-mutations.md`) when first formalized.
- Server Action mutations (`'use server'`) — same rule applies; the discriminator is framework-agnostic.
- Multi-step wizard / confirmation flow — separate UX standard.
- Two-factor confirm / re-auth before action — separate security standard.
