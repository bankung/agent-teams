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

# No system deps yet — pure-Python wheel install. Keep the apt-get block as a
# placeholder so #850 can add build-essential / libpq-dev if a sdist needs it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
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
