# Engine A — Characterisation

Raw detection mask → `slick.geojson`. Deterministic geometry, no ML.

```bash
python -m engines.characterise --mask <tif> --scene-meta <json> --out slick.geojson
```

| | |
|---|---|
| **In** | 0/1 mask GeoTIFF (Indhu's `/detect`), `scene_meta.json`, optional Sigma0 dB scene |
| **Out** | `slick.geojson` (handbook §4.2) + status object |
| **Errors** | `MISSING_INPUT`, `EMPTY_MASK` |
| **Config** | `config/characterise.yaml` |

## Options

```
--scene-db <tif>        Sigma0 dB scene for the damping ratio
                        (default: the "file_path" in the scene metadata)
--config <yaml>         tuning file
--confidence <0-1>      used only when the scene metadata omits it
--slick-id-prefix <s>   default: the scene_id's trailing token
```

## What it computes

Connected components (8-connectivity) → per slick: area, perimeter, centroid, best-fit
ellipse axes and orientation, boundary polygon, backscatter damping ratio, Fay age proxy.

Accuracy against a drawn ellipse of 7.9 × 2.4 km at 62°, area 14.891 km²:

| Quantity | Measured | Error |
|---|---|---|
| Area | 14.890 km² | −0.007% |
| Perimeter | 17.375 km | +0.12% |
| Major / minor axis | 7.900 / 2.400 km | <0.01% |
| Orientation | 61.997° | −0.003° |
| Damping ratio | 7.008 dB | +0.11% |

## Conventions

- **`orientation_deg` is a bearing from true north**, clockwise, folded to [0, 180)
  because an ellipse axis is undirected. Chosen here (no handbook section fixes it) so it
  matches AIS COG and Engine C can subtract the two directly.
- All metric quantities are computed in a local east/north frame at each slick's own
  centroid, and pixel area is accumulated per raster row — a degree of longitude is
  ~10.84 m at the demo latitude against ~11.06 m for latitude.
- Perimeter comes from the polygon simplified at 1.5 px. The raw raster staircase
  overstates it by ~31%; simplification beats `skimage.perimeter_crofton` (+0.86%) while
  staying correct for anisotropic pixels.

## Known issues

`age_hours_est` is dominated by the assumed 1 mm slick thickness (`t ∝ h^−4/3`), not by
the measurement, which is why `age_confidence` is always `"low"`. See
[KNOWN_ISSUES.md](../../KNOWN_ISSUES.md) §2.
