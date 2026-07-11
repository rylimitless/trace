"""Service-layer package for TRACE.

Slice C (grading) owns ``grading``.
Slice D (orchestration) owns: ``aggregation``, ``routing``, ``handoff``,
``scheduler``, ``justify``. Slice B (intake) will own ``messaging``.
Slice D imports through :mod:`app.services.ports` for decoupling.
"""
