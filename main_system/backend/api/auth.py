"""Sign in, sign out, and who am I.

Shipped alongside the existing routes rather than in front of them: this
prompt adds the capability and proves it in isolation, and PROMPT-07 turns it
on for the rest of the API in one place. Guarding routes and building the
guard in the same change makes a failure ambiguous -- you cannot tell a broken
login from a mis-applied dependency.

Login responses are deliberately uniform. A different message or a different
response time for "no such account" versus "wrong password" turns the login
form into an account-enumeration oracle, which for a system whose users are
named investigators is a real exposure.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from backend.core import security
from backend.core.authz import (SessionConfigError, clear_session_cookie,
                                current_user, is_evaluator, issue_token,
                                set_session_cookie)
from backend.core.config import get_settings
from backend.models.db import ROLES, User, get_db, utcnow
from backend.services import audit as audit_service

router = APIRouter()
settings = get_settings()
log = logging.getLogger(__name__)

# Verifying against this when the account does not exist keeps the failed-login
# path the same shape either way, so response time does not reveal whether an
# email is registered. Generated once at import; its plaintext is never used.
_DUMMY_HASH: Optional[str] = None


def _dummy_hash() -> Optional[str]:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        try:
            _DUMMY_HASH = security.hash_password("not-a-real-password-placeholder")
        except Exception:                          # noqa: BLE001 - argon2 unavailable
            _DUMMY_HASH = ""
    return _DUMMY_HASH or None


class LoginRequest(BaseModel):
    # A plain normalised string rather than pydantic's EmailStr: the address is
    # a lookup key, not something we send mail to, and full RFC validation
    # would pull in another dependency to reject inputs that simply fail to
    # match a row anyway.
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def _normalise(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or v.startswith("@") or v.endswith("@"):
            raise ValueError("email must contain a local part and a domain")
        return v


class UserOut(BaseModel):
    id: int
    email: str
    display_name: Optional[str] = None
    role: str
    active: bool
    # True for the password-less public evaluator (OT_PUBLIC_EVALUATOR). The UI
    # labels the session as such and offers a real sign-in beside it.
    evaluator: bool = False

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(id=user.id, email=user.email, display_name=user.display_name,
                   role=user.role, active=bool(user.active),
                   evaluator=is_evaluator(user))


@router.post("/auth/login")
def login(request: Request, body: LoginRequest, response: Response,
          db: Session = Depends(get_db)):
    """Exchange credentials for an HttpOnly session cookie."""
    user = db.query(User).filter(User.email == body.email.lower()).one_or_none()

    if user is None or not user.active:
        # Same work, same answer, whether or not the account exists.
        security.verify_password(_dummy_hash(), body.password)
        audit_service.record(db, "auth.login", request=request,
                             resource=body.email, actor=body.email,
                             detail="failed: unknown or inactive account")
        raise HTTPException(401, "invalid email or password")

    if not security.verify_password(user.password_hash, body.password):
        # Recorded because a burst of these is the signal an operator needs;
        # the reason is kept vague in the RESPONSE, not in the log.
        audit_service.record(db, "auth.login", request=request,
                             resource=user.email, actor=user.email,
                             actor_user_id=user.id, detail="failed: bad password")
        raise HTTPException(401, "invalid email or password")

    try:
        token = issue_token(user)
    except SessionConfigError as exc:
        # A deployment that cannot sign safely must say so, not issue a weak
        # token and carry on.
        raise HTTPException(503, str(exc))

    # Opportunistic upgrade when argon2's parameters have moved on. The user
    # just proved the password, so this is the only moment it can be done.
    if security.needs_rehash(user.password_hash or ""):
        user.password_hash = security.hash_password(body.password)

    user.last_login_utc = utcnow()
    audit_service.record(db, "auth.login", request=request, resource=user.email,
                         actor=user.email, actor_user_id=user.id,
                         detail=f"signed in as {user.role}", commit=False)
    db.commit()

    set_session_cookie(response, token)
    return {"user": UserOut.of(user).model_dump()}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response,
           db: Session = Depends(get_db)) -> Response:
    """Drop the session cookie. Safe to call when not signed in.

    The injected `response` is mutated and returned; constructing a fresh
    Response here would discard the Set-Cookie header just written to it and
    leave the caller signed in.
    """
    # Recorded before the cookie is cleared, so the session still names the actor.
    audit_service.record(db, "auth.logout", request=request, detail="signed out")
    clear_session_cookie(response)
    response.status_code = 204
    return response


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    """The signed-in user. 401 when there is no valid session.

    In the public evaluator view a visitor without a session gets the
    evaluator account here instead of a 401.
    """
    return UserOut.of(user).model_dump()


@router.get("/auth/mode")
def mode():
    """Whether this deployment serves a public evaluator view. Public: the
    sign-in screen reads it to offer a way back to the evaluator view."""
    return {"public_evaluator": settings.public_evaluator,
            "evaluator_role": settings.evaluator_role if settings.public_evaluator else None}


def bootstrap_admin() -> Optional[str]:
    """Create the first administrator from the environment, once.

    Returns a message for the boot log, or None when there is nothing to do.
    Never rewrites an existing account: if the email is already present, the
    stored password stands. Otherwise anyone able to set an environment
    variable could silently reset an operator's credentials on restart.
    """
    email = (settings.admin_email or "").strip().lower()
    password = settings.admin_password or ""
    if not email or not password:
        return None

    from backend.models.db import SessionLocal

    with SessionLocal() as db:
        existing = db.query(User).filter(User.email == email).one_or_none()
        if existing is not None:
            return f"admin {email} already present; left unchanged"
        if db.query(User).count() > 0:
            # Bootstrapping is for an empty system only. A populated user table
            # with a new OT_ADMIN_EMAIL is more likely a mistake than intent.
            return (f"users already exist; not bootstrapping {email} "
                    f"(create the account through the admin API)")
        try:
            db.add(User(email=email, password_hash=security.hash_password(password),
                        display_name="Administrator", role="admin", active=True))
            db.commit()
        except ValueError as exc:
            return f"admin bootstrap refused: {exc}"
        return f"bootstrapped administrator {email}"


# Roles are published so the UI does not hardcode its own copy.
@router.get("/auth/roles")
def roles(user: User = Depends(current_user)):
    return {"roles": list(ROLES), "you": user.role}
