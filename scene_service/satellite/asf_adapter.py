"""ASF (Alaska Satellite Facility) Adapter.

Handles search and retrieval of Sentinel-1 SAR products from NASA's ASF DAAC
as the secondary/fallback provider.
"""

import base64
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from .models import GeoBoundingBox, SceneMetadata, SceneSearchResult

logger = logging.getLogger(__name__)

# Default ASF Vertex Search Endpoint
DEFAULT_ASF_SEARCH_ENDPOINT = "https://api.daac.asf.alaska.edu/services/search/param"


class ASFAdapter:
    """Adapter for interacting with Alaska Satellite Facility (ASF) DAAC API."""

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        mock_mode: bool = False,
        search_endpoint: str = DEFAULT_ASF_SEARCH_ENDPOINT,
    ):
        """Initializes the ASF adapter.

        Args:
            username: NASA Earthdata username (defaults to ASF_USERNAME env var).
            password: NASA Earthdata password (defaults to ASF_PASSWORD env var).
            mock_mode: If True, operates completely offline without network calls.
            search_endpoint: ASF search API URL.
        """
        self.username = username or os.getenv("ASF_USERNAME")
        self.password = password or os.getenv("ASF_PASSWORD")
        self.mock_mode = mock_mode
        self.search_endpoint = search_endpoint

    def _build_search_params(
        self,
        bbox: GeoBoundingBox,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        product_type: str = "GRD",
        max_results: int = 10,
    ) -> Dict[str, str]:
        """Constructs query parameter dictionary for ASF Vertex Search API."""
        params: Dict[str, str] = {
            "dataset": "SENTINEL-1",
            "bbox": f"{bbox.min_lon},{bbox.min_lat},{bbox.max_lon},{bbox.max_lat}",
            "output": "json",
            "maxResults": str(max_results),
            "beamMode": "IW",
        }

        # Processing level mapping
        if product_type.upper() == "GRD":
            params["processingLevel"] = "GRD_HD,GRD_MD,GRD_FD,GRD,GRD_HS"
        elif product_type.upper() == "SLC":
            params["processingLevel"] = "SLC"

        if start_time:
            params["start"] = start_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if end_time:
            params["end"] = end_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        return params

    def search_asf(
        self,
        bbox: Union[GeoBoundingBox, List[float]],
        start_time: Optional[Union[datetime, str]] = None,
        end_time: Optional[Union[datetime, str]] = None,
        product_type: str = "GRD",
        max_results: int = 10,
    ) -> SceneSearchResult:
        """Searches ASF for Sentinel-1 scenes matching criteria.

        Args:
            bbox: GeoBoundingBox or [min_lon, min_lat, max_lon, max_lat].
            start_time: Query start time (UTC).
            end_time: Query end time (UTC).
            product_type: Desired product type (e.g. GRD, SLC).
            max_results: Maximum results to retrieve.

        Returns:
            SceneSearchResult containing standardized SceneMetadata records.
        """
        if isinstance(bbox, (list, tuple)):
            geo_bbox = GeoBoundingBox.from_list(list(bbox))
        else:
            geo_bbox = bbox

        dt_start = self._normalize_datetime(start_time)
        dt_end = self._normalize_datetime(end_time)

        if self.mock_mode:
            # Deterministic mock scene strictly compatible with contracts/mocks/mock_scene.json
            mock_scene = SceneMetadata(
                scene_id="S1A_IW_GRDH_1SDV_20231012T172530",
                platform="Sentinel-1A",
                acquisition_time=dt_start or datetime(2023, 10, 12, 17, 25, 30, tzinfo=timezone.utc),
                bbox=geo_bbox,
                product_type=product_type,
                polarisation="VV+VH",
                orbit_direction="DESCENDING",
                download_url="https://datapool.asf.alaska.edu/GRD_HD/SA/S1A_IW_GRDH_1SDV_20231012T172530.zip",
                file_size_bytes=1048576,
            )
            return SceneSearchResult(
                query_bbox=geo_bbox,
                query_start=dt_start,
                query_end=dt_end,
                total_count=1,
                scenes=[mock_scene],
                provider="ASF",
            )

        params = self._build_search_params(geo_bbox, dt_start, dt_end, product_type, max_results)
        encoded_query = urllib.parse.urlencode(params)
        req_url = f"{self.search_endpoint}?{encoded_query}"

        req = urllib.request.Request(
            req_url,
            headers={"Accept": "application/json", "User-Agent": "OceanTrace-SatelliteService"},
            method="GET",
        )

        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"ASF search returned HTTP status {resp.status}")
                raw_data = resp.read().decode("utf-8")
                data = json.loads(raw_data)
        except urllib.error.HTTPError as err:
            raise RuntimeError(f"ASF search failed: HTTP {err.code}") from None
        except urllib.error.URLError as err:
            raise RuntimeError(f"ASF search network error: {err.reason}") from None
        except Exception as err:
            raise RuntimeError(f"ASF search error: {err}") from None

        # ASF JSON output is typically a list of lists [[{result1}, {result2}]] or [{result1}, ...]
        results_list: List[Dict[str, Any]] = []
        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, list):
                    results_list.extend(entry)
                elif isinstance(entry, dict):
                    results_list.append(entry)
        elif isinstance(data, dict) and "results" in data:
            results_list = data["results"]

        scenes: List[SceneMetadata] = []
        for item in results_list:
            if isinstance(item, dict):
                parsed = self._parse_asf_item(item, geo_bbox)
                if parsed:
                    scenes.append(parsed)

        return SceneSearchResult(
            query_bbox=geo_bbox,
            query_start=dt_start,
            query_end=dt_end,
            total_count=len(scenes),
            scenes=scenes,
            provider="ASF",
        )

    def _parse_asf_item(self, item: Dict[str, Any], query_bbox: GeoBoundingBox) -> Optional[SceneMetadata]:
        """Maps an ASF result entity to standardized SceneMetadata."""
        scene_id = item.get("sceneName") or item.get("granuleName") or item.get("productName")
        if not scene_id:
            return None

        # Platform
        raw_platform = item.get("platform")
        if raw_platform:
            platform = "Sentinel-1A" if "1A" in raw_platform else "Sentinel-1B" if "1B" in raw_platform else raw_platform
        else:
            platform = "Sentinel-1A" if scene_id.startswith("S1A") else "Sentinel-1B" if scene_id.startswith("S1B") else "Sentinel-1"

        # Acquisition time
        start_time_str = item.get("startTime") or item.get("sceneDate")
        if start_time_str:
            try:
                acq_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            except ValueError:
                acq_time = datetime.now(timezone.utc)
        else:
            acq_time = datetime.now(timezone.utc)

        # Polarization and orbit direction
        polarisation = item.get("polarization")
        orbit_dir = item.get("flightDirection")
        product_type = item.get("processingLevel") or ("GRD" if "GRD" in scene_id else "SLC" if "SLC" in scene_id else None)
        download_url = item.get("downloadUrl") or item.get("url")

        # File size
        size_bytes = item.get("bytes")
        if size_bytes is not None:
            try:
                file_size = int(size_bytes)
            except (ValueError, TypeError):
                file_size = None
        elif item.get("sizeMB") is not None:
            try:
                file_size = int(float(item["sizeMB"]) * 1024 * 1024)
            except (ValueError, TypeError):
                file_size = None
        else:
            file_size = None

        checksum = item.get("md5") or item.get("sha256")

        return SceneMetadata(
            scene_id=scene_id,
            platform=platform,
            acquisition_time=acq_time,
            bbox=query_bbox,
            product_type=product_type,
            polarisation=polarisation,
            orbit_direction=orbit_dir,
            download_url=download_url,
            file_size_bytes=file_size,
            checksum=checksum,
        )

    def download_scene(
        self,
        scene: Union[SceneMetadata, str],
        destination_dir: str,
        filename: Optional[str] = None,
        retries: int = 3,
    ) -> SceneMetadata:
        """Streams scene binary file to destination directory and verifies integrity.

        Args:
            scene: SceneMetadata object or scene ID string.
            destination_dir: Directory where the file should be saved.
            filename: Target filename (defaults to '<scene_id>.tif').
            retries: Number of retry attempts on transient network errors.

        Returns:
            Updated SceneMetadata pointing to the downloaded file.
        """
        os.makedirs(destination_dir, exist_ok=True)

        if isinstance(scene, str):
            scene_id = scene
            metadata = SceneMetadata(
                scene_id=scene_id,
                acquisition_time=datetime.now(timezone.utc),
                bbox=[0.0, 0.0, 1.0, 1.0],
                download_url=f"https://datapool.asf.alaska.edu/GRD_HD/SA/{scene_id}.zip",
            )
        else:
            scene_id = scene.scene_id
            metadata = scene

        target_name = filename or f"{scene_id}.tif"
        final_path = os.path.join(destination_dir, target_name)
        temp_path = final_path + ".tmp"

        if self.mock_mode:
            # Deterministic mock download content
            mock_content = (
                b"MOCK_SENTINEL1_GEOTIFF_DATA_ASF_" + scene_id.encode("utf-8") + b"_CHECKSUM_VERIFIED"
            )
            with open(temp_path, "wb") as f:
                f.write(mock_content)
            os.replace(temp_path, final_path)

            file_size = len(mock_content)
            sha = hashlib.sha256(mock_content).hexdigest()

            meta_dict = metadata.model_dump()
            meta_dict["file_path"] = final_path
            meta_dict["file_size_bytes"] = file_size
            meta_dict["checksum"] = sha
            return SceneMetadata.model_validate(meta_dict)

        download_url = metadata.download_url
        if not download_url:
            raise ValueError(f"No download URL available for ASF scene {scene_id}")

        headers = {"User-Agent": "OceanTrace-SatelliteService"}
        if self.username and self.password:
            auth_str = f"{self.username}:{self.password}"
            encoded_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
            headers["Authorization"] = f"Basic {encoded_auth}"

        attempt = 0
        last_error = None
        while attempt < retries:
            attempt += 1
            try:
                req = urllib.request.Request(download_url, headers=headers, method="GET")
                sha256_hash = hashlib.sha256()
                total_bytes = 0

                with urllib.request.urlopen(req, timeout=60.0) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"Download returned HTTP status {resp.status}")

                    with open(temp_path, "wb") as out_file:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            out_file.write(chunk)
                            sha256_hash.update(chunk)
                            total_bytes += len(chunk)

                if total_bytes == 0:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    raise RuntimeError(f"Downloaded scene {scene_id} is empty (0 bytes)")

                calculated_checksum = sha256_hash.hexdigest()

                if metadata.checksum and metadata.checksum.lower() != calculated_checksum.lower():
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    raise RuntimeError(
                        f"Checksum mismatch for {scene_id}: expected={metadata.checksum}, actual={calculated_checksum}"
                    )

                os.replace(temp_path, final_path)

                meta_dict = metadata.model_dump()
                meta_dict["file_path"] = final_path
                meta_dict["file_size_bytes"] = total_bytes
                meta_dict["checksum"] = calculated_checksum
                return SceneMetadata.model_validate(meta_dict)

            except Exception as err:
                last_error = err
                logger.warning(f"Download attempt {attempt}/{retries} for {scene_id} failed: {err}")
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                if attempt < retries:
                    time.sleep(1.0 * attempt)

        raise RuntimeError(f"Failed to download ASF scene {scene_id} after {retries} attempts: {last_error}")

    @staticmethod
    def _normalize_datetime(dt_input: Optional[Union[datetime, str]]) -> Optional[datetime]:
        """Normalizes string or datetime input to a UTC-aware datetime."""
        if dt_input is None:
            return None
        if isinstance(dt_input, datetime):
            if dt_input.tzinfo is None:
                return dt_input.replace(tzinfo=timezone.utc)
            return dt_input.astimezone(timezone.utc)
        if isinstance(dt_input, str):
            try:
                dt = datetime.fromisoformat(dt_input.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except ValueError as err:
                raise ValueError(f"Invalid ISO datetime string: {dt_input}") from err
        raise TypeError(f"Unsupported datetime type: {type(dt_input)}")

