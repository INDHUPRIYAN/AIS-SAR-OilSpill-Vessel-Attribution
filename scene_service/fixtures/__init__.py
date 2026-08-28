"""Deterministic Offline Demo Fixtures for Satellite Scene Service.

Provides offline Sentinel-1 SAR scene metadata and synthetic GeoTIFF raster fixtures
for local testing, demonstrations, and offline verification without remote dependencies.
"""

import hashlib
import json
import os
import struct
from pathlib import Path
from typing import Tuple

FIXTURES_DIR = Path(__file__).parent.resolve()
DEMO_SCENE_DIR = FIXTURES_DIR / "demo_scene"
DEMO_META_PATH = DEMO_SCENE_DIR / "scene_meta.json"
DEMO_RASTER_PATH = DEMO_SCENE_DIR / "scene_sigma0_db.tif"

SCENE_ID = "S1A_IW_GRDH_1SDV_20231012T172530"


def generate_deterministic_tiff(width: int = 64, height: int = 64) -> Tuple[bytes, str]:
    """Generates a valid, deterministic uncompressed 8-bit grayscale baseline TIFF raster.

    The raster contains a synthetic SAR ocean backscatter surface with a low-backscatter
    center feature representing an offline demonstration SAR anomaly.
    """
    header = b"II\x2a\x00\x08\x00\x00\x00"
    num_tags = 8
    ifd_offset = 8
    tag_size = 12
    ifd_len = 2 + (num_tags * tag_size) + 4
    data_offset = ifd_offset + ifd_len

    raw_pixels = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            dist_sq = (x - width // 2) ** 2 + (y - height // 2) ** 2
            if dist_sq < 100:
                val = 20  # Low backscatter anomaly / slick
            else:
                val = int(128 + 30 * ((x * 7 + y * 13) % 5 - 2))  # Ocean clutter
            raw_pixels[y * width + x] = max(0, min(255, val))

    pixel_bytes = bytes(raw_pixels)
    byte_count = len(pixel_bytes)

    tags = [
        (256, 3, 1, width),          # ImageWidth (SHORT)
        (257, 3, 1, height),         # ImageLength (SHORT)
        (258, 3, 1, 8),              # BitsPerSample (SHORT = 8)
        (259, 3, 1, 1),              # Compression (SHORT = 1, None)
        (262, 3, 1, 1),              # PhotometricInterpretation (SHORT = 1, BlackIsZero)
        (273, 4, 1, data_offset),    # StripOffsets (LONG)
        (278, 3, 1, height),         # RowsPerStrip (SHORT)
        (279, 4, 1, byte_count),     # StripByteCounts (LONG)
    ]

    ifd_bytes = struct.pack("<H", num_tags)
    for tag_id, type_id, count, val in tags:
        ifd_bytes += struct.pack("<HHII", tag_id, type_id, count, val)
    ifd_bytes += struct.pack("<I", 0)  # Next IFD offset = 0

    full_tiff = header + ifd_bytes + pixel_bytes
    sha256 = hashlib.sha256(full_tiff).hexdigest()
    return full_tiff, sha256


def ensure_demo_fixture() -> None:
    """Ensures demo fixture directory, metadata JSON, and TIFF raster exist on disk."""
    DEMO_SCENE_DIR.mkdir(parents=True, exist_ok=True)

    tiff_bytes, sha256 = generate_deterministic_tiff(64, 64)

    with open(DEMO_RASTER_PATH, "wb") as f:
        f.write(tiff_bytes)

    metadata = {
        "scene_id": SCENE_ID,
        "platform": "Sentinel-1A",
        "acquisition_time": "2023-10-12T17:25:30Z",
        "bbox": [2.5, 51.5, 3.2, 52.1],
        "product_type": "GRD",
        "polarisation": "VV+VH",
        "orbit_direction": "DESCENDING",
        "file_path": str(DEMO_RASTER_PATH.as_posix()),
        "checksum": sha256,
        "file_size_bytes": len(tiff_bytes),
        "download_url": f"mock://cdse.dataspace.copernicus.eu/demo/{SCENE_ID}.tif",
        "_fixture_note": "OFFLINE_DEMO_FIXTURE — Synthetic Sentinel-1 SAR backscatter fixture for testing and demonstration",
    }

    with open(DEMO_META_PATH, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)


# Automatically create the fixture on module import if not present
ensure_demo_fixture()
