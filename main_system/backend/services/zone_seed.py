"""The Bay of Bengal operational zone set, seeded once.

Why a computed seed rather than a table of typed coordinates
------------------------------------------------------------
The operational zones must sit inside the theatre boundary, and
`assert_inside_parent` enforces that on every edit. If the sub-zone polygons
were hand-typed, six of them would each have to agree with the theatre's own
vertices to within a tolerance, and the first person to nudge the theatre would
produce a seed that the API's own validation rejects.

So the theatre is the only hand-drawn shape here. Each operational zone is the
**intersection** of the theatre with a latitude/longitude band, computed by
Shapely at seed time. Containment is then true by construction, and moving the
theatre re-derives the divisions instead of breaking them.

Honesty about what these boundaries are
---------------------------------------
The theatre polygon is an **offshore monitoring boundary**, not a coastline and
not a maritime claim. It deliberately stands off the littoral states' coasts by
a margin rather than tracing them, because tracing a coastline from memory
would put a wrong line on a map that operators read as authoritative. Its
`jurisdiction` code is ``INTL`` for the same reason: the Bay of Bengal is
bordered by India, Bangladesh, Myanmar, Sri Lanka, Thailand and Indonesia, and
stamping any one country's code on the whole basin would assert something
untrue.

The operational divisions are **tasking boundaries**. They decide whose desk an
incident lands on. They assert nothing about EEZ, territorial sea or
jurisdiction, and the seeded `notes` say so on every row -- that text is what
the Zone Management page and the report annex display.

The southern limit follows the IHO convention (a line from Dondra Head on Sri
Lanka to the northern tip of Sumatra) at whole-degree precision.
"""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy.orm import Session

from backend.models.db import Zone, utcnow
from backend.services import zones as zsvc

THEATRE_ID = "zone-bob"
THEATRE_NAME = "Bay of Bengal"

# Offshore monitoring boundary, WGS84, LONGITUDE FIRST. Read counter-clockwise
# from the southern IHO limit. Every vertex is offshore on purpose -- see the
# module docstring.
THEATRE_RING: list[tuple[float, float]] = [
    (80.80, 5.50),    # southern limit, west end (S of Dondra Head, Sri Lanka)
    (95.30, 5.50),    # southern limit, east end (longitude of N Sumatra)
    (95.30, 8.00),
    (93.00, 11.00),   # west of the Andaman & Nicobar chain
    (93.20, 14.00),
    (94.50, 16.00),   # offshore of the Gulf of Martaban approach
    (92.50, 20.50),   # offshore Myanmar / Bangladesh
    (91.00, 21.50),   # offshore Bangladesh
    (88.50, 21.00),   # offshore of the Sundarbans
    (86.50, 20.30),   # offshore Odisha
    (84.50, 18.50),   # offshore Andhra Pradesh
    (82.00, 16.30),   # offshore Kakinada
    (80.50, 13.00),   # offshore Chennai
    (80.50, 10.50),   # NE of Point Pedro, Sri Lanka
    (82.20, 9.00),    # east of Sri Lanka, clear of the coast
    (82.00, 6.20),
]

THEATRE_NOTES = (
    "Offshore monitoring theatre for the Bay of Bengal. This boundary is an "
    "operational monitoring extent, NOT a coastline and NOT a maritime claim. "
    "It stands off the littoral coasts by a margin rather than tracing them. "
    "Jurisdiction code INTL: the basin is bordered by India, Bangladesh, "
    "Myanmar, Sri Lanka, Thailand and Indonesia, and no single national code "
    "applies. Southern limit follows the IHO convention (Dondra Head to the "
    "northern tip of Sumatra) at whole-degree precision."
)

DIVISION_NOTE = (
    "Operational tasking division. Decides which officer receives incidents "
    "detected here. Asserts nothing about EEZ, territorial sea or national "
    "jurisdiction."
)

# Each division is a lat/lon band, intersected with the theatre at seed time.
# The bands tile the theatre's bounding box exactly, so the divisions cover all
# of it with no interior overlap. `None` means unbounded on that side.
#
# The bands SHARE their internal edges -- Zone 03 ends at lon 87.0 and Zone 04
# begins there. A point lying exactly on a shared edge is therefore covered by
# both neighbours, and `zones.zone_for_point` resolves it with a documented
# tie-break (deepest wins, then lowest zone id). Do not try to fix this by
# nudging a band to 86.999999: that creates a real gap of ocean belonging to
# nobody, which is strictly worse than an overlap that routing resolves.
DIVISIONS: list[dict] = [
    {"id": "zone-bob-01", "name": "Zone 01 - North-West Approaches",
     "lon": (None, 88.0), "lat": (18.0, None),
     "note": "Odisha and northern Andhra Pradesh offshore approaches."},
    {"id": "zone-bob-02", "name": "Zone 02 - Delta Approaches",
     "lon": (88.0, None), "lat": (18.0, None),
     "note": "Ganges-Meghna delta and northern Myanmar offshore approaches."},
    {"id": "zone-bob-03", "name": "Zone 03 - West Central Bay",
     "lon": (None, 87.0), "lat": (11.0, 18.0),
     "note": "Central Coromandel and Andhra offshore shipping lanes."},
    {"id": "zone-bob-04", "name": "Zone 04 - East Central Bay",
     "lon": (87.0, None), "lat": (11.0, 18.0),
     "note": "Central bay and Myanmar offshore approaches."},
    {"id": "zone-bob-05", "name": "Zone 05 - South-West Bay",
     "lon": (None, 88.0), "lat": (None, 11.0),
     "note": "Sri Lanka eastern approaches and the southern shipping lane."},
    {"id": "zone-bob-06", "name": "Zone 06 - South-East Bay",
     "lon": (88.0, None), "lat": (None, 11.0),
     "note": "Nicobar approaches and the Malacca-bound lane."},
]


def _theatre_polygon():
    from shapely.geometry import Polygon

    poly = Polygon(THEATRE_RING)
    if not poly.is_valid:
        from shapely.validation import explain_validity

        raise ValueError(f"THEATRE_RING is not a simple polygon: "
                         f"{explain_validity(poly)}")
    return poly


def _band_box(theatre, lon_range, lat_range):
    """The division's band, clipped to the theatre's bounding box."""
    from shapely.geometry import box

    t_lon_min, t_lat_min, t_lon_max, t_lat_max = theatre.bounds
    # A one-degree overshoot so the band fully covers the theatre edge; the
    # intersection trims it back to the real boundary.
    lon_min = lon_range[0] if lon_range[0] is not None else t_lon_min - 1
    lon_max = lon_range[1] if lon_range[1] is not None else t_lon_max + 1
    lat_min = lat_range[0] if lat_range[0] is not None else t_lat_min - 1
    lat_max = lat_range[1] if lat_range[1] is not None else t_lat_max + 1
    return box(lon_min, lat_min, lon_max, lat_max)


def _largest_part(geom):
    """One polygon from a possibly multi-part intersection.

    A band can clip the theatre into two pieces (the west edge steps around Sri
    Lanka). Keeping only the largest part is the honest simplification: a zone
    is a contiguous area of responsibility, and a 3 km² sliver on the far side
    of an island is not something an officer patrols. The dropped area is
    reported by `seed_bay_of_bengal` rather than silently discarded.
    """
    if geom.geom_type == "Polygon":
        return geom, 0.0
    parts = sorted(getattr(geom, "geoms", []), key=lambda g: g.area,
                   reverse=True)
    if not parts:
        return None, 0.0
    dropped = sum(p.area for p in parts[1:])
    return parts[0], dropped


def seed_bay_of_bengal(db: Session, force: bool = False) -> dict:
    """Create the theatre and its operational divisions. Idempotent.

    Skips entirely when any zone already exists, unless `force`. That is the
    same rule the AOI YAML migration uses, and for the same reason: a seed that
    re-ran on every boot would resurrect a zone an operator deliberately
    deleted, and would fight a boundary they deliberately moved.

    `force` re-seeds only the zones this function owns, and still refuses to
    touch one whose `source` is not "seed" -- an operator-drawn zone that
    happens to share an id is not ours to overwrite.
    """
    existing = db.query(Zone).count()
    if existing and not force:
        return {"seeded": False, "reason": f"{existing} zone(s) already exist",
                "zones": []}

    theatre_poly = _theatre_polygon()
    created: list[str] = []
    skipped: list[dict] = []
    notes: list[str] = []

    def _upsert(zone_id: str, name: str, poly, kind: str,
                parent_id: Optional[str], note: str) -> bool:
        row = db.get(Zone, zone_id)
        if row is not None:
            if row.source != "seed":
                skipped.append({"id": zone_id,
                                "reason": f"exists with source={row.source!r}; "
                                          "not overwriting an operator-drawn zone"})
                return False
            db.delete(row)
            db.flush()
        geom = zsvc.normalise_geometry(json.loads(
            json.dumps({"type": "Polygon",
                        "coordinates": [list(map(list, poly.exterior.coords))]})))
        db.add(Zone(
            id=zone_id, name=name, kind=kind, parent_id=parent_id,
            geometry_json=geom.geometry_json,
            bbox_json=json.dumps(geom.bbox, separators=(",", ":")),
            jurisdiction="INTL",
            protected=(kind == "jurisdiction"),
            status="active", area_km2=geom.area_km2, notes=note,
            revision=1, source="seed", created_utc=utcnow()))
        created.append(zone_id)
        return True

    _upsert(THEATRE_ID, THEATRE_NAME, theatre_poly, "jurisdiction", None,
            THEATRE_NOTES)
    db.flush()

    for spec in DIVISIONS:
        band = _band_box(theatre_poly, spec["lon"], spec["lat"])
        piece = theatre_poly.intersection(band)
        if piece.is_empty:
            skipped.append({"id": spec["id"],
                            "reason": "band does not intersect the theatre"})
            continue
        poly, dropped = _largest_part(piece)
        if poly is None or poly.area <= 0:
            skipped.append({"id": spec["id"], "reason": "empty intersection"})
            continue
        if dropped > 0:
            notes.append(
                f"{spec['id']}: kept the largest of "
                f"{len(getattr(piece, 'geoms', []))} disjoint parts; dropped "
                f"{dropped / piece.area * 100:.2f}% of the band's area")
        _upsert(spec["id"], spec["name"], poly, "operational", THEATRE_ID,
                f"{spec['note']} {DIVISION_NOTE}")

    db.commit()

    theatre_row = db.get(Zone, THEATRE_ID)
    theatre_area = (theatre_row.area_km2 or 0.0) if theatre_row else 0.0
    divisions_area = sum((db.get(Zone, z).area_km2 or 0.0) for z in created
                         if z != THEATRE_ID)

    return {
        "seeded": bool(created),
        "zones": created,
        "skipped": skipped,
        "notes": notes,
        "theatre_area_km2": round(theatre_area, 2),
        "divisions_area_km2": round(divisions_area, 2),
        # Reported rather than asserted: the divisions should tile the theatre,
        # and a gap would mean some water routes to nobody. The caller (and the
        # test) checks it instead of this function quietly assuming it.
        "coverage_fraction": round(_planar_coverage(db, created), 6),
    }


def _planar_coverage(db: Session, created: list[str]) -> float:
    """What fraction of the theatre the divisions tile, measured PLANAR.

    This must not use the stored `area_km2`, and the reason is a genuine trap.
    Those figures are geodesic: `pyproj` treats each polygon edge as a geodesic
    on the ellipsoid. Shapely, however, clips in planar lon/lat space, so a
    division's edge along the theatre boundary is a straight line in degrees
    with extra vertices inserted along it -- and a geodesic path through
    intermediate points is not the same path as one geodesic between the
    endpoints. Summing the geodesic areas of the six divisions therefore came
    to 100.19% of the theatre's geodesic area, which reads exactly like a
    0.19% overlap bug and is not one.

    Planar degree-area is meaningless as an area and exactly right as a ratio
    here: it is the space Shapely actually clipped in, so it is additive, and
    the distortion cancels between numerator and denominator.
    """
    from shapely.ops import unary_union

    theatre = db.get(Zone, THEATRE_ID)
    if theatre is None:
        return 0.0
    theatre_poly = zsvc.zone_polygon(theatre)
    if theatre_poly.area <= 0:
        return 0.0
    parts = [zsvc.zone_polygon(db.get(Zone, z)) for z in created
             if z != THEATRE_ID and db.get(Zone, z) is not None]
    if not parts:
        return 0.0
    # `unary_union` rather than a sum, so an accidental overlap between two
    # divisions shows up as coverage below 1.0 instead of inflating past it.
    return unary_union(parts).intersection(theatre_poly).area / theatre_poly.area


def seed_if_empty(db: Session) -> Optional[str]:
    """Boot hook: seed only a database that has no zones at all."""
    if db.query(Zone).count():
        return None
    result = seed_bay_of_bengal(db)
    if not result["seeded"]:
        return None
    return (f"seeded {len(result['zones'])} Bay of Bengal zone(s); "
            f"divisions cover {result['coverage_fraction']:.4f} of the theatre")
