# P03 — scheduler import path (audit P2/C7)

Executed: 2026-09-06T17:38:49Z

## Before (captured in P00, live uvicorn)
```
POST /api/aois/poll?dry_run=true
  errors: [ {danish-straits, BAD_RESPONSE,
             "ModuleNotFoundError: No module named \x27satellite\x27"},
            {chennai-coast, BAD_RESPONSE, same} ]
```

## Root cause
```
watcher.py:139   from satellite.chain import SceneRetrievalChain
main.py:17-20    sys.path gets REPO_ROOT + main_system only
scene_service/   has no __init__.py -> 'satellite' needs scene_service ON sys.path
pytest.ini       declares all five roots -> the suite passed, deployment did not
```

## Fix — the pattern already in the repo
Copied from `backend/services/pipeline/ais_index.py:25-27`, which is why the AIS
store kept working under uvicorn while the scheduler did not. Covers
`scene_service`, `ais_service`, `metocean_service`.

`pyproject.toml packages = []` was NOT touched — installing the roots as packages
would have fixed the symptom while changing the multi-root layout that every
module and pytest.ini depend on. A test now pins that.

## After
```
POST /api/aois/poll?dry_run=true
  polled   : ['danish-straits', 'chennai-coast']
  disabled : ['us-gulf-coast', 'mumbai-offshore']
  errors   : []
  new_scenes (3) - LIVE CDSE results:
    S1C_IW_GRDH_1SDV_20260905T170829_20260905T170854_009315_01284B_B333  2026-09-05T17:08:29+00:00
    S1C_IW_GRDH_1SDV_20260905T170854_20260905T170919_009315_01284B_7321  2026-09-05T17:08:54+00:00
    S1C_IW_GRDH_1SDV_20260905T170919_20260905T170944_009315_01284B_64E8  2026-09-05T17:09:19+00:00
```

ACCEPTANCE: `errors: []`. Beyond the requirement, the poll reached live CDSE and
returned three real Sentinel-1C acquisitions — the scheduler is genuinely working,
not merely importable.
