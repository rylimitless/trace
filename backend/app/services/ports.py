"""Cross-slice interface ports.

Slice D (orchestration) needs four functions owned by other slices:

* ``get_batch_photo(batch) -> bytes``  — Slice B (intake) stores the upload.
* ``send_farmer_update(chat_id, event)`` — Slice B (intake) messaging.
* ``grade(image_bytes, crop) -> dict``  — Slice C (grading).
* ``simulate_decay(image_bytes) -> bytes`` — Slice C (grading).

Those slices land on their own branches. To keep Slice D **mergeable and
unit-testable independently**, this module resolves each function lazily at
call time:

* If the owning module exists, its real function is used.
* If it does not yet exist, a clear ``PortNotImplemented`` is raised — except
  in tests, where callers monkeypatch these helpers (see ``conftest.py``).

Slice D code imports the *names* from here, never the underlying modules
directly, so the seam is stable regardless of merge order.
"""

from __future__ import annotations

from typing import Any, Callable


class PortNotImplemented(RuntimeError):
    """A cross-slice port's owning module has not landed yet."""


def _resolve(module: str, attr: str) -> Callable[..., Any]:
    """Return the real callable from ``module.attr`` if importable, else a stub.

    The returned stub raises :class:`PortNotImplemented` when called, so import
    never fails but a missing port fails loudly at call time (or is monkey-
    patched by tests).
    """

    try:
        import importlib

        mod = importlib.import_module(module)
        return getattr(mod, attr)
    except Exception:
        def _missing(*_args: Any, **_kwargs: Any) -> Any:
            raise PortNotImplemented(
                f"{module}.{attr} is not available yet — the owning slice "
                f"has not landed. Monkeypatch app.services.ports.{attr} for tests."
            )

        _missing.__name__ = attr
        return _missing


# Resolved lazily on first call so importing this module never fails.
def get_batch_photo(batch: Any) -> bytes:
    """Return the stored farm photo bytes for ``batch`` (Slice B)."""
    return _resolve("app.services.messaging", "get_batch_photo")(batch)


def send_farmer_update(chat_id: str, event: dict) -> None:
    """Send a category-framed update to a farmer (Slice B)."""
    return _resolve("app.services.messaging", "send_farmer_update")(chat_id, event)


def grade(image_bytes: bytes, crop: str = "tomato") -> dict:
    """Grade a produce photo (Slice C). Returns ``{grade, reason}``."""
    return _resolve("app.services.grading", "grade")(image_bytes, crop)


def simulate_decay(image_bytes: bytes) -> bytes:
    """Degrade a photo to simulate transit spoilage (Slice C)."""
    return _resolve("app.services.grading", "simulate_decay")(image_bytes)
