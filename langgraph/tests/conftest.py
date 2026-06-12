"""pytest conftest — namespace-collision fix (Kanban #986).

The local project dir `/repo/langgraph/` contains an empty `__init__.py`,
and pytest's rootpath ascension picks up `/repo` as a sys.path entry so it
can import test modules under the dotted name `langgraph.tests.test_*`.
This makes the local `langgraph/__init__.py` win over the upstream LangGraph
PyPI package's namespace-package layout — any `from langgraph.X import ...`
that hasn't been pre-resolved fails with `ModuleNotFoundError`.

The fix: at conftest-load time (BEFORE test modules + the local `hitl` /
`worker` modules are imported), replace the half-loaded local `langgraph`
package shim in sys.modules with a synthetic namespace module pointing at
the upstream tree. From there importlib resolves `langgraph.checkpoint`,
`langgraph.types`, `langgraph.graph`, etc. correctly because Python's regular
package-loader walks the upstream tree from `__path__`.

This is a workaround, not a structural fix — the proper fix is to either
rename the project dir (breaks the `:/repo` bind-mount path) or remove the
empty `__init__.py` (which broke a Kanban permission gate; not attempted
here). Both are bigger-scope changes; conftest workaround keeps #986 surgical.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_working_path_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic default for the #2215 working_path resolution + caches.

    Two jobs, both per-test (mirrors the #2187 cache-isolation lesson):

      1. Reset the per-project working_path cache (`nodes._working_path_cache`)
         and the operator-allowlist cache (`sandbox._allowlist_cache`) so a
         value cached by one test never leaks into the next.
      2. Default-stub `nodes._fetch_project_working_path` to return None so a
         test that drives `backend_specialist_node` with a real project_id does
         NOT make a live httpx GET to the API. Tests that want to exercise the
         real precedence override this stub themselves (same pattern the loop
         tests use for `_fetch_tools_config`).

    Imports are best-effort so this fixture is a no-op in the rare case the
    modules can't load (keeps unrelated import-error tests from cascading).
    """
    try:
        import nodes

        nodes._working_path_cache_clear()

        async def _no_working_path(project_id):  # type: ignore[no-untyped-def]
            return None

        monkeypatch.setattr(nodes, "_fetch_project_working_path", _no_working_path)
    except Exception:
        pass
    try:
        from tools import sandbox

        sandbox._allowlist_cache_clear()
    except Exception:
        pass
    try:
        # Kanban #2327 — clear effort-overrides cache alongside the allowlist
        # cache so a tmp-path override from one test never leaks into the next.
        import worker as _w

        _w._effort_overrides_cache_clear()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _strip_session_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove LANGGRAPH_SESSION_ID from the environment for every test.

    The langgraph container now runs with LANGGRAPH_SESSION_ID set to a real
    session id (usage metering, Kanban #2135).  Worker tests use mock httpx
    transports and their handlers hard-code expected URL paths that do NOT
    include /api/sessions/<id>/runs — so when the ambient env var leaks in,
    the worker POSTs to a URL the mock never registered and the test fails with
    'unexpected request'.  Production behaviour is correct; the tests must not
    inherit the host env.

    Tests that specifically exercise session-id behaviour (e.g. an integration
    test for the usage-reporting path) set LANGGRAPH_SESSION_ID themselves via
    monkeypatch.setenv inside that test.
    """
    monkeypatch.delenv("LANGGRAPH_SESSION_ID", raising=False)

_SITE_PKGS = Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"


def _install_upstream_langgraph() -> None:
    upstream_root = _SITE_PKGS / "langgraph"
    if not upstream_root.is_dir():
        # Either we're running outside the langgraph container, or the
        # upstream pkg is uninstalled. Either way: nothing to fix.
        return
    # Synthetic namespace-package module that points at the upstream tree.
    # Replacing sys.modules['langgraph'] is sufficient — the regular import
    # machinery uses sys.modules cache first, so subsequent
    # `from langgraph.types import ...` walks `__path__` (set below) and
    # finds the real submodules.
    sys.modules.pop("langgraph", None)
    mod = types.ModuleType("langgraph")
    mod.__path__ = [str(upstream_root)]  # type: ignore[attr-defined]
    mod.__file__ = None  # type: ignore[assignment]
    sys.modules["langgraph"] = mod


_install_upstream_langgraph()

# Post-install diagnostic — only fires if the shim above thought install
# succeeded but `from langgraph.types import ...` would still fail. The most
# likely cause is a venv layout mismatch: this conftest hardcodes the Unix
# CPython layout (`lib/pythonX.Y/site-packages`), while Windows venvs use
# `Lib/site-packages` (capital L, no python-version segment).
import importlib.util  # noqa: E402

if importlib.util.find_spec("langgraph.types") is None:
    raise ImportError(
        "langgraph upstream import failed after install — likely a venv-layout mismatch "
        "(Windows uses Lib/site-packages, Unix uses lib/pythonX.Y/site-packages). "
        "Inspect _install_upstream_langgraph() in this file."
    )
