"""Auth HTTP endpoints: login and logout only.

Login verifies credentials and signs the session cookie; logout clears it.
Every other route (batch capture, grading, payouts, admin timeline) is owned
by Task 7 and is gated by the role dependencies defined in ``app.auth``.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import clear_session, set_session, verify_password
from app.db import get_db
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(body: LoginBody, response: Response, db: Session = Depends(get_db)):
    """Authenticate by email + password; on success set the session cookie.

    Returns ``{"role": <role>}`` so the client can route to the right UI.
    A wrong email or password both resolve to the same 401 to avoid user
    enumeration.
    """
    user = db.query(User).filter(User.email == body.email).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    set_session(response, user.id)
    return {"role": user.role.value}


@router.post("/logout")
def logout(response: Response):
    """Clear the session cookie; always returns ``{"ok": true}``."""
    clear_session(response)
    return {"ok": True}
