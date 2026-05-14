# Next.js — agent-teams web standards

Conventions for `web/` (Next.js 14 App Router, React, TypeScript, Tailwind).

---

## Windows + Docker dev-server stale-bundle gotcha

**Symptom:** You edit `web/app/**` or `web/components/**`, `tsc --noEmit` passes, but `curl` against the dev server returns the **old HTML**. The Next.js process kept the pre-edit bundle in memory.

**Root cause:** Docker Desktop on Windows (WSL2 backend) under-fires inotify events for files under those directories. The Next.js file-watcher (chokidar + inotify) misses the change → no rebuild → stale bundle served. macOS/Linux hosts do NOT exhibit this.

**Workaround (mandatory after every FE edit on Windows):** `docker compose restart web`. Then verify the new bundle is live before reporting done.

### Smoke loop — required for every dev-frontend task on Windows

1. **Edit** the file under `web/app/**` or `web/components/**`.
2. **TS check:** `docker compose exec -T web npx tsc --noEmit` → must exit 0, no output.
3. **Fetch** the affected route — `curl http://localhost:5431/<route>` (or PowerShell `Invoke-WebRequest`).
4. **If the new content is NOT in the response:**
   - `docker compose -p agent-teams restart web` (see project-name rule below)
   - Wait for "ready on" log line
   - Re-fetch step 3
   - If still stale: check file syntax (`tsc --noEmit` again); check container saw the write (`docker compose exec -T web cat /app/<path>`).
5. **If the new content IS in the response on first try:** done — watcher caught it this time (rare; happens occasionally).

### Worktree safety — always pass `-p agent-teams` to `docker compose` when running from a worktree

When you run `docker compose <cmd>` from a git worktree directory (`.claude/worktrees/<slug>/`), Docker Compose defaults the project name to the **worktree folder name** (e.g., `festive-bartik-04b551`). That:
- Creates a **separate compose project** (`docker compose ls` shows two: `agent-teams` and `<worktree-slug>`).
- Creates a **separate network** (`<worktree-slug>_default`) — the web container ends up on a different network from the main project's `api` / `db` / `langgraph` → web can't resolve `http://api:8456` → fetch fails.
- Builds + tags a new image (`<worktree-slug>-web`) → the running container serves the worktree's edited code, but the main compose project's `web` container is gone.

**Always pass `-p agent-teams` explicitly** when working from a worktree, OR `cd` to the main repo root first:

```bash
# Safe — restart the running container in place
docker compose -p agent-teams restart web

# Safe — recreate from worktree code, but keep the network membership
docker compose -p agent-teams up -d --no-deps --build web

# UNSAFE from a worktree dir (silently claims web under a new project + new network)
docker compose up -d --no-deps web
```

**Recovery if you've already claimed web under a worktree project:**
1. `docker compose -p <worktree-slug> down` (removes the misplaced web container + worktree network)
2. From the main repo root: `docker compose -p agent-teams up -d --no-deps web` (re-attaches web to the main network)
3. Verify: `docker ps --format "{{.Names}} {{.Image}}"` — web should show `agent-teams-web` (image), not `<worktree-slug>-web`.

### Bind-mount path caveat — `restart` won't pick up worktree-only files

If the running `agent-teams-web` container was originally `up`-ed from the **main repo**, its `/app` bind-mount points at `<main-repo>/web`. A `docker compose -p agent-teams restart web` from a worktree directory **does NOT rebind** the mount — the container keeps serving the main repo's `web/`, so new files created under `.claude/worktrees/<slug>/web/` (e.g., `web/public/agentboard-icons.svg`, new components) will be invisible to the container. `tsc --noEmit` against the worktree source still passes; the live curl still shows stale HTML or 404s for the new assets.

**To rebind the container to the worktree's `web/`:** from the worktree directory, run `docker compose -p agent-teams up -d --no-deps --build web`. This recreates the container with the worktree path as the bind source.

**Verify the rebind landed:**
```powershell
docker inspect agent-teams-web --format '{{range .Mounts}}{{.Source}}{{println}}{{end}}'
# Source should point under .claude\worktrees\<slug>\web (NOT the main repo)
```

Observed in Kanban #914 (2026-05-14): a worktree-only `web/public/agentboard-icons.svg` 404'd via curl after `restart`; succeeded after `up -d --no-deps --build`.

### End-of-worktree-session restore — mandatory checklist

A worktree session that touched `web/` will leave the `agent-teams-web` container in one of two drift states:
- (a) Container claimed under a `<worktree-slug>` compose project (separate network → web↔api broken)
- (b) Container bound to the worktree path (works during the session; breaks when the worktree is removed; main-repo edits invisible until rebind)

**Before closing a worktree session (or before another session reuses main):**

1. **Sync main first** — from the main repo root (NOT the worktree):
   ```bash
   git status --short              # confirm clean working tree
   git pull --rebase origin main   # ff or rebase local main onto origin
   ```
   If `git pull --rebase` fails with "Not possible to fast-forward" + "Diverging branches", the main repo has a local commit that diverged from what was just pushed via the worktree. Investigate the divergent local commit before rebasing (it may be the user's work). If it's safe to rebase, the working tree usually carries the same content as the incoming origin commits and a `git checkout -- <file>` discard of the noise modifications + retry pull is the recipe.

2. **Rebind web container to main repo path** — from the main repo root:
   ```bash
   docker compose -p agent-teams up -d --no-deps --build web
   ```
   This rebuilds the web image from main's `web/` and recreates the container with `/app` mounted at `<main-repo>/web` (NOT the worktree).

3. **Verify rebind landed**:
   ```bash
   docker inspect agent-teams-web --format '{{range .Mounts}}{{.Source}}{{println}}{{end}}'
   # Source should be <main-repo>\web (NOT under .claude\worktrees\<slug>\web)

   curl --output /dev/null --write-out "%{http_code}\n" http://localhost:5431/p/agent-teams   # 200
   curl --output /dev/null --write-out "%{http_code}\n" http://localhost:8456/api/projects     # 200
   ```

4. **Worktree is now safe to remove**:
   ```bash
   git worktree remove .claude/worktrees/<slug>
   git branch -D claude/<slug>    # only after worktree is gone
   ```

**Anti-pattern caught (2026-05-14, end of festive-bartik-04b551 session):** skipping step 1 → `git pull --rebase` later in main worktree silently fails with diverging-branches (user's NewsAnalyzer local commit blocks ff). Always sync BEFORE rebinding; rebind from a clean known-good main.

Worked example: Kanban #875 smoke loop ran `docker compose up -d --no-deps web` from `.claude/worktrees/festive-bartik-04b551/` → web ended up on `festive-bartik-04b551_default` network → cross-page fetches to `/api/*` returned errors until web was re-attached to `agent-teams_default`.

### Worked examples — 4-strike pattern (2026-05-13)

| # | Case | Files | Resolution |
|---|---|---|---|
| [#769](http://localhost:5431/p/agent-teams/task/769) | **New file** (App Router) | `web/app/dashboard/page.tsx` | New route 404'd until `docker compose restart web`. |
| [#778](http://localhost:5431/p/agent-teams/task/778) | **New file** (component) | `web/components/SourcesBadge.tsx` | Import resolved but bundle never included new file; restart fixed. |
| [#869](http://localhost:5431/p/agent-teams/task/869) | **Edit existing** (App Router) | `web/app/dashboard/page.tsx`, `web/app/page.tsx` | First documented stale-edit case (not just new-file). |
| [#871](http://localhost:5431/p/agent-teams/task/871) | **Edit existing** (App Router + lib) | `web/app/dashboard/page.tsx`, `web/lib/api.ts` | Second stale-edit; codification trigger met. |

### Out of scope (separate research)

- **Permanent fix:** polling watch mode (`WATCHPACK_POLLING=true`), Turbopack migration, mount-option tuning — all require upstream changes. The restart workaround is the practical interim.
- **macOS / Linux behavior:** not observed in this codebase; the watcher works correctly there.

### Affects

- ✅ `web/app/**` — App Router pages, layouts, route handlers
- ✅ `web/components/**` — shared components
- ❌ `api/**` — FastAPI rebuilds per request (no in-process bundle cache)
- ❌ `context/**` — read at session start, not live-watched

---

## Icon kit — `agentboard-icons.svg`

Adopted in Kanban #914 (2026-05-14). Single SVG sprite at `web/public/agentboard-icons.svg` (31 UI icons at 24×24 viewBox + 5 app icon sizes). Consumed via `web/components/Icon.tsx`:

```tsx
<Icon name="add-task" size={14} />                        // decorative — omit aria-label
<Icon name="status-done" size={16} aria-label="Done" />   // semantic — pass aria-label
```

Pass `name` WITHOUT the `icon-` prefix. The component renders `<svg><use href="/agentboard-icons.svg#icon-{name}"/></svg>`.

**a11y contract** (don't pass `aria-hidden` directly — it's not a prop and TypeScript will reject it):
- **Decorative icon** (paired with visible text, e.g. inside a chip whose parent already has `aria-label` / `title`): omit `aria-label`. The component sets `aria-hidden={true}` on the `<svg>` automatically so screen readers don't duplicate the label.
- **Semantic icon** (standalone — the icon IS the label): pass `aria-label="..."`. The component sets `role="img"` and skips `aria-hidden`.

Codified after Kanban #915 (2026-05-14): spawn briefs originally wrote `<Icon name="..." aria-hidden />` literally; that's a TS error against the component signature. The doc now matches the implementation.

### Inventory

**Functional (18):** `lead-agent`, `spawn`, `task-card`, `board`, `multi-project`, `run`, `dev-agent`, `writer-agent`, `backlog`, `status-running`, `status-done`, `status-blocked`, `status-queued`, `add-task`, `agent-config`, `sprint`, `alert`, `logs`.

**Agents + run-modes (8):** `human-agent`, `ai-agent`, `manual-run`, `auto-run`, `tooltip`, `info`, `view-board`, `view-list`.

**App sizes (5, native viewBox):** `app-512`, `app-192`, `app-48`, `app-32`, `app-16` — reserved for favicon + PWA manifest; not yet wired (separate follow-up).

### Color palette

Adopt these as Tailwind theme tokens when the next palette pass lands (out of scope #914):

| Token | Hex | Intended use |
|---|---|---|
| `agent-ai` | `#7C3AED` | AI-agent accent, hexagon-head badges, primary brand purple |
| `agent-ai-light` | `#C4B5FD` | AI-agent fill (light), secondary surfaces inside app icons |
| `accent-warn` | `#F59E0B` | Manual-run / status-queued / lightning glyphs / soft-warn signals |
| `accent-done` | `#10B981` | Status-done check, auto-run cycle arcs |
| `accent-done-light` | `#34D399` | Status-done lighter variant, app-icon highlights |
| `accent-blocked` | `#EF4444` | Status-blocked / alert dot / hard error signal |
| `surface-app` | `#1A1A2E` | App icon background, dark-surface anchor |

Each status icon in the sprite hard-codes its semantic color (e.g., `icon-status-done` uses `#10B981` regardless of `currentColor`); functional icons use `currentColor` so they inherit text color. Don't override status-icon colors via `className` — they are intentionally fixed for color-coded recognition.
