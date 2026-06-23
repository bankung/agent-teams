# Web design — house style (glassmorphism, calm)

> System default visual language for web UI produced by agent-teams.
> Intended home: `context/standards/web/design-language.md`. Loaded for the frontend lane via `projects.json` → `standards.web`.

## When this applies
- DEFAULT for every user-facing web design deliverable from agent-teams (boards, dashboards, pages, components), across teams and projects.
- Override: if the requester names a different aesthetic, that choice wins for that request. Absent any instruction, this is the default.
- Out of scope: internal CLIs / non-visual tooling.

## The look
Glassmorphism, kept calm. Frosted translucent surfaces float over a soft colour-blob backdrop. Calm over vivid — saturated palettes are eye-straining and are not the default. The aesthetic must never cost readability (see Guardrails).

## Recipe — three knobs flip between light and dark
| knob | light | dark |
|---|---|---|
| glass surface | more opaque white film `rgba(255,255,255,0.55)` | thin light film `rgba(255,255,255,0.08)` |
| text polarity | dark text | light text |
| lift shadow | soft tinted `rgba(120,110,160,0.16)` | deep `rgba(0,0,0,0.42)` |

## Tokens — light
- backdrop surface: `#f0edf7`
- blobs (blur ~50px, opacity ~0.85): lavender `#d7c6f2` · pink `#f7cce0` · mint `#c4ecd9` · peach `#fcdcc4`
- glass: `background: rgba(255,255,255,0.55)` · `backdrop-filter: blur(18px) saturate(120%)` · `border: 1px solid rgba(255,255,255,0.8)` · `box-shadow: 0 8px 28px -8px rgba(120,110,160,0.22), 0 2px 8px -4px rgba(120,110,160,0.16)`
- text: primary `#3c3754` · secondary `#645d7d`

## Tokens — dark
- backdrop surface: `#18151f`
- blobs (blur ~58px, opacity ~0.55): violet `#6a4fb0` · rose `#a85273` · teal `#2c7a72` · indigo `#4a5ba8`
- glass: `background: rgba(255,255,255,0.08)` · `backdrop-filter: blur(20px) saturate(140%)` · `border: 1px solid rgba(255,255,255,0.16)` · `box-shadow: 0 10px 30px -10px rgba(0,0,0,0.5), 0 2px 10px -6px rgba(0,0,0,0.42)`
- text: primary `#f2f0f7` · secondary `rgba(255,255,255,0.58)`

> Text-on-glass note: a frosted surface MAY inherit the base-theme text colour instead of the hex above when the card scrim keeps the result ≥ WCAG AA (this is how the #2453 board renders — no glass-specific text override). The hex values are the target where a surface paints its own text.

## Status pills & avatars
- Light: light fill + dark text from the SAME colour family.
- Dark: deep fill + light text from the same family.
- The label must clear WCAG AA either way.

## Guardrails — non-negotiable (aesthetic never overrides these)
1. **Light + dark parity** — every surface themed in both modes.
2. **Contrast** — WCAG AA for all text, pills, avatars. Where text overlaps a bright/busy blob region, add a scrim (subtle darken/lighten film) so it never drops below AA.
3. **Perf budget** — the blob backdrop is ONE page-level layer, never one behind every card; cap the number of simultaneously-blurred layers; scroll stays smooth at realistic element counts.
4. **Fallback** — guard with `@supports (backdrop-filter: blur())`; without support, fall back to a solid low-opacity surface, never a faint invisible outline.
5. **Lean** — implement with CSS + existing tokens; no heavy new UI dependency / framework.
6. **Additive** — ship as a theme variant/toggle; never delete an existing flat theme or lose current readability.

## References
- First implementation: Kanban task #2453 (agent-teams board).
- Accessibility siblings: `context/standards/react/aria-label-vs-data-attribute.md`.
