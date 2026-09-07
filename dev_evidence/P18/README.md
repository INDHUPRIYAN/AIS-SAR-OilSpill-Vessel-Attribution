# P18 — Tile server and workspace depth

The workspace could draw a slick outline over a basemap but never the SAR
itself at native resolution. A polygon over OpenStreetMap is a claim; the
backscatter is the evidence, and an analyst who cannot see the pixels cannot
check the outline against them.

## Deviation: rio-tiler is not installable here

PROMPT 18 names `rio-tiler` as the dependency. It cannot be installed in this
environment:

```
rio-tiler 6.x depends on color-operations
Additionally, some packages in these conflicts have no matching
distributions available for your environment:  color-operations
ERROR: ResolutionImpossible
```

`color-operations` ships no wheel for this Python/platform and there is no
compiler toolchain available to build it.

Rather than drop the capability, the tile server is implemented on **rasterio**
— already a dependency, and what rio-tiler wraps. `WarpedVRT` does the
web-mercator reprojection, numpy the dB stretch, Pillow the PNG encode. The
deviation is recorded here and in the module docstring rather than hidden
behind an import guard, because a capability that silently does not exist is
worse than one that is documented as built differently.

## The endpoints

```
GET /api/tiles/{run_id}/{z}/{x}/{y}.png?db_min=&db_max=
GET /api/tiles/{run_id}/info
```

Measured against the frozen flagship's real Sentinel-1 raster
(19884 × 30034, bounds `[-92.995, 27.218, -90.087, 29.143]`):

| zoom | tile | bytes | budget |
|---|---|---|---|
| 8 | 62/107 | 24,537 | ✓ |
| 10 | 251/428 | 45,166 | ✓ |
| 12 | 1006/1713 | 63,720 | ✓ |
| 14 | 4025/6854 | 53,929 | ✓ |

All well under the 256 KB ceiling. Georeferencing verified against the scene's
own corner coordinates: a tile at the scene centre is **100% opaque**, a tile
3° west is **0% opaque**.

## Three properties the tiles hold to

**The stretch is the training range.** Defaults come from the frozen
`normalisation.yaml` clip — the same [−35, 0] dB the segmenter was trained on —
so what the analyst sees is what the model saw. A per-scene autostretch was
deliberately not used: it makes every scene look equally contrasty and hides
that one of them is mostly noise. `db_min`/`db_max` override for inspection,
and `X-Stretch-Db-Min` / `X-Stretch-Db-Max` report what was applied, so a
screenshot cannot silently misrepresent contrast.

**Nodata is transparent, never black.** Land and scene edges are alpha 0. Black
reads as *very dark water*, which is exactly what an oil slick looks like in
SAR — the one confusion this system cannot afford to introduce.

**A partially-covered tile composites, it does not stretch.** `WarpedVRT`
refuses boundless reads, so the overlap is read and pasted at the correct
offset. Clamping the read to the raster and stretching it across the whole tile
would smear edge pixels across open water.

## Viewport decimation, and the thing it must never do

`GET /api/runs/{id}/vessels_geojson?bbox=&zoom=`

`zoom` thins the number of **points** along each track — at zoom 4 a 300-point
line and a 20-point line are the same three pixels. `bbox` culls whole tracks.

**A ranked suspect is never culled.** A suspect that vanished when the analyst
zoomed out would be a map disagreeing with the ranking printed beside it, and
the first casualty of viewport culling is always the thing you were looking
for. Tested by requesting a box in the mid-Atlantic that nothing in the run
touches: every ranked MMSI still comes back.

**Thinning changes the picture, not the numbers.** `distance_km` and duration
are computed from every point before decimation, so a thinned track still
reports the distance the vessel actually travelled. The response carries
`points_drawn` / `points_total` and a `viewport` block, so a decimated map is
never mistaken for a sparse one.

## A real defect this surfaced

`vessels_geojson` **crashed outright on real AIS**:

```
ValueError: Out of range float values are not JSON compliant
```

The AIS standard sends `511` for "heading unavailable" and the ingest turns
that into NaN. **29,679 of the flagship's 86,830 rows carry no heading.** The
endpoint had only ever been exercised on synthetic AIS, where every field is
populated — so the bug was invisible until a test pointed it at the real-data
flagship.

Fixed by serialising NaN as `null`, which is the true statement: the vessel did
not transmit that field. A zero would have been worse than the crash — it reads
as "pointing due north".

## Frontend

The SAR layer was a single downsampled PNG stretched over the footprint —
adequate at zoom 8, useless at zoom 14 where an analyst is checking whether a
slick edge follows a genuine backscatter boundary or the segmenter invented
one. It is now a deck.gl `TileLayer` against the tile endpoint, reaching native
10 m resolution, with the stretch passed through so the drawn pixels remain the
ones the model saw.

## Tests

16, in `main_system/tests/test_tiles.py`. Georeferencing at two zooms (a
projection error that survives one zoom rarely survives two), transparency
outside the scene, the size budget at four zooms, the applied stretch in the
headers, inverted-stretch and impossible-coordinate refusals, session gating,
and four decimation tests including the ranked-suspect guarantee.

One test hygiene fix along the way: `test_tiles_require_a_session` cleared
cookies on a **module-scoped** client and did not restore them, so every later
test in the file asserted against a 401 it did not intend — a suite reporting a
working feature as broken. It now re-authenticates in a `finally`.

Evidence: [`pytest_full.txt`](pytest_full.txt).

## Not done

Route-level code-splitting for the maplibre/deck bundles is listed in P18 and
is **not** implemented. The build warns that chunks exceed 500 kB. This is a
load-time optimisation with no correctness or honesty dimension, and it is
recorded here as outstanding rather than quietly dropped.
