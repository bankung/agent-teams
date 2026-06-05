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

This command works in several modes. Choose the one that fits your situation:

### Model A — pull pre-built images (fastest, no clone)

```bash
npx @bankung/agent-teams up --images
```

Pulls pre-built images from the GitHub Container Registry (GHCR) and starts
the full stack. **No git clone or local build required** — Docker just pulls
the images. This is the recommended path for users who want to run the platform
without modifying source code.

Aliases: `--images` and `--pull` are equivalent.

Requirements:
- Docker Desktop (or Docker Engine) installed and running.
- Images must be published to GHCR by the release CI (`v*` tag push). If you
  want to pin a specific version set `AGENT_TEAMS_VERSION=1.2.3` in `.env`.

### Model B — clone + build from source (default)

#### Standalone mode (run from any empty directory)

```bash
mkdir my-agent-teams && cd my-agent-teams
npx @bankung/agent-teams up
```

The CLI detects that `docker-compose.yml` is absent, clones
`https://github.com/bankung/agent-teams.git` into `<cwd>/agent-teams`, then
builds and starts the stack from that cloned directory.

**Note:** Standalone mode requires the GitHub repository to be public.

**Note:** The first `up` builds Docker images from source — this can take
several minutes. Subsequent runs use the Docker layer cache and are fast.

You can override the clone destination:

```bash
npx @bankung/agent-teams up /path/to/target-dir
```

#### In-repo mode (run from inside a cloned repository)

```bash
git clone https://github.com/bankung/agent-teams.git
cd agent-teams
npx @bankung/agent-teams up
```

The CLI detects `docker-compose.yml` in the package root and uses the existing
checkout directly — no clone occurs.

### What `up` does (both modes)

1. Verifies the Docker daemon is reachable (clear error if not).
2. **`--images` mode:** pulls pre-built GHCR images.
   **Default mode:** resolves/clones the repo root, then runs `docker compose up -d --build`.
3. Copies `.env.example` to `.env` if no `.env` exists yet.
4. Generates a `CREDENTIALS_MASTER_KEY` (Fernet key) if the value is empty —
   prints a backup reminder. **Back up this key to a password manager.**
5. Applies database migrations (`alembic upgrade head`).
6. Waits up to 60 seconds for the API to become healthy on port 8456.
7. Runs the seed script (idempotent — safe to re-run).
8. Prompts for your Claude Code plan (Max / Pro) and applies the matching tier
   preset. Skipped automatically in non-interactive environments.
9. Prints the Kanban URL and attempts to open it in your default browser.

## Commands

| Command | Description |
|---------|-------------|
| `up [targetDir]` | Build and start all services. Idempotent — safe to run on an existing install. In standalone mode, clones the repo first. |
| `up --images` | Pull pre-built images from GHCR and start all services. No clone or build. Alias: `--pull`. |
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

### Pinning an image version

Set `AGENT_TEAMS_VERSION` in your `.env` (or export it) to pull a specific
release rather than `latest`:

```bash
echo "AGENT_TEAMS_VERSION=1.2.3" >> .env
npx @bankung/agent-teams up --images
```

Images are published to GHCR by the GitHub Actions workflow
`.github/workflows/release-images.yml` on every `v*` tag push. `npm publish`
is performed by the operator separately.

## Port defaults

| Service | Default port | Override via .env |
|---------|-------------|-------------------|
| Web (Kanban UI) | 5431 | `WEB_PORT` |
| API | 8456 | `API_PORT` |
| PostgreSQL | 5432 | `POSTGRES_PORT` |
| LangGraph | 8465 | `LANGGRAPH_PORT` |
