"""Service-layer package for TRACE.

Slice D (orchestration) owns: ``aggregation``, ``routing``, ``handoff``,
``scheduler``, ``justify``. Slices B (intake) and C (grading) own their
modules; Slice D imports them through :mod:`app.services.ports` so it is
decoupled from their import-time availability.
"""
