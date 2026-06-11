## langgraph engine container — dev image.
##
## At runtime, docker-compose bind-mounts the repo root at /repo, overlaying the
## COPY below so edits in langgraph/*.py trigger uvicorn --reload. The COPY is
## still required because `pip install -e .` needs the package layout (the
## py-modules + tools/ subpackage listed in pyproject.toml) to resolve at
## build time. Same pattern as the api service.

FROM python:3.12-slim

WORKDIR /repo/langgraph

# System deps:
# - git: required by the specialist tool layer (Kanban #977 → git_diff,
#   git_status, git_commit). The container needs to invoke git as a
#   subprocess against the bind-mounted /repo worktree.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Copy the package layout. setuptools needs the py-modules listed in
# pyproject.toml (graph, state, nodes, llm, worker, hitl, content_safety) and
# the tools/ subpackage visible at install time.
COPY . /repo/langgraph/

RUN pip install --no-cache-dir -e .

EXPOSE 8000

# uvicorn --reload watches /repo/langgraph (the WORKDIR, overlaid by the compose
# bind-mount) and restarts on source edits. Mirrors the api service.
# Kanban #2136: exclude scenarios/ from reload-watch — editing regression_pack.py
# mid-run would otherwise restart the server and kill the in-flight worker task.
CMD ["uvicorn", "graph:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-exclude", "scenarios/*"]
