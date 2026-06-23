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
# CLI pinned to 0.43.96; supply-chain hardened (#2488 Phase 2): pinned .deb from
# github.com/Infisical/cli v0.43.96 release + SHA256 verified before install.
# To update: fetch the new checksums.txt from the Infisical/cli GitHub release, replace
# INFISICAL_DEB_SHA256 below, and update the version strings in the URLs.
ARG INSTALL_INFISICAL_CLI=false
RUN if [ "$INSTALL_INFISICAL_CLI" = "true" ]; then \
      INFISICAL_DEB_URL="https://github.com/Infisical/cli/releases/download/v0.43.96/infisical_0.43.96_linux_amd64.deb" \
   && INFISICAL_DEB_SHA256="bf563a5b9f1ac5aac29aeeab7c45d455ff18f175f01aea144743768271f1d10c" \
   && curl -fsSL -o /tmp/infisical.deb "$INFISICAL_DEB_URL" \
   && echo "${INFISICAL_DEB_SHA256}  /tmp/infisical.deb" | sha256sum -c - \
   && (dpkg -i /tmp/infisical.deb || apt-get install -y -f) \
   && rm -f /tmp/infisical.deb \
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
