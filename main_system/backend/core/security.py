"""Credential storage and admin authentication.

Two rules from the spec drive everything here:

  * a stored key is encrypted at rest and **never returned in full** by any
    endpoint -- the UI only ever sees the last four characters;
  * every key change is written to an audit log, and the log records the field
    that changed, never its value.

Encryption uses Fernet when `cryptography` is available. When it is not, the
value is NOT silently stored in clear: it is marked as unencrypted so the
monitoring page can say so out loud. A credential store that quietly downgrades
to plaintext is worse than one that refuses, because nobody finds out.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from typing import Optional, Tuple

from backend.core.config import get_settings

_PLAIN_PREFIX = "plain:"          # explicit marker; never silent
_FERNET_PREFIX = "fernet:"


def _derive_key(secret: str) -> bytes:
    """32-byte urlsafe-base64 key from the configured secret."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet():
    settings = get_settings()
    if not settings.secret_key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(_derive_key(settings.secret_key))
    except Exception:
        return None


def encryption_available() -> Tuple[bool, str]:
    """(available, reason) -- surfaced on the key-management page."""
    settings = get_settings()
    if not settings.secret_key:
        return False, "SECRET_KEY is not set; credentials cannot be encrypted at rest"
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return False, "the `cryptography` package is not installed"
    return True, "Fernet (AES-128-CBC + HMAC)"


def encrypt(value: str) -> str:
    f = _fernet()
    if f is None:
        return _PLAIN_PREFIX + base64.urlsafe_b64encode(value.encode()).decode()
    return _FERNET_PREFIX + f.encrypt(value.encode()).decode()


def decrypt(stored: str) -> Optional[str]:
    if stored.startswith(_FERNET_PREFIX):
        f = _fernet()
        if f is None:
            return None
        try:
            return f.decrypt(stored[len(_FERNET_PREFIX):].encode()).decode()
        except Exception:
            return None
    if stored.startswith(_PLAIN_PREFIX):
        try:
            return base64.urlsafe_b64decode(
                stored[len(_PLAIN_PREFIX):].encode()).decode()
        except Exception:
            return None
    return None


def is_encrypted(stored: str) -> bool:
    return stored.startswith(_FERNET_PREFIX)


def mask(value: str) -> str:
    """`••••1234`. The only representation any API is allowed to return."""
    if not value:
        return ""
    tail = value[-4:] if len(value) >= 4 else value
    return "•" * 4 + tail


def last_four(value: str) -> str:
    return value[-4:] if len(value) >= 4 else value


# --------------------------------------------------------------------------
# admin auth
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# passwords
#
# Separate from the credential vault above and deliberately so: the vault
# encrypts secrets we must be able to READ BACK (a provider key is useless
# otherwise), while a password must never be recoverable at all. Same module,
# opposite guarantees -- mixing them is how reversible "password encryption"
# gets shipped.
# --------------------------------------------------------------------------

_ARGON2_UNAVAILABLE = "argon2-cffi is not installed; refusing to store a password"


def _hasher():
    from argon2 import PasswordHasher

    # Library defaults (argon2id, m=64MiB, t=3, p=4) are the RFC 9106
    # second-recommended profile and are left alone: hand-tuning these without
    # measuring on the deployment host usually weakens them.
    return PasswordHasher()


def hash_password(password: str) -> str:
    """argon2id hash, or a loud refusal.

    There is no fallback to a weaker scheme. The vault above degrades visibly
    to `plain:` because an unreadable provider key breaks the demo and the
    banner tells the operator; a password store has no equivalent excuse --
    silently downgrading it would be invisible to everyone, forever.
    """
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    try:
        return _hasher().hash(password)
    except ImportError as exc:                     # pragma: no cover - install issue
        raise RuntimeError(_ARGON2_UNAVAILABLE) from exc


def verify_password(stored: Optional[str], password: str) -> bool:
    """Constant-time-ish verification that never raises on bad input.

    Returns False for an absent hash rather than throwing, so an account with
    no password set (OIDC-only, once that exists) simply cannot log in by
    password instead of producing a 500 that leaks which accounts exist.
    """
    if not stored or not password:
        return False
    try:
        from argon2.exceptions import VerificationError, VerifyMismatchError

        try:
            return bool(_hasher().verify(stored, password))
        except (VerifyMismatchError, VerificationError):
            return False
        except Exception:                          # noqa: BLE001 - malformed hash
            return False
    except ImportError:                            # pragma: no cover - install issue
        return False


def needs_rehash(stored: str) -> bool:
    """True when a stored hash predates the current argon2 parameters."""
    try:
        return bool(_hasher().check_needs_rehash(stored))
    except Exception:                              # noqa: BLE001
        return False


def verify_admin(token: Optional[str]) -> bool:
    """Constant-time comparison against the configured admin token."""
    if not token:
        return False
    return secrets.compare_digest(token, get_settings().admin_token)


def resolve_credential(db, provider: str, field: str) -> Optional[str]:
    """The value the application should actually use for a provider field.

    The database wins over the environment, because that is what an operator
    can change from the Key Management page without a redeploy. Falling back to
    the environment keeps a fresh checkout working from `.env` alone.
    """
    from backend.models.db import ApiKey

    row = (db.query(ApiKey)
             .filter(ApiKey.provider == provider, ApiKey.field == field)
             .order_by(ApiKey.updated_utc.desc())
             .first())
    if row is not None:
        value = decrypt(row.ciphertext)
        if value:
            return value
    return os.getenv(field)
