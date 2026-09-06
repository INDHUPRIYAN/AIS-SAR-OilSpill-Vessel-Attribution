# Technical Note 01 — Drift Physics

**PS 26143 (NTRO) · OilGuard AI · Engine B (hindcast + forecast)**
*Companion code: `analysis_engines/engines/drift/` · Config: `analysis_engines/config/drift.yaml`*

This note states exactly what physics the drift engine solves, what it assumes, how
uncertainty is represented, and how the implementation was validated. It exists so the
origin window — the quantity attribution depends on — can be defended number by number.

---

## 1. The governing model

Surface oil is advected as an ensemble of Lagrangian particles. Each particle's velocity
is the sum of three terms:

```
dx/dt = u_current(x, t)  +  α · u_wind10(x, t)  +  u_diffusion
```

| Term | Source | Value / behaviour |
|---|---|---|
| `u_current` | `currents.nc` (`uo`,`vo`) — CMEMS GLORYS12V1 / SMOC, HYCOM fallback | trilinear interpolation in (time, lat, lon) |
| `α · u_wind10` | `wind.nc` (`u10`,`v10`) — ERA5, Open-Meteo fallback | windage α = 0.03, perturbed per particle |
| `u_diffusion` | horizontal eddy diffusivity K | random walk, step `R·√(2K·Δt)`, R ~ N(0,1) per axis |

**Windage (α ≈ 3%).** Oil at the surface moves with the current plus a few percent of the
10 m wind; 3% is the standard operational value (2–4% is the literature envelope).
Rather than trusting one number, α is *perturbed per particle* around 0.03, so the
windage uncertainty is inside the ensemble spread instead of hidden.

**Diffusion.** Sub-grid turbulence the forcing fields cannot resolve is modelled as a
Gaussian random walk with diffusivity K (default in `drift.yaml`). This term makes a
backtrack irreversible — which is physically correct, and is asserted by a test.

**Integration.** Forward Euler with a 90-minute step. Hindcast is the same integrator
with a negated timestep. Metre↔degree conversion uses each particle's own latitude,
re-evaluated every step, so nothing silently distorts away from the equator.

## 2. Hindcast → origin window

~300 particles are seeded across the detected slick polygon (the particles at
`timestep_h: 0` *are* the slick). They are integrated backwards for the configured
horizon (default 24 h; 12 h reachable via `--hours 12`). At every timestep the engine
fits a 90% confidence ellipse to the cloud (χ²-scaled covariance ellipse).

The **origin window** `[origin_window_start_utc, origin_window_end_utc]` is derived from
the evolution of the cloud — where the backtracked ensemble is most concentrated is the
most probable release time and place. Two honesty rules apply:

1. **The output is always a cloud, never a point** (standing rule 2). A single origin
   point would claim precision the physics cannot support.
2. **When the flow field does not deform the cloud** — uniform translation or rigid
   rotation — backtracked particles never converge and *no release time is recoverable
   from drift alone*. The engine then widens the window to the whole run and records the
   degradation in `origin_window.method`. (See `KNOWN_ISSUES.md` §4.)

## 3. Forecast

The same ensemble is integrated forward to +6 h, +12 h and +24 h. At each horizon the
engine emits **two** predicted-extent polygons: the **50%** contour (the likely core) and
the **90%** contour (the containment/response planning region), each hulled with
`shapely.concave_hull` over the particles inside that confidence region, plus an
`uncertainty_growth` ratio against the spread at seeding. Confidence levels come from
`forecast_confidence_levels` in `drift.yaml`.

## 4. Engine ladder

Three backends sit behind one signature (`backends.py`): **OpenOil → OceanDrift →
Euler**, selected by availability with `--engine auto`. Pinning a missing engine is an
error, never a silent downgrade — a run that used different physics than requested would
*mean* something different. Every output names the engine used. The Euler fallback is
permanent and fully tested; OpenDrift adds oil weathering (evaporation, emulsification,
entrainment) when its environment is present.

## 5. Provenance in the artefacts

Both `origin_cloud.geojson` and `forecast.geojson` carry `metadata.forcing`: the source
file per field, the variables matched, any fallback mode ("zero-current mode", "no wind
leeway"), the windage, and the engine. A saved output states what drove it — a two-year-old
investigation can be re-read without the run logs.

## 6. Validation

| Check | Result |
|---|---|
| Analytic backtrack — 24 h in a constant current vs hand-computed `seed − v·t` | within 15 m |
| Round trip — forward 12 h then backward 12 h, diffusion off | closes within 50 m |
| Irreversibility — round trip with diffusion **must not** close | asserted by test |
| Ellipse growth vs backtrack duration | monotone (sanity, not truth) |
| Suite | part of the 202-test `analysis_engines` suite |

## 7. Stated limitations

- No oil weathering on the Euler path (no evaporation/emulsification); horizons are kept
  ≤ 24 h partly for this reason.
- Forcing resolution bounds everything: 1/12° currents cannot see sub-mesoscale eddies;
  the diffusion term is the honest admission of that.
- Stokes drift is included only when the SMOC current product supplies it.
- The origin window inherits slick-age uncertainty from Engine A (`age_confidence` is
  reported "low" by design).

*All timestamps UTC with `Z`; all coordinates WGS84 `[lon, lat]` (standing rule 1).*
