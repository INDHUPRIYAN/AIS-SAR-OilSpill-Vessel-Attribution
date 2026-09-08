# Flagship run card — `inv-gulf-flagship-20230108-2day`

*Rendered 2026-09-08T07:43:11Z by `scripts/flagship_run_card.py` from the sealed artefacts. Not hand-written.*

| | |
|---|---|
| **Canonical pointer** | `dev_evidence/P14/flagship.json` → `inv-gulf-flagship-20230108-2day` |
| **Artefact digest** | `fd42e078f836611043fd088e1e0abc490064b05404f887c2a69571ab65f94a84` |
| **Immutable** | True · **verifies:** True (8 artefacts, problems: none) |
| **Sealed** | 2026-09-07T08:04:36Z · 384.3 s end-to-end · code `03b48a779b2e` |
| **Registry row** | status `complete` · `5/5` real · `registry_source = reconciled` · investigation `None` · incident `None` |

## Inputs

| input | source |
|---|---|
| SAR scene | `S1A_IW_GRDH_1SDV_20230108T001008_20230108T001033_046685_059887_E5A1_COG` — Sentinel-1A IW GRDH via CDSE, calibrated to Sigma0 dB |
| Segmenter | `unet-r34-fullcorpus-e48` · sha256 `a4aac81f3ddd54ce…` |
| Screen | `yolo11n-screen-dartis-2026-08-24` · sha256 `9a6ff8df0d2b4a8f…` |
| AIS | `real` · covers origin window: True — a real AIS archive holds reports inside the computed origin window |
| Forcing | {'currents': {'provider': 'Copernicus Marine Service (CMEMS)', 'variables': ['uo', 'vo'], 'fallback': None, 'file': 'currents.nc', 'dataset': 'OceanTrace Normalized Surface Currents', 'normalised': 'Normalized at 2026-09-07T06:34:21.349113+00:00'}, 'wind': {'provider': 'ECMWF ERA5 Reanalysis (CDS API)', 'variables': ['u10', 'v10'], 'fallback': None, 'file': 'wind.nc', 'dataset': 'OceanTrace Normalized 10m Atmospheric Winds', 'normalised': 'Normalized at 2026-09-07T07:13:27.663782+00:00'}, 'windage': 0.03, 'engine': 'euler', 'ml_residual': {'model': None, 'applied': False, 'mean_correction_m_per_step': 0.0, 'corrects': 'forward-Euler truncation error at the operational timestep'}, 'hours': 24} |

## Stages — 5/5 real, 0 mock, 0 failed

| stage | status | source | time | detail |
|---|---|---|---|---|
| detect | ok | real | 154.4 s | engine=ml, 31 oil + 361 look-alike candidate(s), confidence 0.6882 |
| characterise | ok | real | 205.4 s | Engine A: 62 slick(s) |
| drift_hindcast | ok | real | 4.4 s | Engine B [euler] hindcast 24h, currents+wind |
| drift_forecast | ok | real | 2.7 s | Engine B [euler] forecast 23h, currents+wind |
| attribution | ok | real | 3.6 s | Engine C: 4 suspect(s), top MMSI 367653160 score 0.67 |

## Result — what the artefacts say, and only that

- **Detection:** 31 oil + 361 look-alike candidates over ? tiles; look-alikes are reported, never counted as oil. **No ground truth exists for this scene** — the 31 oil regions are model output in a basin with natural seeps.
- **Origin:** window `2023-01-07T11:10:08Z` → `2023-01-08T00:10:08Z`, method `cloud_convergence`. Uncertainty **0.3658 km** (0.908 coverage), method: *calibrated physics heuristic (not ML): m*k*cloud_sigma, k=0.0907 m=1.75, fitted on 1665 closed-loop scenarios over 24 real fields*. 25/25 ellipses non-zero. The convergence peak sits at the acquisition instant: present this as a window, never a discharge time.
- **Attribution:** 32 vessels considered → 4 ranked, 28 filtered; `source = real`. Weights {"proximity": 0.3, "temporal": 0.2, "trajectory": 0.2, "behaviour": 0.1, "ais_gap": 0.15, "vessel_prior": 0.05} (sum 1.00).
  **Rank #1 · Score 0.6691** — MMSI 367653160 (Σwᵢsᵢ recomputes to 0.6691). `vessel_name = None` — MarineCadastre carried no static identity; absence left absent. A rank is a position in a weighted ordering, not a finding of responsibility.
- **Same MMSI across the flagship family:** inv-gulf-flagship-20230108 (rank 1, 0.6606), inv-gulf-flagship-20230108-2day (rank 1, 0.6691), inv-gulf-flagship-20230108-final (rank 1, 0.6689), inv-gulf-flagship-20230108-v2 (rank 1, 0.6689).

## Standing limitations that travel with this card

1. No ground truth for this scene; the oil regions are model output.
2. A ranked suspect is not a culprit.
3. All ranked vessels have `vessel_name: null` — absence of data, left absent.
4. The 13-hour window is wide and weak; the drift did not localise a release earlier than the image.
5. Slick age is always LOW confidence.
6. The registry row is **reconciled** (rebuilt from this manifest after the fact); investigation and incident are unknown to the artefacts and are not invented.

