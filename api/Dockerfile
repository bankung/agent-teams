# Dev image for the agent-teams FastAPI backend.
# Single-stage on python:3.12-slim — fine for dev. Revisit (multi-stage,
# non-root user, no --reload) when we cut a prod image.
#
# At runtime, docker-compose bind-mounts the repo at /repo, overlaying the
# COPY below so edits in api/src/ trigger uvicorn --reload. The COPY is still
# required because `pip install -e .` needs the package layout to resolve
# [tool.setuptools.packages.find] at build time.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /repo/api

# asyncpg ships manylinux wheels for cpython 3.12 — no compiler needed.
# If a future dep forces a build, add: build-essential libpq-dev.

# curl is used by the compose healthcheck (GET /health) and is handy for
# ad-hoc `docker exec` debugging. ~5 MB on slim — acceptable for dev image.
# postgresql-client provides pg_dump for the nightly backup runner (Kanban #959)
# — ~25 MB; the wire-protocol version is compatible with the PG 16 server.
# Image hardening (drop curl, multi-stage) deferred per standards/docker/compose.md.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl postgresql-client \
 && rm -rf /var/lib/apt/lists/*

# Infisical CLI — opt-in (Kanban #2488). Off by default to keep the base image lean;
# docker-compose.secrets.yml sets INSTALL_INFISICAL_CLI=true. ~40 MB when enabled.
# CLI pinned to 0.43.96 (verified working 2026-06-23, #2488); bump if pruned from the repo.
ARG INSTALL_INFISICAL_CLI=false
RUN if [ "$INSTALL_INFISICAL_CLI" = "true" ]; then \
      curl -1sLf 'https://artifacts-cli.infisical.com/setup.deb.sh' | bash \
   && apt-get update && apt-get install -y --no-install-recommends infisical=0.43.96 \
   && rm -rf /var/lib/apt/lists/* ; \
    fi

# Copy the package layout first. setuptools.packages.find needs src/ and
# scripts/ visible at install time (see api/pyproject.toml).
COPY . /repo/api/

RUN pip install --upgrade pip \
 && pip install -e ".[dev]"

EXPOSE 8456

# Compose overrides this command (passes the same args), but keep it sensible
# for `docker run` outside compose.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8456", "--reload"]
