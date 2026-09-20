"""Build the bundled geopolitical basemap from Natural Earth.

The globe's offline/default basemap: real countries, land borders, coastline,
country labels and sea/ocean names -- public-domain Natural Earth data, not
drawn by hand. It replaces `public/geo/land.json` (a 0.2-degree land raster
with no countries), and it is what the map shows when no tile provider is
configured or reachable (the e2e "airplane mode" test blocks every host).

Sources (https://www.naturalearthdata.com, public domain):
  ne_10m_admin_0_countries_ind   1:10m countries, **India point of view** --
                                 the variant Natural Earth publishes for
                                 deployments that must show India's boundaries
                                 as India depicts them. The default file does not.
  ne_50m_geography_marine_polys  1:50m ocean / sea / bay / gulf names

Usage:
  python scripts/build_basemap_natural_earth.py [--src DIR] [--tolerance 0.05]

Without --src the two files are downloaded from the natural-earth-vector
repository. Output: main_system/frontend/public/geo/ne/*.json + SOURCE.json.

Simplification is `shapely.coverage_simplify`, which keeps shared borders
shared -- neighbours never gap or overlap -- so land borders can be derived as
"country boundary that is not coastline".
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import shapely
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "main_system" / "frontend" / "public" / "geo" / "ne"
BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"
COUNTRIES = "ne_10m_admin_0_countries_ind"
MARINE = "ne_50m_geography_marine_polys"
MIN_COAST_LEN_DEG = 0.1          # islet outlines below ~11 km are not drawn as coast
MIN_PART_AREA_DEG2 = 0.0008   # keeps the Andaman, Nicobar and Lakshadweep islands
PRECISION = 3                 # ~110 m


def load(name: str, src: Path | None) -> dict:
    if src:
        return json.loads((src / f"{name}.geojson").read_text(encoding="utf-8"))
    with urllib.request.urlopen(f"{BASE}/{name}.geojson", timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def rounded(obj):
    """GeoJSON coordinates rounded at write time. The geometry itself is never
    snapped: snapping before the union moves shared edges apart and the border
    derivation below stops recognising the coast."""
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(obj[0], PRECISION), round(obj[1], PRECISION)]
        return [rounded(o) for o in obj]
    return obj


def geojson(geom) -> dict:
    m = mapping(geom)
    return {"type": m["type"], "coordinates": rounded(m["coordinates"])}


def drop_specks(geom):
    parts = getattr(geom, "geoms", [geom])
    kept = [p for p in parts if p.area >= MIN_PART_AREA_DEG2]
    if not kept:                       # a country that is only specks keeps its largest
        kept = [max(parts, key=lambda p: p.area)]
    return unary_union(kept) if len(kept) > 1 else kept[0]


def lines_of(geom) -> list:
    if geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if g.geom_type == "LineString" and g.length > 0]


def write(name: str, features: list, meta: dict) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"type": "FeatureCollection", "metadata": meta, "features": features},
                      ensure_ascii=False, separators=(",", ":"))
    (OUT / name).write_text(body, encoding="utf-8")
    return len(body.encode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=None)
    ap.add_argument("--tolerance", type=float, default=0.05)
    args = ap.parse_args()

    raw = load(COUNTRIES, args.src)["features"]
    geoms = [shapely.make_valid(shape(f["geometry"])) for f in raw]
    simplified = shapely.coverage_simplify(geoms, args.tolerance)
    geoms = [shapely.make_valid(g) for g in simplified]

    meta = {
        "source": "Natural Earth (public domain), naturalearthdata.com",
        "dataset": COUNTRIES, "point_of_view": "India (ne *_ind variant)",
        "tolerance_deg": args.tolerance, "precision_decimals": PRECISION,
        "generated_by": "scripts/build_basemap_natural_earth.py",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    countries, labels = [], []
    for f, g in zip(raw, geoms):
        p = f["properties"]
        if g.is_empty:
            continue
        props = {"name": p["NAME"], "a3": p["ADM0_A3"], "rank": int(p.get("LABELRANK") or 6)}
        countries.append({"type": "Feature", "properties": props, "geometry": geojson(drop_specks(g))})
        x, y = p.get("LABEL_X"), p.get("LABEL_Y")
        if x is None or y is None:
            pt = g.representative_point()
            x, y = pt.x, pt.y
        labels.append({"type": "Feature",
                       "properties": {**props, "min_zoom": float(p.get("MIN_LABEL") or 3)},
                       "geometry": {"type": "Point", "coordinates": [round(x, 3), round(y, 3)]}})

    # Coastline = the outline of all land; land borders = whatever country
    # outline is left. Holds because the coverage kept shared edges identical.
    land = unary_union(geoms)
    coast = land.boundary
    every = unary_union([g.boundary for g in geoms])
    borders = every.difference(coast.buffer(1e-6))
    coast_f = [{"type": "Feature", "properties": {}, "geometry": geojson(ln)}
               for ln in lines_of(shapely.line_merge(coast)) if ln.length >= MIN_COAST_LEN_DEG]
    border_f = [{"type": "Feature", "properties": {}, "geometry": geojson(ln)}
                for ln in lines_of(shapely.line_merge(borders))]

    marine = []
    for f in load(MARINE, args.src)["features"]:
        p = f["properties"]
        name = p.get("name") or p.get("label")
        if not name:
            continue
        pt = shapely.make_valid(shape(f["geometry"])).representative_point()
        marine.append({"type": "Feature",
                       "properties": {"name": name, "kind": p.get("featurecla"),
                                      "rank": int(p.get("scalerank") or 5)},
                       "geometry": {"type": "Point", "coordinates": [round(pt.x, 3), round(pt.y, 3)]}})

    sizes = {
        "countries.json": write("countries.json", countries, meta),
        "borders.json": write("borders.json", border_f, meta),
        "coast.json": write("coast.json", coast_f, meta),
        "country_labels.json": write("country_labels.json", labels, meta),
        "marine_labels.json": write("marine_labels.json", marine, {**meta, "dataset": MARINE}),
    }
    (OUT / "SOURCE.json").write_text(json.dumps({**meta, "files": sizes}, indent=2), encoding="utf-8")
    for k, v in sizes.items():
        print(f"{k:22s} {v / 1024:8.0f} KB")
    print(f"countries {len(countries)} · borders {len(border_f)} · coast {len(coast_f)} · marine {len(marine)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
