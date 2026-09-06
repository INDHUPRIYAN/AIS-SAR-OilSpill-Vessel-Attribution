# P02 — UI launches real runs (audit N-14)

Executed: 2026-09-06T17:35:12Z

## Before
```
Dashboard.jsx:31   scene_meta_path: "contracts/mocks/scene_meta.json"
-> every browser-launched run was 1-of-5-real
```

## GET /api/scenes/local
```
count=7  available=7  default=chennai-2017-01-29

* chennai-2017-01-29  REAL (Sentinel-1 acquisition)       measured  OK
  emed-oil-00000      REAL (SAR corpus · held out)       assigned  OK
  gulf-oil-00035      REAL (SAR corpus · held out)       assigned  OK
  sumatra-oil-00055   REAL (SAR corpus · held out)       assigned  OK
  java-oil-00081      REAL (SAR corpus · held out)       assigned  OK
  bali-oil-00074      REAL (SAR corpus · TRAINING IMAGE) assigned  OK
  mock-smoke          MOCK (pipeline smoke test)          assigned  OK
```

## Acceptance run — created and launched through the API path the UI uses
```
run: inv-14b9860484-173035  state: complete
  detect          ok      real      167.53s  engine=ml, 0 oil + 78 look-alike candidate(s), confide
  characterise    ok      real       54.15s  Engine A: 18 slick(s)
  drift_hindcast  ok      real        4.59s  Engine B [euler] hindcast 24h, currents+wind
  drift_forecast  ok      real        2.80s  Engine B [euler] forecast 24h, currents+wind
  attribution     ok      real        2.61s  Engine C: 1 suspect(s), top MMSI 237717848 score 0.64

manifest: stages_real=5  stages_total=5  stages_mock=0  total=244.78s
ACCEPTANCE PASS
```

## Two provenance findings surfaced while building this

### F2 — the previous demo scene is TRAINING data
```
Bali demo raster  : data/raw/trujillo/part3/Images/Oil/00074.tif
test split has    : 26 Oil/ scenes, Oil/00074 NOT among them
training note     : model card, 'trujillo part 1+2+3carve'
=> the deployed segmenter trained on the old demo scene.
   Its 0.9332 confidence is memorisation, not accuracy.
```
Kept in the catalog (sealed runs reference it) but labelled
`REAL (SAR corpus · TRAINING IMAGE)` with an explicit caveat.

### F3 — corpus acquisition times are ASSIGNED, not measured
```
raster TIFF tags      : {'AREA_OR_POINT': 'Area'}  -- no date
zenodo_*.json         : file manifests (Key/Size/Md5/Url) -- no dates
=> Trujillo scene_meta acquired_utc values were assigned for the demo.
   The hindcast integrates BACKWARD from that timestamp, so the origin
   window and the AIS query window inherit the same basis.
```
Declared per entry via `time_basis`; the new default avoids it entirely.

### Why the default changed
```
chennai-2017-01-29  S1A_IW_GRDH_1SDV_20170129T003132_..._COG
  provenance : sentinel1_real   (genuine Sentinel-1A IW GRDH product)
  time_basis : measured         (acquired_utc == product id 20170129T003132)
  raster     : 28072 x 21162, EPSG:4326, source='real'
```

## Honest result worth recording
```
detect: 0 oil + 78 look-alike candidates  (YOLO11n screen)
characterise: 18 slick(s)                 (U-Net segmenter)
```
The screen and the segmenter disagree on this scene. That is a real
measurement on real data, not a defect to tune away -- and exactly the
kind of finding the look-alike gate exists to surface. Recorded as-is.
