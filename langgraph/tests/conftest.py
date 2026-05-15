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
