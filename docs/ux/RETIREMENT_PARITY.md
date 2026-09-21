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

## `/incident` (Incident Replay) → workspace — DEFERRED at P7

Checked at the end of P7. Two of the three conditions hold; the third does not,
so the route **stays alive** and keeps working, per the approved decision
("if parity cannot be reached in P7, keep the old route alive, log it as
deferred, and continue").

- [x] A8 inside the workspace: the presentation plays beat by beat from real
      artefacts, holds on unfinished stages, stops on failure — e2e
      `investigation.spec.js` 6, green through P3–P7 on the one map engine.
- [x] The old address redirects: `/incident` → `/operations/replay`
      (permanent, `lib/urls.canonical`), and every in-page link now spells the
      canonical address (e2e S7).
- [ ] **C8 inside the workspace: not reached.** The replay page carries an
      incident-driven transport with eleven map-mode presets and a step-by-step
      reveal that the workspace's stage timeline does not reproduce. Building
      it into the workspace is a phase of its own, and the honest alternative —
      dropping it — would lose a capability an operator uses.

What is true today: one engine, one camera and one palette across both, so the
replay is no longer a second map stack. What remains is a second *page*.

Next step when it is picked up: fold the eleven presets into the workspace's
layer control as named views, drive them from the stage timeline, then flip
`/operations/replay` to a redirect and delete `Incident.jsx`, `CommandMap.jsx`,
`ReplayGlobe.jsx`, `ReplayControls.jsx` and `incident.css`. The decorative
radar/scanline/lock-on animation is **not** ported (functionality matrix row 20).
