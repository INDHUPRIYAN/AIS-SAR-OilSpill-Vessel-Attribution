"""UTC time helpers.

Handbook ground rule: **UTC everywhere**, serialised with a ``Z`` suffix. The named
trap is IST (UTC+05:30) leaking in from a naive local timestamp, so parsing here is
deliberately strict: a timestamp with no timezone is rejected rather than assumed.
"""

from __future__ import annotations

from datetime import datetime, timezone

_UTC = timezone.utc


def parse_utc(value: str | datetime, *, field: str = "timestamp") -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware UTC datetime.

    Accepts a trailing ``Z`` (which :func:`datetime.fromisoformat` only learned in
    3.11) or an explicit offset. Rejects naive strings - guessing would be exactly
    the IST bug the handbook warns about.
    """
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith(("z", "Z")):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field}: {value!r} is not an ISO-8601 timestamp") from exc

    if dt.tzinfo is None:
        raise ValueError(
            f"{field}: {value!r} has no timezone. All timestamps must be UTC "
            "with an explicit 'Z' or offset (naive local time is not accepted)."
        )
    return dt.astimezone(_UTC)


def format_utc(dt: datetime, *, seconds: bool = True) -> str:
    """Serialise a datetime as ``YYYY-MM-DDTHH:MM:SSZ`` (contract format)."""
    dt = dt.astimezone(_UTC)
    fmt = "%Y-%m-%dT%H:%M:%SZ" if seconds else "%Y-%m-%dT%H:%MZ"
    return dt.strftime(fmt)


def now_utc() -> datetime:
    return datetime.now(_UTC)


def now_utc_str() -> str:
    """Current time in contract format - used for ``generated_utc`` fields."""
    return format_utc(now_utc())
