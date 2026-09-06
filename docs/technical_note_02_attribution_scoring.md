# Technical Note 02 — Attribution Scoring

**PS 26143 (NTRO) · OilGuard AI · Engine C (vessel attribution)**
*Companion code: `analysis_engines/engines/attribution/` · Config: `analysis_engines/config/attribution_weights.yaml` · Benchmark: `analysis_engines/benchmark/`*

Attribution is the part of this system that could support an enforcement action against
a named operator. This note explains why it is a transparent weighted score rather than
a learned model, states every gate, factor, weight and threshold, and reports measured
performance including the sensitivity of the result to the weights.

---

## 1. Why not a classifier

Two reasons, both structural:

1. **No ground truth exists.** There is no labelled corpus of real spills with confirmed
   culprits at the scale training requires. A model trained on synthetic culprits learns
   the generator, not the sea.
2. **Explainability is the requirement, not a nice-to-have.** The output may be examined
   by an investigator, a lawyer, or a court. "The network said 0.87" is not evidence.
   Every quantity in our score can be recomputed by hand from the AIS track and the
   origin cloud.

The system therefore **ranks candidates; a human decides**. The UI vocabulary is
*suspect / candidate / evidence* — never *guilty*.

## 2. Stage 1 — Gates (filter, with reasons)

Three gates run before any scoring. A vessel failing any gate is excluded **with the
reason recorded** and still appears in the output (`filtered: true`, `filter_reason`,
`failed_gates`) — the filtered-out list is shown in the UI because principled exclusion
is itself evidence of a fair process.

| Gate | Test | Threshold (config) |
|---|---|---|
| Spatial | track intersects the buffered origin region | buffer 5.0 km |
| Temporal | present in that region **during** the origin window | ± 90 min slack |
| Trajectory | course compatible with the slick's major axis (undirected) | ≤ 45° offset |

An AIS blackout is **not a free pass**: the temporal gate has a closest-approach
fallback, and proximity is measured along the *interpolated* path (§3), so going dark
while crossing the origin cannot lower a vessel's score — that would reward the exact
evasion the engine exists to catch.

## 3. Stage 2 — Six factors, each ∈ [0, 1]

`total_score = Σ wᵢ · sᵢ` with `Σ wᵢ = 1.0` (enforced by config renormalisation *and* a
contract test that recomputes the sum).

| Factor | Weight | Signal | Key thresholds |
|---|---|---|---|
| `proximity` | 0.30 | depth of the interpolated path inside the origin cloud, weighted by particle density | — |
| `temporal` | 0.20 | alignment of presence with the estimated discharge time | — |
| `trajectory` | 0.20 | angle vs slick major axis + path-overlap length | folded to [0, 90] |
| `anomaly` (behaviour) | 0.10 | slowdown, course change, loitering | loiter < 3 kn; 50% slowdown saturates; 60° course change saturates |
| `ais_gap` | 0.15 | transmission blackout overlapping the origin window | gap > 15 min counts; 30 min saturates |
| `prior` (vessel_prior) | 0.05 | discharge plausibility by type and draught | tanker 1.0 → passenger 0.2; draught ref 12 m, influence 0.15 |

Weight rationale: the two factors that place the vessel *at the origin at the right
time* (proximity + temporal) carry half the score; behaviourally suggestive but
circumstantial factors (anomaly, gap) carry a quarter; the prior is deliberately almost
negligible (0.05) so no vessel is suspected merely for being a tanker.

The slick axis used by `trajectory` is derived from the particles at `timestep_h: 0`
(they *are* the seeded slick); passing Engine A's measured axis via `--slick` agreed to
within 1.5° on the demo scene.

## 4. Evidence, not just scores

Every ranked suspect carries the frozen contract's `evidence` block
(`contracts/schemas/tabular.py`): `closest_approach_km`,
`time_in_origin_window_min`, `ais_gap_minutes`, `course_delta_deg` ∈ [0, 180],
`min_sog_kn`, `track_points_in_cloud`. These are the *same raw numbers* the generated
`reason` sentence quotes — the prose is produced from the evidence, never from the
score, so an investigator can check every claim against the numbers, and both against
the raw AIS track. Fields a run genuinely did not compute stay `null` rather than being
invented.

The applied weights are embedded in every `suspects.json` and rendered in the UI, so the
scoring of any historical run is auditable as it was, not as the config is today.

## 5. Measured performance

50 seeded scenarios, one planted culprit each (synthetic AIS is the only source of
ground truth — this is why the generator is a first-class component):

| Metric | Result |
|---|---|
| Top-1 accuracy | **86%** |
| Top-3 accuracy | **100%** |
| Culprit lost to the gates | **0 / 50** |
| Top-1 across six alternative weightings | 80–90% (`benchmark/SENSITIVITY.md`) |
| Hard tier (evasive culprits) top-1 | 62% — reported, not hidden |

The sensitivity sweep is the important defence: the ranking is **robust to the exact
weights**. If moving weights ±50% changed the answer, the weights would be the verdict;
they are not.

## 6. Stated limitations

- Gate thresholds are reasoned from operational literature, not fitted — there is no
  ground truth to fit them to (see `KNOWN_ISSUES.md` §5–7).
- The contract weights are deliberately *not* the best-performing set from the sweep —
  choosing weights to maximise benchmark accuracy would be fitting to our own generator.
- Synthetic benchmarks measure the algorithm, not the world: real-world accuracy is
  bounded by AIS coverage, spoofing, and origin-window width.
- A vessel with no AIS transponder at all is invisible to attribution; the system can
  only say "no AIS-carrying candidate matches" — itself useful evidence (dark-vessel
  cross-check against SAR ship detection is future scope).

*All timestamps UTC with `Z`; all coordinates WGS84 `[lon, lat]`. Synthetic data is
labelled `synthetic` everywhere, including the UI (standing rules 1, 9).*
