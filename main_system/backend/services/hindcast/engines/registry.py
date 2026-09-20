"""The pipeline, as data: seven engines in stage order."""
from __future__ import annotations

from backend.services.hindcast.engines.base import Engine
from backend.services.hindcast.engines.bounds import BoundsEngine
from backend.services.hindcast.engines.drift import DriftEngine
from backend.services.hindcast.engines.forcing_engine import ForcingEngine
from backend.services.hindcast.engines.ingest import IngestEngine
from backend.services.hindcast.engines.posterior import PosteriorEngine
from backend.services.hindcast.engines.shape import ShapeEngine
from backend.services.hindcast.engines.verify import VerifyEngine

_ENGINES: list[type[Engine]] = [IngestEngine, BoundsEngine, ForcingEngine, DriftEngine,
                                ShapeEngine, VerifyEngine, PosteriorEngine]
ENGINE_ORDER: list[str] = [e.id for e in sorted(_ENGINES, key=lambda e: e.order)]
_BY_ID = {e.id: e for e in _ENGINES}


def all_engines() -> list[type[Engine]]:
    return [_BY_ID[i] for i in ENGINE_ORDER]


def get_engine(engine_id: str) -> Engine:
    try:
        return _BY_ID[engine_id]()
    except KeyError as exc:
        raise KeyError(f"unknown engine '{engine_id}'") from exc
