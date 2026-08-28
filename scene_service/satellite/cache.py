import hashlib
import json
import logging
import os
import shutil
from typing import Any, BinaryIO, List, Optional, Union

from .models import SceneMetadata

logger = logging.getLogger(__name__)


class LocalSceneCache:
    """Local filesystem cache for Sentinel-1 SAR scenes and metadata.

    Directory Layout:
    <cache_dir>/
    └── scenes/
        └── <scene_id>/
            ├── scene_meta.json
            └── scene_sigma0_db.tif
    """

    def __init__(self, cache_dir: str = "./data/cache/satellite"):
        self.cache_dir = os.path.abspath(cache_dir)
        self.scenes_dir = os.path.join(self.cache_dir, "scenes")
        self._ensure_dir(self.scenes_dir)

    @staticmethod
    def _ensure_dir(path: str) -> None:
        """Ensures that the directory hierarchy exists."""
        os.makedirs(path, exist_ok=True)

    def get_scene_dir(self, scene_id: str) -> str:
        """Returns the specific directory path for a given scene ID."""
        return os.path.join(self.scenes_dir, scene_id)

    def get_meta_path(self, scene_id: str) -> str:
        """Returns the expected scene_meta.json path for a given scene ID."""
        return os.path.join(self.get_scene_dir(scene_id), "scene_meta.json")

    def get_raster_path(self, scene_id: str) -> str:
        """Returns the expected scene_sigma0_db.tif path for a given scene ID."""
        return os.path.join(self.get_scene_dir(scene_id), "scene_sigma0_db.tif")

    @staticmethod
    def compute_sha256(file_path: str) -> Optional[str]:
        """Calculates SHA-256 checksum of a file on disk."""
        if not os.path.isfile(file_path):
            return None
        sha256_hash = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    sha256_hash.update(chunk)
            return sha256_hash.hexdigest().lower()
        except (OSError, IOError) as err:
            logger.warning(f"Failed to calculate SHA-256 for {file_path}: {err}")
            return None

    def validate_cache_entry(self, scene_id: str) -> Optional[SceneMetadata]:
        """Validates that a cached scene has both valid metadata and readable raster.

        Returns:
            SceneMetadata if valid and intact, otherwise None.
        """
        meta_path = self.get_meta_path(scene_id)
        raster_path = self.get_raster_path(scene_id)

        # 1. Both files must exist
        if not os.path.isfile(meta_path) or not os.path.isfile(raster_path):
            return None

        # 2. Raster must be readable and non-empty
        try:
            if os.path.getsize(raster_path) == 0:
                logger.debug(f"Cached raster for {scene_id} is empty (0 bytes).")
                return None
        except OSError:
            return None

        # 3. Metadata must be valid JSON and parseable into SceneMetadata
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            metadata = SceneMetadata.model_validate(data)
        except Exception as err:
            logger.warning(f"Failed to load or validate metadata for scene {scene_id}: {err}")
            return None

        # 4. If checksum is provided in metadata, verify against raster on disk
        if metadata.checksum:
            actual_checksum = self.compute_sha256(raster_path)
            if not actual_checksum or actual_checksum.lower() != metadata.checksum.lower():
                logger.warning(
                    f"Checksum mismatch for scene {scene_id}: "
                    f"expected={metadata.checksum}, actual={actual_checksum}"
                )
                return None

        # Ensure metadata file_path points to the validated cached raster
        metadata.file_path = raster_path
        if metadata.file_size_bytes is None:
            try:
                metadata.file_size_bytes = os.path.getsize(raster_path)
            except OSError:
                pass

        return metadata

    def has_scene(self, scene_id: str) -> bool:
        """Returns True if a valid, uncorrupted cached scene exists."""
        return self.validate_cache_entry(scene_id) is not None

    def get(self, scene_id: str) -> Optional[SceneMetadata]:
        """Retrieves cached SceneMetadata if present and valid, else returns None (cache miss)."""
        return self.validate_cache_entry(scene_id)

    def save_metadata(self, metadata: SceneMetadata) -> str:
        """Writes SceneMetadata to scene_meta.json under the scene directory."""
        scene_dir = self.get_scene_dir(metadata.scene_id)
        self._ensure_dir(scene_dir)
        meta_path = self.get_meta_path(metadata.scene_id)

        # Write to temporary file first, then atomic rename
        temp_meta_path = meta_path + ".tmp"
        with open(temp_meta_path, "w", encoding="utf-8") as f:
            f.write(metadata.model_dump_json(indent=2))
        os.replace(temp_meta_path, meta_path)
        return meta_path

    def save_raster(self, scene_id: str, raster_source: Union[str, bytes, BinaryIO]) -> str:
        """Stores GeoTIFF content as scene_sigma0_db.tif under the scene directory."""
        scene_dir = self.get_scene_dir(scene_id)
        self._ensure_dir(scene_dir)
        raster_path = self.get_raster_path(scene_id)
        temp_raster_path = raster_path + ".tmp"

        if isinstance(raster_source, str):
            if not os.path.isfile(raster_source):
                raise FileNotFoundError(f"Source raster file not found: {raster_source}")
            shutil.copy2(raster_source, temp_raster_path)
        elif isinstance(raster_source, bytes):
            with open(temp_raster_path, "wb") as f:
                f.write(raster_source)
        elif hasattr(raster_source, "read"):
            with open(temp_raster_path, "wb") as f:
                shutil.copyfileobj(raster_source, f)
        else:
            raise TypeError(f"Unsupported raster_source type: {type(raster_source)}")

        os.replace(temp_raster_path, raster_path)
        return raster_path

    def put(
        self,
        metadata: SceneMetadata,
        raster_source: Optional[Union[str, bytes, BinaryIO]] = None,
    ) -> SceneMetadata:
        """Stores both raster file and metadata in the cache.

        Args:
            metadata: SceneMetadata object to store.
            raster_source: Optional source file path, bytes, or file-like object.
                           If None, uses metadata.file_path.

        Returns:
            Updated SceneMetadata pointing to the cached file.
        """
        source = raster_source or metadata.file_path
        if source is None:
            raise ValueError(f"No raster source provided or found in metadata for scene {metadata.scene_id}")

        # 1. Save raster file
        cached_raster_path = self.save_raster(metadata.scene_id, source)

        # 2. Update metadata fields (checksum, file_path, file_size_bytes)
        file_size = os.path.getsize(cached_raster_path)
        calculated_checksum = self.compute_sha256(cached_raster_path)

        metadata_dict = metadata.model_dump()
        metadata_dict["file_path"] = cached_raster_path
        metadata_dict["file_size_bytes"] = file_size
        metadata_dict["checksum"] = metadata.checksum or calculated_checksum

        updated_metadata = SceneMetadata.model_validate(metadata_dict)

        # 3. Save metadata JSON
        self.save_metadata(updated_metadata)
        return updated_metadata

    def list_cached_scenes(self) -> List[str]:
        """Lists all scene IDs that have valid cache entries."""
        if not os.path.isdir(self.scenes_dir):
            return []
        valid_scenes = []
        for entry in os.listdir(self.scenes_dir):
            if os.path.isdir(os.path.join(self.scenes_dir, entry)):
                if self.has_scene(entry):
                    valid_scenes.append(entry)
        return sorted(valid_scenes)

    def delete(self, scene_id: str) -> bool:
        """Removes a scene entry from the cache."""
        scene_dir = self.get_scene_dir(scene_id)
        if os.path.isdir(scene_dir):
            try:
                shutil.rmtree(scene_dir)
                return True
            except OSError as err:
                logger.warning(f"Failed to delete scene directory {scene_dir}: {err}")
                return False
        return False

