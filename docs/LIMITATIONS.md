# OilGuard AI — System Limitations

**PS 26143 (NTRO) · repo-wide honest statement · feeds the "honesty slide"**

Every claim the system makes is bounded by the items below. We state them because the
output may support enforcement: a limitation disclosed up front is context; one
discovered later is an impeachment. Per-module detail lives in
`analysis_engines/KNOWN_ISSUES.md`; this file is the system-level view.

---

## Data availability

1. **No public bulk historic AIS exists for Indian waters.** MarineCadastre covers US
   waters, DMA covers Danish/Baltic waters; AISStream is live-only. This is a
   data-availability fact, not an engineering gap. **Any run over Indian waters uses
   synthetic AIS, labelled SYNTHETIC everywhere including the UI.** Runs over US waters
   use real MarineCadastre archives: the flagship run
   `inv-gulf-flagship-20230108-2day` ingested the 2023-01-07 and 2023-01-08 archives
   (86,830 rows, 441 MMSI) and its attribution stage carries `source: real`. The
   MarineCadastre adapter has therefore been exercised end to end on real archives; the
   DMA adapter has not, and is still only unit-tested against format fixtures.
   `ensure_vessels` chooses real AIS only when it actually covers the run's computed
   origin window — a real archive for the wrong day is rejected in favour of clearly
   labelled synthesis, and the manifest's `ais` block records which path ran.
2. **Vessels without AIS are invisible to attribution.** A transponder-off or spoofing
   vessel cannot be ranked. The system can still say "no AIS-carrying candidate
   matches" — itself investigative signal. SAR ship detection (CFAR) crossed against
   AIS is the future-scope answer (see `resolution_and_sensor_choice.md` §3).
3. **Sentinel-1 revisit is not continuous.** A slick can appear and disperse between
   passes. Detection latency is bounded by the constellation, not by us.

## Detection physics

4. **SAR slick detection has a wind window** (~2–12 m/s). Below it the whole sea is
   smooth and everything resembles oil; above it slicks disperse and contrast collapses.
   Look-alikes (biogenic films, wind shadow, rain cells, upwelling) are intrinsic to
   the physics — which is why look-alike candidates are **classified and reported as
   `class: lookalike`, never silently deleted**, and why a human confirms before an
   alert becomes an incident. The current screening model is single-class, so the
   `phenomenon` sub-type field is reserved and not yet populated.
5. **Model metrics are dataset metrics.** mAP/IoU are measured on DARTIS and Trujillo
   holdouts. Performance on full real scenes depends on the calibration chain matching
   the training normalisation (`config/normalisation.yaml` — checked by an explicit
   histogram gate before any real-scene claim).

## Slick age and origin

6. **`age_hours_est` is an order-of-magnitude bracket, not a timestamp.** Fay's law
   needs a volume; a mask gives area only, closed with an assumed ~1 mm thickness, and
   age scales as thickness^(−4/3). `age_confidence` is hard-wired `"low"`. Where the
   Fay age and the hindcast origin window disagree, **the hindcast window is the
   defensible number** and the UI leads with it.
7. **The origin is always a cloud with a time window, never a point.** Under a
   non-deforming current field (uniform translation/rigid rotation) no release *time*
   is recoverable from drift alone; the engine then widens the window to the whole run
   and says so. Forcing resolution (1/12° currents) bounds everything downstream.
8. **The drift engine is an in-house Lagrangian particle integrator, dependency-free by
   design, and carries no oil weathering** (evaporation, emulsification). Horizons are
   kept ≤ 24 h partly for this reason. OpenDrift/OpenOil integration is architecturally
   supported and left as future work: it is not installed, and no run has used it.
   Run manifests written before 2026-09-01 may carry `engine: openoil` from a
   log-text provenance bug; the backend that actually ran in every one of them was
   the Euler integrator (fixed in `pipeline/engines.py`).

## Attribution

9. **Attribution is probabilistic ranking, not proof.** 86% top-1 / 100% top-3 on the
   50-scenario benchmark; **62% top-1 on the hard tier by design** — several hard
   scenarios contain genuinely no distinguishing evidence, and all 7 misses ranked the
   culprit 2nd. The system ranks candidates; a human decides.
10. **Benchmarks are synthetic.** They are the only source of attribution ground truth
    (real confirmed-culprit corpora do not exist), so they measure the algorithm, not
    the world. Real-world accuracy is additionally bounded by items 1–3.
11. **Gate thresholds are reasoned, not fitted** (5 km buffer, ±90 min, 45° axis
    offset). With dense real traffic, expect more vessels through the gates than the
    mock suggests. Reassurance: 0/50 benchmark culprits were lost to the gates — a gate
    false-negative is unrecoverable, so that is the metric we watch first.
12. **The deployed weights are deliberately not the benchmark-optimal ones.** A
    behaviour-heavy weighting scores 90% vs our 86%, but tuning weights to our own
    generator would be fitting the exam. The sensitivity sweep (top-1 moves only 10
    points across six wildly different weightings) is the defence against "you tuned
    until it looked good".

## Processing honesty

13. **Geocoding is GCP-warp, not full Range-Doppler terrain correction.** Over open
    ocean (flat) the difference is within GCP accuracy; near steep coasts it is not,
    and land is masked out anyway.
14. **Degraded runs are labelled, not hidden — on two separate axes.** Every stage
    carries an execution provenance (`source`: real code ran / fallback / mock, with the
    backend named in `engine_used`) **and** a data provenance (`data_source`: `sensor`
    bytes measured by an instrument or a reanalysis product, `synthetic` bytes we
    fabricated, or `cached` real data served from a local copy). The two are
    independent on purpose: a run of real code on the mock scene badges **SYNTHETIC**,
    and a run on a genuine Sentinel-1 scene with synthetic AIS badges the detection
    layers **SENSOR** and the attribution layer **SYNTHETIC**. The UI renders
    `data_source` first. A run's manifest records exactly which providers, models
    (`model_version`) and parameters produced it — reproducible years later.

## Explicitly out of scope

- **Super-resolution** was evaluated as an approach and deliberately excluded: geometry
  must come from native-resolution pixels, and SR on speckle fabricates texture. No SR
  code exists in this repository.
- **Orbit-file application** is not performed; absolute geolocation relies on the
  product's own annotation.
- **Speckle filtering** is deliberately absent from the calibration chain: it softens
  slick edges, and the slick geometry feeds attribution. Offered as an ablation, not a
  default.
