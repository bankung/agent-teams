"""Compatibility shim — constants moved to tools/base.py (Phase 1 minimization).

This file is preserved as a thin re-export so any surviving direct import
of `tools.iteration_limit` continues to work without modification. The
canonical home of these constants is now `tools.base`.
"""

from .base import MAX_TOOL_LOOP_ITERATIONS, TOOL_LOOP_HALT_REASON

__all__ = ["MAX_TOOL_LOOP_ITERATIONS", "TOOL_LOOP_HALT_REASON"]
