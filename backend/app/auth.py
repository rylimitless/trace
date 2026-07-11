"""Auth primitives: bcrypt passwords, signed-cookie sessions, role deps,
and the per-batch capture-token generator.

All cookie read/write happens here through one itsdangerous serializer (built
from ``settings.session_secret``), so encode/decode stay consistent regardless
of whether the caller is a route handler (``set_session``), a dependency
(``current_user``), or the logout endpoint (``clear_session``). No Starlette
``SessionMiddleware`` is used — the signed cookie carries only the user id,
which we resolve against the DB on each protected request.

Role dependencies are produced by factories so the router can declare them
inline (``Depends(require_admin)`` or ``Depends(require_buyer(UserRole.composter))``)
and Task 7 can reuse them on every REST route.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Batch, User, UserRole

# Cookie name — exported so routes (logout, tests) reference one constant.
SESSION_COOKIE = "trace_session"
# Max cookie age in seconds (24h), mirrored as the serializer's max_age.
SESSION_MAX_AGE = 24 * 60 * 60

# One serializer for the whole app. Built once at import: settings is already
# a constructed Settings instance with a populated session_secret.
_serializer = URLSafeTimedSerializer(settings.session_secret, salt="trace-session")


# ---------------------------------------------------------------------------
# Password hashing (bcrypt)
# ---------------------------------------------------------------------------


def hash_password(pw: str) -> str:
    """Return a bcrypt hash of ``pw`` (string, includes cost + salt)."""
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(pw: str, hashed: str) -> bool:
    """Return True iff ``pw`` matches the stored ``hashed`` value.

    Constant-time comparison is provided by bcrypt. The hash may be stored as
    str (DB column) or bytes; both are handled.
    """
    if isinstance(hashed, str):
        hashed = hashed.encode("utf-8")
    return bcrypt.checkpw(pw.encode("utf-8"), hashed)


# ---------------------------------------------------------------------------
# Signed-cookie session (itsdangerous)
# ---------------------------------------------------------------------------


def set_session(response, user_id: int) -> None:
    """Sign ``user_id`` into the session cookie on ``response``.

    The cookie is httpOnly (no JS access), SameSite=lax (sent on top-level
    GET navigations, not on cross-site POSTs), and scoped to ``/``. The signed
    value is opaque to the client; only the server can read/verify it.
    """
    token = _serializer.dumps({"uid": user_id})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_session(response) -> None:
    """Delete the session cookie from ``response`` (idempotent)."""
    response.delete_cookie(SESSION_COOKIE, path="/")


def _read_session_uid(request: Request) -> int | None:
    """Return the verified user id from the request cookie, or None.

    None covers all failure modes (no cookie, tampered, expired) — callers
    turn that into the appropriate HTTP status.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid")
    return uid if isinstance(uid, int) else None


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Resolve the signed session cookie to a ``User`` row or raise 401.

    Used directly by the role dependencies below; safe to use as a Depends
    target anywhere that wants "the caller's User".
    """
    uid = _read_session_uid(request)
    if uid is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = db.get(User, uid)
    if user is None:
        # Cookie is valid but the user was deleted — treat as not authenticated.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


# ---------------------------------------------------------------------------
# Role dependencies (factories returning Depends-compatible callables)
# ---------------------------------------------------------------------------


def _require_role(*allowed: UserRole):
    """Build a dependency that 401s without a session, 403s on a wrong role.

    Shared by the public factories; ``allowed`` is the set of roles that pass.
    """

    def _dep(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden",
            )
        return user

    return _dep


def require_admin(user: User = Depends(current_user)) -> User:
    """Allow only ``admin`` role users; 401 without session, 403 otherwise."""
    if user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
        )
    return user


def require_buyer(role: UserRole):
    """Factory: return a dependency that allows only the given buyer role.

    Usage: ``Depends(require_buyer(UserRole.premium_buyer))``.
    The role enum is ``admin | premium_buyer | secondary_buyer | composter``;
    pass the specific buyer role the route is for.
    """
    return _require_role(role)


def require_composter(user: User = Depends(current_user)) -> User:
    """Allow only ``composter`` role users (shorthand for require_buyer)."""
    if user.role != UserRole.composter:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
        )
    return user


# ---------------------------------------------------------------------------
# Capture token (per-batch, time-boxed)
# ---------------------------------------------------------------------------

CAPTURE_TOKEN_TTL = timedelta(hours=24)


def generate_capture_token(db: Session, batch: Batch) -> str:
    """Issue a fresh capture token on ``batch``: write a urlsafe random token,
    set a 24h expiry, commit, and return the token.

    The token authenticates a farmer's Telegram capture upload for a single
    batch (Task 6). Regenerating replaces any prior token.
    """
    batch.capture_token = secrets.token_urlsafe(32)
    batch.capture_token_expires_at = (
        datetime.now(timezone.utc) + CAPTURE_TOKEN_TTL
    )
    db.commit()
    return batch.capture_token
