"""Shared helpers for Engines A, B and C: error taxonomy, status object, geodesy,
UTC handling and small IO utilities.
"""

from .errors import (
    BAD_GRID,
    EMPTY_MASK,
    ERROR_CLASSES,
    MISSING_INPUT,
    NO_VESSELS_IN_WINDOW,
    EngineError,
    bad_grid,
    empty_mask,
    missing_input,
    no_vessels_in_window,
)
from .status import FALLBACK, PRIMARY, Status, error_status
from .timeutil import format_utc, now_utc, now_utc_str, parse_utc

__all__ = [
    "MISSING_INPUT",
    "BAD_GRID",
    "EMPTY_MASK",
    "NO_VESSELS_IN_WINDOW",
    "ERROR_CLASSES",
    "EngineError",
    "missing_input",
    "bad_grid",
    "empty_mask",
    "no_vessels_in_window",
    "Status",
    "PRIMARY",
    "FALLBACK",
    "error_status",
    "parse_utc",
    "format_utc",
    "now_utc",
    "now_utc_str",
]
