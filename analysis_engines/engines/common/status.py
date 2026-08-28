"""The status object every engine run returns alongside its file.

Handbook §4.5 / §14:

    { "ok": true|false, "engine_used": "primary"|"fallback", "warnings": [] }

On failure the object additionally carries ``error`` with the declared error class,
so the main system can show its badge and decide whether to retry with a fallback
engine. The shape is identical for all three engines.
"""

from __future__ import annotations

from typing import Any

from .errors import EngineError

PRIMARY = "primary"
FALLBACK = "fallback"


class Status:
    """Mutable status accumulator; call :meth:`to_dict` when the run finishes."""

    def __init__(self, engine_used: str = PRIMARY) -> None:
        self.ok = True
        self.engine_used = engine_used
        self.warnings: list[str] = []
        self.error: dict[str, Any] | None = None
        self.outputs: dict[str, str] = {}

    def warn(self, message: str) -> None:
        """Record a non-fatal problem. The run still produces its output file."""
        if message not in self.warnings:
            self.warnings.append(message)

    def set_engine(self, engine_used: str) -> None:
        self.engine_used = engine_used

    def add_output(self, name: str, path: str) -> None:
        """Record a file this run wrote, so the caller need not guess paths."""
        self.outputs[name] = str(path)

    def fail(self, err: EngineError) -> "Status":
        self.ok = False
        self.error = err.to_dict()
        return self

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "engine_used": self.engine_used,
            "warnings": list(self.warnings),
        }
        if self.outputs:
            payload["outputs"] = dict(self.outputs)
        if self.error is not None:
            payload["error"] = self.error
        return payload


def error_status(err: EngineError, engine_used: str = PRIMARY) -> dict[str, Any]:
    """One-liner for the failure path: build a failed status dict from an error."""
    return Status(engine_used).fail(err).to_dict()
