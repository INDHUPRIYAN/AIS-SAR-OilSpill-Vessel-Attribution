"""CDSE (Copernicus Data Space Ecosystem) Adapter.

Handles OAuth2 authentication, OData catalog searching, and scene downloading
for Sentinel-1 SAR imagery products.
"""

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

# Default CDSE Endpoints
DEFAULT_TOKEN_ENDPOINT = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
)
DEFAULT_ODATA_ENDPOINT = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"


class CDSEAdapter:
    """Adapter for interacting with Copernicus Data Space Ecosystem (CDSE) APIs."""

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        mock_mode: bool = False,
        token_endpoint: str = DEFAULT_TOKEN_ENDPOINT,
        odata_endpoint: str = DEFAULT_ODATA_ENDPOINT,
    ):
        """Initializes the CDSE adapter.

        Args:
            username: CDSE account username or email (defaults to CDSE_USERNAME env var).
            password: CDSE account password (defaults to CDSE_PASSWORD env var).
            mock_mode: If True, operates completely offline without network calls.
            token_endpoint: Keycloak OAuth2 token URL.
            odata_endpoint: CDSE OData catalogue URL.
        """
        self.username = username or os.getenv("CDSE_USERNAME")
        self.password = password or os.getenv("CDSE_PASSWORD")
        self.mock_mode = mock_mode
        self.token_endpoint = token_endpoint
        self.odata_endpoint = odata_endpoint
        self.token: Optional[str] = None
        self.token_expiry: float = 0.0

    def refresh_token(self) -> str:
        """Authenticates with CDSE Keycloak/OAuth server and stores access token.

        Returns:
            Bearer access token string.

        Raises:
            ValueError: If credentials are not configured.
            RuntimeError: If authentication fails.
        """
        if self.mock_mode:
            self.token = "mock_cdse_bearer_token_xyz"
            self.token_expiry = time.time() + 3600.0
            return self.token

        if not self.username or not self.password:
            raise ValueError(
                "CDSE credentials missing. Provide username/password or set CDSE_USERNAME / CDSE_PASSWORD."
            )

        payload = {
            "client_id": "cdse-public",
            "username": self.username,
            "password": self.password,
            "grant_type": "password",
        }
        encoded_data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            self.token_endpoint,
            data=encoded_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"CDSE authentication returned HTTP status {resp.status}")
                body = json.loads(resp.read().decode("utf-8"))

            self.token = body.get("access_token")
            expires_in = float(body.get("expires_in", 300))
            self.token_expiry = time.time() + expires_in
            if not self.token:
                raise RuntimeError("CDSE authentication response missing 'access_token'")
            return self.token
        except urllib.error.HTTPError as err:
            raise RuntimeError(f"CDSE authentication failed: HTTP {err.code}") from None
        except urllib.error.URLError as err:
            raise RuntimeError(f"CDSE authentication network error: {err.reason}") from None
        except Exception as err:
            raise RuntimeError(f"CDSE authentication error: {err}") from None

    def get_valid_token(self) -> str:
        """Returns a valid, non-expired Bearer token, refreshing if necessary."""
        if self.token and time.time() < (self.token_expiry - 30.0):
            return self.token
        return self.refresh_token()

    def _build_odata_filter(
        self,
        bbox: GeoBoundingBox,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        product_type: str = "GRD",
    ) -> str:
        """Constructs an OData $filter string for Sentinel-1 products."""
        clauses = [
            "Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'collection' and att/OData.CSC.StringAttribute/Value eq 'SENTINEL-1')",
            f"Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' and att/OData.CSC.StringAttribute/Value eq '{product_type}')",
            f"OData.CSC.Intersects(area=geography'SRID=4326;{bbox.to_wkt()}')",
        ]

        if start_time:
            iso_start = start_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            clauses.append(f"ContentDate/Start ge {iso_start}")
        if end_time:
            iso_end = end_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            clauses.append(f"ContentDate/End le {iso_end}")

        return " and ".join(clauses)

    def search_scenes(
        self,
        bbox: Union[GeoBoundingBox, List[float]],
        start_time: Optional[Union[datetime, str]] = None,
        end_time: Optional[Union[datetime, str]] = None,
        product_type: str = "GRD",
        top: int = 10,
    ) -> SceneSearchResult:
        """Searches CDSE OData catalog for matching Sentinel-1 scenes.

        Args:
            bbox: GeoBoundingBox or [min_lon, min_lat, max_lon, max_lat].
            start_time: Query start time (UTC).
            end_time: Query end time (UTC).
            product_type: Sentinel-1 product type (e.g. GRD, SLC).
            top: Maximum number of scenes to return.

        Returns:
            SceneSearchResult containing matching SceneMetadata records.
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
                download_url=f"{self.odata_endpoint}(S1A_IW_GRDH_1SDV_20231012T172530)/$value",
                file_size_bytes=1048576,
            )
            return SceneSearchResult(
                query_bbox=geo_bbox,
                query_start=dt_start,
                query_end=dt_end,
                total_count=1,
                scenes=[mock_scene],
                provider="CDSE",
            )

        token = self.get_valid_token()
        filter_query = self._build_odata_filter(geo_bbox, dt_start, dt_end, product_type)
        params = {
            "$filter": filter_query,
            "$orderby": "ContentDate/Start desc",
            "$top": str(top),
            "$expand": "Attributes",
        }
        encoded_query = urllib.parse.urlencode(params)
        req_url = f"{self.odata_endpoint}?{encoded_query}"

        req = urllib.request.Request(
            req_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            method="GET",
        )

        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"CDSE search returned HTTP status {resp.status}")
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            raise RuntimeError(f"CDSE catalog search failed: HTTP {err.code}") from None
        except urllib.error.URLError as err:
            raise RuntimeError(f"CDSE catalog search network error: {err.reason}") from None
        except Exception as err:
            raise RuntimeError(f"CDSE search error: {err}") from None

        products = data.get("value", [])
        scenes: List[SceneMetadata] = []
        for prod in products:
            parsed_meta = self._parse_odata_product(prod, geo_bbox)
            if parsed_meta:
                scenes.append(parsed_meta)

        return SceneSearchResult(
            query_bbox=geo_bbox,
            query_start=dt_start,
            query_end=dt_end,
            total_count=len(scenes),
            scenes=scenes,
            provider="CDSE",
        )

    def _parse_odata_product(self, item: Dict[str, Any], query_bbox: GeoBoundingBox) -> Optional[SceneMetadata]:
        """Maps an OData product JSON entity to standardized SceneMetadata."""
        name = item.get("Name") or item.get("Id")
        if not name:
            return None

        # Platform deduction
        platform = "Sentinel-1A" if name.startswith("S1A") else "Sentinel-1B" if name.startswith("S1B") else "Sentinel-1"

        # Acquisition time
        content_date = item.get("ContentDate", {})
        start_str = content_date.get("Start") or item.get("OriginDate")
        if start_str:
            try:
                acq_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            except ValueError:
                acq_time = datetime.now(timezone.utc)
        else:
            acq_time = datetime.now(timezone.utc)

        # Attribute extraction
        attributes = item.get("Attributes", [])
        attr_map = {}
        for attr in attributes:
            att_name = attr.get("Name")
            att_val = attr.get("Value")
            if att_name:
                attr_map[att_name] = att_val

        product_type = attr_map.get("productType") or ("GRD" if "GRD" in name else "SLC" if "SLC" in name else None)
        polarisation = attr_map.get("polarisationChannels") or attr_map.get("polarisation")
        orbit_dir = attr_map.get("orbitDirection")

        prod_id = item.get("Id")
        download_url = f"{self.odata_endpoint}({prod_id})/$value" if prod_id else None
        length = item.get("ContentLength")
        file_size = int(length) if length is not None else None

        checksum_list = item.get("Checksum", [])
        checksum = checksum_list[0].get("Value") if checksum_list else None

        return SceneMetadata(
            scene_id=name,
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
            Updated SceneMetadata pointing to the downloaded file with checksum and file size.
        """
        os.makedirs(destination_dir, exist_ok=True)

        if isinstance(scene, str):
            scene_id = scene
            metadata = SceneMetadata(
                scene_id=scene_id,
                acquisition_time=datetime.now(timezone.utc),
                bbox=[0.0, 0.0, 1.0, 1.0],
                download_url=f"{self.odata_endpoint}({scene_id})/$value",
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
                b"MOCK_SENTINEL1_GEOTIFF_DATA_CDSE_" + scene_id.encode("utf-8") + b"_CHECKSUM_VERIFIED"
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

        download_url = metadata.download_url or f"{self.odata_endpoint}({scene_id})/$value"
        token = self.get_valid_token()

        attempt = 0
        last_error = None
        while attempt < retries:
            attempt += 1
            try:
                req = urllib.request.Request(
                    download_url,
                    headers={"Authorization": f"Bearer {token}"},
                    method="GET",
                )
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

                # If metadata had expected checksum, verify it
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

        raise RuntimeError(f"Failed to download CDSE scene {scene_id} after {retries} attempts: {last_error}")

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

