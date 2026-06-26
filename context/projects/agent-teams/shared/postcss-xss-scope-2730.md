# Security scoping note — postcss XSS (GHSA-qx2v-qp2m-jg93)

**Task:** #2730 (scope/review) · **Fix task:** #2734 (bug) · **Date:** 2026-06-26 · **Source:** Snyk 2026-06-26 (npm, web/package.json) cross-referenced with `npm audit` (web container).

## Finding
- **Package:** `postcss@8.4.31` — **moderate** (Snyk "Medium") XSS, **GHSA-qx2v-qp2m-jg93** ("Unescaped `</style>` in CSS Stringify Output").
- **Dependency type:** **TRANSITIVE** — nested under `next@16.2.9` (`node_modules/next/node_modules/postcss`). Next.js pins its own bundled postcss.
- **Not the direct dep:** the project's **direct** `postcss` is `8.5.15` (≥8.5.10, already patched); autoprefixer / tailwindcss / vite all dedupe to that patched 8.5.15. **Only next's bundled copy is vulnerable.**

## Risk (practical)
**LOW.** postcss is a **build-time** tool (Next.js CSS compilation + Tailwind over *first-party* CSS). The XSS requires postcss to stringify **attacker-controlled** CSS — our app never feeds untrusted CSS through postcss at runtime. The finding is worth clearing for hygiene + Snyk/audit cleanliness, not because it is runtime-exploitable here.

## Recommended fix (→ #2734) — low-risk, no `next` downgrade
Add an npm **override** to `web/package.json`:
```json
"overrides": { "postcss": "^8.5.10" }
```
then `npm install` **in the web container** (host has no node) to update `package-lock.json` (forces next's nested 8.4.31 to dedupe to 8.5.x), rebuild web, and confirm `npm audit` → 0 vulns. Patch within postcss 8.x → backward-compatible; next 16 / tailwind / autoprefixer already run 8.5.15, so no peer-dep conflict.

## Rejected / alternative
- ❌ `npm audit fix --force` — downgrades **next 16 → 9.3.3** (catastrophic). Do not run.
- ↪ Alternative: bump `next 16.2.9 → ≥16.3.0` (minor; advisory upper-bound is `16.3.0-canary.5`, so a 16.3.x stable likely bundles patched postcss). Bigger surface than the override; use only if the override misbehaves.

No `.snyk` ignore needed — a real fix (override) exists.
