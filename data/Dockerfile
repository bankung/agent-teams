# Preinstalled Python data-analysis sandbox for the data-analytics team (Kanban #1307).
# Idle container — data-analytics agents `docker compose exec` into it to run
# pandas/polars/duckdb scripts against the bind-mounted repo. Preinstalled so
# agents never `pip install` at runtime (no network dependency mid-task, no
# version drift between runs).
#
# Single-stage on python:3.12-slim, same pattern as api/ and langgraph/ Dockerfiles.
# No curl/git — this service has no HTTP endpoint (healthcheck is a python -c
# import check) and doesn't shell out to git.

# Same digest api/Dockerfile + langgraph/Dockerfile pin (refreshed 2026-06-26, #2729) —
# keeps all three Python services on the identical Debian patch level.
FROM python:3.12-slim@sha256:6c4dd321d176d61ea848dc8c73a4f7dbae8f70e0ee48bb411ea2f045b599fa8e

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg

WORKDIR /repo

# Versions verified against PyPI on 2026-07-10 (`pip index versions <pkg>`).
# numpy/pandas/scipy/matplotlib pinned to the latest patch of a settled minor
# line (via ~=) rather than that day's absolute-newest release — the
# numpy-ABI-coupled scientific stack needs matching compiled wheels across
# packages, and picking each package's literal tip risks a combo where one
# wheel hasn't caught up yet, forcing a slow from-source build. duckdb /
# polars / plotly / openpyxl / ipykernel / jupyter-client have no numpy-ABI
# coupling, so those are pinned exact.
RUN pip install --upgrade pip \
 && pip install \
      numpy~=2.2.0 \
      pandas~=2.3.0 \
      scipy~=1.15.0 \
      matplotlib~=3.10.0 \
      polars==1.34.0 \
      duckdb==1.5.4 \
      plotly==6.9.0 \
      openpyxl==3.1.5 \
      ipykernel==7.3.0 \
      jupyter-client==8.9.1

# Idle by design — compose `command` overrides this too; kept sensible for
# `docker run` outside compose (mirrors api/Dockerfile's CMD comment).
CMD ["tail", "-f", "/dev/null"]
