# React — `aria-label` (humans) vs `data-*` (machines)

**Scope:** how to expose enum-valued state on a JSX element when both screen readers AND machine consumers (CSS selectors, JS hooks, smoke probes) need to read it. Two attributes serve two audiences — don't conflate them.

## Rule

For a React component that renders an enum-valued state:

1. **`data-<name>=<enum-verbatim>`** — machine-facing. Use the underlying enum value byte-for-byte (matches DB schema, API contract, TypeScript Literal type). CSS selectors, JS hooks, smoke probes, and analytics tags read this. NEVER drift from the canonical enum.
2. **`aria-label=<human-readable>`** — screen-reader-facing. Replace underscores / camelCase / kebab-case with **spaces** so the screen reader pronounces the label naturally. Optionally translate to project locale (if i18n is on).

Both attributes go on the same element (typically the wrapping `<span>` or interactive element). They MUST agree semantically — the human label is the enum's human-readable form, not a different concept.

`title=<human-readable>` mirrors `aria-label` for hover tooltips (browser-native). Reuse the same string.

## Canonical worked example (in-repo)

[web/components/RunModeBadge.tsx](../../../web/components/RunModeBadge.tsx) (Kanban #764):

```tsx
// run_mode = "auto_pickup" → renders this badge
<span
  aria-label="auto pickup"            // ← human (space form)
  title="auto pickup"
  data-run-mode="auto_pickup"          // ← machine (enum verbatim, lives on the wrapper or parent <article>)
  className="..."
>
  <svg aria-hidden="true">...</svg>
</span>
```

The icon `<svg>` carries `aria-hidden="true"` because the wrapping `<span>` already provides the semantic label — otherwise the screen reader announces the SVG markup as noise.

`data-run-mode="auto_pickup"` (underscore) is consumed by:
- The Tier-1 smoke matrix's grep probes (`grep 'data-run-mode="auto_pickup"' probe.html`).
- Any future CSS rule (`[data-run-mode^="auto_"] { ... }`).
- JS event handlers reading `element.dataset.runMode === "auto_pickup"`.

`aria-label="auto pickup"` (space) is consumed by:
- Screen readers (NVDA, JAWS, VoiceOver) announcing the badge.
- `title` tooltip on hover.

## Anti-patterns

### 1. Same string in both — drift waiting to happen

```tsx
// ❌ aria-label gets the underscored form → screen reader announces "auto underscore pickup"
<span aria-label="auto_pickup" data-run-mode="auto_pickup">
```

Failure mode: screen-reader UX degrades. Devs eventually fix the aria-label to a human-readable form, but if the convention isn't codified, the next enum addition (e.g., `auto_scheduled`) regresses.

### 2. Same string in both — pretty string everywhere

```tsx
// ❌ Pretty string leaks into data-attribute → CSS selectors + smoke probes break
<span aria-label="auto pickup" data-run-mode="auto pickup">
```

Failure mode: `grep 'data-run-mode="auto_pickup"' probe.html` returns 0 — smoke probe FAILS spuriously. CSS selectors `[data-run-mode="auto_pickup"]` no longer match. Any caller serializing the DB enum to compare with `dataset.runMode` breaks.

### 3. `aria-label` on the SVG itself + missing on the wrapper

```tsx
// ❌ Screen reader announces "auto pickup" twice (svg + parent span confusion)
<span>
  <svg aria-label="auto pickup">...</svg>
</span>
```

Failure mode: redundant announcements + accessibility-tree noise. The `<svg>` should be `aria-hidden="true"` and the wrapper carries the label.

## Verification recipe

```bash
# Anti-pattern grep — any aria-label that still uses underscore-or-camelCase enum form:
grep -rEn 'aria-label="[a-z]+_[a-z_]+"' web/components/

# Anti-pattern grep — any data-* attribute with a space (should be underscore/enum form):
grep -rEn 'data-[a-z-]+="[a-zA-Z]+ [a-zA-Z]+"' web/components/

# Both greps should return 0 hits.
```

Smoke probes assert both forms exist on the rendered page:

```bash
grep -c 'aria-label="auto pickup"' probe.html    # ≥ 1 if any auto_pickup task rendered
grep -c 'data-run-mode="auto_pickup"' probe.html # ≥ 1 — must match aria-label count
```

## Out of scope

- **i18n / localization** — when the project ships in multiple languages, `aria-label` and `title` get localized strings (`aria-label="หยิบอัตโนมัติ"` for Thai). The data-attribute stays English / enum-verbatim. The convention extends naturally.
- **Multi-word non-enum labels** — descriptive labels like `aria-label="Open consent grant dialog"` are not enum-derived; no machine counterpart needed. This standard covers only the enum-valued case.
- **Icon-only buttons** (no enum state) — `aria-label="Close"` is sufficient; no data-attr.
- **Complex semantic states** (e.g., `aria-busy`, `aria-expanded`) — these are framework-native attributes, not custom enums; follow ARIA spec.

## Cross-references

- `nextjs/server-client-composition.md` — pure-presentational icon components stay Server.
- `tailwind/` (future) — color-coding by `data-*` value via Tailwind's arbitrary variant `[data-run-mode="auto_pickup"]:bg-emerald-50`.
