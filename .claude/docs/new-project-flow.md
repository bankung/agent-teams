# Adding a new project

Projects are created from the **Kanban UI** via `POST /api/projects`:

1. UI sends name, description, paths, stack, standards mapping, **and `lead`** (mandatory — picks the playbook this project will use, e.g., `'dev'` or `'novel'`) to the FastAPI service.
2. The service inserts a row in the `projects` table.
3. The service **auto-scaffolds the folder structure** based on the active lead's roster:
   - For `lead='dev'`: `context/projects/<new>/{shared, dev-frontend, dev-backend, dev-devops, dev-tester, dev-reviewer}/`
   - For `lead='novel'`: `context/projects/<new>/{shared, novel-writer, novel-editor}/`
   - Copies template files into `shared/` (templates per lead live in `api/src/templates/<lead>/`)
   - `.gitkeep` in each role folder
4. Returns 201 + project_id.

**Lead does not create projects via the Edit tool.** Always go through the API.

If the user asks to create a project while the UI is unavailable:
- Spawn the appropriate role for the active lead to call `POST /api/projects` (e.g., `dev-backend` if you're already in a dev project), or
- Lead `curl POST` directly (the user will be prompted to approve).
