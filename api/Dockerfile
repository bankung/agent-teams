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

# Copy the package layout first. setuptools.packages.find needs src/ and
# scripts/ visible at install time (see api/pyproject.toml).
COPY . /repo/api/

RUN pip install --upgrade pip \
 && pip install -e ".[dev]"

EXPOSE 8456

# Compose overrides this command (passes the same args), but keep it sensible
# for `docker run` outside compose.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8456", "--reload"]
