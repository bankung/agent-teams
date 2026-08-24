---
name: zb-mobile-scaffold
description: >-
  Scaffold an Ionic page or Angular standalone component in the bound mobile project —
  generates the component, its route wiring, and its spec file in one pass, then verifies
  the files actually landed. Use when the operator says "add a page", "new screen",
  "สร้างหน้า", "scaffold a component", "generate a page", "เพิ่มหน้าใหม่", or names a screen
  to create in an Angular/Ionic/Capacitor project. NOT for backend endpoints (use the API
  lane) and NOT for building or running the app on a device (use zb-mobile-build).
argument-hint: "<page|component> <name> [--route <path>] [--no-route]"
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash(npx:*)
  - Bash(npm:*)
  - Bash(curl:*)
metadata:
  version: 1.0.0
  category: stack
  tags: [mobile, angular, ionic, scaffold, mutate]
---

# /zb-mobile-scaffold — generate a page or component in the bound mobile project

Wraps the Angular CLI with this project's conventions and closes the two gaps the raw CLI
leaves open: **route wiring** and **goal-driven verification that the files exist**.

## Step 1 — resolve the bound project and confirm it is a mobile project

Resolve the project id per session (`bin/lead-project-id.ps1`), then:

```
curl --silent "http://localhost:8456/api/projects/<id>" -o _scratch/mob_proj.json -w "%{http_code}"
```

Read `paths_web` (the Angular workspace root) and `config.standards.mobile`.

- `config.standards.mobile` absent or empty → **STOP.** Either the project is not a mobile
  project, or the lane was never populated. Report which, do not guess.
- `paths_web` points at a directory that does not exist → **STOP** and report. Do not create it.

## Step 2 — read the conventions actually in use

Do NOT assume a layout. Glob the workspace first and match what is there:

- `<paths_web>/src/app/**/*.routes.ts` — routing style (standalone `Routes` vs NgModule)
- an existing page nearest the target area — folder shape, naming, `changeDetection`, test style

If the workspace has no page yet (first screen in the project), say so and state the layout you
are about to establish. The first scaffold sets the precedent for every later one.

## Step 3 — generate

```
npx ng generate component <path>/<name> --standalone --change-detection OnPush
```

Add `--skip-tests` ONLY if the workspace has no test setup at all. Never invent flags the
installed Angular version does not have — check `npx ng version` when unsure.

## Step 4 — wire the route (unless `--no-route`)

Add the lazy route to the nearest `*.routes.ts`:

```typescript
{ path: '<route>', loadComponent: () => import('./<path>/<name>.component').then(m => m.<Name>Component) }
```

## Step 5 — verify (never skip)

The CLI reporting success is not proof. Independently confirm:

```
ls <paths_web>/src/app/<path>/<name>/
grep -n "<route>" <paths_web>/src/app/**/*.routes.ts
npx tsc --noEmit -p <paths_web>/tsconfig.json
```

Report the raw output. If `tsc` fails, report the error verbatim — do not summarize it.

## Step 6 — report

State: files created (with paths), route added (or why not), `tsc` result, and any convention
you had to invent because the workspace did not answer the question.

---

## Why this exists

`ng generate` creates files but does not wire routes, does not follow project conventions it
was never told about, and reports success without anyone checking the result compiles. Steps 2
and 5 are the whole point of the skill; step 3 is the easy part.

## Usage

```
/zb-mobile-scaffold page daily-reading --route /daily
/zb-mobile-scaffold component tarot-card --no-route
```

## Related skills

- **zb-mobile-build** — build and run the app on a device once the screen exists.
- **zb-task-create** — open the Kanban task this scaffold belongs to (do this FIRST).
