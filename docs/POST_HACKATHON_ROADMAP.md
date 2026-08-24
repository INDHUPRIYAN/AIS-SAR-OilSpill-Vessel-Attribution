# OceanTrace — Post-Hackathon Roadmap (before SIH Day)

**Written:** 2026-08-25, at the end of the 24-hour hackathon sprint.
**Purpose:** the ruthless list of what must happen between now and the SIH
finale, ordered by what changes the judges' verdict — not by what is fun to
build. Anyone on the team should be able to pick an item off this list and
know why it matters, what it depends on, and when it is done.

---

## Where the system stands today (context for the list)

| Area | State |
|---|---|
| Pipeline | 5/5 real stages on all 5 demo incidents, 0 mocks, ~25 s per run |
| Incidents | Chennai (Bay of Bengal), Red Sea, Persian Gulf, Gulf of Mexico, Malacca Strait — cinematic 11-step Incident Replay with selector + play-next |
| Detection | YOLO11n screen (mAP@0.5 0.626, bg-FP 3.1%) + U-Net segment (oil-IoU 0.878 **on a POC holdout**, not an untouched split) |
| AIS | 100% synthetic (kinematic generator, land-aware, per-scene fleets) — labelled `synthetic` everywhere |
| Forcing | Real CMEMS currents + Open-Meteo wind fetched live per incident |
| Attribution | Planted culprit ranks #1 in 4/5 incidents (0.61–0.93); Malacca #6 — kept as the honest hard case |
| Tests | 433 passed / 0 failed, runnable from repo root |
| Data downloads | Trujillo Part 3 done (in use); Part 1 at ~75% resuming; Part 2 queued |

Honest caveats currently carried by the system (each maps to an item below):
the Chennai demo raster is synthetic; the 4 world incidents have assigned
(not true) acquisition dates; segmentation metrics come from a Part-III
holdout; the screening model is unresponsive outside the DARTIS domain; AIS
has never touched real data.

---

## P0 — Credibility. Do these or a sharp judge dismantles us.

### 0. Push everything to the personal GitHub — tonight
A full day of work (replay UI, generator rewrite, six bug-fix rounds, three
new incidents, theme system) is uncommitted on one laptop. One disk failure
erases the hackathon.
- Pull → commit → push to the **personal** account (`INDHUPRIYAN`). Never the
  office account (`IndhuPriyan-sustainworld`).
- **Done when:** `git status` clean, remote shows today's work.
- Effort: 30 min.

### 1. Retrain on Trujillo Parts 1–2, re-measure on untouched Part 3
The single biggest accuracy lever left.
- Blocked on the Parts 1+2 download finishing (a background watcher pings on
  completion / stall). Then: `prepare_trujillo --discard` per part, overnight
  U-Net retrain, `ml.evaluate` against Part 3 as a **genuinely untouched**
  test split.
- Kills the "POC holdout" caveat on the Analytics page; should also fix the
  over-segmentation on unseen scenes (44% mask coverage observed on one
  Trujillo oil scene — far too high).
- **Done when:** Analytics shows metrics with the `poc_holdout` flag false and
  the warning banner gone.
- Effort: 1 overnight training run + half a day of evaluation.

### 2. Real AIS ingestion — at least one real incident end-to-end
AIS is the half of the problem statement that has never touched real data.
- Danish DMA and MarineCadastre archives are free. The parsers already exist
  (`5_krishnan_ais_service/ais/dma_ingest.py`, `mc_ingest.py`) and are
  untested against real files.
- Pick an archive date/region, ingest, run attribution on genuine traffic.
  Even without a known culprit, a vessel layer that says `source: REAL`
  transforms the pitch from "simulation" to "system".
- **Done when:** one investigation renders with real AIS tracks and the
  provenance badge reads REAL.
- Effort: 1 day (parsing surprises are likely; archives are big).

### 3. Calibrate the real Chennai Sentinel-1 scene
The 1.18 GB scene from one day after the 2017 Ennore spill is already
downloaded, with calibration and noise LUTs (`data/scenes/S1A_CHENNAI_20170129/`).
- Write `ml/calibrate.py`: sigma0 = DN² / sigmaNought² → dB, GCP-warp to
  EPSG:4326, subset to the Chennai bbox, write scene GeoTIFF + scene_meta.
- Makes the flagship incident **real imagery of a real Indian spill** —
  exactly what an NTRO judge will ask for — and retires the "demo raster is
  synthetic" caveat.
- **Done when:** the Chennai incident in the replay uses the calibrated scene
  and detection still finds the slick.
- Effort: 1 day.

### 4. Fine-tune the screening model on Trujillo tiles
The DARTIS-trained screen produces zero detections outside its home domain,
so look-alike rejection silently switches itself off on 4 of 5 incidents
(the code correctly refuses to treat blanket silence as rejection).
- After Parts 1–2 land: crop YOLO training patches from Trujillo oil scenes,
  short fine-tune of the screen, re-export ONNX (keep the fingerprint chain).
- The Persian Gulf incident already shows the payoff where the screen *is*
  responsive: "rejected 8 look-alikes, confirmed 1 oil" is a demo moment.
- **Done when:** the screen fires on Trujillo-domain scenes and background-FP
  stays under ~10%.
- Effort: half a day once data is tiled.

---

## P1 — Strong wins. High value, contained effort.

### 5. Auto-fetch forcing inside the pipeline
Today a new scene needs a manual metocean CLI call before drift can run.
The pipeline should invoke the chain itself when `resolve_metocean` finds no
grid covering the scene bbox/date.
- Makes "pick any scene, press run" literally true.
- Effort: half a day. **Done when:** a brand-new bbox completes 5/5 with no
  manual step.

### 6. Evidence report export (PDF)
One button on the Evidence step: incident summary, SAR chip, origin map,
ranked suspects with scores, the WHY-checklist, provenance labels, and the
honest caveats. Judges and agencies think in reports; this is what an
investigator would actually file. React-to-print is sufficient.
- Effort: half a day.

### 7. Fix the ellipse writer + re-run the attribution benchmark on realistic traffic
- Engine B writes `semi_major_m: 0.0` on **every** origin ellipse; the UI
  currently works around it by measuring the ring geometry. Fix at the source
  (`2_nandha_engines`, drift output writer).
- Nandha's 50-scenario benchmark (the 86% top-1 headline) was measured on
  straight-line tracks with random headings. Port the kinematic traffic
  model into `benchmark/scenarios.py` and re-measure. Accept the number may
  drop — **a defensible 78% beats an indefensible 86%.**
- Effort: 1 day. **Done when:** `benchmark/results.json` regenerated and the
  Analytics page quotes the new numbers.

### 8. Offline demo hardening
The map basemaps (CARTO / Esri tiles) are the **only network dependency left
in the demo path** — venue Wi-Fi will betray us.
- Cache tiles for the 5 incident bboxes (or add a fallback plain-ocean map
  style); rehearse the full replay with Wi-Fi off.
- Verify the offline replay path (26+ runs replay from disk with zero
  network).
- `onnxruntime-gpu` still silently falls back to CPU (`cublasLt64_12.dll`
  not on PATH) — fix the PATH or accept CPU knowingly (~11 s detect).
- Effort: half a day + one full offline rehearsal.

### 9. Draw the origin *corridor* in replay step 08
The teal marker is the mid-window snapshot; the true origin region is a
corridor over the whole time window (18 × 58 km on the Red Sea incident).
A judge will ask "why does no vessel touch the circle?" — the map should
answer it before it is asked: faint dashed corridor outline labelled
"origin region over window" behind the pulsing marker.
- Effort: 2–3 hours.

---

## P2 — Polish. Only if time remains.

- **Malacca case-study slide**: why the culprit ranked #6 of 24 — turn the
  weakness into the "we don't fake results" proof point.
- **Krishnan's `tests/` is still empty.** Even five tests on generator
  invariants (culprit pinned at origin, zero on-land fixes, AIS gap fixes
  really missing, per-fleet MMSI uniqueness, legacy path bit-stable) protects
  the demo from regressions.
- **Timestamps honesty note** in the About page for the 4 world incidents:
  real Sentinel-1 imagery, assigned acquisition dates, real forcing fetched
  for those dates.
- **Cross-machine rehearsal**: 1080p projector, Windows 125% scaling,
  Chrome and Edge, both themes.
- **Pitch scripts**: 90-second and 5-minute versions keyed to the 11 replay
  steps; decide in advance which incident opens (Persian Gulf has the best
  detection story; Chennai has the India story — stronger once item 3 lands).

---

## Non-technical P0

- **Change the password pasted into an AI chat during the hackathon**
  (`indhupriyanmofficial@gmail.com`). It exists in a transcript and those
  credentials touch the CDSE/CMEMS accounts used in the demo.
- Keep API keys in `.env` only; the Keys page masks values — keep it that way.

---

## Suggested execution order (dependency-aware)

```
Tonight        : [0] push  →  start [2] real-AIS + [3] Chennai calibration in parallel
While download : [5] auto-fetch forcing, [6] PDF export, [9] origin corridor
Parts 1–2 land : [1] retrain overnight  →  [4] screen fine-tune  →  [7] benchmark re-run
Final stretch  : [8] offline hardening  →  P2 polish  →  full rehearsals
```

The watcher on the Trujillo download will announce Part 1 / Part 2
completion or a stall; item 1 starts the moment it fires.
