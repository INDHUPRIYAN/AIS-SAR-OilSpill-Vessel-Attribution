# P16 — Report v2: compose, annex, review workflow, versioning, CSV

The report page used to assemble itself in the browser from six parallel
fetches. That works, and it means the report only exists while a tab is open:
nothing can be versioned, reviewed, approved, or quoted later with any
confidence that it still says what it said.

`services/report_compose.py` now builds the document server-side, once, from a
sealed run's own artefacts. Composed against the frozen flagship
`inv-gulf-flagship-20230108-2day` (digest `fd42e078f8366110`) — the body is in
[`flagship_report_body.json`](flagship_report_body.json), the CSV in
[`flagship_suspects.csv`](flagship_suspects.csv).

## Three rules the composer follows

**Artefacts only.** Every value came from a file the pipeline sealed. A stage
that did not run produces no section — not a placeholder, an average or a
hopeful default. Composing a report for an **unsealed** run raises rather than
producing a document that changes under the reader.

**The omission is named.** `body.omitted` lists sections that did not render.
For the flagship that is `["decisions"]` — nobody has recorded a verdict yet. A
silently missing section reads as "nothing to report" rather than "that stage
did not run", and those are different statements.

**Limitations come from a file.** The methodology section is parsed from
`docs/LIMITATIONS.md`. A limitations list written by hand into a composer drifts
out of date the moment the system changes, and a stale limitation is worse than
none — it is a false statement about what the system cannot do.

### That rule immediately caught a stale limitation

`docs/LIMITATIONS.md` item 1 read:

> **All AIS in this repository is synthetic and labelled SYNTHETIC everywhere,
> including the UI.** … the ingest adapters have not yet been exercised on a
> real archive.

Both halves became false when the flagship ingested two real MarineCadastre
archives. Corrected to say what is now true: Indian-waters runs use synthetic
AIS; US-waters runs use real archives; the **MarineCadastre** adapter has been
exercised end to end and the **DMA** adapter has not. A test pins that the old
claim cannot come back.

## Neutral language, and where the line actually is

The templated executive summary contains no word that assigns responsibility,
and it says so explicitly:

> 32 vessels were considered; 28 were excluded by the gates and 4 were ranked.
> The highest-ranked is MMSI 367653160 at Rank #1 · score 0.67.
> A rank is a position in a weighted, explainable ordering of vessels near the
> estimated origin. It is not a determination of responsibility.

A first version of the language test scanned the **whole body** for words like
"culprit" and failed — because `docs/LIMITATIONS.md` legitimately uses the word
discussing benchmark ground truth ("real confirmed-culprit corpora do not
exist"). Censoring a cited source to satisfy a lint would have made the report
*less* accurate. The test is now scoped to prose the composer **writes**; a
quoted document may discuss what this system may not assert.

## The annex is what `/verify` checks

Artefact hashes are **copied from the manifest, not recomputed**. An annex that
reassures while being wrong is the worst possible outcome, so the two cannot
drift:

```
annex.artefacts        == manifest.artefacts        (8 files)
annex.artefact_digest  == manifest.artefact_digest  fd42e078f8366110
verify_run(...).checked == len(annex.artefacts)
```

The annex also carries the code SHA, both model hashes (`unet-r34-fullcorpus-e48`,
`yolo11n-screen-dartis-2026-08-24`), the attribution weight profile, and a
per-layer source table (scene / currents / wind / AIS with provider and
`data_source`).

## The review state machine

```
draft ──submit──▶ in_review ──publish──▶ published ──revise──▶ draft (v+1)
```

**Published is immutable.** There is no edit endpoint and no force flag.
Revising composes the next version as a draft and leaves the published one
exactly as approved — a document that can change after sign-off is not a
signed-off document, and an investigation report is precisely where that
matters. Publishing is reviewer-gated (`admin`, `reviewer`, `investigator`) and
audited; the `analyst` role can compose and submit but not approve, because
approving your own conclusions is not review.

Versioning is keyed to `artefact_digest`, not a timestamp. `GET /api/reports/{id}`
returns `digest_matches_run`, so a report composed from bytes that have since
been re-sealed is **detectably stale** rather than quietly wrong.

## CSV export

21 fixed columns, golden-file tested. A silently reordered column is how a
spreadsheet ends up attributing one vessel's evidence to another.

```
run_id, scene_id, rank, mmsi, vessel_name, vessel_type, total_score, source,
proximity, temporal, trajectory, behaviour, ais_gap, vessel_prior,
closest_approach_km, time_in_origin_window_min, ais_gap_minutes,
course_delta_deg, min_sog_kn, track_points_in_cloud, reason
```

Note `vessel_name` is empty for all four flagship rows. MarineCadastre carried
no static identity for those MMSIs in that window — absence of data, left
absent rather than filled from elsewhere.

## Frontend

`ReportReview` adds what the printable page lacked, without rewriting the
layout most likely to be screenshotted: the review state and version, the
lifecycle buttons, the stale-digest warning, the provenance annex tables, and a
**DRAFT watermark that deliberately prints**. Hiding it under `@media print`
would defeat the only moment it matters — when an unapproved page leaves the
screen as a PDF.

Action buttons are shown to everyone and the server refuses what a role may not
do. Hiding a control teaches an operator the capability does not exist, rather
than that they lack the role for it.

## Tests

20, in `main_system/tests/test_report_v2.py`. Beyond P16's four named cases
(annex hashes equal `/verify`, publish immutability, role gates, CSV golden
file) they pin the judgement calls: an unsealed run cannot be reported on;
omitted sections are named; the age label travels with the age number; the
origin section carries its derivation method and the caveat when that method
localises nothing; the environment section names no mock provider; the AIS
section carries the rejection ledger so "we used real AIS" is falsifiable.

Composition tests skip cleanly when the flagship is not in the checkout.
