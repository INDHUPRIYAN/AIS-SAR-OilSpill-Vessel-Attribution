# BACKEND_GAPS — UI requirements the backend cannot serve today

Started in Phase 0; appended to in each phase. Per hard rule 1 the frontend
does not invent this data. Each row states what the UI will show instead.

| # | Requirement (brief) | What exists | Gap | UI until closed |
|---|---|---|---|---|
| G1 | Hindcast 50% and 90% contours | `origin_cloud.geojson` ellipses are all `confidence_level = 0.9` (schema allows both) | 50% never written by Engine B | show 90% only, labelled; BAYES-TRACK HDR shown where a job exists |
| G2 | Coastline-impact ETA on forecast | nothing computes landfall anywhere | whole feature | omitted; no warning is fabricated |
| G3 | Notification when a run completes or fails | `raise_alert` fires only for `new_scene` and auto-incident `detection` | no run-lifecycle alerts | client-side notice for runs this browser started or is watching; Dashboard "Needs attention" from `/api/runs` |
| G4 | Percent progress within a stage | `/api/jobs/{id}` gives `stages_done/total`, `current_stage`; no per-tile progress | intra-stage percent | indeterminate bar + "stage 2 of 5" |
| G5 | Real PDF export | HTML report + browser print; server exports CSV/JSON | server-side PDF | "Open printable report" → print dialog |
| G6 | Vessel DWT, owner, port calls, imagery | no provider supplies them | whole fields | rows removed |
| G7 | "Created by" on a run | run registry records no actor (audit trail does) | field | link to audit entries for the run |
| G8 | Wind/current for an arbitrary scrubbed timestamp outside a run | forcing served per run (`/api/runs/{id}/forcing_field`); HDF5 reads not thread-safe, 503 under contention | global wind/current modes on the Live Map | wind/current modes available only with a run in context; otherwise "Select an investigation to load forcing" |
| G9 | Live AIS coverage over the Bay of Bengal | AISStream delivers 0 messages there (measured 2026-09-12) | provider coverage | "Live AIS unavailable in this region" + stream status |
| G10 | Search deep links | `search.py:103,150` emit `/incidents?incident=` and `/investigation?scene=` | backend emits non-canonical URLs | frontend redirects both shapes |
| G11 | Demo case as a filed investigation | flagship run is CLI-produced, `registry_source = reconciled`, `investigation_id` NULL | unfiled | to verify in P10 whether an investigation can be created over the same scene and replay it (replay falls back to any complete run of the scene) |
| G12 | Link from a pipeline run to its BAYES-TRACK jobs | `from_run` creates jobs; whether job listings expose `run_id` for lookup is unverified | possibly a filter | to verify in P4; otherwise client-side filter of `/api/hindcast/jobs` |
| G13 | Oil thickness / volume, validated age model | age is a damping + Fay heuristic, always LOW confidence | science | age shown as "≈ N h — Confidence: Low"; no volume |
| G14 | Per-polygon AOI scene search | search takes a bbox | polygon filter | UI states the polygon's bounding box is searched |
