# Adding a new project

Projects are created from the **Kanban UI** via `POST /api/projects`:

1. UI sends name, description, paths, stack, and standards mapping to the backend.
2. Backend inserts a row in the `projects` table.
3. Backend **auto-scaffolds the folder structure**:
   - `context/projects/<new>/{shared,frontend,backend,devops,qa,reviewer}/`
   - copies template files into `shared/{decisions,api-contracts,db-schema}.md` (fixed templates in `api/` source)
   - `.gitkeep` in the 5 role folders
4. Returns 201 + project_id.

**Lead does not create projects via the Edit tool.** Always go through the API.

If the user asks to create a project while the UI is unavailable:
- Spawn `backend` to call `POST /api/projects` (subagent makes the HTTP call), or
- Lead `curl POST` directly (the user will be prompted to approve).
