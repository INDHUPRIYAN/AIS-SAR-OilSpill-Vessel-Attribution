# Live AIS ingestion — evidence

Measured 2026-09-12 against the real AISStream provider with the key in the
gitignored `.env`. Two probes, 40 s each, temp database, no mocks.

## What was built

```
AISStream (wss)  ->  socket reader (no DB work)   ->  bounded queue (20 000)
                 ->  normalise + validate         ->  dedup (bounded, FIFO)
                 ->  ais_live      (one row per MMSI, the live map)
                 ->  AISStore      (day-partitioned Parquet, the investigation
                                    archive the pipeline already reads)
```

Protocol adapter: `ais_service/ais/aisstream.py` (pure, no DB, no socket).
Worker: `main_system/backend/services/ais_live.py`.
Routes: `main_system/backend/api/ais_live.py` (9 endpoints).
Tests: `main_system/tests/test_ais_live.py` — 54 passed, 1 skipped.

## Probe A — North Sea / Danish waters (AISStream has receiver coverage)

Subscription `[[3.0, 53.0, 14.0, 59.0]]` (longitude-first), 40 s:

| measure | value |
|---|---|
| messages received | 1021 |
| positions stored | 827 |
| static (identity) frames | 186 |
| duplicates detected and dropped | 6 |
| control frames (handshake) | 1 |
| unique vessels in `ais_live` | 799 |
| with a transmitted name | 781 |
| **heading NOT transmitted (stored NULL)** | **368 (46%)** |
| sog not transmitted | 4 |
| queue peak / capacity | 86 / 20 000 |
| frames dropped | 0 |
| `functionally_working` | **true** |

The 368 null headings are the point. AIS sends 511 for "heading unavailable";
storing 0 would read as due north. The same defect hit 29 679 of the
flagship's 86 830 MarineCadastre rows, and the live path does not repeat it.

Only 21 of 799 vessels carried a `vessel_type`, because identity arrives on a
~6 minute cycle against a 2–10 s position cycle and a static frame is merged
onto an existing row rather than creating one (a static frame has no position;
inserting a row for it would need a fabricated lat/lon). That figure rises with
run time. It is reported, not padded.

## Probe B — Bay of Bengal (the seeded theatre)

Subscription `[[80.5, 5.5, 95.3, 21.5]]`, 40 s:

```
state=connected  subscription_confirmed=True  functionally_working=False
counters: received 0, positions 0, statics 0
```

**AISStream has effectively no coverage of the Bay of Bengal.** It is relayed
by volunteer receivers, and there are few or none over the northern Indian
Ocean. Verified as coverage rather than a fault: on the same key, a
globally-bounded subscription delivered a firehose immediately (traffic from
Denmark, Canada, Spain, Finland, the Netherlands), so the subscription
mechanism, the key and the frame are all correct.

Reported as:

> subscription CONFIRMED and zero positions received. The subscription is
> valid, so this is receiver coverage, not a fault: AISStream is relayed by
> volunteer receivers and has little or none over the northern Indian Ocean.
> Vessels are present in this water and unheard — this is not an empty sea.

### Consequence, stated plainly

- The live vessel layer over the Bay of Bengal **will be empty in a demo**.
  That is the truth about the data, not a bug, and the UI must show the note
  rather than an empty map.
- The live archive therefore **cannot be presented as a record of Bay of
  Bengal traffic**. Historical attribution for that water still depends on
  bulk archives, and `PROVIDER_COVERAGE` says MarineCadastre is US waters and
  DMA is Danish/Baltic — so neither covers it either.
- A live-AIS demonstration should point the subscription at covered water
  (the North Sea box above). The zone model makes that a matter of drawing a
  zone, with no code change.

## Three defects found by running it

1. **Go timestamps.** `MetaData.time_utc` is
   `2026-09-12 04:33:21.123456789 +0000 UTC` — a space instead of `T`, nine
   fractional digits where Python 3.10 accepts six, and `+0000` where it
   requires `+00:00`. The first implementation returned None for **every real
   message**, which would have been a stream that connects, reports healthy
   and ingests nothing. Now a regex parser; five formats covered by tests.

2. **The subscription handshake counted as traffic.** AISStream answers with
   `{"MessageType": "SubscriptionConfirmation"}`. Counting it set "last
   message" to the instant of connection, so `functionally_working` —
   originally "connected and not silent" — held immediately. Probe B reported
   **`working=True` with zero vessels stored**. Now: handshake frames are
   control frames, counted separately, and `functionally_working` requires
   connected AND a position actually STORED AND recent traffic.

3. **`StaticDataReport` nests its fields.** AIS message 24 arrives in two
   halves and the provider nests them under `ReportA` (name) and `ReportB`
   (call sign, ship type, dimensions). Reading only the flat `ShipStaticData`
   shape found none of them — indistinguishable from a vessel transmitting no
   identity, so every Class B vessel silently had no name. Both shapes are now
   flattened through one path.

## Not verified — Parquet is blocked on this machine

`flush_archive` returned:

```
ImportError: Unable to find a usable engine; tried using: 'pyarrow', 'fastparquet'
```

A Windows Application Control policy is blocking pyarrow's native DLL on this
host (see the session memory note). So the **archive write path is implemented
and unit-tested for its failure behaviour — a failed flush re-buffers and
loses nothing — but has NOT been executed end to end.** The test that asserts
archived rows are contract-shaped and readable back through
`AISStore.query` is present and SKIPPED with that reason:

```
SKIPPED main_system/tests/test_ais_live.py: no usable parquet engine:
ImportError: DLL load failed while importing lib: An Application Control
policy has blocked this file.
```

Re-run `pytest main_system/tests/test_ais_live.py -rs` once Parquet works to
close this gap. Nothing else in the live-AIS path depends on it.

## Reproducing

```
.venv/Scripts/python.exe -m pytest main_system/tests/test_ais_live.py -q -rs
```

The probe scripts were session scratch files and are not committed; they only
called `AisLiveWorker(bboxes=[[...]]).start()` and printed `status()`.
