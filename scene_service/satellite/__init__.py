"""Satellite Scene Service package.

Acquires Sentinel-1 Synthetic Aperture Radar (SAR) imagery scenes
via CDSE and ASF providers with local caching and fallback chains.
"""

from .models import (
    GeoBoundingBox,
    ProviderHealth,
    RetrievalResponse,
    SceneMetadata,
    SceneSearchResult,
)

__all__ = [
    "GeoBoundingBox",
    "SceneMetadata",
    "SceneSearchResult",
    "ProviderHealth",
    "RetrievalResponse",
]
