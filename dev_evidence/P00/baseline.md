# P00 — Baseline capture

Captured: 2026-09-06T16:38:09Z

## Git state
```
HEAD:   b2a8fd58d52e13e5994d536fa40eb92a0027b01c
SHORT:  b2a8fd5
BRANCH: feat/indhu-detection-pipeline
REMOTE: https://INDHUPRIYAN@github.com/INDHUPRIYAN/AIS-SAR-OilSpill-Vessel-Attribution.git
```

### Working tree summary
```
     63 ??
      2 D
    167 M
TOTAL: 232 entries
```

## Database — canonical DB
```
path: data/oceantrace.db
  aoi_watch              2
  api_calls           7647
  api_keys               0
  api_providers         10
  audit_log              1
  decisions              1
  investigations       137
  runs                  91
```

## Database — orphan (H3, removed in P06)
```
path: main_system/data/oceantrace.db
  api_calls             49
  api_keys               0
  api_providers         10
  audit_log              0
  investigations         0
  runs                   0
```

## Live route surface (uvicorn, port 8077)
```
GET    /
GET    /api/aois
POST   /api/aois/poll
GET    /api/aois/{aoi_id}
POST   /api/aois/{aoi_id}/poll
GET    /api/apis/status
POST   /api/apis/test-all
GET    /api/apis/{provider}/calls
POST   /api/apis/{provider}/test
GET    /api/decisions
GET    /api/investigations
POST   /api/investigations
GET    /api/investigations/{investigation_id}
GET    /api/investigations/{investigation_id}/layers/{layer}
POST   /api/investigations/{investigation_id}/replay
POST   /api/investigations/{investigation_id}/run
GET    /api/investigations/{investigation_id}/status
GET    /api/investigations/{investigation_id}/suspects
GET    /api/keys
PUT    /api/keys
GET    /api/keys/audit
POST   /api/keys/{provider}/test
GET    /api/layers/{run_id}/{layer}
GET    /api/metrics
GET    /api/replay/runs
GET    /api/runs
GET    /api/runs/{run_id}
GET    /api/runs/{run_id}/decisions
POST   /api/runs/{run_id}/decisions
GET    /api/runs/{run_id}/export
GET    /api/runs/{run_id}/forcing_field
GET    /api/runs/{run_id}/mask_png
GET    /api/runs/{run_id}/scene_png
GET    /api/runs/{run_id}/verify
GET    /api/runs/{run_id}/vessels_geojson
GET    /health
GET    /healthz
GET    /readyz

TOTAL ROUTES: 38
```

## Defect reproduction (all six)

### D1 · M-05 — /api/metrics serves the UNDEPLOYED epoch-29 checkpoint
```
served checkpoint_epoch : 29
served checkpoint       : None
deployed identity       : unet-r34-fullcorpus-e48 (weights/model_card.md)
VERDICT                 : DEFECT REPRODUCED
```

### D3 · P2/C7 — scheduler import fails at RUNTIME (masked by pytest.ini)
```
$ curl -X POST "/api/aois/poll?dry_run=true"
{
 "started_utc": "2026-09-06T16:39:18.985580+00:00",
 "polled": [
  "danish-straits",
  "chennai-coast"
 ],
 "skipped": [],
 "disabled": [
  "us-gulf-coast",
  "mumbai-offshore"
 ],
 "new_scenes": [],
 "opened": [],
 "errors": [
  {
   "aoi_id": "danish-straits",
   "error_class": "BAD_RESPONSE",
   "detail": "ModuleNotFoundError: No module named 'satellite'"
  },
  {
   "aoi_id": "chennai-coast",
   "error_class": "BAD_RESPONSE",
   "detail": "ModuleNotFoundError: No module named 'satellite'"
  }
 ]
}

VERDICT: DEFECT REPRODUCED
```

### D5 · X-05 — ?lite=true is SLOWER than the full file (O(n^2) _lite_origin)
```
run: inv-final-audit
  full            0.159s     1399106 bytes
  ?lite=true      3.117s      492579 bytes
```

### D2 · N-14 — UI hardcodes the mock scene (1-of-5-real runs from the UI)
```
31:        name, scene_meta_path: "contracts/mocks/scene_meta.json",
VERDICT: DEFECT REPRODUCED — mock path is the only UI launch input
```

### D4 · H-06 — every published confidence ellipse has zero radius
```
ellipse features       : 22
with non-zero semi_major: 0
metadata keys published : ['scene_id', 'origin_window_start_utc', 'origin_window_end_utc', 'backtrack_hours', 'n_particles', 'timestep_minutes', 'forcing', 'source', 'crs']
native origin_uncertainty_km : 0.9857  (DROPPED by normalise)
VERDICT: DEFECT REPRODUCED
```

### D6 · PC-11 — the static mock provider snapshot is copied into every run dir
```
mock sha256: f08862b1f34821bd
run dirs with provider_status.json : 95
byte-identical to the MOCK         : 19
VERDICT: DEFECT REPRODUCED
```

### F1 · NEW — weathering block dropped by normalise (same class as H-06/H-11)
```
native metadata keys   : ['forcing', 'weathering']
published metadata keys: ['scene_id', 'issued_utc', 'horizons_h', 'forcing', 'crs']

native weathering block (DROPPED):
   model                   : fingas+mackay
   oil_type_assumed        : medium_crude
   temperature_c_assumed   : 15.0
   wind_speed_m_s_used     : 2.753
   confidence              : low
   processes_not_modelled  : ['dispersion', 'dissolution', 'photo-oxidation', 'biodegradation', 'sedimentation', 'spreading-driven thickness change', 'coastline stranding']

VERDICT: CONFIRMED � weathering absent from published artefact
```

## v1.1 Part B — oil-type classifier null-result grep
```
$ grep -rniE "oil_type|crude|emulsi|classif" analysis_engines/ scene_service/ main_system/backend/services/ --include=*.py

Hits that could indicate a CLASSIFIER:
analysis_engines/engines/attribution/scoring.py:12:This is deliberately **not** a trained classifier (handbook pitfall #8): no ground truth
scene_service/satellite/asf_adapter.py:25:    classify_http_status,
scene_service/satellite/asf_adapter.py:150:                    raise classify_http_status(resp.status, f"ASF search returned HTTP status {resp.status}", "ASF")
scene_service/satellite/asf_adapter.py:156:            raise classify_http_status(err.code, f"ASF search failed: HTTP {err.code}", "ASF") from None
scene_service/satellite/asf_adapter.py:327:                        raise classify_http_status(resp.status, f"Download returned HTTP status {resp.status}", "ASF")
scene_service/satellite/cdse_adapter.py:24:    classify_http_status,
scene_service/satellite/cdse_adapter.py:101:                    raise classify_http_status(resp.status, f"CDSE authentication returned HTTP status {resp.status}", "CDSE")
scene_service/satellite/cdse_adapter.py:113:            raise classify_http_status(err.code, f"CDSE authentication failed: HTTP {err.code}", "CDSE") from None
scene_service/satellite/cdse_adapter.py:225:                    raise classify_http_status(resp.status, f"CDSE search returned HTTP status {resp.status}", "CDSE")
scene_service/satellite/cdse_adapter.py:230:            raise classify_http_status(err.code, f"CDSE catalog search failed: HTTP {err.code}", "CDSE") from None
scene_service/satellite/cdse_adapter.py:382:                        raise classify_http_status(resp.status, f"Download returned HTTP status {resp.status}", "CDSE")
scene_service/satellite/errors.py:7:classify. These exceptions subclass ``RuntimeError`` on purpose so every
scene_service/satellite/errors.py:16:    """Base class for a classified provider failure.
scene_service/satellite/errors.py:61:def classify_http_status(status_code: int, message: str, provider: Optional[str] = None) -> ProviderError:
scene_service/tests/test_errors_and_status.py:20:    classify_http_status,
scene_service/tests/test_errors_and_status.py:45:    def test_03_classify_http_status(self):
scene_service/tests/test_errors_and_status.py:46:        self.assertIsInstance(classify_http_status(401, "x"), AuthFailedError)
scene_service/tests/test_errors_and_status.py:47:        self.assertIsInstance(classify_http_status(403, "x"), AuthFailedError)
scene_service/tests/test_errors_and_status.py:48:        self.assertIsInstance(classify_http_status(429, "x"), RateLimitedError)
scene_service/tests/test_errors_and_status.py:49:        self.assertIsInstance(classify_http_status(408, "x"), ProviderTimeoutError)
scene_service/tests/test_errors_and_status.py:50:        self.assertIsInstance(classify_http_status(500, "x"), UnavailableError)
scene_service/tests/test_errors_and_status.py:51:        self.assertIsInstance(classify_http_status(503, "x"), UnavailableError)
scene_service/tests/test_errors_and_status.py:52:        self.assertIsInstance(classify_http_status(404, "x"), BadResponseError)
main_system/backend/services/detection/service.py:366:def classify_regions(regions: List[dict], screen: Optional[dict],
main_system/backend/services/detection/service.py:386:    and inventing one from its silence is worse than not classifying at all.
main_system/backend/services/detection/service.py:455:                    classify_regions(regions, screen)
main_system/backend/services/detection/service.py:480:    classify_regions(res.regions, screen)
main_system/backend/services/scheduler/watcher.py:66:def _classify(exc: BaseException) -> str:
main_system/backend/services/scheduler/watcher.py:194:            error_class = _classify(exc)

-> The only 'classif' hits are DISCLAIMERS. Verbatim:
   analysis_engines/engines/attribution/scoring.py:12:This is deliberately **not** a trained classifier (handbook pitfall #8): no ground truth

oil_type occurrences are an ASSUMED INPUT to the weathering model, never a prediction:
   analysis_engines/engines/drift/weathering.py:97:DEFAULT_OIL_TYPE = "medium_crude"
   analysis_engines/engines/drift/weathering.py:259:        "oil_type_assumed": oil_type,
   analysis_engines/tests/test_drift_weathering.py:141:    assert out["oil_type_assumed"] in OIL_TYPES
   analysis_engines/tests/test_drift_weathering.py:156:    assert out["oil_type_assumed"] == DEFAULT_OIL_TYPE

NULL RESULT CONFIRMED: no oil-type classifier, no thickness measurement, in any service.
```

## Frozen-surface fingerprints (Rule 7 — re-verify after every prompt)
```
7560abe18b0bc9e7  contracts/schemas/tabular.py
2099a009a84a5222  contracts/schemas/geo.py
08b8ddd808fb18af  contracts/schemas/scene.py
95570a4f51dd43a2  contracts/schemas/common.py
11e6f00a9b8bbfec  contracts/schemas/__init__.py
78ce232050e72835  pyproject.toml
02ac4f8c7101396f  pytest.ini

VESSEL_COLUMNS count : 14  (MUST remain 14)
stage names          : ['detect', 'characterise', 'drift_hindcast', 'drift_forecast', 'attribution']
pyproject packages   : packages = []
```

## Regression baseline — full suite
```
$ .venv/Scripts/python -m pytest
654 passed, 3 skipped, 25 warnings in 83.41s (0:01:23)

Skips (all 3 are OpenDrift-not-installed):
654 passed, 3 skipped, 25 warnings in 83.41s (0:01:23)

GATE: every subsequent prompt must hold >= 654 passed, 3 skipped.
```

## CONFLICT C-P00b — D4's rollback anchor protects nothing for 43 source files

`git status` classification of the 232 dirty entries:
```
  modified (tracked)  167   -> restorable via git
  deleted  (tracked)    2
  untracked            63 entries, expanding to 43 SOURCE files
```

Core, load-bearing modules that git has NEVER seen:
```
  UNTRACKED  main_system/backend/services/pipeline/provenance.py
  UNTRACKED  main_system/backend/services/pipeline/ais_index.py
  UNTRACKED  main_system/backend/services/scheduler/watcher.py
  UNTRACKED  main_system/backend/api/scheduler_routes.py
  UNTRACKED  main_system/backend/backfill_runs.py
  UNTRACKED  ais_service/ais/contract.py
  UNTRACKED  ais_service/ais/index.py
  UNTRACKED  analysis_engines/engines/drift/weathering.py
  UNTRACKED  scene_service/satellite/calibrate.py
  UNTRACKED  scene_service/satellite/errors.py
  UNTRACKED  main_system/tests/test_provenance.py
  UNTRACKED  main_system/tests/test_scheduler.py
  UNTRACKED  pyproject.toml
  UNTRACKED  docs/LIMITATIONS.md
```

**Impact.** D4 orders: tag anchor -> gitignore -> commit source. A tag points at a commit;
these 43 files are in no commit, so `git checkout <tag>` cannot restore them. The anchor
would omit provenance sealing, the AIS contract module, the AIS store, the scheduler, the
weathering module (F1's subject), pyproject.toml, and 3 test modules inside the 654 baseline.

**Recommended reorder (P00b):** stage untracked SOURCE first, THEN tag, then gitignore, then
commit modified source. Same three commits D4 asks for; the anchor becomes real.

Frozen surfaces are safe: contracts/schemas/* and pytest.ini are tracked AND clean.
pyproject.toml is frozen-by-rule but untracked - it must enter git before any edit.
