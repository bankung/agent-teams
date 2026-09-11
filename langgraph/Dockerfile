## langgraph engine container — Kanban #3170 (prod-image hardening).
##
## Multi-stage `base` -> `dev`/`prod`, mirroring api/Dockerfile's pattern
## (Kanban #2529): `dev` keeps today's editable-install + uvicorn --reload
## workflow byte-equivalent; `prod` adds a non-root runtime user and no
## --reload. docker-compose.yml pins `target: dev` on the langgraph service;
## docker-compose.prod.yml and .github/workflows/release-images.yml pin
## `target: prod`.
##
## At runtime (dev + docker-compose.prod.yml, which both inherit the base
## service's `.:/repo` bind mount), docker-compose overlays the repo root at
## /repo, overlaying the COPY below so edits in langgraph/*.py trigger
## uvicorn --reload in dev. The COPY is still required because `pip install
## -e .` needs the package layout (py-modules + tools/ subpackage listed in
## pyproject.toml) to resolve at build time.

# refreshed 2026-06-26 (#2729) — re-pull/re-pin periodically to pick up Debian patches
FROM python:3.12-slim@sha256:6c4dd321d176d61ea848dc8c73a4f7dbae8f70e0ee48bb411ea2f045b599fa8e AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /repo/langgraph

# System deps:
# - git: required by the specialist tool layer (Kanban #977 → git_diff,
#   git_status, git_commit). The container needs to invoke git as a
#   subprocess against the bind-mounted /repo worktree.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Kanban #3170 discovery: git 2.35.2+'s CVE-2022-24765 mitigation refuses to
# operate on a repo whose directory owner differs from the running uid
# ("dubious ownership") — the prod stage's non-root appuser (below) would hit
# this against the host-owned bind-mounted /repo, breaking git_diff/git_status/
# git_commit (Kanban #977). --system (not --global) so it applies without a
# $HOME — appuser is created with --no-create-home. Harmless for the dev
# stage too (root historically hit the same check on some hosts).
RUN git config --system --add safe.directory /repo

EXPOSE 8000

# -----------------------------------------------------------------------------
# dev — default local workflow. Behavior unchanged from the pre-#3170 image.
# -----------------------------------------------------------------------------
FROM base AS dev

# Copy the package layout. setuptools needs the py-modules listed in
# pyproject.toml (graph, state, nodes, llm, worker, hitl, content_safety,
# gemini_schema) and the tools/ subpackage visible at install time.
COPY . /repo/langgraph/

RUN pip install --upgrade pip \
 && pip install -e .

# uvicorn --reload watches /repo/langgraph (the WORKDIR, overlaid by the compose
# bind-mount) and restarts on source edits.
# Kanban #2136: exclude scenarios/ from reload-watch — editing regression_pack.py
# mid-run would otherwise restart the server and kill the in-flight worker task.
CMD ["uvicorn", "graph:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-exclude", "scenarios/*"]

# -----------------------------------------------------------------------------
# prod — hardened image (Kanban #3170, same pattern as api's #2529):
# non-editable install, non-root user, no --reload.
# -----------------------------------------------------------------------------
FROM base AS prod

# --chown=1000:1000 bakes ownership into this COPY layer directly (numeric ids
# resolve without a /etc/passwd lookup, so this works even though appuser
# doesn't exist until the RUN below creates it), same reasoning as
# api/Dockerfile — avoids a separate recursive chown layer duplicating the
# tree's size.
COPY --chown=1000:1000 . /repo/langgraph/

# Non-editable install (no "[test]" extra — pytest/pytest-asyncio/respx have
# no runtime purpose in a shipped image and are skipped automatically since
# nothing pulls them in without the extra).
RUN pip install --upgrade pip \
 && pip install --no-cache-dir .

# Non-root runtime user, EXPLICIT uid/gid 1000 (matches api's prod user, #2529)
# — a host checkout already chown'd 1000:1000 for the api service covers this
# service's bind-mounted /repo too, so no separate one-time fix is needed.
# Windows Docker Desktop bind-mounts are permissive regardless of uid either
# way. Unlike api, this image does NOT chown /repo itself: docker-compose.images.yml
# (the npx GHCR pull-based install) mounts no volume at /repo for langgraph —
# it "uses baked paths / LANGGRAPH_* vars and does not write to /repo" (see
# that file's langgraph service comment) — so only /repo/langgraph's own
# directory entry needs fixing up (WORKDIR in `base`, above, pre-creates it as
# root before this COPY runs, and --chown on a COPY only tags the content it
# copies in, not a pre-existing destination directory's own entry).
RUN groupadd --gid 1000 appuser \
 && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin appuser \
 && chown appuser:appuser /repo/langgraph
USER appuser

CMD ["uvicorn", "graph:app", "--host", "0.0.0.0", "--port", "8000"]
