# Retirement parity checklist

Two standalone destinations are approved for retirement, conditionally. A
route flips to a permanent redirect only when every box for it is ticked, with
the evidence named. Until then the page stays mounted and reachable.

Redirects are permanent once made (`lib/urls.js` `canonical()`).

## `/hindcast` → `/system/engines` — DONE in P1

This was a relocation, not a rebuild: the same `HindcastEngines` page is
mounted at the new address, so parity holds by construction.

- [x] D1 backend: 7 engines in order, demo + from_run, WS auth + stream,
      failing engine skips downstream — `pytest main_system/tests/test_hindcast_api.py
      test_hindcast_engines.py test_hindcast_science.py` (green in the P1 gate run)
- [x] Page renders at `/system/engines` inside the shell with no script error — e2e `shell.spec.js` S1
- [x] `/hindcast` and `/hindcast?job=…` land on `/system/engines` with the query kept — e2e S2, unit `routes.test.jsx`
- [x] Listed under System ▸ Engine Monitoring; not in the main sidebar — unit `routes.test.jsx`

Open (P4, not a retirement condition): the BAYES-TRACK posterior also appears
inside the workspace Origin stage.

## `/incident` (Incident Replay) → workspace — NOT YET

P1 only moved it to `/operations/replay` (old address redirects). The page is
still the replay. It retires when the workspace covers it:

- [ ] A8 inside the workspace: presentation plays beat by beat from real
      artefacts, holds on unfinished stages, stops on failure — e2e
      `investigation.spec.js` 6 + unit `cinematic`, `useCinematic` (already true
      today; re-verify after P3–P5 restructure the workspace)
- [ ] C8 inside the workspace: replay of an **incident's** run on both the
      regional map and the globe, opened from the incident record
      (`/operations/incidents` → "Replay" → workspace with `present=1`)
- [ ] Every replay layer has a workspace equivalent: SAR scene + mask,
      hindcast trajectories + density, origin, forecast horizons, AIS tracks
      with candidates, wind/current from the run's forcing grid
- [ ] `?run=` and `?autoplay=1` deep links map to the workspace address
- [ ] The decorative radar/scanline/lock-on animation is **not** ported
      (functionality matrix row 20) — this is a deliberate non-parity
- [ ] New e2e: open an incident, press Replay, assert the workspace plays its run

Then: `/operations/replay` and `/incident` both redirect to the workspace;
`Incident.jsx`, `CommandMap.jsx`, `ReplayGlobe.jsx`, `ReplayControls.jsx`,
`incident.css` are deleted.
