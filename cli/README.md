# agent-teams CLI

Zero-dependency Node.js CLI that launches the agent-teams AI Kanban platform
locally via Docker Compose.

## Prerequisites

**Docker Desktop** (or Docker Engine on Linux) must be installed and running
before any CLI command will work. This package does NOT install Docker.

**git** must be installed and on PATH. It is required when running the CLI
outside a cloned repository (standalone mode — see below). This package does
NOT install git.

- Docker: <https://docs.docker.com/get-docker/>
  - Windows / macOS: install Docker Desktop and start it.
  - Linux: install Docker Engine + `docker compose` plugin and ensure the daemon
    is running (`sudo systemctl start docker`).
- git: <https://git-scm.com/downloads>

## Quick start

```bash
npx @bankung/agent-teams up
```

This command works in two modes depending on where you run it:

### Standalone mode (run from any empty directory — no prior clone needed)

```bash
mkdir my-agent-teams && cd my-agent-teams
npx @bankung/agent-teams up
```

The CLI detects that `docker-compose.yml` is absent, clones
`https://github.com/bankung/agent-teams.git` into `<cwd>/agent-teams`, then
proceeds with the full setup from that cloned directory.

**Note:** Standalone mode requires the GitHub repository to be public.

**Note:** The first `up` builds Docker images from source — this can take
several minutes. Subsequent runs use the Docker layer cache and are fast.

You can override the clone destination:

```bash
npx @bankung/agent-teams up /path/to/target-dir
```

### In-repo mode (run from inside a cloned repository)

```bash
git clone https://github.com/bankung/agent-teams.git
cd agent-teams
npx @bankung/agent-teams up
```

The CLI detects `docker-compose.yml` in the package root and uses the existing
checkout directly — no clone occurs.

### What `up` does

1. Verifies the Docker daemon is reachable (clear error if not).
2. Resolves the repo root (clone if needed — see modes above).
3. Copies `.env.example` to `.env` if no `.env` exists yet.
4. Generates a `CREDENTIALS_MASTER_KEY` (Fernet key) if the value is empty —
   prints a backup reminder. **Back up this key to a password manager.**
5. Runs `docker compose up -d --build` (builds images on first run; cached on
   subsequent runs).
6. Applies database migrations (`alembic upgrade head`).
7. Waits up to 60 seconds for the API to become healthy on port 8456.
8. Runs the seed script (idempotent — safe to re-run).
9. Prompts for your Claude Code plan (Max / Pro) and applies the matching tier
   preset. Skipped automatically in non-interactive environments.
10. Prints the Kanban URL and attempts to open it in your default browser.

## Commands

| Command | Description |
|---------|-------------|
| `up [targetDir]` | Build and start all services. Idempotent — safe to run on an existing install. In standalone mode, clones the repo first. |
| `down` | Stop all containers. Volumes (database data) are preserved. |
| `status` | Show container health (`docker compose ps`) and probe the API on :8456. |
| `reset` | **Destructive.** Wipes the Postgres volume (all data gone) and rebuilds. Prompts for confirmation (type `WIPE`) unless `--yes` is passed. |
| `--help` / `help` | Print usage. |
| `--version` | Print CLI version. |

### `reset` bypass

```bash
# flag
npx @bankung/agent-teams reset --yes

# env var
AGENT_TEAMS_RESET_YES=1 npx @bankung/agent-teams reset
```

## A future release will offer pre-built images

This release clones the repo and builds the images locally. A future release
will offer pre-built images for a faster, no-clone install.

## Port defaults

| Service | Default port | Override via .env |
|---------|-------------|-------------------|
| Web (Kanban UI) | 5431 | `WEB_PORT` |
| API | 8456 | `API_PORT` |
| PostgreSQL | 5432 | `POSTGRES_PORT` |
| LangGraph | 8465 | `LANGGRAPH_PORT` |
