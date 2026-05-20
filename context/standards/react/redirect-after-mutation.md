# React — redirecting after a state-flipping mutation

**Scope:** when a Client Component performs a state-flipping mutation (PATCH / POST / DELETE) and wants to navigate-on-success, the UX flow needs both (a) a visible success cue and (b) the route transition. Firing both simultaneously erases the cue before the user can register it.

## The pattern

Defer `router.push(...)` by ~500-800ms after the toast/cue renders:

```tsx
// web/components/TaskFocusView.tsx (Kanban #1001 canonical example)

const handleApprove = async () => {
  setSubmitting(true);
  try {
    await api.tasks.patch(task.id, {
      process_status: 5,
      status_change_reason: "Approved via push quick-action",
    });
    setToast({ kind: "success", message: "Task approved" });
    // ~600ms — long enough for the user to register the toast, short
    // enough that the transition still feels responsive.
    setTimeout(() => router.push("/inbox"), 600);
  } catch (err) {
    setError(err instanceof Error ? err.message : "Failed to approve");
  } finally {
    setSubmitting(false);
  }
};
```

Pair with `aria-live="polite"` on the toast so a screen-reader user hears the cue before the new page mounts:

```tsx
<div role="status" aria-live="polite" className="...">
  {toast.message}
</div>
```

## Why 600ms

- **<400ms** — the user's eye can register the toast started rendering, but not what it says. Feels like the page jumped for no reason.
- **400-800ms** — the sweet spot. Long enough to read a short success message, short enough to still feel snappy.
- **>1000ms** — feels sluggish; the user starts wondering if the click registered.

Tune for your specific toast verbosity: a one-word "Done" toast can use ~400ms; a longer "Task approved — open the next pending task?" needs closer to 1s.

## Anti-patterns

- **Immediate `router.push` on success** — toast renders for ~1 frame before unmount; user perceives no feedback at all. Especially bad on slow mobile where the route transition is itself a perceptible pause.
- **No toast, just navigate** — the destination page has no idea what just happened on the previous one. Operator wonders "did it actually save?".
- **4s+ delay before navigate** — feels like the page froze. If you want the toast to persist on the next page, use a global toast/snackbar provider that survives the navigation rather than blocking the navigation on the toast.

## Cross-reference

- Canonical implementation: `web/components/TaskFocusView.tsx::handleApprove` (Kanban #1001, 2026-05-20).
- Sibling concern: optimistic mutations on TaskMuteToggle / SettingsPanel — see `react/deliberate-action-mutations.md` (if present) for when to flip OPTIMISTICALLY vs WAIT-FOR-SERVER. Both standards apply: WAIT-FOR-SERVER mutations that THEN navigate use the 600ms-defer pattern; OPTIMISTIC mutations that stay on the same page don't need it.
