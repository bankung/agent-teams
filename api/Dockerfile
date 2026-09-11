# Base image for the agent-teams FastAPI backend — shared setup for the
# `dev` and `prod` build targets below (Kanban #2529 — prod-image hardening).
#
# `dev` (today's workflow, unchanged): editable install, keeps uvicorn
# --reload. docker-compose bind-mounts the repo at /repo, overlaying the
# COPY below so edits in api/src/ trigger the reloader. The COPY is still
# required because `pip install -e .` needs the package layout to resolve
# [tool.setuptools.packages.find] at build time.
#
# `prod`: non-editable install pinned by requirements.lock, non-root user,
# no --reload (api/bin/infisical-entrypoint.sh's UVICORN_RELOAD switch keeps
# the same no-reload-by-default guarantee on the Infisical-wrapped path).
#
# Stages are named explicitly because Docker's implicit default target is
# whichever stage is LAST in the file. docker-compose.yml pins `target: dev`
# on the api/telegram-poller services and docker-compose.prod.yml pins
# `target: prod` — nothing relies on the default. `prod` is placed last by
# convention only.

# refreshed 2026-06-26 (#2729) — re-pull/re-pin periodically to pick up Debian patches
FROM python:3.12-slim@sha256:6c4dd321d176d61ea848dc8c73a4f7dbae8f70e0ee48bb411ea2f045b599fa8e AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /repo/api

# asyncpg ships manylinux wheels for cpython 3.12 — no compiler needed.
# If a future dep forces a build, add: build-essential libpq-dev.

# curl is used by the compose healthcheck (GET /health) and is handy for
# ad-hoc `docker exec` debugging. ~5 MB on slim — required by both targets.
# postgresql-client provides pg_dump for the nightly backup runner (Kanban #959)
# — ~25 MB; the wire-protocol version is compatible with the PG 16 server.
# Both stay in prod too — package-diet is out of scope for #2529.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl postgresql-client \
 && rm -rf /var/lib/apt/lists/*

# Infisical CLI — opt-in (Kanban #2488), usable from either target since it's
# installed in this shared `base` stage. Off by default to keep the image
# lean; docker-compose.secrets.yml sets INSTALL_INFISICAL_CLI=true (works
# against a dev or prod build). ~40 MB when enabled. CLI pinned to 0.43.96;
# supply-chain hardened (#2488 Phase 2): pinned .deb from
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

EXPOSE 8456

# -----------------------------------------------------------------------------
# dev — default local workflow. Behavior unchanged from the pre-#2529 image.
# -----------------------------------------------------------------------------
FROM base AS dev

# Copy the package layout first. setuptools.packages.find needs src/ and
# scripts/ visible at install time (see api/pyproject.toml).
COPY . /repo/api/

# requirements.lock pins the resolved transitive closure (Kanban #2440) so
# rebuilds are reproducible instead of floating pyproject's version ranges.
RUN pip install --upgrade pip \
 && pip install -e ".[dev]" -c requirements.lock

# Compose overrides this command (passes the same args), but keep it sensible
# for `docker run` outside compose.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8456", "--reload"]

# -----------------------------------------------------------------------------
# prod — hardened image (Kanban #2529): non-editable + lock-pinned install,
# non-root user, no --reload. Still a plain COPY + pip install (no separate
# builder-stage venv to copy forward): asyncpg/etc ship prebuilt manylinux
# wheels (see the "no compiler needed" note above) so there's no build-tool
# layer to strip by splitting further — this already IS the minimal shape.
# -----------------------------------------------------------------------------
FROM base AS prod

# --chown=1000:1000 bakes ownership into this COPY layer directly (numeric ids
# resolve without a /etc/passwd lookup, so this works even though appuser
# doesn't exist until the RUN below creates it) instead of a separate
# recursive `chown -R` afterward, which would duplicate the whole tree's size
# as its own layer on top of the COPY's (measured ~12.4MB duplicated layer via
# `docker history` before this change — Kanban #2529 review M3).
COPY --chown=1000:1000 . /repo/api/

# Non-editable + base deps only (no "[dev]" extra — pytest/pytest-asyncio/
# pip-audit/respx/moto have no runtime purpose in a shipped image and are
# skipped automatically since nothing pulls them in without the extra).
# requirements.lock still constrains whatever IS installed (Kanban #2440).
RUN pip install --upgrade pip \
 && pip install . -c requirements.lock

# Non-root runtime user, EXPLICIT uid/gid 1000 (not --system's auto-assigned
# <1000, e.g. 999): bin/install.sh prod mode on a Linux host bind-mounts the
# repo at /repo owned by the host user, which is uid 1000 for the default
# first Linux user account — a --system uid would fail runtime writes to
# /repo/context, /repo/_scratch (EMAIL_TOOLS_AUDIT_PATH), /repo/_runtime.
# Windows Docker Desktop bind-mounts are permissive regardless of uid either
# way; an existing Linux checkout predating #2529 needs a one-time chown too
# (see docker-compose.prod.yml's header).
# chown below is NON-recursive (both dir ENTRIES, not their contents):
# everything under /repo/api is already appuser-owned via COPY --chown above,
# so -R here would just re-walk and duplicate that layer's size for no
# benefit. The /repo/api entry itself still needs a direct (non-recursive)
# chown though — WORKDIR (in the shared `base` stage, above) pre-creates it as
# root BEFORE this COPY runs, and --chown on a COPY only tags the content it
# copies in, not a pre-existing destination directory's own entry (confirmed:
# without this, `ls -ld /repo/api` still showed root:root after COPY --chown).
# /repo itself needs the same treatment: docker-compose.images.yml (the npx
# GHCR pull-based install) mounts a NAMED volume at /repo instead of a source
# bind-mount, and Docker seeds a named volume's ownership from the image's
# content at that path on first use — a root-owned /repo there would block
# appuser from mkdir'ing context/_scratch/etc. at runtime. Source bind-mount
# modes (docker-compose.yml / docker-compose.prod.yml) overlay /repo entirely
# at container start regardless of image-baked ownership, so this chown is
# moot for them.
# shortcut: an EXISTING docker-compose.images.yml deployment upgrading from a
# root-run image to this non-root image keeps its OLD root-owned named volume
# — chown here only affects a volume's FIRST seed, not one that already
# exists. One-time fix documented next to the volume in docker-compose.images.yml.
RUN groupadd --gid 1000 appuser \
 && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin appuser \
 && chown appuser:appuser /repo /repo/api
USER appuser

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8456"]
