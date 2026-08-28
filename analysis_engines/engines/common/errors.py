"""Error taxonomy for all three engines.

Handbook rule (§4.5): engines return *structured* errors, never crash. An engine
function catches its own failures and hands back an ``EngineError``; only the CLI
layer decides what to print and which exit code to use.

Declared classes (handbook §4.5, frozen):

    MISSING_INPUT           a required input file/field is absent or unreadable
    BAD_GRID                NetCDF lacks an expected variable or does not cover the slick
    EMPTY_MASK              no slick survives thresholding
    NO_VESSELS_IN_WINDOW    zero vessels pass the gates (a valid outcome, not a bug)

The team-wide provider taxonomy (AUTH_FAILED, TIMEOUT, RATE_LIMITED, UNAVAILABLE,
BAD_RESPONSE) belongs to the API services, not to these engines — our engines never
touch a network.
"""

from __future__ import annotations

from typing import Any

MISSING_INPUT = "MISSING_INPUT"
BAD_GRID = "BAD_GRID"
EMPTY_MASK = "EMPTY_MASK"
NO_VESSELS_IN_WINDOW = "NO_VESSELS_IN_WINDOW"

ERROR_CLASSES = (MISSING_INPUT, BAD_GRID, EMPTY_MASK, NO_VESSELS_IN_WINDOW)


class EngineError(Exception):
    """A declared, structured engine failure.

    Raised freely *inside* an engine; every engine's top-level runner catches it and
    converts it into a status object, so callers see data, never a traceback.
    """

    def __init__(self, error_class: str, message: str, **detail: Any) -> None:
        if error_class not in ERROR_CLASSES:
            raise ValueError(
                f"{error_class!r} is not a declared error class; "
                f"expected one of {ERROR_CLASSES}"
            )
        super().__init__(message)
        self.error_class = error_class
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_class": self.error_class,
            "message": self.message,
            "detail": self.detail,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"EngineError({self.error_class}: {self.message})"


def missing_input(message: str, **detail: Any) -> EngineError:
    return EngineError(MISSING_INPUT, message, **detail)


def bad_grid(message: str, **detail: Any) -> EngineError:
    return EngineError(BAD_GRID, message, **detail)


def empty_mask(message: str, **detail: Any) -> EngineError:
    return EngineError(EMPTY_MASK, message, **detail)


def no_vessels_in_window(message: str, **detail: Any) -> EngineError:
    return EngineError(NO_VESSELS_IN_WINDOW, message, **detail)
