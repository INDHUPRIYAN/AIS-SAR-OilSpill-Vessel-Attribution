"""Sessions and role checks.

The system names vessels as suspects, so who did what has to be attributable.
That makes two properties non-negotiable here:

  * the actor comes from the session, never from the request body. An audit
    row whose actor a client can choose is decoration, not evidence;
  * the token lives in an HttpOnly cookie, not `localStorage`, so a single
    XSS bug cannot read it out of the page.

`SameSite=Lax` is what makes a cookie safe to use for mutating routes without
a separate CSRF token: the browser withholds it from cross-site POSTs. The dev
frontend on :5173 and the API on :8000 are the same site (same registrable
host, port is not part of same-site), so this works in development too --
`credentials: "include"` is still required on the fetch.

This module issues and reads sessions. It deliberately does NOT guard any
existing router: that happens in one place in `main.py` (PROMPT-07), because
per-route decoration is exactly how a route gets missed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.models.db import IMPLICIT_ROLES, ROLES, User, get_db

settings = get_settings()

# Below this a signed token is not meaningfully protected (RFC 7518 §3.2 for
# HS256). Checked at issue time rather than at import so a misconfigured
# deployment fails on the login attempt, with a message, instead of at boot.
MIN_SECRET_BYTES = 32


class SessionConfigError(RuntimeError):
    """The deployment cannot issue tokens safely."""


def _secret() -> str:
    secret = settings.jwt_secret or ""
    if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise SessionConfigError(
            f"JWT_SECRET (or SECRET_KEY) must be at least {MIN_SECRET_BYTES} bytes; "
            f"got {len(secret.encode('utf-8'))}. Refusing to sign sessions with it.")
    return secret


def issue_token(user: User) -> str:
    """A short-lived session token for `user`.

    The role is embedded so a request needs no user lookup to be rejected, but
    it is re-read from the database on every authenticated request anyway
    (`current_user`), so a demoted or deactivated account stops working at
    once rather than at token expiry.
    """
    import jwt

    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.jwt_ttl_hours)).timestamp()),
    }
    return jwt.encode(payload, _secret(), algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> Optional[dict]:
    """Claims, or None. Never raises for an invalid or expired token."""
    import jwt

    try:
        return jwt.decode(token, _secret(), algorithms=[settings.jwt_algorithm])
    except SessionConfigError:
        raise
    except Exception:                              # noqa: BLE001 - expired/tampered/malformed
        return None


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        max_age=settings.jwt_ttl_hours * 3600,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    # The deleting cookie has to match the attributes of the one it replaces
    # (path, samesite, secure) or the client keeps the original and the user
    # stays signed in after clicking sign out.
    response.delete_cookie(
        key=settings.session_cookie,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )


# --------------------------------------------------------------------------
# public evaluator view
# --------------------------------------------------------------------------

# Writes the anonymous evaluator may NOT make, even though it holds an
# administrative role. Everything else -- running and replaying investigations,
# recording decisions, opening incidents, composing reports -- is open, because
# the evaluation is of the workflow. These are the actions whose effect would
# outlive the demo or reach past it: replacing provider credentials, creating
# or changing accounts, deleting or re-staffing zones, driving the live AIS
# worker, and wiping logs.
EVALUATOR_BLOCKED_WRITES = (
    "/api/keys",
    "/api/users",
    "/api/ais/stream",
    "/api/ais/live/prune",
    "/api/logs/clear",
    "/api/incidents/backfill-zones",
)


def _evaluator_blocked(request: Request) -> bool:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return False
    path = request.url.path
    if any(path.startswith(p) for p in EVALUATOR_BLOCKED_WRITES):
        return True
    # Zone deletion and officer assignment are jurisdictional facts; the
    # evaluator may draw a new operational zone but not delete or re-staff one.
    if path.startswith("/api/zones/") and (
            request.method == "DELETE" or "/assignments" in path):
        return True
    return False


def evaluator_user(db: Session) -> User:
    """The shared evaluator account, created on first use.

    A real row rather than a synthetic object, so everything that records an
    actor (audit, decisions, incidents, reports) names the evaluator instead of
    "anonymous". It has no password hash, so it can never be signed into, and
    its role is re-applied here so a change to OT_EVALUATOR_ROLE takes effect.
    """
    email = settings.evaluator_email
    user = db.query(User).filter(User.email == email).one_or_none()
    role = settings.evaluator_role if settings.evaluator_role in ROLES else "admin"
    if user is None:
        user = User(email=email, password_hash=None, display_name="SIH Evaluator",
                    role=role, active=True)
        db.add(user)
        db.commit()
        db.refresh(user)
    elif user.role != role or not user.active:
        user.role = role
        user.active = True
        db.commit()
    return user


def is_evaluator(user: Optional[User]) -> bool:
    return bool(user is not None and settings.public_evaluator
                and user.email == settings.evaluator_email)


def _session_user(request: Request, db: Session) -> Optional[User]:
    token = request.cookies.get(settings.session_cookie)
    if not token:
        return None
    claims = decode_token(token)
    if not claims:
        return None
    try:
        user = db.get(User, int(claims.get("sub", 0)))
    except (TypeError, ValueError):
        return None
    # A valid token for a deleted or deactivated account is not a session.
    if user is None or not user.active:
        return None
    return user


def optional_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """The signed-in user, or None. For routes that adapt rather than reject.

    With OT_PUBLIC_EVALUATOR on, a request without a valid session is the
    evaluator. A real session always wins, so signing in shows that user's own
    role-scoped view.
    """
    user = _session_user(request, db)
    if user is not None:
        return user
    if not settings.public_evaluator:
        return None
    if _evaluator_blocked(request):
        raise HTTPException(
            status_code=403,
            detail="not available in the public evaluator view: this action "
                   "changes credentials, accounts, zone staffing or live "
                   "workers. Sign in with a production account to perform it.")
    return evaluator_user(db)


def current_user(user: Optional[User] = Depends(optional_user)) -> User:
    """The signed-in user, or 401."""
    if user is None:
        raise HTTPException(
            status_code=401, detail="authentication required",
            headers={"WWW-Authenticate": "Cookie"})
    return user


def authenticated(request: Request,
                  user: Optional[User] = Depends(optional_user)) -> Optional[User]:
    """Router-level gate: a session, or the deprecated admin token.

    This is what `main.py` applies to whole routers. It exists rather than
    using `current_user` directly because the shared `X-Admin-Token` is being
    retired over one release: a blanket session requirement would cut it off
    immediately, which is a breaking change dressed up as a deprecation.

    The token is only honoured when `OT_ALLOW_LEGACY_ADMIN_TOKEN=true`, it is
    off by default, and it still cannot reach anything past the admin routes'
    own `require_admin` check. Returns None for a token-authenticated caller
    because there is no user to return -- which is precisely the reason the
    token is going away: an audit row it produces can name nobody.
    """
    if user is not None:
        return user

    if settings.allow_legacy_admin_token:
        from backend.core.security import verify_admin

        if verify_admin(request.headers.get("x-admin-token")):
            return None

    raise HTTPException(
        status_code=401, detail="authentication required",
        headers={"WWW-Authenticate": "Cookie"})


def require_role(*roles: str):
    """Dependency asserting the caller holds one of `roles`.

    `admin` and `super_admin` pass every check: the alternative is listing
    them at every call site, which is the kind of repetition that eventually
    omits one somewhere. Unknown role names raise at import rather than
    silently never matching.

    `super_admin` being implicit here is deliberate and is the reason adding
    the role changed no existing route's meaning. The routes `super_admin`
    holds *exclusively* are the ones that use `require_super_admin` below --
    it is defined by what `admin` cannot reach, not by a longer grant list.
    """
    unknown = [r for r in roles if r not in ROLES]
    if unknown:
        raise ValueError(f"unknown role(s) {unknown}; valid roles are {list(ROLES)}")
    allowed = set(roles) | set(IMPLICIT_ROLES)

    def _guard(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            # 403, not 404: the caller is authenticated, and pretending the
            # route does not exist would hide a permissions bug from its own
            # operator.
            raise HTTPException(
                status_code=403,
                detail=f"role '{user.role}' may not perform this action")
        return user

    # Declared on the guard so the route table can be audited without calling
    # the handlers behind it. Some of those handlers start pipeline runs; a
    # test that probed them live would be starting real work to find out who
    # was allowed to start real work.
    _guard.allowed_roles = frozenset(allowed)
    return _guard


def require_super_admin():
    """Dependency admitting `super_admin` and nobody else.

    This is the one guard `require_role` cannot express, because `require_role`
    grants `admin` implicitly -- `require_role("super_admin")` would admit
    `admin` too, which is the opposite of the intent.

    What sits behind it is everything that changes the platform's own rules
    rather than the work it is doing: modifying a protected jurisdiction
    boundary, granting or revoking a role, and deactivating another
    administrator. An operational administrator runs the operation; they do
    not get to redraw an international boundary or promote themselves.
    """

    def _guard(user: User = Depends(current_user)) -> User:
        if user.role != "super_admin":
            raise HTTPException(
                status_code=403,
                detail=f"role '{user.role}' may not perform this action; "
                       "super_admin is required")
        return user

    _guard.allowed_roles = frozenset({"super_admin"})
    return _guard


def declared_roles(route) -> Optional[frozenset]:
    """Roles a mounted route permits, read from its dependency tree.

    None means the route carries no role guard, i.e. any authenticated user
    may call it. Walks the whole tree because guards can be attached at the
    router or the route.
    """
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return None

    found = None
    stack = [dependant]
    while stack:
        node = stack.pop()
        roles = getattr(getattr(node, "call", None), "allowed_roles", None)
        if roles is not None:
            found = roles if found is None else (found & roles)
        stack.extend(getattr(node, "dependencies", []))
    return found
