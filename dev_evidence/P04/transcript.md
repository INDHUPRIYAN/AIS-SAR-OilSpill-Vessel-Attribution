# P04 - uncertainty, forcing, age and weathering reach the contract

Executed: 2026-09-07T01:02:53Z

Four fields the engines measured and normalisation discarded.
Verified against the engine-native artefacts of the sealed run inv-final-audit.

## H-06 - ellipse axes
```
BEFORE  22 ellipse features, 0 with non-zero semi_major (all zero-filled)
AFTER   25 ellipse features, 25 with non-zero semi_major

first ellipse, engine-native (t=0):
  semi_major_m   13333.336
  semi_minor_m     658.021
  orientation_deg  100.006   <- slick's own orientation is 100.0
```
At t=0 the cloud IS the slick, so the ellipse bearing should match Engine A's
slick orientation. It does, to 0.006 deg - an independent check that the axes
describe the real covariance rather than a placeholder.

## H-06 - origin uncertainty
```
BEFORE  dropped entirely; metadata had no uncertainty key at all
AFTER   origin_uncertainty_km       : 0.9857
        origin_uncertainty_coverage : 0.908
        origin_uncertainty_method   : calibrated physics heuristic (not ML): ...
```
The method string says 'not ML' in the artefact itself, so the UI cannot
misattribute the hindcast.

## H-11 - forcing block
```
BEFORE  "forcing": {"currents": "currents.nc", "wind": "wind.nc",
                     "engine": "euler", "hours": 24}

AFTER
{
 "currents": {
  "provider": "Copernicus Marine Service (CMEMS)",
  "variables": [
   "uo",
   "vo"
  ],
  "fallback": null,
  "file": "currents.nc",
  "dataset": "OceanTrace Normalized Surface Currents",
  "normalised": "Normalized at 2026-08-24T22:22:32.684165+00:00"
 },
 "wind": {
  "provider": "Open-Meteo API",
  "variables": [
   "u10",
   "v10"
  ],
  "fallback": null,
  "file": "wind.nc",
  "dataset": "OceanTrace Normalized 10m Atmospheric Winds (Open-Meteo Fallback)",
  "normalised": "Normalized at 2026-08-24T22:23:29.348413+00:00"
 },
 "windage": 0.03,
 "engine": "euler",
 "ml_residual": {
  "model": null,
  "applied": false,
  "mean_correction_m_per_step": 0.0,
  "corrects": "forward-Euler truncation error at the operational timestep"
 },
 "hours": 24
}
```

**This surfaced a real finding.** The Bali run's wind did not come from ERA5 --
it came from the **Open-Meteo fallback**. The old artefact published only
`"wind": "wind.nc"`, so the fallback was invisible. Provider identity is read
from the grid file's own global attributes, so a fallback cannot be presented
as the primary.

## C-05 / C-07 - age qualification
```
BEFORE  age_hours_estimate 32.8 · age_confidence 0.25 · (age_method dropped)
AFTER   age_hours_estimate    : 32.8
        age_confidence       : 0.25      (numeric, for ranking)
        age_confidence_label : low       <- the UI renders LOW
        age_method           : damping+fay
```

## F1 - weathering assumptions
```
BEFORE  published metadata: scene_id, issued_utc, horizons_h, forcing, crs
AFTER   + weathering:
          model                  : fingas+mackay
          oil_type_assumed       : medium_crude
          temperature_c_assumed  : 15.0
          wind_speed_m_s_used    : 2.753
          confidence             : low
          processes_not_modelled : 7 listed
```
This is the replacement for image violation #21 ("Type: Possible Crude Oil"):
an assumed input, labelled as assumed, with the fate model's own LOW confidence
and an explicit list of what it does not model. Never a classifier.

## Frozen-surface note - contracts/schemas/geo.py was extended

Rule 7 freezes `contracts/schemas/`. P04's approved scope explicitly authorises
"extend contracts/schemas/geo.py additively (new optional fields)", and v1.1 did
not strike it. Resolution taken:

```
SlickProperties   + age_method: Optional[str]
                  + age_confidence_label: Optional[str]
OriginMetadata    + origin_uncertainty_km: Optional[float]
                  + origin_uncertainty_coverage: Optional[float]
                  + origin_uncertainty_method: Optional[str]
ForecastMetadata  + weathering: Optional[Dict[str, Any]]
```

All six are Optional with defaults, so no producer or consumer breaks and every
artefact written before this change still validates - pinned by
`test_sealed_artefacts_still_validate`, which re-validates the sealed run on disk.

`VESSEL_COLUMNS` (14) untouched. No contract version constant exists, so none bumped.

Fields were NOT nested under the free-form `forcing` dict to dodge the freeze:
origin uncertainty is not forcing and weathering is not forcing, and mislabelling
them to avoid a schema line would have been the worse contract abuse.

## Chennai result preserved

The P02 acceptance run (0 oil + 78 look-alike from the screen, 18 slicks from the
segmenter) is untouched. No threshold, gate or weight was changed in P04.
