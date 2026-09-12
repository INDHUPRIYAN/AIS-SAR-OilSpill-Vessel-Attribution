"""User and role administration.

Until now accounts could only be created by `bootstrap_admin` (one, from the
environment, into an empty table) or by inserting rows by hand. That made the
officer model undeliverable: spec section 20 requires zones and officers to be
created dynamically -- "do NOT hard-code four officers" -- and there was no way
to create an officer at all.

THE ESCALATION RULES, AND WHY EACH ONE EXISTS
---------------------------------------------
Every rule here is about one account being unable to gain authority it was not
given. They are enforced server-side; the UI's hidden buttons are a courtesy.

  * **Only a super_admin may grant or change a role.** Otherwise an
    `admin` promotes itself to `super_admin` and the protected-boundary rule
    (section 16) evaporates -- the whole point of which is that an operational
    administrator cannot redraw an international border.

  * **An admin may create and manage accounts BELOW itself**, which is the
    "manage officer accounts where permitted" of section 19. It may create a
    `zone_officer`, `investigator`, `analyst`, `reviewer` or `auditor`, and may
    not create an `admin` or a `super_admin`.

  * **Nobody may deactivate or demote themselves.** Not paternalism: the last
    super_admin locking itself out leaves a system whose protected boundaries
    can never be edited again, and whose only recovery is editing the database
    by hand.

  * **The last active super_admin cannot be removed.** Same failure, reached
    by a different route.

  * **A password is never returned, never logged, and never echoed.** Creating
    an account returns the account, not the credential; the operator who typed
    it is the one who knows it.

PASSWORD POLICY
---------------
A minimum length and nothing else. Composition rules ("one digit, one symbol")
measurably push people towards `Password1!` and are not enforced here. Length
is the property that actually resists guessing.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from backend.core import security
from backend.core.authz import current_user, require_role
from backend.models.db import (IMPLICIT_ROLES, ROLES, User, Zone,
                               ZoneAssignment, get_db, utcnow)
from backend.services import audit as audit_service
from backend.services import zones as zsvc

router = APIRouter(tags=["users"])

# Roles an `admin` may assign. Derived by subtraction from IMPLICIT_ROLES
# rather than listed, so adding a privileged role does not silently become
# assignable by an administrator who should not be able to grant it.
ADMIN_ASSIGNABLE = tuple(r for r in ROLES if r not in IMPLICIT_ROLES)

# Long enough to resist guessing; no composition rules. See the module
# docstring.
MIN_PASSWORD_LENGTH = 12

# NOTE on guards: the role-change check lives in the HANDLER rather than as a
# route-level `require_super_admin`, because the same PATCH legitimately
# renames an administrator's display name or resets their password -- a route
# guard would block those too. The handler refuses the role change specifically,
# with a reason. `test_rbac_matrix.VALUE_GUARDED` records that this is
# deliberate.


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)
    display_name: Optional[str] = Field(default=None, max_length=120)
    role: str = "zone_officer"

    @field_validator("email")
    @classmethod
    def _normalise(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("role")
    @classmethod
    def _known_role(cls, v: str) -> str:
        if v not in ROLES:
            raise ValueError(f"role must be one of {list(ROLES)}")
        return v


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=120)
    active: Optional[bool] = None
    # Changing this is super_admin only, checked in the handler rather than by
    # a route guard, because the same route legitimately edits a display name
    # for an admin.
    role: Optional[str] = None
    password: Optional[str] = Field(default=None, min_length=MIN_PASSWORD_LENGTH,
                                    max_length=200)

    @field_validator("role")
    @classmethod
    def _known_role(cls, v):
        if v is not None and v not in ROLES:
            raise ValueError(f"role must be one of {list(ROLES)}")
        return v


def _dict(db: Session, user: User, include_zones: bool = True) -> dict:
    out = {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "active": bool(user.active),
        "created_utc": user.created_utc,
        "last_login_utc": user.last_login_utc,
        # Never the hash, and never a placeholder that looks like one. What
        # matters operationally is whether the account CAN sign in with a
        # password, which is a different question from what the password is.
        "can_sign_in_with_password": bool(user.password_hash),
    }
    if include_zones:
        rows = (db.query(ZoneAssignment)
                .filter(ZoneAssignment.user_id == user.id).all())
        zones = []
        for row in rows:
            zone = db.get(Zone, row.zone_id)
            if zone is None:
                continue
            zones.append({"zone_id": zone.id, "name": zone.name,
                          "kind": zone.kind, "is_primary": bool(row.is_primary)})
        out["zones"] = zones
        out["zone_count"] = len(zones)
        # An officer with no assignment can act nowhere. Surfaced because an
        # account that looks configured and routes nothing is the failure this
        # page exists to catch.
        out["unassigned_officer"] = bool(
            user.role == "zone_officer" and not zones and user.active)
    return out


def _assert_may_manage(actor: User, target_role: str,
                       action: str) -> None:
    """Can `actor` create or modify an account with `target_role`?"""
    if actor.role == "super_admin":
        return
    if actor.role != "admin":
        raise HTTPException(
            403, f"role '{actor.role}' may not {action} accounts")
    if target_role in IMPLICIT_ROLES:
        raise HTTPException(
            403, f"an admin may not {action} an account with role "
                 f"'{target_role}'. Assignable roles are "
                 f"{sorted(ADMIN_ASSIGNABLE)}; only a super_admin may create "
                 f"or modify an administrator.")


@router.get("/users", dependencies=[Depends(require_role("admin"))])
def list_users(role: Optional[str] = None,
               active: Optional[bool] = None,
               zone_id: Optional[str] = None,
               q: Optional[str] = None,
               limit: int = Query(200, ge=1, le=1000),
               db: Session = Depends(get_db),
               _user: User = Depends(current_user)):
    """The account register. Administrator and above.

    Not readable by every role: the list of who investigates spills, with
    their addresses and last sign-in, is not situational awareness. What every
    role CAN see is who is responsible for a zone, through
    `/api/zones` -- that is operational and is published there.
    """
    query = db.query(User)
    if role:
        if role not in ROLES:
            raise HTTPException(422, f"role must be one of {list(ROLES)}")
        query = query.filter(User.role == role)
    if active is not None:
        query = query.filter(User.active.is_(active))
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.filter(User.email.ilike(like)
                             | User.display_name.ilike(like))
    if zone_id:
        ids = [r.user_id for r in db.query(ZoneAssignment)
               .filter(ZoneAssignment.zone_id == zone_id).all()]
        query = query.filter(User.id.in_(ids or [-1]))

    rows = query.order_by(User.role.asc(), User.email.asc()).limit(limit).all()
    return {
        "users": [_dict(db, u) for u in rows],
        "count": len(rows),
        "roles": list(ROLES),
        "admin_assignable_roles": list(ADMIN_ASSIGNABLE),
        "min_password_length": MIN_PASSWORD_LENGTH,
    }


@router.post("/users", status_code=201,
             dependencies=[Depends(require_role("admin"))])
def create_user(body: UserCreate, request: Request,
                db: Session = Depends(get_db),
                actor: User = Depends(current_user)):
    """Create an account. This is how a zone officer comes to exist."""
    _assert_may_manage(actor, body.role, "create")

    if db.query(User).filter(User.email == body.email).one_or_none() is not None:
        raise HTTPException(409, f"an account already exists for {body.email}")

    user = User(email=body.email,
                password_hash=security.hash_password(body.password),
                display_name=(body.display_name or body.email.split("@")[0]),
                role=body.role, active=True, created_utc=utcnow())
    db.add(user)
    db.flush()

    # The password is deliberately absent from the audit detail. An audit row
    # that carried it would make the log a credential store.
    audit_service.record(
        db, "user.create", request=request, resource=f"user:{user.id}",
        detail=json.dumps({"email": user.email, "role": user.role}),
        commit=False)
    db.commit()
    return _dict(db, user)


@router.get("/users/{user_id}", dependencies=[Depends(require_role("admin"))])
def get_user(user_id: int, db: Session = Depends(get_db),
             _user: User = Depends(current_user)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, f"no user {user_id}")
    return _dict(db, user)


@router.patch("/users/{user_id}",
              dependencies=[Depends(require_role("admin"))])
def update_user(user_id: int, body: UserUpdate, request: Request,
                db: Session = Depends(get_db),
                actor: User = Depends(current_user)):
    """Rename, reset, deactivate or re-role an account.

    The role change is the sensitive one and is super_admin only. The rest an
    administrator may do for the accounts it is allowed to manage.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, f"no user {user_id}")

    editing_self = user.id == actor.id

    # An account may always edit its OWN profile -- display name and password.
    # `_assert_may_manage` is skipped for self on purpose: it asks "may you
    # manage an account with this role", and for an admin editing itself the
    # answer was no, so an administrator could not rename itself or reset its
    # own password. It also meant a self-deactivation attempt was refused with
    # a message about role management rather than about deactivating yourself.
    #
    # What self-editing does NOT permit is a role change or a deactivation;
    # both are refused below, by name.
    if not editing_self:
        _assert_may_manage(actor, user.role, "modify")
    changes: dict = {}

    if body.role is not None and body.role != user.role:
        # Granting a role is privilege escalation. Not something an
        # operational administrator does, even to somebody else -- an admin
        # who could mint another admin has effectively minted itself one.
        if actor.role != "super_admin":
            raise HTTPException(
                403, "only a super_admin may change an account's role; "
                     "granting a role is a privilege escalation")
        if user.id == actor.id:
            raise HTTPException(
                403, "you cannot change your own role. A super_admin who "
                     "demoted itself would leave a system whose protected "
                     "boundaries nobody can edit.")
        _assert_last_super_admin_survives(db, user, new_role=body.role)
        changes["role"] = {"from": user.role, "to": body.role}
        user.role = body.role

    if body.active is not None and bool(body.active) != bool(user.active):
        if user.id == actor.id and not body.active:
            raise HTTPException(
                403, "you cannot deactivate your own account")
        if not body.active:
            _assert_last_super_admin_survives(db, user)
        changes["active"] = {"from": bool(user.active), "to": bool(body.active)}
        user.active = bool(body.active)
        if not body.active:
            # An inactive account must not stay the responsible officer for a
            # zone: alerts would route to somebody who cannot sign in, and the
            # zone would look covered. Removed, and the removal is reported so
            # an administrator knows which zones are now unrouted.
            orphaned = [r.zone_id for r in
                        db.query(ZoneAssignment)
                        .filter(ZoneAssignment.user_id == user.id).all()]
            if orphaned:
                (db.query(ZoneAssignment)
                 .filter(ZoneAssignment.user_id == user.id).delete())
                changes["zones_unassigned"] = orphaned

    if body.display_name is not None:
        changes["display_name"] = True
        user.display_name = body.display_name

    if body.password is not None:
        user.password_hash = security.hash_password(body.password)
        # Recorded as an event, never with the value.
        changes["password"] = "reset"

    if changes:
        action = ("role.change" if "role" in changes
                  else "user.deactivate" if changes.get("active", {}).get("to") is False
                  else "user.change")
        audit_service.record(
            db, action, request=request, resource=f"user:{user.id}",
            detail=json.dumps(changes, default=str), commit=False)
    db.commit()

    out = _dict(db, user)
    out["changed"] = sorted(changes)
    if changes.get("zones_unassigned"):
        out["now_unrouted_zones"] = changes["zones_unassigned"]
        out["warning"] = (
            f"deactivating this account removed it from "
            f"{len(changes['zones_unassigned'])} zone(s), which now route by "
            f"escalation or not at all: "
            f"{', '.join(changes['zones_unassigned'])}")
    return out


def _assert_last_super_admin_survives(db: Session, target: User, *,
                                      new_role: Optional[str] = None) -> None:
    """Refuse a change that would leave no active super_admin.

    Without this, a system can reach a state where protected jurisdiction
    boundaries can never be edited again and the only recovery is editing the
    database by hand. That is not a permission failure, it is a deployment
    that has to be repaired offline.
    """
    if target.role != "super_admin":
        return
    if new_role == "super_admin":
        return
    remaining = (db.query(User)
                 .filter(User.role == "super_admin")
                 .filter(User.active.is_(True))
                 .filter(User.id != target.id).count())
    if remaining == 0:
        raise HTTPException(
            409, "this is the last active super_admin. Removing it would "
                 "leave a system whose protected boundaries and role grants "
                 "nobody can change. Create another super_admin first.")


@router.get("/roles")
def list_roles(db: Session = Depends(get_db),
               user: User = Depends(current_user)):
    """The role vocabulary, what each one is for, and what you hold.

    Readable by any authenticated account, because a user being able to see
    the shape of the permission model is not a leak -- and an officer who
    cannot find out why a button is disabled files a bug instead.
    """
    counts = {}
    for row in db.query(User).all():
        counts[row.role] = counts.get(row.role, 0) + 1

    described = {
        "super_admin": "Full platform authority. The only role that may "
                       "modify a protected jurisdiction boundary, grant a "
                       "role, or manage another administrator.",
        "admin": "Operational administrator. Manages zones, officer "
                 "assignments, alert routing, API credentials and operational "
                 "settings. May not move a protected boundary or grant a role.",
        "investigator": "Opens investigations, runs the pipeline, records "
                        "decisions, composes reports.",
        "analyst": "As investigator, plus provider testing and attribution "
                   "weight inspection.",
        "reviewer": "Concludes a case (attributed / closed) and publishes a "
                    "report. Reads the audit log.",
        "auditor": "Read-only across the platform, including the audit log. "
                   "Changes nothing.",
        "zone_officer": "Global read, jurisdiction-scoped write. Sees every "
                        "zone, incident and vessel for situational awareness; "
                        "acts, and reads reports, only within assigned zones.",
    }
    return {
        "roles": [{"role": r, "purpose": described.get(r, ""),
                   "accounts": counts.get(r, 0),
                   "grants_everything": r in IMPLICIT_ROLES,
                   "zone_scoped": r == "zone_officer"}
                  for r in ROLES],
        "you": user.role,
        "your_zone_scope": (sorted(zsvc.assigned_zone_ids(db, user))
                            if user.role == "zone_officer" else "all"),
        "admin_assignable_roles": list(ADMIN_ASSIGNABLE),
    }


@router.get("/users/{user_id}/zones",
            dependencies=[Depends(require_role("admin"))])
def user_zones(user_id: int, db: Session = Depends(get_db),
               _user: User = Depends(current_user)):
    """Which zones this account is answerable for, assignments expanded down.

    The expansion matters: an officer assigned a jurisdiction owns the
    operational zones inside it, and a list showing only the literal
    assignment would understate their authority.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, f"no user {user_id}")
    effective = sorted(zsvc.assigned_zone_ids(db, user))
    return {
        "user_id": user.id,
        "email": user.email,
        "assignments": _dict(db, user)["zones"],
        "effective_zone_ids": effective,
        "note": ("effective_zone_ids expands each assignment downward: an "
                 "officer assigned a jurisdiction may act in every "
                 "operational zone inside it"),
    }
