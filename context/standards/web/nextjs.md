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
