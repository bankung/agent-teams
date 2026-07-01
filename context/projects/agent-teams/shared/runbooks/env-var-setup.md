# Env-var setup runbook

How container-bound env vars actually flow in agent-teams + how to add a new one. Written after the 2026-05-22 `#1217` Gmail SMTP trap (~30 minutes lost diagnosing why `api/.env` values weren't reaching the container).

## TL;DR

Env vars for the api / web / langgraph containers live in **ROOT `.env`** (next to `docker-compose.yml`), NOT in `api/.env`. They must ALSO be explicitly mapped in `docker-compose.yml` under the service's `environment:` block via `${VAR:-default}` substitution.

Without the compose mapping, the container can't see the var — even if it's in `.env`.

## The trap (caught 2026-05-22, #1217)

Operator created `api/.env` with Gmail SMTP values per (mis-)guidance from Lead. Container kept reporting `digest_email_disabled` + `recipient=<unset>` despite the `.env` file being well-formed. Cause:

1. **docker compose only reads root `.env`** for variable substitution. `api/.env` is unused unless an `env_file:` directive points at it (none does today).
2. **Even with values in root `.env`, the container won't see them** unless `docker-compose.yml` references them via `${VAR}` in the service's `environment:` block.

Fix path was: move values to root `.env` + add `${GMAIL_SMTP_*}` lines to compose api service's `environment:` block + `docker compose up -d api` (recreate, not just restart).

## Add a new env var — 3 steps

### 1. Add the value to root `.env`

```
MYVAR=somevalue
```

Conventions (matters — see Gotchas):
- No quotes: `MYVAR=somevalue` not `MYVAR="somevalue"`
- No spaces around `=`: `MYVAR=v` not `MYVAR = v`
- No inline comments: put `# explanation` on its OWN line above, NOT trailing the value line
- No CRLF / BOM: save UTF-8 without BOM (Notepad on Windows adds BOM — use VS Code / Notepad++ instead)

### 2. Map in `docker-compose.yml` under the appropriate service's `environment:` block

```yaml
  api:
    ...
    environment:
      ...
      MYVAR: ${MYVAR:-default-if-unset}
```

The `${VAR:-default}` form means: substitute from root `.env` if defined, else use the default. Keeps the container working even when an operator forgets to set the var.

### 3. Recreate the container (NOT just restart)

```bash
docker compose up -d api
```

**`docker compose restart api` does NOT pick up new `environment:` mappings.** It restarts the SAME container with the SAME env. You need `up -d` to recreate the container with the new env block.

## Verify the env actually reached the container

```bash
docker compose exec -T api sh -c 'env | grep MYVAR'
```

Should print `MYVAR=somevalue`. If it prints `MYVAR=` (empty) → `.env` value missing or compose mapping missing or container needs recreate. If it prints nothing → no mapping in compose at all.

## Gotchas catalog (from real incidents)

### Trailing comments in `.env` values
```
NTFY_BASE_URL=https://ntfy.sh           # or http://<self-host>   ← BAD
```
The whole post-`=` is the value, including the whitespace + comment. Container ends up with `NTFY_BASE_URL` = `"https://ntfy.sh           # or http://<self-host>"`. Move the comment to its own line above:
```
# Public ntfy.sh or http://<self-host>
NTFY_BASE_URL=https://ntfy.sh
```

### Gmail App Password has spaces
Google shows the 16-char password as `xxxx xxxx xxxx xxxx`. Strip the spaces before pasting into `.env`:
```
GMAIL_SMTP_APP_PASSWORD=xxxxxxxxxxxxxxxx    # 16 chars, no spaces
```

### `docker compose restart` vs `up -d`
- `restart` = restart SAME container — no env reload, no image reload
- `up -d` = recreate container with current compose config — picks up env changes
- `up -d --build` = rebuild image first, then recreate — needed for dependency changes (e.g., adding a package to `pyproject.toml`)

### File location: root `.env` vs `api/.env`
- ROOT `.env` is what docker compose reads — for `${VAR}` substitution
- `api/.env` is what pydantic-settings reads when uvicorn runs OUTSIDE the container (local-dev mode) — see `api/.env.example` header
- For container-based development (the standard path), only root `.env` matters

### `.env.example` documentation is split-brain (cleanup followup)
Today there are TWO `.env.example` files: root (compose substitution vars) and `api/.env.example` (pydantic-settings local-dev). Operators often confuse them. Consolidation tracked as a followup on #1449 (or its successor).

## Where current env vars live

All container-bound vars are in ROOT `.env`. The api service maps:

| Env var | Source | Purpose |
|---|---|---|
| `DATABASE_URL` | constructed in compose from `POSTGRES_PASSWORD` | DB connection |
| `APP_ENV` / `APP_DEBUG` | root `.env` | runtime flags |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | root `.env` | LLM provider keys |
| `BACKUP_*` | root `.env` | off-site backup config (#959) |
| `GMAIL_SMTP_*` + `DIGEST_EMAIL_*` | root `.env` | digest email channel (#1217) || `WEB_BASE_URL` | root `.env` | deep-link generation in emails / pushes |
| `SECRET_KEY` | root `.env` | itsdangerous token signing (#1437) |

For exact compose mapping lines, see `docker-compose.yml` `api.environment:` block.

## When env propagation fails — debug checklist

1. **Is the var in root `.env`?** `grep MYVAR .env`
2. **Is the var mapped in compose?** `grep MYVAR docker-compose.yml`
3. **Was the container recreated after the mapping was added?** `docker compose ps api` — `CREATED` column should be AFTER the mapping landed
4. **Does the container see the var?** `docker compose exec -T api sh -c 'env | grep MYVAR'`
5. **Is the value parsed correctly (no inline comment / spaces / BOM)?** `... | sed "s/=.*/=<set-len-$(echo -n \"\$MYVAR\" | wc -c)>/"` — length should match expected value

If 1+2+3+4 all check out but value is wrong: it's the parsing-the-value step (Gotchas above).

## Connects to

- `#1217` Gmail SMTP digest (caught the trap)
- `#1437` opt-out (itsdangerous added → required `docker compose up -d --build api` rebuild)
- `#1449` this doc + cleanup-of-the-discoverability-gap parent task
