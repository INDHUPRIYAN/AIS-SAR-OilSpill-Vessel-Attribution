# Known issues — Nandha's core engines

Handbook Part G item 8, and §3's honesty rule: "no metric the system cannot back". These
are the things I would want to hear about before integrating, not after.

## 1. OpenDrift is not installed — Euler is the only drift path

**Status:** Phase 3 not started. There is no conda on the development machine, so the
`conda install -c conda-forge opendrift` chain has never run.

**Impact:** `engine_used` reports `"fallback"` / `"euler"` on every drift run. The
architecture slide claims OpenDrift `OpenOil` as the primary engine; today that is
aspirational.

**Mitigation:** the handbook's escalation rule (§5.2, pitfall #2) anticipated exactly
this and told me to write the Euler integrator first. It is complete, tested in both
directions, and validated against a hand-computed analytic case, so the demo path works
without OpenDrift. `environment.yml` is still the hand-written, **untested** file that
came with the skeleton — it must not be trusted until someone runs the export.

## 2. `age_hours_est` is dominated by an assumption, not by the measurement

Fay's law needs a volume; a mask gives only area. The two are closed with an assumed mean
slick thickness of 1 mm, and **age scales as `h^(−4/3)`** — assuming a slick 10× thinner
makes it ~21× older. Area itself enters only as `A^(2/3)`.

`age_confidence` is therefore hard-wired to `"low"` and the field should be read as an
order-of-magnitude bracket ("hours, not days"), never as a timestamp.

**It will visibly disagree with Engine B's origin window** — the demo scene reads ~30 h
from Fay against a 12 h hindcast window. That is not a bug in either engine. If the UI
shows both, lead with the hindcast window; it is the defensible number.

## 3. `origin_cloud.geojson` is large

2.4 MB for 300 particles × 25 hourly timesteps, because the contract asks for one Point
feature per particle per timestep. Fine for one investigation, potentially not fine with
several open at once.

**Dial:** `output_every_h` and `particles` in `config/drift.yaml`. Halving the cadence
roughly halves the file.

## 4. A non-deforming current field cannot localise the origin *time*

Uniform translation and rigid rotation both preserve a cloud's shape exactly, so the
backtracked particles never converge and there is no minimum to find. In that case the
engine widens the origin window to the **whole run** and says so in a warning.

This is the correct answer, not a degradation: an elongated slick under a non-deforming
flow implies a *moving* source, so the origin is a track in space-time rather than a
point — which is precisely what Engine C then resolves against AIS tracks. But the UI
must render the window as a range, or it will imply precision that does not exist.

Real GLORYS fields do have deformation, so this mostly affects synthetic and
constant-current cases.

## 5. Attribution top-1 is 62% on the hard tier

By design. `benchmark/RESULTS.md` reports 86% overall / 100% top-3, but the hard-tier
scenarios give the culprit no slowdown, no AIS gap, a plain cargo hull, and two to three
innocent tankers running the same course through the same region in the same window.
Several of those contain genuinely no evidence distinguishing the culprit.

All 7 misses across 50 scenarios were rank 2, five of them by a margin under 0.03.

## 6. The contract weights are not the best-performing weights

`benchmark/SENSITIVITY.md`: the contract weighting scores 86% top-1, while a
behaviour-heavy weighting (anomaly + `ais_gap` at 0.55 combined) scores 90%. I have
**not** re-tuned to chase that, because the §4.4 weights are what the contract shows and
what the UI displays. Worth a team decision rather than a silent change.

The same sweep is the answer to "did you tune the weights until it looked good?" — top-1
moves only 10 points across six wildly different weightings, including two that discard a
whole factor.

## 7. Gate tuning is unvalidated against real traffic

`spatial_buffer_km: 5.0`, `temporal_buffer_min: 90`, `max_axis_offset_deg: 45` are
reasoned defaults, not fitted ones. On the demo scene the 24 h cloud is already large
enough that a vessel planted 30 km from the origin sits only 5.5 km outside the buffered
region. With dense real AIS, expect more vessels through the gates than the mock suggests
— `spatial_buffer_km` is the dial.

The reassuring number: 0 of 50 benchmark culprits were discarded by the gates. A gate
false-negative is unrecoverable, so that is the metric I would watch first.

## 8. `slick.geojson` carries one Polygon per feature

Per the §4.2 contract. If simplification ever splits a slick into disjoint parts, only
the largest is written and a warning records it. Not observed on any test scene.

## 9. Repository hygiene (not mine to fix)

- The repo-root `.gitignore` has a bare `docs/` rule, which matches at any depth — so all
  four handbooks under `2_nandha_engines/docs/` are **untracked**. It needs to be `/docs/`
  to scope it to the root.
- The same file's bare `data/` rule is why the test fixtures under
  `tests/fixtures/data/` are untracked. That one is the behaviour I want; the committed
  demo inputs live in `samples/inputs/` instead.
- `contracts/schemas/` holds only `sar_detection.json` and `vessel_attribution.json`,
  whose field names do not match the handbook's `slick.geojson` / `suspects.json`. I code
  to handbook §4.2–4.4; the Pydantic models in `engines/schemas/` are the working
  definition and can be lifted into `contracts/` if wanted.
