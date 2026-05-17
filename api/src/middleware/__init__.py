"""HTTP middleware package.

Kanban #1115 (2026-05-17, L18 prevention) — payload-size cap to defend
against hammer-test FINDING #10 (T-DOS-1: API accepted 10MB body with no
guard at any layer).
"""
