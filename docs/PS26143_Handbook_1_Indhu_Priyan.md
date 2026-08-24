# OceanTrace — Personal Developer Handbook

## Developer 1 of 5 — INDHU PRIYAN

**Role:** ML Lead · Main System Owner · Final Integrator (~60% of core work)

| | |
|---|---|
| Event | SIH 2026 · Problem Statement 26143 (NTRO) |
| Title | Leveraging satellite imagery to determine oil spills at sea with AIS correlation to identify the responsible vessel |
| Category / Theme | Software / Space Technology |
| Project codename | OceanTrace |
| Companion docs | `PS26143_System_Specification` · `PS26143_Team_Split_Handbook` |
| Datasets locked | DARTIS (PANGAEA) + Trujillo (Zenodo Sentinel-1) |
| Tooling | Claude Code as primary development assistant |

> **How to use this handbook.** This is your single working document. It contains everything you need to build your components without waiting for anyone: the contracts you code against, your day-0 setup, a phased build plan, the frozen interfaces, the must-pass tests, and the handover rules you enforce on everyone else. If this handbook and a teammate's memory disagree, the contract files in `contracts/` win.

**Contents:** 1 Project in one page · 2 Your mission · 3 Ground rules · 4 Contracts · 5 Day-0 setup · 6 Build plan · 7 Frozen interfaces · 8 Testing · 9 Pitfalls · 10 Integration & handover

---

## 1. The Project in One Page

### 1.1 What the finished system does

The system takes a Sentinel-1 SAR satellite scene, detects and characterises any oil slick in it, uses ocean-current and wind data to trace the slick backwards to its probable origin point and time (hindcast) and forwards to its future spread (forecast), reconstructs historic AIS vessel traffic around that origin window, filters irrelevant vessels, scores the remaining candidates (proximity, temporal and trajectory correlation, behaviour anomalies, AIS gaps, vessel prior), and presents a ranked, explainable suspect list on a GIS investigation interface. Two extra pages — API Monitoring and API Key Management — make every external dependency observable and manageable, with automatic provider fallback. Every stage has a fallback; the last fallback in every chain is dependency-free.

### 1.2 Full pipeline and ownership

```
Sentinel-1 scene acquisition ............ Pavitra
        ↓  scene_meta.json + calibrated GeoTIFF
Oil-spill detection (2-stage ML) ........ INDHU  ◄◄ you
        ↓  raw mask
Characterisation (Engine A) ............. Nandha
        ↓  slick.geojson       ◄── currents.nc + wind.nc .. Keerthana
Drift hindcast + forecast (Engine B) .... Nandha
        ↓  origin_cloud.geojson ◄── vessels.parquet ....... Krishnan
Filter + Score + Rank (Engine C) ........ Nandha
        ↓  suspects.json
GIS UI + API Monitoring + Key Mgmt ...... INDHU  ◄◄ you
        ↓
FINAL INTEGRATION ....................... INDHU  ◄◄ you
        ↓
COMPLETE SYSTEM
```

### 1.3 Your modules highlighted

```
Scene (Pavitra)
        ↓
╔══════════════════════════════════════════╗
║ 2. OIL SPILL DETECTION (ML)              ║ ◄◄◄ YOUR MODULE
╚══════════════════════════════════════════╝
        ↓ raw mask
Characterise → Drift → Origin (Nandha)   ◄── met-ocean (Keerthana)
AIS (Krishnan) → Filter → Score → Rank (Nandha)
        ↓ suspects.json
╔══════════════════════════════════════════╗
║ 10. GIS UI + MONITORING + KEY MGMT       ║ ◄◄◄ YOUR MODULE
╚══════════════════════════════════════════╝
        ↓
╔══════════════════════════════════════════╗
║ FINAL INTEGRATION                        ║ ◄◄◄ YOUR MODULE
╚══════════════════════════════════════════╝
```

## 2. Your Mission

### 2.1 Components owned

1. **Detection ML** — DARTIS screening model (YOLOv8n/11n, look-alike rejection) + Trujillo U-Net segmentation (ResNet-34 encoder), trained, evaluated, exported (ONNX/TorchScript) and wrapped as the `/detect` inference service, with a dependency-free CPU fallback (adaptive threshold + morphology).
2. **Main System** — FastAPI backend, SQLAlchemy database (SQLite for POC, Postgres-ready), admin auth, React + MapLibre GIS UI, API Monitoring page, API Key Management page, replay mode.
3. **`contracts/`** — the frozen schemas and all mock files, delivered in the first two days (everyone else's independence depends on this).
4. **Final integration** of all five components, the demo scenes, the degradation ladder and the rehearsal.

### 2.2 Where it fits

Detection sits between Pavitra's scene output and Nandha's characterisation — your raw mask is the input to Nandha's Engine A. The Main System is the shell everything plugs into: it calls each component with its contract input, validates the output, renders the layers, and shows provider health.

### 2.3 What you own

Both models end-to-end (data acquisition, preparation, training, evaluation, selection, export); the inference service and its CPU fallback; the backend, DB and auth; the UI, Monitoring page, Key Management page and replay mode; all mocks; the integration process and freeze rule; demo rehearsal.

### 2.4 What you do NOT own

Characterisation maths, drift physics, attribution scoring (Nandha). Scene fetching (Pavitra). Met-ocean fetching (Keerthana). AIS fetching/generation (Krishnan). You integrate their outputs through the contracts — you do not rewrite their components.

### 2.5 Why you are never blocked

Datasets are public — zero teammate dependency. The UI is built entirely on your own mocks. Inference is tested on Trujillo Part III tiles, not on live scenes. Nothing in your Stage-1 work waits on anyone.

## 3. Ground Rules (identical for all five developers)

| Rule | Meaning |
|---|---|
| Contract is law | If your output matches the schema in `contracts/`, integration works without touching your code. Changes to a frozen contract require team sign-off. |
| WGS84 everywhere | All vector data in EPSG:4326 (lon/lat). Convert only at ingest boundaries; one assert per boundary. |
| UTC everywhere | All timestamps UTC (`Z` suffix). Beware IST = UTC+05:30 — never let local time leak into data. |
| Error taxonomy | Standard classes: `AUTH_FAILED`, `TIMEOUT`, `RATE_LIMITED`, `UNAVAILABLE`, `BAD_RESPONSE`, plus the component-specific classes listed in each handbook. Structured errors, never crashes. |
| Mocks first | `contracts/mocks/` exists from day 1 (you create it). Everyone develops against mocks; real files swap in with zero code change. |
| No blocking | Every developer builds, runs and tests alone. Nobody waits for anybody. |
| Freeze rule | Once a component is integrated and green, nobody edits it without your sign-off. |
| Honesty | Drift is always a probability cloud with uncertainty, never a point. Synthetic data is flagged in the schema and labelled in the UI. No metric the system cannot back. |

## 4. Contracts — Your Inputs and Outputs

You produce the mocks for ALL eight contracts, but this section covers the ones your own code reads and writes. Example values below are illustrative (Scene A = Chennai/Ennore 2017 demo region); the field names and types are the law.

### 4.1 Input — `scene_meta.json` + calibrated GeoTIFF (from Pavitra; mock until she delivers)

```json
{
  "scene_id": "S1A_IW_GRDH_20170202T0039_DEMO-A",
  "acquired_utc": "2017-02-02T00:39:42Z",
  "bbox": [80.10, 12.90, 80.55, 13.35],
  "crs": "EPSG:4326",
  "db_range": [-35.0, 0.0],
  "file_path":
    "data/scenes/S1A_IW_GRDH_20170202T0039_DEMO-A/scene_sigma0_db.tif",
  "provider_used": "CDSE",
  "source": "real"
}
```

The GeoTIFF is Sigma0 in dB, single band (plus optional incidence-angle band), EPSG:4326, land already masked or maskable.

### 4.2 Output — the `/detect` result (frozen; consumed by Nandha's Engine A)

Input to your service:

```json
{ "scene_path": "data/scenes/.../scene_sigma0_db.tif",
  "scene_id": "S1A_IW_GRDH_20170202T0039_DEMO-A",
  "mode": "full" }
```

Output from your service:

```json
{ "scene_id": "S1A_IW_GRDH_20170202T0039_DEMO-A",
  "mask_path": "data/runs/inv-001/raw_mask.tif",
  "confidence": 0.91,
  "candidates": [
    { "bbox": [80.27, 13.01, 80.36, 13.09], "class": "oil",
      "score": 0.93 },
    { "bbox": [80.44, 13.20, 80.49, 13.24], "class": "lookalike",
      "score": 0.81 }
  ],
  "model_version": "unet-r34-v1.2+yolo11n-v1.0",
  "engine": "ml" }
```

`mask_path` is a georeferenced GeoTIFF of 0/1 (same grid as the scene). `engine` is `"ml"` or `"threshold_fallback"`. Consumers read the mask and the flags; they never need model internals.

### 4.3 Inputs to your UI — all eight contract files

| Contract | Producer | Your UI layer |
|---|---|---|
| `scene_meta.json` + GeoTIFF | Pavitra | SAR basemap layer, scene picker |
| `slick.geojson` | Nandha (A) | Slick mask + boundary + geometry annotations |
| `origin_cloud.geojson` | Nandha (B) | Origin heatmap + confidence ellipse + particle animation |
| `forecast.geojson` | Nandha (B) | Forecast spread +6/+12/+24 h |
| `vessels.parquet` | Krishnan | AIS tracks coloured by suspicion; dimmed filtered vessels |
| `suspects.json` | Nandha (C) | Ranked suspect panel with per-factor bars |
| `currents.nc` / `wind.nc` | Keerthana | (optional) current/wind arrows layer |
| `provider_status.json` | Pavitra, Keerthana, Krishnan | API Monitoring page + header status strip |

`provider_status.json` entry shape (what your Monitoring page parses):

```json
{ "provider": "ERA5", "purpose": "historical wind for drift",
  "status": "FAILED", "last_code": 403, "last_latency_ms": 512,
  "last_success_utc": "2026-08-24T13:10:02Z",
  "last_failure_utc": "2026-08-24T13:55:41Z",
  "last_error_class": "AUTH_FAILED",
  "chain": ["ERA5", "OpenMeteo", "StaticCache"],
  "active_provider": "OpenMeteo" }
```

### 4.4 Your error classes

| Class | When | Main-system reaction (also yours to build) |
|---|---|---|
| `BAD_RESPONSE` | Corrupt/unreadable GeoTIFF, missing metadata | Structured error, badge, no crash |
| `UNAVAILABLE` | Model weights missing / GPU path broken | Auto-switch to `threshold_fallback`, badge "fallback engine" |
| Empty result | Scene has no candidate slick | Valid empty mask + `confidence: 0`, UI shows "no slick detected" |

## 5. Day-0 Setup

### 5.1 Accounts and credentials

None required for your own component (datasets are open). You will later *receive* keys from the others via your Key Management page: CDSE, Earthdata, Copernicus Marine, CDS/ERA5, AISStream.

### 5.2 Environment

```
# Training / inference (venv or conda, Python 3.11)
pip install torch torchvision segmentation-models-pytorch ultralytics \
            onnx onnxruntime-gpu rasterio scikit-image shapely numpy \
            fastapi uvicorn sqlalchemy pydantic pytest httpx \
            python-multipart
# Frontend
npx create-vite@latest frontend -- --template react
npm i maplibre-gl deck.gl recharts
```

Hardware: local RTX 4050 (training), laptop plugged in, Turbo mode. Backup: Kaggle/Colab.

### 5.3 Your folders

```
oceantrace/
├── backend/app/            # main.py, api/, core/, services/,
│                           # models/, schemas/
│   └── services/detection/ # your inference service
├── backend/ml/             # training, eval, export scripts
├── backend/tests/
├── frontend/               # React + MapLibre
├── contracts/              # schemas/ + mocks/   ◄ you create first
├── config/normalisation.yaml   # dB clip constants —
│                               # shared by train AND inference
└── data/                 # scenes/, runs/, metocean/, ais/ (gitignored)
```

### 5.4 Mock inputs you develop against

Trujillo Part III tiles (150 oil / 150 look-alike / 150 no-oil) for inference; a hand-made `scene_meta.json`; your own eight mock contract files for the UI.

## 6. Build Plan — Phase by Phase

| Phase | Deliverable | Depends on |
|---|---|---|
| 0 | Repo skeleton + environments | — |
| 1 | `contracts/` schemas + all mocks (DAY 1–2, hard deadline) | — |
| 2 | Datasets acquired, audited, prepared | — |
| 3 | Both models trained, evaluated, selected, exported | Phase 2 |
| 4 | Inference service `/detect` + CPU fallback | Phase 1 (3 for real weights) |
| 5 | Backend skeleton + DB + auth | Phase 1 |
| 6 | GIS UI, all layers from mocks | Phase 1, 5 |
| 7 | API Monitoring + Key Management pages | Phase 5 |
| 8 | Replay mode | Phase 6 |
| 9 | Integration ladder | Everyone's handovers |
| 10 | Demo prep + rehearsal | Phase 9 |

### Phase 1 — Contracts and mocks (day 1–2; everything else in the team waits on this)

☐ Write Pydantic schemas for all eight contracts in `contracts/schemas/`.
☐ Hand-make small, valid mock files in `contracts/mocks/`: `scene_meta.json`, tiny GeoTIFF (e.g. 512×512 crop with a painted slick), `slick.geojson`, `origin_cloud.geojson` (a few hundred particle points + ellipse per timestep), `forecast.geojson`, `vessels.parquet` (~20 vessels incl. one culprit), `suspects.json`, `provider_status.json`.
☐ Each mock validates against its schema in CI (`pytest contracts/`).
☐ Announce frozen; changes now require team sign-off.

### Phase 2 — Datasets (your 12-step training workflow, steps 1–4)

1. **Acquire.** DARTIS from PANGAEA (doi:10.1594/PANGAEA.980773). Trujillo from Zenodo — Part III (record 13761290) FIRST (small; becomes the untouched test harness), then Part I (8346860) and Part II (8253899) via the tile-and-discard script: download → tile → keep → delete source archive. Never hold 40–60 GB on disk.
2. **Audit.** Open 20 random image/mask pairs per set; verify mask alignment, dB value ranges, DARTIS look-alike categories (low wind, internal waves, biogenic films, rain cells, eddies, RFI); record all counts in `docs/data_card.md`.
3. **Prepare.** Trujillo: tile 256×256; keep tiles ≥1% oil + matched hard negatives; store as uint8 memory-mapped `.npy`; freeze dB clip constants (≈ −35…0 dB) in `config/normalisation.yaml`. DARTIS: convert to Ultralytics YOLO format. Never merge the two datasets (different radiometry, format, label geometry) — one model per dataset per stage.
4. **Split.** Trujillo train/val from Parts I–II; Part III untouched as test. DARTIS val split stratified across look-alike phenomena.

### Phase 3 — Training (steps 5–9)

5. **Train.** Model 1: U-Net (ResNet-34, ImageNet weights, `segmentation-models-pytorch`), Dice+BCE loss, AMP fp16, batch 12–16 at 256², ~40 epochs, checkpoint every epoch (~2 h on the 4050). Model 2: YOLOv8n/11n on DARTIS, ~50 epochs (~2–3 h). Run sequentially.
6. **Evaluate.** Model 1: binary IoU + precision/recall on Part III. Model 2: mAP@0.5 + per-phenomenon false-positive table.
7. **Compare.** Optional second segmentation baseline (plain U-Net / DeepLabv3+) for the metrics slide; pick by test IoU + visual inspection of 20 predictions.
8. **Select.** Freeze the winning checkpoint per model; tag in git.
9. **Export.** ONNX/TorchScript; bake normalisation into the preprocessing function; write `model_card.md` (metrics, dB constants, tile size).

### Phase 4 — Inference service (steps 10–11)

☐ Implement `POST /detect` exactly per §4.2 (frozen).
☐ Tiling for full scenes (256² tiles, overlap-stitch), batched ONNX Runtime inference, mask reassembly with georeferencing preserved.
☐ Screening stage gates segmentation: DARTIS model rejects look-alikes; rejected candidates still reported with `class: "lookalike"`.
☐ CPU fallback: adaptive thresholding + morphological cleanup (scikit-image) behind the same interface, `engine: "threshold_fallback"`.
☐ Golden-file test (fixed tile → expected mask hash) + throughput benchmark + fallback test.

### Phase 5 — Backend skeleton

☐ FastAPI app factory, config from `.env`, logging.
☐ SQLAlchemy models: `investigations`, `scenes`, `runs`, `api_providers`, `api_keys` (encrypted at rest), `api_calls` (provider, endpoint, status, latency, error), `audit_log`.
☐ REST: `/investigations` CRUD, `/investigations/{id}/run`, `/layers/{name}` (serves contract files), `/apis/status`, `/apis/{p}/test`, `/keys` (admin), `/detect`.
☐ Admin auth for key routes. Background health-check scheduler (ping each provider ~60 s + passive status from real calls).
☐ Pipeline orchestrator: call component → validate output vs Pydantic schema → on failure/declared error class fall back (cache/mock) + set UI badge — the pipeline never halts.

### Phase 6 — GIS UI (built entirely on mocks)

☐ Investigation workflow: create/select investigation → pick scene → run/replay → explore layers.
☐ MapLibre map with pre-downloaded OSM tiles for the demo bboxes (offline-capable); SAR scene as a basemap layer option.
☐ Toggleable layers per §4.3 table; time slider; suspect panel with per-factor bars and plain-language reason; data-source badges REAL / CACHED / SYNTHETIC per layer.
☐ Metrics table (IoU, per-phenomenon FP rates, top-k hit rate); loading/error states everywhere.
☐ Fallback UI (Streamlit + Folium) only if React path fails — decide early, don't build both.

### Phase 7 — Monitoring + Key Management

☐ Monitoring page: one row per provider — name/purpose, status chip (WORKING/FAILED/DEGRADED), last code + latency, last success, last failure + error class, chain with active provider highlighted, actions (Test now · View recent calls · Switch provider, admin). Live via WebSocket or polling. Mirror status chips as a small strip in the investigation header.
☐ Key Management (admin): masked keys (`••••1234`), change/save (server-side only), Test connection (real authenticated ping, reports exact failure class), audit log. Keys never in frontend code, never returned in full by any API.

### Phase 8 — Replay mode

☐ "Replay" loads pre-computed contract files for an investigation and drives every layer + animation without any computation or network. This is degradation rung 2 and also your fastest dev loop.

### Phase 9 — Integration ladder (strict order)

mocks end-to-end → Pavitra (real scenes) → Keerthana (real NetCDFs) → Krishnan (real + synthetic AIS) → Nandha's engines → real ML weights → two demo scenes → replay mode → rehearsal. After each rung: green tests, then freeze.

### Phase 10 — Demo

☐ Scene A (headline): Indian waters (Chennai/Ennore 2017 area) — real SAR + real currents + real wind + synthetic AIS (labelled). Scene B (proof): Gulf of Mexico or Danish waters — real SAR AND real AIS.
☐ Verify model output on the chosen scenes BEFORE locking them.
☐ Rehearse the ladder: live-from-cache (~20–30 s) → replay → recorded video. Honest metrics slide.

## 7. Frozen Interfaces You Implement

| Interface | Signature |
|---|---|
| Inference | `POST /detect` — §4.2 exactly |
| Pipeline run | `POST /investigations/{id}/run` → orchestrates Pavitra→detect→Nandha A→Keerthana→Nandha B→Krishnan→Nandha C, persisting every contract file under `data/runs/{id}/` |
| Layer serving | `GET /layers/{run_id}/{contract_name}` |
| Health | `GET /apis/status` (feeds Monitoring), `POST /apis/{provider}/test` |
| Keys | `GET/PUT /keys/{provider}` (admin, masked), `POST /keys/{provider}/test` |

## 8. Testing — the must-pass list

☐ Segmentation: binary IoU / precision / recall on Trujillo Part III (never pixel accuracy — sea-class dominance makes it meaningless).
☐ Screening: mAP@0.5 + per-phenomenon FP table on DARTIS.
☐ Golden-file inference test: fixed tile → expected mask hash.
☐ Throughput: < 5 s per scene tile batch on the 4050.
☐ CPU fallback returns a valid mask with no GPU present.
☐ Every UI layer renders from mocks alone.
☐ Monitoring page correctly shows a deliberately killed provider, and the fallback badge appears.
☐ Key Management test-connection reports the right error class for a wrong key.
☐ Failure tests: corrupt GeoTIFF, empty scene, missing metadata → structured errors, never a crash.
☐ Replay mode drives the full UI with network disabled.

## 9. Pitfalls and Traps

1. Mocks late = five people blocked. Phase 1 is a hard 2-day deadline.
2. Never report pixel accuracy; never merge DARTIS and Trujillo.
3. `config/normalisation.yaml` is shared by training AND inference — a mismatch silently destroys real-scene performance (the classic SAR domain-gap failure).
4. Trujillo is 40–60 GB — tile-and-discard, Part III first.
5. Checkpoint every epoch; keep the laptop plugged in (thermal throttling on battery).
6. Keys: never in the frontend bundle, never logged, never returned in full.
7. The pipeline never halts — every component call is wrapped in validate-or-fallback-with-badge.
8. Lock demo scenes only after verifying the model performs on them.
9. Enforce the freeze rule on yourself too — after integration, changes go through sign-off.

## 10. Integration and Handover

### 10.1 You are the receiving end

Your acceptance gate for every teammate's handover: component runs from ONE command against `contracts/mocks/`; its own tests are green; output validates against the Pydantic schema; README covers setup, credentials, known issues; a 30-minute walkthrough is held.

### 10.2 Coordination points

Only two exist in the whole team, and neither is yours: Keerthana↔Nandha (NetCDF variable names) and Krishnan↔Nandha (parquet columns). You verify both are written down.

### 10.3 Common handover checklist (you enforce, 10 points)

1. One-command run. 2. Input schema documented. 3. Output schema documented. 4. Example inputs committed. 5. Example outputs committed. 6. Test results recorded (success, failure, edge). 7. Error behaviour documented per class. 8. README (setup, credentials, known issues). 9. Run instructions. 10. Integration instructions (what main system calls, what returns).

### 10.4 Definition of Done (every component, including yours)

Works independently · mock input works · real input works where applicable · output follows the contract · tests exist incl. failure cases · documentation exists · another developer can consume the output using only the contract · the owner can demonstrate it without the unfinished main system.
