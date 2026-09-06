# OceanTrace — Production UI Specification (SIH 26143)

**Scope:** Every page required for a production-grade deployment of the SIH 26143 solution (oil-spill detection → characterization → drift → AIS attribution), not a judging demo.
**Derivation:** SIH 26143 defines capabilities, not pages. Each page below is traced to the PS clause it serves, then hardened with what production actually demands: auth, roles, job orchestration, provenance, audit, exports, and failure states.
**Stack assumption:** React + MapLibre/deck.gl front end over the existing pipeline (detect → characterise → drift_hindcast → drift_forecast → attribution) and its artifacts (`slick.geojson`, `origin_cloud.geojson`, `forecast.geojson`, `vessels.parquet`, `suspects.json`, run manifest).

---

## 0. Roles (referenced by every page)

| Role | Can |
|---|---|
| **Investigator** | Start investigations, work runs, draft reports |
| **Analyst** | Everything Investigator can + tune weight profiles, manage scenarios |
| **Reviewer / Supervisor** | Review, annotate, approve/sign case reports; cannot edit runs |
| **Admin** | Users/roles, credentials, data sources, system config |
| **Auditor (read-only)** | View everything incl. audit log; change nothing |

---

## 1. ROUTE MAP (production)

```
/login
/monitor                          ← home: global situational view
/alerts
/investigations/new               ← input wizard
/incidents                        ← case registry
/incidents/:incidentId            ← case file (links its runs)
/runs                             ← run history
/runs/:runId/overview             ← pipeline status + manifest
/runs/:runId/detection
/runs/:runId/characterization
/runs/:runId/drift                ← hindcast + forecast
/runs/:runId/ais                  ← traffic + filtering
/runs/:runId/suspects             ← attribution
/runs/:runId/report               ← review + export
/vessels/:mmsi                    ← vessel dossier (cross-run)
/data                             ← data catalog & coverage
/models                           ← model registry + benchmarks
/admin/users  /admin/credentials  /admin/config
/system/health
/system/audit
```

`/runs/:runId/*` pages are one **Investigation Workspace** with a persistent map canvas and switchable analysis rail — one layout, seven addressable views, deep-linkable for evidence citations.

---

## 2. PAGES

### P0 — Sign in
- **PS trace:** none (production requirement — the output accuses vessels; access must be controlled).
- **Purpose:** Authentication (SSO/OIDC preferred for agency deployment), session policy, MFA option.
- **Key components:** login form / SSO button, environment banner (PROD/STAGING), legal notice ("authorized use only — actions are audited").
- **States:** invalid credentials, account locked, SSO failure, maintenance mode.
- **Production notes:** short-lived JWT + refresh; role claims drive nav visibility; all subsequent pages assume RBAC enforcement server-side, not just hidden buttons.

### P1 — Global Monitor (home)
- **PS trace:** "intelligent automated pipeline" — the system is a standing watch, not a script.
- **Purpose:** Single situational picture: all incidents on the globe/map with status, live alert ticker, system heartbeat.
- **Key components:** globe/map with incident markers (active / investigating / attributed / closed / monitoring); alert ticker; UTC (ZULU) clock; provenance chips; quick stats (open incidents, runs in queue, last detection); "New investigation" CTA.
- **Data:** incidents index API; alerts stream (WebSocket/SSE); health summary.
- **States:** zero incidents (empty-state with CTA), stream disconnected (stale-data banner with last-updated time), degraded providers (chip turns amber).
- **Production notes:** never render silently-stale data — timestamp everything; map falls back to 2D if WebGL unavailable.

### P2 — Alerts & Tasking
- **PS trace:** detection at scale implies triage.
- **Purpose:** Queue of machine-generated detections and external tip-offs awaiting human action.
- **Key components:** alert table (time UTC, region, source scene, detector confidence, SLA age); acknowledge / assign / dismiss-with-reason; bulk actions; filter by region/confidence/status.
- **Data:** alerts API; links to auto-created draft incidents.
- **States:** empty queue; alert referencing a scene that failed ingest (blocked badge + reason).
- **Production notes:** every dismissal requires a reason (audit); assignment notifies the assignee; SLA breach highlights.

### P3 — New Investigation (input wizard)
- **PS trace:** clause (a) — automated detection. The user provides *where/when to look*, never *where the oil is*.
- **Purpose:** Start a pipeline run from legitimate inputs only.
- **Key components:**
  - Step 1 — Input mode: AOI (draw bbox/polygon of sea region) + time window · Sentinel-1 scene ID · upload .SAFE/GeoTIFF · link to existing incident.
  - Step 2 — Scene resolution: provider chain results (CDSE → ASF → LocalCache) with product metadata, footprint preview, source badge per candidate scene.
  - Step 3 — Run config: AIS source (real archive / synthetic scenario — labeled), weight profile (default locked for Investigator; Analyst may select), priority.
  - Step 4 — Review & submit → job ID.
- **Data:** scene search API; AIS coverage API (warns if no AIS coverage for window); quota/queue API.
- **States:** no scene found for AOI+time (suggest widening window); credentials down (cache-only banner); AOI over land (validation error); queue full.
- **Production notes:** **no manual spill-geometry field exists anywhere**; input validation is server-side; submission returns immediately with job ID — the UI never blocks on pipeline runtime.

### P4 — Incidents Registry (case files)
- **PS trace:** production wrapper for the whole PS — one spill event may span multiple runs/scenes.
- **Purpose:** Case management: an incident is the unit of accountability; runs are evidence attached to it.
- **Key components:** incident table (ID, name, region, opened, status lifecycle open → investigating → attributed → closed, assigned team, #runs, top suspect if attributed); incident detail view with linked runs, timeline of actions, notes.
- **Data:** incidents API; run index per incident.
- **States:** incident with zero runs (CTA to start one); conflicting attributions across runs (flagged for review).
- **Production notes:** status transitions are permissioned (only Reviewer can mark *attributed*/*closed*) and audited.

### P5 — Run Overview (pipeline status + manifest)
- **PS trace:** "automated pipeline" — the user must see the chain execute.
- **Purpose:** Live status of one run: five stages, their inputs/outputs, provenance, logs.
- **Key components:** stage tracker (detect → characterise → drift_hindcast → drift_forecast → attribution) with per-stage state (queued/running/cache-hit/done/failed/skipped), duration, retry button (permissioned); manifest viewer (inputs, sources, cache keys, artifact hashes); provenance summary chips (REAL S1 · SYNTHETIC AIS · CMEMS CACHE · ERA5 CACHE); log tail per stage; cancel run.
- **Data:** run status stream (WebSocket/SSE); manifest JSON; artifact index.
- **States:** stage failed (error surfaced verbatim + "downstream stages not run"); partial run (clear which artifacts exist); stale view (reconnect banner).
- **Production notes:** a failed stage must never silently substitute an old artifact — the UI shows the gap; artifact hashes displayed so reports can cite them (chain of custody).

### P6 — Detection & Scene view
- **PS trace:** clause (a) detect; dataset clause (Sentinel-1 SAR).
- **Purpose:** Show that the system found the oil, on the real scene.
- **Key components:** SAR bitmap layer (tiled/COG — never ship the 28k×21k raster raw to the browser); crimson mask + boundary overlay; per-slick detection confidence; scene metadata card (product ID, acquisition UTC, mode IW GRDH, polarization, orbit); tile/candidate inspector (YOLO screen count → U-Net segmented count); multi-slick selector; EO tab — present but honest: "EO/optical adapter not available in this deployment" until it exists (never imply SAR covers EO).
- **Data:** scene tiles endpoint; `slick.geojson`; detection stage metadata.
- **States:** no detection ("no slick found in scene" — honest empty state, links to clean-run report); low-confidence-only candidates (shown as *unconfirmed*, visually distinct); scene tiles unavailable (vector-only fallback with notice).
- **Production notes:** server-side tiling with dB-scaled rendering; mask and polygon must visibly coincide (mismatch is a defect, not a style choice).

### P7 — Characterization
- **PS trace:** clause (a) — geometric properties and age if feasible.
- **Purpose:** The measurable identity of each slick.
- **Key components:** metrics panel per slick: area km², perimeter km, centroid, major/minor axes, orientation, backscatter damping (dB) — every value unit-labeled; **age card:** estimate + method note (Fay gravity-viscous inversion, damping-nudged) + confidence badge (LOW) + "why low" tooltip; local-metric-frame note; copy-as-JSON / download GeoJSON.
- **Data:** `slick.geojson` properties; characterization stage output.
- **States:** age not computable (show "age: not estimable for this slick" — never a fabricated number); multi-slick (per-feature tabs, no cross-contamination of metrics).
- **Production notes:** confidence must survive to every place age is displayed, including the final report.

### P8 — Drift Analysis (hindcast + forecast)
- **PS trace:** clause (b) — trace to **origin point and time** using oceanographic and meteorological data; predict future flow.
- **Purpose:** Both halves of clause (b) on one time-controlled map.
- **Key components:** magenta particle cloud (hindcast) animating backward on the time slider; gold origin ring + density ellipse with σ label; **origin time window card** ("est. release 12:47–19:47 UTC · 7.0 h window") — the value the AIS query consumes; amber forecast footprints per horizon (+6/+12/+24 h) with area readouts; forcing provenance panel: currents source (CMEMS GLORYS12 → HYCOM → StaticCache) and winds source (ERA5 → Open-Meteo → StaticCache), each with valid-time vs scene-time check — **mismatch is flagged in amber**; uncertainty readout (cloud spread vs backward time); engine badge (Engine B — physics; when/if an ML hindcast component ships, it is labeled here, and not before).
- **Data:** `origin_cloud.geojson`, `forecast.geojson`, drift diagnostics (particle snapshots), env-provenance from manifest.
- **States:** forcing from fallback cache (disclosed, with valid-time delta); hindcast failed (page renders detection/characterization context + failure card; no fake origin).
- **Production notes:** timestamps UTC everywhere with optional IST display *labeled as IST* — the +05:30 trap is the number-one Indian-deployment bug; time slider state is shareable via URL for evidence citation.

### P9 — AIS Traffic & Filtering
- **PS trace:** clause (c) — reconstruct traffic around the origin window in space and time; filter irrelevant traffic.
- **Purpose:** From "everything that moved" to "who could have done it," transparently.
- **Key components:** cyan vessel tracks within origin-cloud + 25 km buffer, time-synced to the slider; **funnel bar:** found → after spatial → after temporal → after trajectory → candidates, with live counts; exclusion ledger table: every filtered vessel with `filter_reason`, gate, and inspect action — filtered vessels render dimmed grey on the map, never deleted; AIS source card: REAL archive (which files, coverage) vs SYNTHETIC scenario (which generator/config) — badge on every vessel popup; query trace: the exact buffer geometry + time window used, proven to come from *this run's* origin products; data-quality flags (interpolated segments marked; AIS gaps drawn as broken track, not healed).
- **Data:** `vessels.parquet` via API (paged/decimated for the viewport); filtering stage output; AISStore coverage.
- **States:** zero traffic in window ("no vessels in origin window — possible dark vessel"; attribution page will show honest no-candidate state); AIS coverage doesn't span the window (coverage-gap warning with exact bounds).
- **Production notes:** track decimation server-side (viewport + zoom-based); interpolation must not fabricate continuity across transponder gaps — gaps are evidence.

### P10 — Suspect Attribution
- **PS trace:** clause (c) + Expected Solution — score and rank suspects by proximity, trajectory, behavioural anomalies, etc.
- **Purpose:** The defensible ranked answer to "who did it."
- **Key components:** ranked suspect list (rank, name, MMSI, type, total score); six factor bars per suspect with weights shown (proximity .30 · trajectory .20 · temporal .20 · AIS gap .15 · anomalies .10 · type .05) and the weight profile version used; evidence strings per suspect — specific, timestamped ("AIS gap 14:35–17:05Z overlapping 100% of release window"); select suspect → track highlight + camera; anomaly inspector (slowdown/course-change/loitering events with times, plotted on the track); score arithmetic transparency: expandable "total = Σ wᵢ·sᵢ" breakdown; **synthetic-data banner** whenever suspects derive from synthetic AIS: "Demonstration attribution — AIS is synthetic"; confidence framing: scores are *investigation leads*, wording avoids legal-conclusion language.
- **Data:** `suspects.json`; anomaly events; weight profile from run config.
- **States:** no candidates (honest empty state, links to dark-vessel guidance); ties (deterministic tiebreak documented); weight profile deprecated since run (badge: "scored with profile v3").
- **Production notes:** panel values must equal artifact values exactly — any divergence is a release blocker; every suspect row deep-links to the Vessel Dossier.

### P11 — Vessel Dossier (cross-run)
- **PS trace:** clause (c) hardening — an investigator asks "what do we know about this ship."
- **Purpose:** Everything OceanTrace knows about one MMSI across all runs.
- **Key components:** identity card (MMSI, name, type, flag if available, SYNTHETIC badge when applicable); appearance history (which incidents/runs, what rank, what score); full track archive per run; gap/anomaly history; notes (permissioned).
- **Data:** vessel index API aggregating per-run artifacts.
- **States:** synthetic vessel (banner: exists only in scenario data); vessel seen in one run only.
- **Production notes:** with real AIS this touches privacy/legal handling — access to dossiers is role-gated and audited.

### P12 — Review & Case Report (export)
- **PS trace:** the PS's purpose — attribution that survives scrutiny; also the honesty requirement around synthetic data.
- **Purpose:** Consolidate one run (or one incident's runs) into a reviewed, signed, exportable evidence package.
- **Key components:** report composer: auto-assembled sections (scene → detection → characterization → origin point & time → forecast → traffic funnel → ranked suspects → provenance) with investigator annotations; **provenance annex (mandatory, non-removable):** per-source table REAL / SYNTHETIC / CACHE / FALLBACK with valid-times, artifact SHA-256 hashes, run ID, pipeline + model versions; review workflow: draft → submitted → reviewed (Supervisor sign-off with name/time) → published; immutable once published; exports: PDF (report), GeoJSON/CSV bundle (artifacts), JSON (machine-readable summary); every export stamped with run ID + hash.
- **Data:** all run artifacts + manifest + review records.
- **States:** unpublished changes after review (re-review required); export of an unreviewed run (allowed but watermarked DRAFT).
- **Production notes:** published reports are write-once; the provenance annex is what makes a synthetic-AIS demo honest and a real-AIS case defensible.

### P13 — Runs History
- **PS trace:** automated pipeline, repeatability.
- **Purpose:** Every pipeline run, findable and comparable.
- **Key components:** run table (run ID, incident, scene, submitted by, started/duration, per-stage status, provenance badges, top suspect); filters (date, region, status, AIS source, operator); compare view: two runs side-by-side (origin windows, funnels, top-3 suspects); re-run with same inputs (new run ID — never overwrite).
- **Data:** run index API.
- **States:** failed runs listed with failure stage (not hidden); cache-only runs badged.
- **Production notes:** artifacts are run-scoped and immutable — the UI never mixes artifacts across run IDs.

### P14 — Data Catalog & Coverage
- **PS trace:** dataset clauses (Sentinel-1, MarineCadastre AIS, real-or-synthetic AIS).
- **Purpose:** What data the deployment actually holds, so investigators know what's answerable before they ask.
- **Key components:** scene cache browser (products, footprints, dates, source, size); AIS holdings map: region/day partition coverage (real archives ingested vs synthetic scenarios), row counts, ingest status; synthetic scenario manager (Analyst): list/inspect scenario definitions, generate new scenario for a region/window — clearly badged SYNTHETIC end-to-end; env-data cache (currents/winds coverage windows); ingest jobs (MarineCadastre/DMA archive ingest with progress and error reports).
- **Data:** AISStore manifest; scene cache index; env cache index.
- **States:** ingest failure with per-file error detail; coverage hole visualization.
- **Production notes:** this page is where "real historic AIS actually exercised" becomes operationally visible instead of a claim.

### P15 — Models & Benchmarks
- **PS trace:** Expected Solution ("machine learning model") — production ML needs governance.
- **Purpose:** Registry of the ML components and standing proof of accuracy.
- **Key components:** model cards: YOLO11n screen + U-Net ResNet-34 segmenter (version, training data note, ONNX hash, date deployed, which runs used which version); benchmark board: seeded-scenario suite results (top-1, top-3, mean rank) per pipeline version, rerun button (Analyst), trend over versions; detection metrics on labeled chips (if Zenodo labeled set present); honest hindcast note: "hindcast = physics (Engine B); no learned component" until that changes — with the roadmap item linked.
- **Data:** model registry; benchmark harness API.
- **States:** benchmark running; regression detected (red delta vs previous version, blocks "publish" recommendation).
- **Production notes:** ties every accusation to an identifiable model version — mandatory for evidentiary use.

### P16 — Admin (users · credentials · config)
- **PS trace:** none (production).
- **Purpose:** Operate the system without touching servers.
- **Key components:** users & roles CRUD; provider credentials (CDSE, ASF, CMEMS, ERA5/CDS, Open-Meteo) with connection-test buttons and last-success timestamps — secrets write-only; attribution weight profiles: versioned editor (must sum to 1.0, renormalization preview), publish creates new version, old runs keep their version; retention & storage policy settings; alert/threshold config (detector confidence gate, SLA timers).
- **States:** credential expiring/failed (drives amber chips on P1/P5/P8); invalid weight profile (blocked at save).
- **Production notes:** every change here is an audit event with before/after diff.

### P17 — System Health
- **PS trace:** none (production).
- **Purpose:** Is the watchstanding system actually up.
- **Key components:** service status (API, pipeline workers, tile server, AISStore, queue depth, GPU/inference latency); provider reachability board; storage utilization; error-rate sparkline; incident-free uptime.
- **States:** degraded mode explanation ("CDSE down — runs proceed cache-only").
- **Production notes:** feed the same signals into P1's status chips so investigators see degradation in context.

### P18 — Audit Log
- **PS trace:** evidentiary integrity (derived).
- **Purpose:** Who did what, when — non-repudiation for a system that names polluters.
- **Key components:** filterable event stream (login, run submitted, run cancelled, weight profile changed, report published, dismissal reasons, exports, admin changes) with actor, UTC time, target, before/after where applicable; export for compliance.
- **States:** none special; read-only for Auditor/Admin.
- **Production notes:** append-only store; the UI offers no delete.

---

## 3. CROSS-CUTTING PRODUCTION REQUIREMENTS (apply to every page)

1. **Time discipline:** store/display UTC; optional IST rendering always suffixed "IST". One clock component, one formatter.
2. **Provenance is chrome:** REAL / SYNTHETIC / CACHE / FALLBACK chips are part of the layout on every data-bearing page, not a footnote.
3. **States are designed, not defaulted:** every page ships loading (skeleton), empty (instructive), error (verbatim cause + next step), degraded (stale/partial banners). No blank screens, no infinite spinners.
4. **Run isolation:** artifacts render only under their run ID; immutable; hashed; deep-linkable (`/runs/:id/drift?t=-540` style URLs so reports can cite exact views).
5. **Performance:** SAR via server tiles/COG; AIS decimated by viewport; WebSocket for pipeline progress; target < 2 s first meaningful paint on the workspace with a warm cache.
6. **Access:** RBAC enforced server-side; UI reflects, never enforces alone. All mutating actions audited.
7. **Accessibility & resilience:** keyboard operability, visible focus, reduced-motion honored, color meanings duplicated with text/badges (the crimson/gold/cyan scheme must never be the only signal), 2D map fallback without WebGL.
8. **Honest ML wording:** "detection: ML (YOLO11n + U-Net)" and "hindcast: physics (Engine B)" — the UI never blurs the two; when a learned hindcast component exists, it gets a labeled badge and a model-registry entry first.

---

## 4. BUILD PRIORITY

| Tier | Pages | Why |
|---|---|---|
| **T1 — cannot demo credibly without** | P3, P5, P6, P7, P8, P9, P10, P12 | The investigator's chain, input → signed report; covers every PS clause |
| **T2 — production spine** | P0, P1, P4, P13, P18 | Auth, situational home, case files, history, audit |
| **T3 — operate at scale** | P2, P14, P15, P16, P17 | Triage, data ops, model governance, admin, health |
| **T4 — enrichment** | P11 | Cross-run vessel intelligence |

The existing globe monitor maps to P1; the current workspace panels map to P6–P10 views of the single Investigation Workspace layout.

**End of specification.**
