"""Scene Retrieval Fallback Chain.

Orchestrates Sentinel-1 SAR acquisition following the strict priority chain:
1. LOCAL CACHE (Offline-first)
2. CDSE (Primary Provider)
3. ASF (Secondary / Fallback Provider)
4. Structured Failure
"""

import logging
import os
from datetime import datetime
from typing import List, Optional, Union

from .asf_adapter import ASFAdapter
from .cache import LocalSceneCache
from .cdse_adapter import CDSEAdapter
from .models import GeoBoundingBox, RetrievalResponse, SceneMetadata, SceneSearchResult

logger = logging.getLogger(__name__)


class SceneRetrievalChain:
    """Orchestrates scene search and retrieval with fallback across providers and cache."""

    def __init__(
        self,
        cdse_adapter: Optional[CDSEAdapter] = None,
        asf_adapter: Optional[ASFAdapter] = None,
        cache: Optional[LocalSceneCache] = None,
        download_dir: Optional[str] = None,
    ):
        """Initializes the retrieval chain.

        Args:
            cdse_adapter: Instance of CDSEAdapter (primary provider).
            asf_adapter: Instance of ASFAdapter (fallback provider).
            cache: Instance of LocalSceneCache (offline-first storage).
            download_dir: Temporary directory for staging scene downloads before caching.
        """
        self.cache = cache if cache is not None else LocalSceneCache()
        self.cdse = cdse_adapter if cdse_adapter is not None else CDSEAdapter()
        self.asf = asf_adapter if asf_adapter is not None else ASFAdapter()
        self.download_dir = (
            os.path.abspath(download_dir)
            if download_dir
            else os.path.join(self.cache.cache_dir, "downloads")
        )
        os.makedirs(self.download_dir, exist_ok=True)

    def retrieve_scene(
        self, scene: Union[SceneMetadata, str], destination_dir: Optional[str] = None
    ) -> RetrievalResponse:
        """Retrieves a Sentinel-1 scene following the Cache -> CDSE -> ASF fallback flow.

        Args:
            scene: Target SceneMetadata object or scene ID string.
            destination_dir: Optional directory to place download; defaults to cache.

        Returns:
            RetrievalResponse with acquisition metadata, GeoTIFF path, and source provider.
        """
        scene_id = scene.scene_id if isinstance(scene, SceneMetadata) else str(scene)
        logger.info(f"Initiating scene retrieval for {scene_id}")

        # ----------------------------------------------------
        # 1. LOCAL CACHE CHECK (Offline-First)
        # ----------------------------------------------------
        cached_scene = self.cache.get(scene_id)
        if cached_scene and cached_scene.file_path and os.path.isfile(cached_scene.file_path):
            logger.info(f"Cache hit for scene {scene_id} at {cached_scene.file_path}")
            return RetrievalResponse(
                success=True,
                scene_id=scene_id,
                source_provider="CACHE",
                metadata=cached_scene,
                geotiff_path=cached_scene.file_path,
                error_message=None,
            )

        logger.info(f"Cache miss for scene {scene_id}. Attempting CDSE (Primary Provider)...")

        cdse_error_msg: Optional[str] = None
        asf_error_msg: Optional[str] = None

        # ----------------------------------------------------
        # 2. CDSE PRIMARY PROVIDER
        # ----------------------------------------------------
        try:
            downloaded_meta = self.cdse.download_scene(
                scene=scene, destination_dir=self.download_dir
            )
            if downloaded_meta and downloaded_meta.file_path and os.path.isfile(downloaded_meta.file_path):
                # Save into local scene cache
                cached_meta = self.cache.put(
                    metadata=downloaded_meta, raster_source=downloaded_meta.file_path
                )
                logger.info(f"Successfully retrieved and cached scene {scene_id} via CDSE.")
                return RetrievalResponse(
                    success=True,
                    scene_id=scene_id,
                    source_provider="CDSE",
                    metadata=cached_meta,
                    geotiff_path=cached_meta.file_path,
                    error_message=None,
                )
        except Exception as err:
            cdse_error_msg = str(err)
            logger.warning(
                f"CDSE retrieval failed for scene {scene_id}: {err}. Falling back to ASF (Secondary Provider)..."
            )

        # ----------------------------------------------------
        # 3. ASF FALLBACK PROVIDER
        # ----------------------------------------------------
        try:
            downloaded_meta = self.asf.download_scene(
                scene=scene, destination_dir=self.download_dir
            )
            if downloaded_meta and downloaded_meta.file_path and os.path.isfile(downloaded_meta.file_path):
                # Save into local scene cache
                cached_meta = self.cache.put(
                    metadata=downloaded_meta, raster_source=downloaded_meta.file_path
                )
                logger.info(f"Successfully retrieved and cached scene {scene_id} via ASF.")
                return RetrievalResponse(
                    success=True,
                    scene_id=scene_id,
                    source_provider="ASF",
                    metadata=cached_meta,
                    geotiff_path=cached_meta.file_path,
                    error_message=None,
                )
        except Exception as err:
            asf_error_msg = str(err)
            logger.warning(f"ASF fallback retrieval failed for scene {scene_id}: {err}")

        # ----------------------------------------------------
        # 4. STRUCTURED FAILURE
        # ----------------------------------------------------
        error_details = (
            f"All providers failed to retrieve scene {scene_id}. "
            f"CDSE error: {cdse_error_msg or 'N/A'}; "
            f"ASF error: {asf_error_msg or 'N/A'}"
        )
        logger.error(error_details)
        return RetrievalResponse(
            success=False,
            scene_id=scene_id,
            source_provider=None,
            metadata=None,
            geotiff_path=None,
            error_message=error_details,
        )

    def search_scenes(
        self,
        bbox: Union[GeoBoundingBox, List[float]],
        start_time: Optional[Union[datetime, str]] = None,
        end_time: Optional[Union[datetime, str]] = None,
        product_type: str = "GRD",
        top: int = 10,
    ) -> SceneSearchResult:
        """Searches CDSE with fallback to ASF for candidate Sentinel-1 scenes."""
        if isinstance(bbox, (list, tuple)):
            geo_bbox = GeoBoundingBox.from_list(list(bbox))
        else:
            geo_bbox = bbox

        # Try CDSE first
        try:
            cdse_result = self.cdse.search_scenes(
                bbox=geo_bbox,
                start_time=start_time,
                end_time=end_time,
                product_type=product_type,
                top=top,
            )
            if cdse_result and cdse_result.total_count > 0:
                return cdse_result
        except Exception as err:
            logger.warning(f"CDSE catalog search failed: {err}. Falling back to ASF search...")

        # Fallback to ASF
        try:
            asf_result = self.asf.search_asf(
                bbox=geo_bbox,
                start_time=start_time,
                end_time=end_time,
                product_type=product_type,
                max_results=top,
            )
            if asf_result and asf_result.total_count > 0:
                return asf_result
        except Exception as err:
            logger.warning(f"ASF catalog search failed: {err}")

        # Return empty result if both failed or found nothing
        return SceneSearchResult(
            query_bbox=geo_bbox,
            total_count=0,
            scenes=[],
            provider=None,
        )

