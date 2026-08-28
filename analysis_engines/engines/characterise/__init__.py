"""Engine A - Spill characterisation: raw mask -> slick.geojson.

Public entry point:

    from engines.characterise import characterise
    status = characterise("mask.tif", "scene_meta.json", "slick.geojson")

or via the frozen CLI:

    python -m engines.characterise --mask ... --scene-meta ... --out slick.geojson
"""

from .runner import characterise

__all__ = ["characterise"]
