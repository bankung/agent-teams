# Design Memo — Mode-B Runtime/Dependency Gap: per-project `runtime_config` + engine-built per-project image (Option 1)

**Kanban:** #1652 · **Decision owner:** operator (Option 1 selected, PHASED) · **Date:** 2026-06-02 · **Status:** design deliverable (read-only; no code written). Produced by a read-only Plan agent; promoted by Lead.

## (A) Re-verification of the 2026-05-29 finding — CONFIRMED

Mode B (langgraph headless engine) executes project tools as subprocesses INSIDE the `langgraph` container; a binary dep (e.g. ffmpeg) can only be added by editing `langgraph/Dockerfile` + rebuilding the shared image = a CORE edit (violates "adopters add only agents/teams, never edit core"). Mode A is unaffected (host PATH). Citations:
- `langgraph/tools/shell/shell_run.py:209-214` — `asyncio.create_subprocess_exec(*tokens, cwd=working_path or repo_root)`; missing binary → `FileNotFoundError` / `error_code="executable_not_found"` (`:215-221`) = opaque mid-run failure today.
- Exec path: `worker.py:337` (`compiled.ainvoke`) → `nodes.py:186/644` (`_handle_one_tool_call` → `tool.invoke`); `InvokeContext` built `nodes.py:226-232`.
- `langgraph/Dockerfile:17-19` installs only `git`; image built once (`docker-compose.yml:211-215`) + shared across projects → editing it = the anti-pattern.
- **No build/runtime-isolation infra exists today** — no Docker socket, no `docker build` subprocess in `langgraph/`. So Option 1's "engine builds a per-project image" is NET-NEW infra.
- Mode A unaffected; binary-dep projects can run Mode A today with zero platform change.

## (B) Option-1 design

### B.1 `runtime_config` JSONB on `projects`
Nullable JSONB, mirroring `tools_config`/`agent_overrides`/`health_thresholds` conventions. **NULL = "stock image, no per-project build" = today's behavior** (a project that never sets it is byte-for-byte unchanged). Shape (declarative ONLY):
```json
{ "base_image": "agent-teams/langgraph-runtime:py3.12", "apt_packages": ["ffmpeg"], "pip_packages": ["yt-dlp==2024.8.6"], "env": {"FFMPEG_THREADS": "2"} }
```
- Pydantic `RuntimeConfig` (`extra="forbid"`): `base_image` = `Literal` over a base-image ALLOWLIST (lockstep test vs a langgraph-side allowlist); `apt_packages`/`pip_packages` validated against a name+pin regex (reject URLs, `git+`, shell metachars, unpinned pip); `env` keys `^[A-Z_][A-Z0-9_]{0,63}$` with a denylist for engine secrets (`ANTHROPIC_API_KEY`, `DATABASE_URI`, `CREDENTIALS_MASTER_KEY`, `LANGGRAPH_*`).
- Create: OMIT-when-None. Update: key-absent=unchanged, dict=REPLACE (no deep merge), explicit-null=CLEAR to NULL (null-stays-null, like `notification_targets` — NOT coerce-to-{} ). Read: value-tolerant `dict | None` (never 500 a read on a hand-edited row).
- Migration: one nullable `ADD COLUMN` (metadata-only on PG16, no backfill). Revision id ≤32 chars. Add to `db-schema.md`.

### B.2 Engine image-build flow
- **Cache key:** `sha256(canonical_json(runtime_config))[:12]`; image `agent-teams/langgraph-proj-{id}:{hash}`. Hash-in-tag → config edit = new tag (auto cache-invalidation); unchanged config = reuse, zero rebuild. NULL config → no build (no-op path).
- **Build placement (3 candidates):** (a) Docker-socket-in-worker → **REJECT** (root-equiv host compromise; LLM-driven container must not hold the daemon socket). (b) sibling NOT-LLM-facing "engine-builder" service that owns Docker access → recommended if on-demand build is needed. (c) pre-build out-of-band at config-set time; worker only SELECTS a tag + fails clean if absent → **recommended Phase-2 v1** (removes daemon access from runtime entirely).
- **Execution:** per-task launch of the per-project image with the stock env contract + `runtime_config.env`. The per-project image = stock engine layer + extra apt/pip layers (`FROM` allowlisted base `FROM` stock engine), so graph/worker/tool source + all safety layers are UNCHANGED.

### B.3 SECURITY / blast-radius (the dangerous part)
Building images from adopter config = supply-chain + code-exec surface. Required mitigations:
1. **Declarative fields ONLY** — engine generates the Dockerfile; no raw Dockerfile/RUN/COPY/build-arg passthrough (same philosophy as the hardcoded `shell_run` allow/denylist).
2. **Base-image allowlist** enforced both sides (Pydantic Literal 422 + builder re-check), pinned digests, no arbitrary `FROM`.
3. **Package-source pinning** — apt: distro repos only; pip: `--require-hashes`/`==` pins, reject URL/git/local.
4. **Build sandbox + limits** — no network beyond pinned mirrors, wall-clock timeout, CPU/mem/disk caps; over-limit → fail clean → project stays Mode-A-only.
5. **Who can set `runtime_config`** — higher-privilege than `tools_config`. The platform has NO operator-vs-AI write distinction today (only `X-Project-Id`) → an autonomous agent that can PATCH `runtime_config` defines the image it then runs in. Until that distinction exists, `runtime_config` writes require explicit OPERATOR action (typed-ack endpoint, mirror `ProjectGrantConsent`), NOT autonomous PATCH. **Blocking prerequisite.**
6. **Per-project image inherits engine secrets** — scope its env to the minimum (it does NOT need `CREDENTIALS_MASTER_KEY`); rely on 1-4 to keep layers benign.

### B.4 No-core-edit + interaction
Adopter declares deps in `projects.runtime_config` (a DB row) — exactly where they already declare `tools_config`/budgets/`approval_policies`. CORE `langgraph/Dockerfile` edited ZERO times. **Unchanged:** worker loop, graph/nodes, all safety layers, `shell_run` allow/denylist (NOTE: baking a binary does NOT widen the command allowlist — `ffmpeg` baked is necessary but NOT sufficient to invoke; that's a separate per-project allowlist hook). **Net-new:** the JSONB column + plumbing (small), and the build-and-tag pipeline + lifecycle (the substantial piece). `env` is NOT a secret store (secrets stay in the vault; enforced by the env-key denylist).

### B.5 Phased rollout (the decision)
- **Phase 1 — Option-2 guard (UNBLOCK Mode B now, no build infra):** add `required_binaries` (or `runtime_config.required_binaries`) + a worker pre-pickup check (`shutil.which`); missing → PATCH task BLOCKED with clear `halt_reason=runtime_prereq_missing` naming the binary + "Mode-A-only until #1652 Phase 2". Reuses the L17 pre-pickup-gate pattern (`worker.py:246-271`). Unblocks Mode B for the common case (no binary deps); binary-dep projects get a crisp documented Mode-A-only status instead of opaque mid-run failure. **~days, low risk.**
- **Phase 2 — build Option 1 when a real binary-dep project (e.g. papillon-pod) needs headless:** full `runtime_config` schema (B.1, ~1 day, mechanical) + build/launch pipeline + security hardening (B.3) = **Medium-Large, ~1.5-3 weeks, Medium-High risk concentrated in the build surface.** Security review = hard gate before any adopter-set config triggers a real build.

### B.6 Open questions before Phase-2 build
1. Operator-vs-AI auth on `runtime_config` writes (blocking for B.3 #5).
2. Build placement (c) pre-built vs (b) sibling builder.
3. Base-image allowlist contents (few curated bases vs composable apt).
4. Does baking a binary also broaden the `shell_run` command allowlist? (else baked `ffmpeg` still can't run).
5. Image lifecycle / GC + disk budget for hash-tagged images.
6. Single-project-per-env (today) vs multi-project (ties #1191).
7. Build-time network policy (offline vs allowlisted mirrors).

### Critical files (Phase-2 implementation)
- `api/src/models/project.py` (column, mirror `tools_config`) · `api/src/schemas/project.py` (`RuntimeConfig` + Create/Update/Read) · `api/src/routers/projects.py` (omit-when-None + null-stays-null) · `langgraph/worker.py` (Phase-1 pre-pickup check + Phase-2 tag resolution before `ainvoke`) · `langgraph/Dockerfile` + `docker-compose.yml` (base layer + build/launch wiring).
