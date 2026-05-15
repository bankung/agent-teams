## langgraph engine container — Phase 4 (Kanban #851 scaffold; #850 fills in graph.py)
##
## When #850 lands and replaces graph.py with a real LangGraph StateGraph, the CMD
## below will change to a langgraph server invocation (e.g.
##   ["langgraph", "dev", "--host", "0.0.0.0", "--port", "8000"]
## or a custom uvicorn entrypoint wrapping the compiled graph). For #851 we ship a
## minimal FastAPI stub so `docker compose up langgraph` is healthy before the
## graph implementation lands.

FROM python:3.12-slim

WORKDIR /app

# System deps:
# - git: required by the specialist tool layer (Kanban #977 → git_diff,
#   git_status, git_commit). The container needs to invoke git as a
#   subprocess against the bind-mounted /repo worktree.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Copy package metadata first so the pip layer caches across graph.py edits.
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# Source. In dev the compose bind-mount `.:/repo` overlays the langgraph/ dir
# so edits hot-reload via uvicorn --reload (added when #850 lands).
COPY . .

EXPOSE 8000

# Stub entrypoint — boots a FastAPI shell exposing GET /ok. #850 will replace
# this with a real langgraph server invocation.
CMD ["uvicorn", "graph:app", "--host", "0.0.0.0", "--port", "8000"]
